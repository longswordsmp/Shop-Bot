from __future__ import annotations

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.features.server_keys import channel_name_key
from shopbot.features.setup.service import setup_lock
from tests.fakes import http_error
from tests.helpers import TestEnv
from tests.setup.util import audit_events, invoke_setup, setup_command


async def test_command_metadata(tenv: TestEnv) -> None:
    command = setup_command(tenv)
    assert command.default_permissions == discord.Permissions(administrator=True)
    assert command.guild_only is True
    params = {p.name: p for p in command.parameters}
    assert params["dry_run"].default is False and params["publish"].default is True
    assert command.description and all(p.description for p in command.parameters)


async def test_admin_runs_setup_and_gets_private_report(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    interaction = await invoke_setup(tenv, admin)
    assert interaction.response_kinds() == ["defer"]
    assert interaction.response.calls[0][1] == {"ephemeral": True, "thinking": True}
    assert interaction.sent, "the report must be delivered"
    first = interaction.sent[0]
    assert getattr(first, "ephemeral", False) is True
    assert first.embeds[0].title == tenv.app.op("server.setup.title")
    field_names = {f.name for m in interaction.sent for e in m.embeds for f in e.fields}
    for section in ("roles", "categories", "channels", "panels", "problems"):
        assert tenv.app.op(f"server.setup.section.{section}") in field_names
    created = [c for c in tenv.guild.text_channels if c.name == tenv.text(channel_name_key("shop"))]
    assert len(created) == 1
    events = await audit_events(tenv, "setup.run")
    assert [e.actor_id for e in events] == [admin.id]


async def test_dry_run_option_is_passed_through(tenv: TestEnv) -> None:
    interaction = await invoke_setup(tenv, tenv.member(admin=True), dry_run=True)
    assert interaction.sent[0].embeds[0].title == tenv.app.op("server.setup.title_dry_run")
    assert tenv.guild.categories == []


async def test_publish_option_false_skips_panels(tenv: TestEnv) -> None:
    interaction = await invoke_setup(tenv, tenv.member(admin=True), publish=False)
    values = "\n".join(f.value or "" for m in interaction.sent for e in m.embeds for f in e.fields)
    assert tenv.app.op("server.setup.publish_skipped") in values


async def test_staff_and_customers_are_denied_with_configured_text(tenv: TestEnv) -> None:
    for user in (tenv.member(staff=True), tenv.member()):
        interaction = await invoke_setup(tenv, user)
        assert interaction.last_sent is not None
        assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    assert tenv.guild.categories == [] and tenv.guild.audit == []
    assert await audit_events(tenv, "setup.run") == []


async def test_denial_without_configured_text_is_a_silent_ack(tenv: TestEnv) -> None:
    tenv.app.content.replace(file_values={})  # no customer-facing text configured at all
    interaction = await invoke_setup(tenv, tenv.member())
    assert interaction.sent == []
    # Slash commands have no silent acknowledgement: the deferred "thinking" response is deleted right away.
    assert interaction.response_kinds() == ["defer"]
    assert getattr(interaction, "original_deleted", False) is True


async def test_concurrent_run_is_refused(tenv: TestEnv) -> None:
    lock = setup_lock(tenv.app)
    async with lock:
        interaction = await invoke_setup(tenv, tenv.member(admin=True))
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].title == tenv.app.op("server.setup.busy")
    assert tenv.guild.categories == []


async def test_report_delivery_failure_does_not_undo_the_work(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    from tests.fakes import FakeInteraction

    command = setup_command(tenv)
    interaction = FakeInteraction(
        client=tenv.bot, user=admin, guild=tenv.guild, type=discord.InteractionType.application_command
    )

    async def expired(*args: object, **kwargs: object) -> None:
        raise http_error(discord.NotFound, 404, 10015, "Unknown Webhook")

    interaction.followup.send = expired  # type: ignore[method-assign, assignment]
    await command.callback(interaction, False, True)
    assert len(tenv.guild.categories) == len(tenv.app.settings.server.categories)
    assert len(await audit_events(tenv, "setup.run")) == 1


async def test_unavailable_server_is_reported_to_the_admin(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenv.bot, "get_guild", lambda guild_id: None)
    interaction = await invoke_setup(tenv, tenv.member(admin=True))
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].title == tenv.app.op("server.setup.no_guild")
    assert tenv.guild.categories == []
    assert await audit_events(tenv, "setup.run") == []
