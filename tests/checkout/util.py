"""Helpers for checkout tests: environments with provisioned channels, products, and common flows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import discord

from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features import all_modules
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog.repo import Product
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, views
from shopbot.features.credits import repo as credits_repo
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, deep_merge, make_settings, make_test_env, provision_channels

# Fulfillment options, a details form, fees and timers (all synthetic) for the full workflow.
FULL_SETTINGS: dict[str, Any] = {
    "checkout": {
        "fulfillment_options": [
            {"id": "pickup", "fee_minor": 0},
            {
                "id": "delivery",
                "fee_minor": 500,
                "form": [
                    {"id": "address", "style": "paragraph", "required": True, "max_length": 500},
                    {"id": "phone", "style": "short", "required": False, "max_length": 40},
                ],
            },
        ],
        "details_form": [{"id": "name", "style": "short", "required": True, "min_length": 2, "max_length": 80}],
        "fees": [
            {"id": "service", "kind": "flat", "amount_minor": 150},
            {"id": "tax", "kind": "percent", "basis_points": 1000},
        ],
    }
}


# The customer's form answers are also posted under the order record in the staff channel (off by default).
ANSWERS_IN_CHANNEL: dict[str, Any] = {"operations": {"answers_in_orders_channel": True}}


def post_answers_in_channel(tenv: TestEnv) -> None:
    """Switch ``operations.answers_in_orders_channel`` on for an existing environment."""
    settings = tenv.app.settings
    tenv.app.settings = replace(settings, operations=replace(settings.operations, answers_in_orders_channel=True))


async def make_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    # Many clicks by the same fake user happen within milliseconds; keep the anti-spam limiter out of the way.
    settings = make_settings(deep_merge({"bot": {"rate_limit_per_10s": 100}}, overrides or {}))
    tenv = await make_test_env(tmp_path, settings=settings, **kwargs)
    await provision_channels(tenv)
    return tenv


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return found


def render(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.app.content.render(key, **values)
    assert text is not None, key
    return text


def money(tenv: TestEnv, minor: int) -> str:
    text = tenv.app.ui.money(minor)
    assert text is not None
    return text


async def add_product(
    tenv: TestEnv, name: str = "[p]", *, price: int = 1000, stock: int | None = None, **kwargs: Any
) -> Product:
    product = await tenv.app.db.transaction(
        catalog_repo.create_product,
        GUILD_ID,
        name=name,
        price_minor=price,
        stock_on_hand=stock,
        now=tenv.clock.now(),
        **kwargs,
    )
    return product


async def product(tenv: TestEnv, product_id: int) -> Product:
    found = await tenv.app.db.run(catalog_repo.get_product, GUILD_ID, product_id)
    assert found is not None
    return found


async def set_price(tenv: TestEnv, product_id: int, price: int) -> None:
    current = await product(tenv, product_id)
    await tenv.app.db.transaction(
        catalog_repo.update_product, product_id, current.version, now=tenv.clock.now(), price_minor=price
    )


async def give_credits(tenv: TestEnv, user_id: int, amount: int) -> None:
    await tenv.app.db.transaction(
        credits_repo.adjust,
        guild_id=GUILD_ID,
        user_id=user_id,
        delta_minor=amount,
        actor_id=1,
        note="[test]",
        now=tenv.clock.now(),
    )


async def balance(tenv: TestEnv, user_id: int) -> int:
    return await tenv.app.db.run(credits_repo.balance, GUILD_ID, user_id)


async def session_of(tenv: TestEnv, user: FakeMember) -> m.Session:
    rows = await tenv.app.db.run(
        lambda conn: conn.execute(
            "SELECT id FROM checkout_sessions WHERE user_id = ? ORDER BY id DESC", (user.id,)
        ).fetchone()
    )
    assert rows is not None, "no session"
    session = await tenv.app.db.run(repo.get_session, int(rows["id"]))
    assert session is not None
    return session


async def reload_session(tenv: TestEnv, session_id: int) -> m.Session:
    session = await tenv.app.db.run(repo.get_session, session_id)
    assert session is not None
    return session


async def order(tenv: TestEnv, order_id: int) -> m.Order:
    found = await tenv.app.db.run(repo.get_order, order_id)
    assert found is not None
    return found


async def orders(tenv: TestEnv) -> list[m.Order]:
    return await tenv.app.db.run(repo.list_orders, GUILD_ID, limit=100, offset=0)


def ticket_intro_message(ch: FakeTextChannel) -> FakeMessage:
    """The order ticket's introduction: the first message the bot posts in an order channel."""
    bot_messages = [msg for msg in ch.messages if msg.author is ch.guild.me]
    assert bot_messages, "no ticket introduction"
    return bot_messages[0]


