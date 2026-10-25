"""Helpers shared by the /setup tests."""

from __future__ import annotations

from typing import Any

import discord

from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features import all_modules
from shopbot.features.setup.report import SetupReport
from shopbot.features.setup.service import run_setup
from tests.fakes import FakeBot, FakeInteraction, FakeMember
from tests.helpers import TestEnv, shipped_settings_dict


async def setup(tenv: TestEnv, *, dry_run: bool = False, publish: bool = True, app: App | None = None) -> SetupReport:
    guild: Any = tenv.guild  # duck-typed FakeGuild
    return await run_setup(
        app or tenv.app, guild, actor_id=tenv.app.env.owner_id or 1, dry_run=dry_run, publish=publish
    )


def setup_command(tenv: TestEnv, app: App | None = None) -> Any:
    app = app or tenv.app
    assert app.bot is not None
    command = app.bot.tree.get_command("setup", guild=app.guild_object)
    assert command is not None
    return command


async def invoke_setup(
    tenv: TestEnv, user: FakeMember, *, dry_run: bool = False, publish: bool = True
) -> FakeInteraction:
    """Run /setup like Discord would: checks first, then the callback (errors go to the tree handler)."""
    from shopbot.commands import on_tree_error

    command = setup_command(tenv)
    interaction = FakeInteraction(
        client=tenv.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        await command._check_can_run(interaction)
    except discord.app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    await command.callback(interaction, dry_run, publish)
    return interaction


def restarted_app(tenv: TestEnv) -> App:
    """A fresh App on the same database and guild, as after a process restart."""
    old = tenv.app
    app = App(
        env=old.env,
        settings=old.settings,
        content_values=dict(tenv.content),
        overrides={},
        db=old.db,
        clock=tenv.clock,
        modules=all_modules(),
        config_dir=old.config_dir,
    )
    FakeBot(app, tenv.guild)
    for module in app.modules:
        if module.register is not None:
            module.register(app)
    app.scheduler.bind(app)
    return app


def shipped_server() -> dict[str, Any]:
    server: dict[str, Any] = shipped_settings_dict()["server"]
    return server


async def audit_events(tenv: TestEnv, action: str) -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, tenv.app.env.guild_id, limit=100)
    return [event for event in events if event.action == action]


def all_lines(report: SetupReport) -> str:
    return "\n".join(line for lines in report.lines.values() for line in lines)
