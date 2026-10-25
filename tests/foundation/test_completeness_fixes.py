"""Regression tests for gaps found by the final completeness audit."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from pathlib import Path

import discord
import pytest

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import Kind, Registry, ck
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import state
from shopbot.features import core_keys as core
from shopbot.features.admin import service as admin_service
from shopbot.features.setup.service import run_setup
from shopbot.features.tickets import models as ticket_models
from shopbot.features.tickets import repo as ticket_repo
from shopbot.ui.routing import Ctx, Route
from tests.fakes import click
from tests.helpers import BASE_SETTINGS, ROOT, TestEnv, provision_channels


def test_declared_placeholder_without_value_renders_empty_and_other_braces_stay() -> None:
    key = ck("x.msg", Kind.DESCRIPTION, "f", "g", "doc", placeholders=("error_id",))
    store = ContentStore(Registry([key]), {"x.msg": "Oops {error_id} {not_a_placeholder}"})
    assert store.render("x.msg") == "Oops  {not_a_placeholder}"
    assert store.render("x.msg", error_id="abc") == "Oops abc {not_a_placeholder}"


async def test_generic_error_can_show_the_logged_reference(tenv: TestEnv) -> None:
    async def crash(ctx: Ctx) -> None:
        raise ValueError("internal detail")

    tenv.app.content.set_override(core.ERROR_GENERIC, "Something went wrong. Reference: {error_id}")
    tenv.app.router.add(Route("zz", "boom", crash, "component", None))
    interaction = await click(tenv.app, user=tenv.member(), custom_id="sb:zz:boom")
    assert interaction.last_sent is not None
    description = interaction.last_sent.embeds[0].description or ""
    assert re.fullmatch(r"Something went wrong\. Reference: [0-9a-f]{12}", description), description
    assert "internal detail" not in description


async def _publish_ticket_panel(tenv: TestEnv) -> state.PublishedMessage:
    await provision_channels(tenv)
    await run_setup(tenv.app, tenv.guild, actor_id=tenv.member(admin=True).id)
    record = await tenv.app.db.run(state.published_get, tenv.app.env.guild_id, "tickets:panel")
    assert record is not None
    return record


async def test_clearing_required_text_withdraws_the_live_panel(tenv: TestEnv) -> None:
    app = tenv.app
    key = "tickets.panel.title"
    values = dict(tenv.content)
    title = values.pop(key)
    app.content.replace(file_values=values)
    admin = tenv.member(admin=True)
    saved = await admin_service.save_override(app, key, title, actor_id=admin.id)
    assert saved.saved and app.ready("tickets")
    record = await _publish_ticket_panel(tenv)
    channel = tenv.guild.get_channel(record.channel_id)

    await admin_service.clear_override(app, key, actor_id=admin.id)
    assert not app.ready("tickets")
    assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is None
    assert not any(m.id == record.message_id for m in channel.messages)
    actions = [e.action for e in await app.db.run(audit_repo.recent, app.env.guild_id)]
    assert "panels.withdrawn" in actions


async def test_reload_that_disables_a_feature_withdraws_its_panel(tenv: TestEnv) -> None:
    app = tenv.app
    record = await _publish_ticket_panel(tenv)
    config_dir = app.config_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE_SETTINGS, config_dir / "settings.toml")  # the test base layout: every feature switched off
    (config_dir / "content.toml").write_text("", encoding="utf-8")
    result = await app.reload_config()
    assert result.ok
    assert not app.ready("tickets")
    assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is None
    channel = tenv.guild.get_channel(record.channel_id)
    assert not any(m.id == record.message_id for m in channel.messages)


def test_fixture_mode_builds_and_checks_every_state() -> None:
    from tools.check_limits import run_checks

    report = asyncio.run(run_checks(ROOT / "config", all_features=True, fixtures=True))
    assert report.states_checked >= 100
    assert report.violations == []
    assert report.unrouted == []
    assert report.errors == []
    assert report.incomplete == {}


async def test_full_ticket_category_is_refused_cleanly(tenv: TestEnv) -> None:
    app = tenv.app
    await provision_channels(tenv)
    category = app.resources.category(tenv.guild, "tickets")
    assert category is not None
    for index in range(50 - len(category.channels)):
        tenv.guild.add_text_channel(name=f"filler-{index}", category=category)
    customer = tenv.member()
    interaction = await click(app, user=customer, custom_id="sb:tk:open:general")
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tenv.text(core.ERROR_GENERIC)
    assert len(category.channels) == 50  # nothing was created
    tickets = await app.db.run(ticket_repo.list_tickets, app.env.guild_id)
    assert [t.status for t in tickets] == [ticket_models.FAILED]
    events = await app.db.run(audit_repo.recent, app.env.guild_id, limit=50)
    assert any("category_full" in str(e.details) for e in events)


def test_checkout_menu_placeholders_are_required() -> None:
    from shopbot.features.checkout import content_keys as checkout_keys
    from tests.helpers import make_settings

    settings = make_settings({"checkout": {"fulfillment_options": [{"id": "a", "fee_minor": 0}]}})
    keys = {k.key: k for k in checkout_keys.content_keys(settings)}
    for key in (
        checkout_keys.BROWSE_SELECT_PLACEHOLDER,
        checkout_keys.CART_EDIT_PLACEHOLDER,
        checkout_keys.FULFILLMENT_SELECT_PLACEHOLDER,
    ):
        assert keys[key].required, key


async def test_shutdown_drain_waits_for_running_clicks_and_refuses_new_ones(tenv: TestEnv) -> None:
    release = asyncio.Event()
    finished: list[bool] = []

    async def slow(ctx: Ctx) -> None:
        await release.wait()
        finished.append(True)
        await ctx.ack()

    tenv.app.router.add(Route("zz", "slow", slow, "component", None))
    running = asyncio.create_task(click(tenv.app, user=tenv.member(), custom_id="sb:zz:slow"))
    await asyncio.sleep(0)
    drain = asyncio.create_task(tenv.app.router.drain(5))
    await asyncio.sleep(0)
    assert not drain.done()  # waits for the running handler

    late = await click(tenv.app, user=tenv.member(name="late"), custom_id="sb:zz:slow")
    assert late.last_sent is not None
    assert late.last_sent.embeds[0].description == tenv.text(core.ERROR_UNAVAILABLE)

    release.set()
    assert await drain is True
    await running
    assert finished == [True]  # only the click that started before shutdown ran


async def test_shutdown_drain_is_bounded(tenv: TestEnv) -> None:
    never = asyncio.Event()

    async def stuck(ctx: Ctx) -> None:
        await never.wait()

    tenv.app.router.add(Route("zz", "stuck", stuck, "component", None))
    running = asyncio.create_task(click(tenv.app, user=tenv.member(), custom_id="sb:zz:stuck"))
    await asyncio.sleep(0)
    assert await tenv.app.router.drain(0.05) is False
    running.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await running


async def test_content_clear_defers_before_withdrawing_panels(tenv: TestEnv) -> None:
    from tests.admin.util import run_command

    app = tenv.app
    key = "tickets.panel.title"
    values = dict(tenv.content)
    title = values.pop(key)
    app.content.replace(file_values=values)
    admin = tenv.member(admin=True)
    await admin_service.save_override(app, key, title, actor_id=admin.id)
    await _publish_ticket_panel(tenv)
    interaction = await run_command(tenv, "clear", group="content", user=admin, key=key)
    calls = interaction.response.calls  # type: ignore[attr-defined]
    assert calls[0][0] == "defer"  # acknowledged before the Discord work (3-second deadline)
    assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is None
    assert interaction.last_sent is not None  # the result still reaches the admin (follow-up)


async def test_content_set_that_makes_a_feature_not_ready_withdraws_its_panel(tmp_path: Path) -> None:
    from tests.tickets.util import make_env

    tenv = await make_env(tmp_path, {"tickets": {"channel_name_pattern": "{prefix}-{user}-{number}"}})
    try:
        app = tenv.app
        assert app.ready("tickets")
        record = await _publish_ticket_panel(tenv)
        admin = tenv.member(admin=True)
        # Valid on its own (60 characters), but the longest possible channel name would exceed 100 characters.
        result = await admin_service.save_override(
            app, "tickets.category.general.channel_prefix", "p" * 60, actor_id=admin.id
        )
        assert result.saved and not app.ready("tickets")
        assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is None
        channel = tenv.guild.get_channel(record.channel_id)
        assert not any(m.id == record.message_id for m in channel.messages)
    finally:
        await tenv.close()


async def test_expected_failures_answered_with_the_generic_text_carry_a_logged_reference(
    tenv: TestEnv, caplog: pytest.LogCaptureFixture
) -> None:
    app = tenv.app
    app.content.set_override(core.ERROR_GENERIC, "Something went wrong. Reference: {error_id}")
    await provision_channels(tenv)
    category = app.resources.category(tenv.guild, "tickets")
    assert category is not None
    for index in range(50 - len(category.channels)):
        tenv.guild.add_text_channel(name=f"filler-{index}", category=category)
    with caplog.at_level(logging.WARNING):
        interaction = await click(app, user=tenv.member(), custom_id="sb:tk:open:general")
    assert interaction.last_sent is not None
    description = interaction.last_sent.embeds[0].description or ""
    match = re.fullmatch(r"Something went wrong\. Reference: ([0-9a-f]{12})", description)
    assert match, description
    logged = [
        getattr(r, "error_id", None) for r in caplog.records if getattr(r, "event", "") == "interaction_generic_error"
    ]
    assert logged == [match.group(1)]  # the same reference is in the log, with the cause


async def test_failed_command_shown_to_a_non_staff_member_keeps_the_reference(
    tenv: TestEnv, caplog: pytest.LogCaptureFixture
) -> None:
    from discord import app_commands

    from shopbot.commands import on_tree_error
    from tests.fakes import FakeInteraction

    tenv.app.content.set_override(core.ERROR_GENERIC, "Reference: {error_id}")
    interaction = FakeInteraction(
        client=tenv.app.bot, user=tenv.member(), guild=tenv.guild, type=discord.InteractionType.application_command
    )
    with caplog.at_level(logging.ERROR):
        await on_tree_error(interaction, app_commands.AppCommandError("boom"))  # type: ignore[arg-type]
    assert interaction.last_sent is not None
    description = interaction.last_sent.embeds[0].description or ""
    match = re.fullmatch(r"Reference: ([0-9a-f]{12})", description)
    assert match, description
    assert [getattr(r, "error_id", None) for r in caplog.records if getattr(r, "event", "") == "command_failed"] == [
        match.group(1)
    ]


async def test_listing_refresh_overlapping_a_withdrawal_does_not_repost(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shopbot.core.panels import withdraw_and_audit
    from shopbot.features.catalog import service as catalog_service
    from tests.catalog.util import add_product, listing_channel

    app = tenv.app
    channel = await listing_channel(tenv)
    for index in range(7):
        await add_product(tenv, f"[p{index}]")
    await catalog_service.request_listing_refresh(app)
    assert len(channel.messages) == 2
    await add_product(tenv, "[new]")  # the next refresh edits both messages

    required = next(k.key for k in app.content.registry.all() if k.feature == "catalog" and k.required)
    original = catalog_service.upsert_message
    withdrawn = False

    async def withdraw_mid_refresh(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal withdrawn
        if not withdrawn:  # an admin clears catalog text while the refresh is posting
            withdrawn = True
            values = dict(tenv.content)
            values[required] = ""
            app.content.replace(file_values=values)
            app.recompute_readiness()
            await withdraw_and_audit(app, actor_id=None, reason="content_clear")
        return await original(*args, **kwargs)

    monkeypatch.setattr(catalog_service, "upsert_message", withdraw_mid_refresh)
    await catalog_service.request_listing_refresh(app)
    assert withdrawn and not app.ready("catalog")
    assert await app.db.run(state.published_with_prefix, app.env.guild_id, "catalog:listing:") == []
    assert channel.messages == []