def workflow_message(ch: FakeTextChannel) -> FakeMessage:
    """The live panel: the bot's message right after the ticket introduction."""
    bot_messages = [msg for msg in ch.messages if msg.author is ch.guild.me]
    assert len(bot_messages) > 1, "no workflow message"
    return bot_messages[1]


def custom_ids(message: FakeMessage) -> list[str]:
    return message.custom_ids()


def component(message: FakeMessage, custom_id: str) -> Any:
    for child in message.components():
        item = getattr(child, "item", child)
        if getattr(item, "custom_id", None) == custom_id:
            return item
    raise AssertionError(f"{custom_id} not in {message.custom_ids()}")


def error_text(interaction: FakeInteraction) -> str | None:
    """Description of the last private reply (configured customer messages)."""
    if interaction.last_sent is None:
        return None
    return interaction.last_sent.embeds[0].description


def op_text(interaction: FakeInteraction) -> str | None:
    """Title of the last private reply (operator replies put their text in the title)."""
    if interaction.last_sent is None:
        return None
    return interaction.last_sent.embeds[0].title


async def start(tenv: TestEnv, user: FakeMember) -> tuple[FakeInteraction, m.Session, FakeTextChannel, FakeMessage]:
    interaction = await click(tenv.app, user=user, custom_id=views.start_id())
    session = await session_of(tenv, user)
    ticket_channel = await ticket_channel_of(tenv, session.ticket_id)
    return interaction, session, ticket_channel, workflow_message(ticket_channel)


async def ticket_channel_of(tenv: TestEnv, ticket_id: int) -> FakeTextChannel:
    from shopbot.features.tickets import service as tickets_service

    ticket = await tickets_service.get_ticket(tenv.app, ticket_id)
    assert ticket is not None and ticket.channel_id is not None
    found = tenv.guild.get_channel(ticket.channel_id)
    assert isinstance(found, FakeTextChannel)
    return found


async def ticket_status(tenv: TestEnv, ticket_id: int) -> str:
    from shopbot.features.tickets import service as tickets_service

    ticket = await tickets_service.get_ticket(tenv.app, ticket_id)
    assert ticket is not None
    return ticket.status


async def set_qty(
    tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, product_id: int, quantity: str | int
) -> FakeInteraction:
    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.qty_id(session_id, product_id),
        fields={views.QUANTITY_INPUT: str(quantity)},
        message=message,
    )


async def press(
    tenv: TestEnv, user: FakeMember, message: FakeMessage | None, custom_id: str, **kwargs: Any
) -> FakeInteraction:
    return await click(tenv.app, user=user, custom_id=custom_id, message=message, **kwargs)


async def reject(
    tenv: TestEnv, user: FakeMember, message: FakeMessage | None, order_id: int, reason: str = "[sample reason]"
) -> FakeInteraction:
    """Submit the rejection form of an unpaid order (the reject button opens it; the reason goes to the customer)."""
    from shopbot.features.checkout import staff_views

    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=staff_views.reject_form_id(order_id, m.PENDING),
        fields={staff_views.REASON_INPUT: reason},
        message=message,
    )


