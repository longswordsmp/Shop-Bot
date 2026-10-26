"""/status: readiness overview, diagnostics, and per-feature problem pages."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.config.readiness import Problem
from shopbot.db.repos import tasks as task_repo
from shopbot.features import core_keys as ck
from shopbot.features.admin import service
from tests.admin.util import admin, is_ephemeral, restarted_app, run_command, sent
from tests.fakes import click
from tests.helpers import TestEnv, make_settings, make_test_env


def fields_of(message: object) -> dict[str, str]:
    embed = message.embeds[0]  # type: ignore[attr-defined]
    return {f.name: f.value for f in embed.fields}


async def test_overview_reports_readiness_and_diagnostics(tenv: TestEnv) -> None:
    app = tenv.app
    values = dict(tenv.content)
    del values[ck.ERROR_GENERIC]
    values["tickets.panel.titel"] = "typo key"
    app.content.replace(file_values=values)
    app.recompute_readiness()
    await app.scheduler.schedule("admin.maintenance", run_at=app.clock.now() + 60, dedupe_key="x1")
    failed_id = await app.db.transaction(
        task_repo.schedule,
        kind="admin.maintenance",
        run_at=0,
        payload={},
        max_attempts=1,
        dedupe_key="x2",
        replace=False,
        now=0,
    )
    assert failed_id is not None
    await app.db.transaction(task_repo.mark_failed_attempt, failed_id, "boom", None, 1)

    interaction = await run_command(tenv, "status", user=admin(tenv))
    assert interaction.response_kinds() == ["defer"]
    message = sent(interaction)
    assert is_ephemeral(message)
    embed = message.embeds[0]
    assert embed.title == "Bot status"
    enabled = [s for s in app.readiness.statuses.values() if s.enabled]
    ready = [s for s in enabled if s.ready]
    assert f"{len(ready)} of {len(enabled)} enabled features are ready." in (embed.description or "")
    fields = fields_of(message)
    assert "⚠️ `core` — enabled, not ready (1 problems)" in fields["Features"]
    assert "✅ `admin` — ready" in fields["Features"]
    assert "`tickets.panel.titel`" in fields["Unknown content keys (likely typos)"]
    assert fields["Bot permissions (server-wide)"] == "All permissions needed by the enabled features are granted."
    assert fields["Gateway intents (connected with)"] == "Message content: yes · Server members: no"
    assert fields["Scheduled tasks"] == "Pending: 1 · Running: 0 · Failed: 1"
    assert str(app.env.db_path) in fields["Database"]


async def test_overview_lists_missing_bot_permissions(tenv: TestEnv) -> None:
    bot_role = tenv.guild.bot_role
    bot_role._permissions = discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True, read_message_history=True
    ).value
    interaction = await run_command(tenv, "status", user=admin(tenv))
    permissions = fields_of(sent(interaction))["Bot permissions (server-wide)"]
    assert "Missing for base: use_external_emojis" in permissions
    assert "Missing for setup: manage_roles, manage_channels" in permissions
    assert "Missing for tickets: manage_channels, manage_roles, attach_files, manage_messages" in permissions
    assert "Missing for catalog: attach_files" in permissions


async def test_overview_when_guild_is_not_visible(tenv: TestEnv) -> None:
    tenv.bot.get_guild = lambda guild_id: None  # type: ignore[method-assign]
    interaction = await run_command(tenv, "status", user=admin(tenv))
    assert fields_of(sent(interaction))["Bot permissions (server-wide)"].startswith(
        "The bot cannot see the configured server"
    )


async def test_overview_warns_when_transcripts_lack_intent(tmp_path: Path) -> None:
    settings = make_settings({"bot": {"message_content_intent": False}})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        assert service.transcripts_enabled(tenv.app.settings)
        interaction = await run_command(tenv, "status", user=admin(tenv))
        intents = fields_of(sent(interaction))["Gateway intents (connected with)"]
        assert "Message content: no" in intents
        assert "need the message content intent" in intents
    finally:
        await tenv.close()


async def test_required_capabilities_follow_enabled_features(tmp_path: Path) -> None:
    settings = make_settings(all_features=False)
    assert service.required_capabilities(settings) == ["base", "setup"]
    assert service.required_capabilities(make_settings()) == ["base", "setup", "tickets", "transcripts", "catalog"]


async def test_feature_problems_paginate_and_buttons_update_the_message(tenv: TestEnv) -> None:
    status = tenv.app.readiness.statuses["tickets"]
    status.ready = False
    status.problems = [
        Problem("tickets", "missing_content", "not configured", f"tickets.synthetic.k{i:03d}") for i in range(200)
    ] + [Problem("tickets", "settings", "tickets.panel_channel: not defined"), Problem("tickets", "dependency", "x")]
    user = admin(tenv)
    first = await run_command(tenv, "status", user=user, feature="tickets")
    message = sent(first)
    embed = message.embeds[0]
    assert embed.title == "Status: tickets"
    assert embed.description == "Enabled: yes · Ready: no · Problems: 202"
    footer = embed.footer.text or ""
    assert footer.startswith("Page 1 of ")
    pages = int(footer.rsplit(" ", 1)[-1])
    assert pages >= 2
    previous, following = message.components()
    assert previous.item.disabled and not following.item.disabled

    seen: list[str] = message.text().splitlines()
    for page in range(2, pages + 1):
        clicked = await click(tenv.app, user=user, custom_id=f"sb:ad:status:tickets:{page}", message=message)
        assert clicked.response_kinds() == ["edit_message"]
        assert message.embeds[0].footer.text == f"Page {page} of {pages}"
        seen += message.text().splitlines()
    for i in range(200):
        assert f"`tickets.synthetic.k{i:03d}` — not configured" in seen
    assert "Settings: tickets.panel\\_channel: not defined" in seen
    assert "Dependency: x" in seen
    previous, following = message.components()
    assert not previous.item.disabled and following.item.disabled

    # Out-of-range pages (stale buttons) are clamped, never an error.
    await click(tenv.app, user=user, custom_id="sb:ad:status:tickets:999", message=message)
    assert message.embeds[0].footer.text == f"Page {pages} of {pages}"


async def test_feature_without_problems(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "status", user=admin(tenv), feature="admin")
    message = sent(interaction)
    assert message.embeds[0].description == "Enabled: yes · Ready: yes · Problems: 0\nNo problems."
    assert message.custom_ids() == []


async def test_unknown_feature(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "status", user=admin(tenv), feature="nope")
    message = sent(interaction)
    assert message.embeds[0].title == "Unknown feature `nope`."
    assert "`tickets`" in (message.embeds[0].description or "")


async def test_stale_button_for_unknown_feature_is_not_found(tenv: TestEnv) -> None:
    interaction = await click(tenv.app, user=admin(tenv), custom_id="sb:ad:status:removed_feature:2")
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_button_on_deleted_message_answers_not_found(tenv: TestEnv) -> None:
    status = tenv.app.readiness.statuses["tickets"]
    status.problems = [Problem("tickets", "missing_content", "not configured", f"t.k{i:03d}") for i in range(200)]
    user = admin(tenv)
    message = sent(await run_command(tenv, "status", user=user, feature="tickets"))
    message.deleted = True
    clicked = await click(tenv.app, user=user, custom_id="sb:ad:status:tickets:2", message=message)
    assert clicked.sent and clicked.sent[-1].embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_pagination_buttons_keep_working_after_restart(tenv: TestEnv) -> None:
    user = admin(tenv)
    message = sent(await run_command(tenv, "status", user=user, feature="admin"))
    app = restarted_app(tenv)
    clicked = await click(app, user=user, custom_id="sb:ad:status:admin:1", message=message)
    assert clicked.response_kinds() == ["edit_message"]
    assert message.embeds[0].title == "Status: admin"


async def test_register_wires_routes_task_kind_and_commands(tenv: TestEnv) -> None:
    from discord import app_commands

    routes = {(r.kind, r.route, r.action, r.access, r.feature) for r in tenv.app.router.routes() if r.route == "ad"}
    assert routes == {
        ("component", "ad", "status", "admin", "admin"),
        ("component", "ad", "miss", "admin", "admin"),
        ("component", "ad", "keys", "admin", "admin"),
        ("modal", "ad", "cset", "admin", "admin"),
        ("component", "ad", "etext", "admin", "admin"),
    }
    assert "admin.maintenance" in tenv.app.scheduler.kinds
    group = tenv.bot.tree.get_command("content", guild=tenv.app.guild_object)
    assert isinstance(group, app_commands.Group)
    assert {c.name for c in group.commands} == {"set", "show", "clear", "missing", "keys", "export"}
