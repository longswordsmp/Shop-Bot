"""Helpers shared by the reviews tests.

Orders and their order channels are inserted directly (the checkout feature owns them); the reviews feature only
reads ``orders`` and ``tickets``.
"""

from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path
from typing import Any

import discord

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features import all_modules
from shopbot.features.reviews import MODULE, repo, service
from shopbot.features.reviews import content_keys as k
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage, FakeTextChannel
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env, provision_channels

_numbers = itertools.count(1)


async def make_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    """Test environment with every configured channel provisioned (as /setup would)."""
    settings = make_settings(overrides) if overrides else None
    tenv = await make_test_env(tmp_path, settings=settings, **kwargs)
    await provision_channels(tenv)
    return tenv


def _insert_order(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    number: int,
    status: str,
    channel_id: int | None,
    now: int,
) -> int:
    ticket_id = None
    if channel_id is not None:
        cursor = conn.execute(
            "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
            "updated_at, last_activity_at) VALUES (?, ?, 'order', 'checkout', ?, ?, 'open', ?, ?, ?)",
            (guild_id, number, user_id, channel_id, now, now, now),
        )
        ticket_id = int(cursor.lastrowid or 0)
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, ticket_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'USD', 1000, 1000, 'manual', ?, ?)",
        (guild_id, number, user_id, ticket_id, status, now, now),
    )
    return int(cursor.lastrowid or 0)


class Order:
    def __init__(self, order_id: int, number: int, channel: FakeTextChannel | None) -> None:
        self.id = order_id
        self.number = number
        self.channel = channel


async def make_order(
    tenv: TestEnv,
    user: FakeMember,
    *,
    status: str = "fulfilled",
    with_channel: bool = True,
    guild_id: int = GUILD_ID,
) -> Order:
    """A fulfilled order with its private order channel (a ticket of kind 'order')."""
    number = next(_numbers)
    channel = tenv.guild.add_text_channel(name=f"order-{number}") if with_channel else None
    order_id = await tenv.app.db.transaction(
        _insert_order,
        guild_id=guild_id,
        user_id=user.id,
        number=number,
        status=status,
        channel_id=channel.id if channel is not None else None,
        now=tenv.clock.now(),
    )
    return Order(order_id, number, channel)


def _set_order_status(conn: sqlite3.Connection, order_id: int, status: str) -> None:
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


async def set_order_status(tenv: TestEnv, order_id: int, status: str) -> None:
    await tenv.app.db.transaction(_set_order_status, order_id, status)


def _mark_ticket_deleted(conn: sqlite3.Connection, order_id: int) -> None:
    conn.execute(
        "UPDATE tickets SET status = 'deleted' WHERE id = (SELECT ticket_id FROM orders WHERE id = ?)", (order_id,)
    )


async def mark_ticket_deleted(tenv: TestEnv, order_id: int) -> None:
    await tenv.app.db.transaction(_mark_ticket_deleted, order_id)


async def review_of(tenv: TestEnv, order: Order, app: App | None = None) -> repo.Review:
    review = await (app or tenv.app).db.run(repo.get_review_by_order, order.id)
    assert review is not None
    return review


async def review_count(tenv: TestEnv) -> int:
    def count(conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0])

    return await tenv.app.db.run(count)


async def prompt_now(tenv: TestEnv, order: Order, app: App | None = None) -> None:
    """Schedule the review request of ``order`` and run due tasks (prompt delay is 0 in tests)."""
    app = app or tenv.app
    await service.schedule_review_prompt(app, order.id)
    await app.scheduler.run_due()


async def prompted(tenv: TestEnv, user: FakeMember, **kwargs: Any) -> tuple[Order, repo.Review, FakeMessage]:
    """A fulfilled order whose review request was delivered by direct message."""
    order = await make_order(tenv, user, **kwargs)
    await prompt_now(tenv, order)
    assert user.dms, "the review request was not sent by direct message"
    return order, await review_of(tenv, order), user.dms[-1]


async def task(tenv: TestEnv, dedupe_key: str, app: App | None = None) -> task_repo.Task | None:
    return await (app or tenv.app).db.run(task_repo.get_by_dedupe, dedupe_key)


def reviews_channel(tenv: TestEnv) -> FakeTextChannel:
    channel_id = tenv.app.resources.channel_id(tenv.app.settings.reviews.channel)
    channel = tenv.guild.get_channel(channel_id)
    assert isinstance(channel, FakeTextChannel)
    return channel


def rendered(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.text(key)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def number_text(tenv: TestEnv, number: int) -> str:
    return service.order_number_text(tenv.app, number)


def only_embed(message: FakeMessage) -> discord.Embed:
    assert len(message.embeds) == 1
    return message.embeds[0]


def select_of(message: FakeMessage) -> discord.ui.Select[Any]:
    for child in message.components():
        item = getattr(child, "item", child)
        if isinstance(item, discord.ui.Select):
            return item
    raise AssertionError("no select menu on the message")


def custom_ids(message: FakeMessage) -> list[str]:
    ids: list[str] = []
    for child in message.components():
        item = getattr(child, "item", child)
        custom_id = getattr(item, "custom_id", None)
        if custom_id:
            ids.append(custom_id)
    return ids


def reply_embed(interaction: FakeInteraction) -> discord.Embed:
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds
    return interaction.last_sent.embeds[0]


def is_ephemeral(interaction: FakeInteraction) -> bool:
    message = interaction.last_sent
    return message is not None and bool(getattr(message, "ephemeral", False))


def review_command(app: App, name: str) -> Any:
    """A subcommand of the staff group /reviews."""
    assert app.bot is not None
    group = app.bot.tree.get_command("reviews", guild=app.guild_object)
    assert isinstance(group, discord.app_commands.Group)
    command = group.get_command(name)
    assert isinstance(command, discord.app_commands.Command)
    return command


async def invoke(
    tenv: TestEnv, user: FakeMember, name: str, *, app: App | None = None, **kwargs: Any
) -> FakeInteraction:
    """Run /reviews <name> like Discord would: checks first (errors go to the tree handler), then the callback."""
    app = app or tenv.app
    command = review_command(app, name)
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


async def audit_actions(tenv: TestEnv, prefix: str = "reviews.") -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e for e in reversed(events) if e.action.startswith(prefix)]


async def restarted_app(tenv: TestEnv) -> App:
    """A fresh App on the same database and guild after the startup sequence (resources, recovery, on_ready)."""
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
    assert MODULE.on_ready is not None
    await MODULE.on_ready(app)
    return app


def published_title(tenv: TestEnv, *, rating: int, scale: int = 5) -> str:
    """The published title of a review with ``rating`` (it never names the member)."""
    return rendered(tenv, k.PUBLISHED_TITLE, stars="★" * rating + "☆" * (scale - rating), rating=rating, scale=scale)


def label(tenv: TestEnv, review: repo.Review) -> str:
    """How staff replies name ``review`` (operator text)."""
    return service.review_label(tenv.app, review)
