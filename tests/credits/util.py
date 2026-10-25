"""Helpers shared by the credits tests."""

from __future__ import annotations

import itertools
import sqlite3
from typing import Any

import discord

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features import all_modules
from shopbot.features.credits import repo
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage
from tests.helpers import GUILD_ID, TestEnv

_order_numbers = itertools.count(1)


def _insert_order(conn: sqlite3.Connection, guild_id: int, user_id: int, number: int, now: int) -> int:
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (?, ?, ?, 'pending_payment', 'USD', 1000, 1000, "
        "'manual', ?, ?)",
        (guild_id, number, user_id, now, now),
    )
    return int(cursor.lastrowid or 0)


async def make_order(tenv: TestEnv, user_id: int, *, guild_id: int = GUILD_ID) -> tuple[int, int]:
    """Insert a minimal order row (the ledger references orders). Returns (order id, order number)."""
    number = next(_order_numbers)
    order_id = await tenv.app.db.transaction(_insert_order, guild_id, user_id, number, tenv.clock.now())
    return order_id, number


async def grant(tenv: TestEnv, user_id: int, amount: int, *, actor_id: int = 1, note: str | None = "[grant]") -> int:
    """Give a member credit through the repository (an admin adjustment)."""
    return await tenv.app.db.transaction(
        repo.add_entry,
        guild_id=GUILD_ID,
        user_id=user_id,
        delta_minor=amount,
        kind=repo.KIND_ADMIN_ADJUST,
        order_id=None,
        actor_id=actor_id,
        note=note,
        now=tenv.clock.now(),
    )


async def balance(tenv: TestEnv, user_id: int) -> int:
    return await tenv.app.db.run(repo.balance, GUILD_ID, user_id)


async def entries(tenv: TestEnv, user_id: int) -> list[repo.Entry]:
    return await tenv.app.db.run(repo.history, GUILD_ID, user_id, limit=1000, offset=0)


def credits_command(app: App, name: str) -> Any:
    assert app.bot is not None
    group = app.bot.tree.get_command("credits", guild=app.guild_object)
    assert isinstance(group, discord.app_commands.Group)
    command = group.get_command(name)
    assert isinstance(command, discord.app_commands.Command)
    return command


async def invoke(
    tenv: TestEnv, user: FakeMember, name: str, *, app: App | None = None, **kwargs: Any
) -> FakeInteraction:
    """Run /credits <name> like Discord would: checks first (errors go to the tree handler), then the callback."""
    app = app or tenv.app
    command = credits_command(app, name)
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        allowed = await command._check_can_run(interaction)
    except discord.app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    assert allowed
    await command.callback(interaction, **kwargs)
    return interaction


def reply_embed(interaction: FakeInteraction) -> discord.Embed:
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds
    return interaction.last_sent.embeds[0]


def buttons(message: FakeMessage) -> list[tuple[str | None, str | None, bool]]:
    """(custom_id, label, disabled) of every button in a message."""
    out: list[tuple[str | None, str | None, bool]] = []
    for child in message.components():
        item = getattr(child, "item", child)
        out.append((getattr(item, "custom_id", None), getattr(item, "label", None), bool(item.disabled)))
    return out


async def audit_actions(tenv: TestEnv, prefix: str = "credits.") -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=100)
    return [e for e in reversed(events) if e.action.startswith(prefix)]


async def restarted_app(tenv: TestEnv) -> App:
    """A fresh App on the same database and guild, after the startup sequence (resources, recovery hooks)."""
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
    await app.resources.refresh()
    await app.load_overrides()
    await app.scheduler.recover()
    for module in app.modules:
        if module.on_ready is not None:
            await module.on_ready(app)
    return app
