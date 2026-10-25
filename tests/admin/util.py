"""Helpers shared by the admin tests."""

from __future__ import annotations

import io
from typing import Any

import discord
from discord import app_commands

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features import all_modules
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage
from tests.helpers import OWNER_ID, TestEnv


def get_command(tenv: TestEnv, name: str, group: str | None = None, app: App | None = None) -> Any:
    app = app or tenv.app
    assert app.bot is not None
    tree = app.bot.tree
    if group is None:
        command = tree.get_command(name, guild=app.guild_object)
    else:
        parent = tree.get_command(group, guild=app.guild_object)
        assert isinstance(parent, app_commands.Group)
        command = parent.get_command(name)
    assert isinstance(command, app_commands.Command), f"command {group or ''} {name} not registered"
    return command


async def run_command(
    tenv: TestEnv,
    name: str,
    *,
    user: Any,
    group: str | None = None,
    app: App | None = None,
    **kwargs: Any,
) -> FakeInteraction:
    """Invoke a slash command like discord.py: checks first, then the callback with converted options.

    Check failures and callback exceptions are passed to the tree error handler, as in production."""
    app = app or tenv.app
    command = get_command(tenv, name, group, app)
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        if not await command._check_can_run(interaction):
            raise app_commands.CheckFailure("check failed")
        await command.callback(interaction, **kwargs)
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    except Exception as exc:
        await on_tree_error(interaction, app_commands.CommandInvokeError(command, exc))  # type: ignore[arg-type]
    return interaction


def admin(tenv: TestEnv) -> FakeMember:
    return tenv.member(name="admin", admin=True)


def owner(tenv: TestEnv) -> FakeMember:
    existing = tenv.guild.get_member(OWNER_ID)
    return existing or tenv.guild.add_member(id=OWNER_ID, name="owner")


def staff(tenv: TestEnv) -> FakeMember:
    return tenv.member(name="staff", staff=True)


def customer(tenv: TestEnv) -> FakeMember:
    return tenv.member(name="customer")


def sent(interaction: FakeInteraction) -> FakeMessage:
    message = interaction.last_sent
    assert message is not None, f"nothing was sent; responses: {interaction.response_kinds()}"
    return message


def embed(interaction: FakeInteraction, index: int = 0) -> discord.Embed:
    return sent(interaction).embeds[index]


def all_text(interaction: FakeInteraction) -> str:
    return "\n".join(message.text() for message in interaction.sent)


def attachment_text(message: FakeMessage) -> str:
    assert message.files, "no attachment"
    fp = message.files[0].fp
    assert isinstance(fp, io.BytesIO)
    return fp.getvalue().decode("utf-8")


def is_ephemeral(message: FakeMessage) -> bool:
    return bool(getattr(message, "ephemeral", False))


async def audit_actions(app: App) -> list[str]:
    events = await app.db.run(audit_repo.recent, app.env.guild_id, limit=100)
    return [event.action for event in reversed(events)]


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