async def go(tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, step: str) -> FakeInteraction:
    return await press(tenv, user, message, views.go_id(session_id, step))


async def to_review(
    tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, items: list[tuple[int, int]]
) -> m.WorkflowData:
    """Add ``items`` (product id, quantity) and go to the review step (default settings: no options/forms)."""
    for product_id, quantity in items:
        await set_qty(tenv, user, message, session_id, product_id, quantity)
    await go(tenv, user, message, session_id, m.CART)
    await go(tenv, user, message, session_id, m.REVIEW)
    data = await tenv.app.db.run(repo.load_workflow, GUILD_ID, session_id, tenv.app.settings, True)
    assert data is not None and data.session.step == m.REVIEW
    return data


async def submit(
    tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, total: int | None = None
) -> FakeInteraction:
    """Press the submit button with the totals the review shows (``total``: override the total to pay)."""
    from shopbot.features.credits import service as credits_service

    data = await tenv.app.db.run(
        repo.load_workflow, GUILD_ID, session_id, tenv.app.settings, credits_service.available_at_checkout(tenv.app)
    )
    assert data is not None
    shown = data.totals.total_minor if total is None else total
    return await press(tenv, user, message, views.submit_id(session_id, shown, data.totals.gross_minor))


async def place_order(
    tenv: TestEnv, user: FakeMember, items: list[tuple[int, int]]
) -> tuple[m.Order, FakeTextChannel, FakeMessage]:
    """Start, fill the cart, review and submit. Returns (order, order channel, workflow message)."""
    _, session, ch, message = await start(tenv, user)
    await to_review(tenv, user, message, session.id, items)
    interaction = await submit(tenv, user, message, session.id)
    session = await reload_session(tenv, session.id)
    assert session.order_id is not None, error_text(interaction)
    return await order(tenv, session.order_id), ch, message


def control_ids(message: FakeMessage) -> list[str]:
    """custom_ids of the status and refund controls of a live order record (without the management buttons: claim,
    note, correction, tracking, details - see tests/operations for those)."""
    from shopbot.ui import custom_ids as cids

    kept = []
    for custom_id in message.custom_ids():
        parsed = cids.parse(custom_id)
        if parsed is None or parsed[1] not in views.MANAGEMENT_ACTIONS:
            kept.append(custom_id)
    return kept


def staff_message(tenv: TestEnv, found: m.Order) -> FakeMessage:
    staff_channel = channel(tenv, "order_queue")
    for message in staff_channel.messages:
        if message.id == found.staff_message_id:
            return message
    raise AssertionError("staff message not found")


async def audit_actions(tenv: TestEnv, prefix: str = "checkout.") -> list[str]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=100)
    return [e.action for e in reversed(events) if e.action.startswith(prefix)]


async def task(tenv: TestEnv, dedupe_key: str) -> Any:
    return await tenv.app.db.run(task_repo.get_by_dedupe, dedupe_key)


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


async def run_command(
    tenv: TestEnv, name: str, *, user: FakeMember, app: App | None = None, **kwargs: Any
) -> FakeInteraction:
    """Invoke an /order subcommand like discord.py does: checks first, then the callback."""
    from discord import app_commands

    from shopbot.commands import on_tree_error

    app = app or tenv.app
    assert app.bot is not None
    group = app.bot.tree.get_command("order", guild=app.guild_object)
    assert isinstance(group, app_commands.Group)
    command = group.get_command(name)
    assert isinstance(command, app_commands.Command)
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        if not await command._check_can_run(interaction):  # type: ignore[arg-type]
            raise app_commands.CheckFailure("check failed")
        await command.callback(interaction, **kwargs)  # type: ignore[arg-type]
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    return interaction


def texts(interaction: FakeInteraction) -> str:
    return "\n".join(message.text() for message in interaction.sent)
