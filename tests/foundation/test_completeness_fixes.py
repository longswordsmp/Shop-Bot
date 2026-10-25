"""Regression tests for gaps found by the final completeness audit."""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil

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
from tests.helpers import ROOT, TestEnv, provision_channels


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
    shutil.copy(ROOT / "config" / "settings.toml", config_dir / "settings.toml")  # every feature switched off
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
