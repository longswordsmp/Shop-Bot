"""``/order`` staff commands: lookup, list, status, refund, cancel, note.

Hidden from non-staff by default permissions (manage_messages); authorized by ``require_staff`` with the checkout
staff roles. Status changes and refunds go through the same code path as the staff order-control buttons. Replies
are private operator text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, reply_operator, require_staff
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service, staff

if TYPE_CHECKING:
    from shopbot.context import App

StatusChoice = Literal[
    "pending_payment", "paid", "processing", "fulfilled", "cancelled", "rejected", "expired", "refunded"
]
TargetChoice = Literal["paid", "processing", "fulfilled", "rejected", "cancelled"]
OrderNumber = app_commands.Range[int, 1, 2_000_000_000]


def staff_role_keys(app: App) -> tuple[str, ...]:
    return app.settings.checkout.staff_roles


async def _defer(interaction: discord.Interaction[Any]) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _ready_app(interaction: discord.Interaction[Any]) -> App | None:
    app = app_of(interaction)
    if not app.ready(k.FEATURE):
        await reply_operator(interaction, app.op("command.feature_disabled", feature=k.FEATURE), tone="warning")
        return None
    return app


async def _order(interaction: discord.Interaction[Any], app: App, number: int) -> m.Order | None:
    order = await service.get_order_by_number(app, number)
    if order is None:
        await reply_operator(interaction, app.op("checkout.op.not_found", number=number), tone="warning")
    return order


async def _send(interaction: discord.Interaction[Any], app: App, result: staff.OpReply) -> None:
    await reply(interaction, staff.reply_spec(app, result), ephemeral=True)


async def order_lookup(interaction: discord.Interaction[Any], number: int) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await _defer(interaction)
    for spec in await staff.lookup_messages(app, order):
        await reply(interaction, spec, ephemeral=True)


async def order_list(
    interaction: discord.Interaction[Any],
    status: str | None = None,
    member: discord.Member | discord.User | None = None,
) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    spec = await staff.list_view(app, 0, status or "", member.id if member is not None else 0)
    await reply(interaction, spec, ephemeral=True)


async def order_status(interaction: discord.Interaction[Any], number: int, new_status: str) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await _defer(interaction)
    await _send(interaction, app, await staff.change_status(app, order, new_status, actor_id=interaction.user.id))


async def order_cancel(interaction: discord.Interaction[Any], number: int, reason: str | None = None) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await _defer(interaction)
    result = await staff.change_status(app, order, m.CANCELLED, actor_id=interaction.user.id, note=reason or None)
    await _send(interaction, app, result)


async def order_refund(
    interaction: discord.Interaction[Any],
    number: int,
    amount: str,
    to_credit: bool = False,
    reason: str | None = None,
) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await _defer(interaction)
    result = await staff.refund(
        app, order, amount_text=amount, to_credit=to_credit, reason=reason or None, actor_id=interaction.user.id
    )
    await _send(interaction, app, result)


async def order_note(interaction: discord.Interaction[Any], number: int, text: str) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await service.add_note(app, order.id, actor_id=interaction.user.id, note=text)
    await _send(interaction, app, staff.OpReply(app.op("checkout.op.note_added", number=number), "success"))


def build_group() -> app_commands.Group:
    """A fresh ``/order`` group (one per application instance)."""
    group = app_commands.Group(name="order", description="Order tools for staff.")
    app_commands.guild_only(group)
    app_commands.default_permissions(manage_messages=True)(group)

    @group.command(name="lookup", description="Show everything about an order.")
    @app_commands.describe(number="Order number")
    @require_staff(staff_role_keys)
    async def lookup(interaction: discord.Interaction[Any], number: OrderNumber) -> None:
        await order_lookup(interaction, number)

    @group.command(name="list", description="List orders (newest first, 10 per page).")
    @app_commands.describe(status="Only orders with this status", member="Only orders of this member")
    @require_staff(staff_role_keys)
    async def list_(
        interaction: discord.Interaction[Any],
        status: StatusChoice | None = None,
        member: discord.Member | discord.User | None = None,
    ) -> None:
        await order_list(interaction, status, member)

    @group.command(name="status", description="Change an order's status (only valid changes are applied).")
    @app_commands.describe(number="Order number", new_status="The new status")
    @require_staff(staff_role_keys)
    async def status(interaction: discord.Interaction[Any], number: OrderNumber, new_status: TargetChoice) -> None:
        await order_status(interaction, number, new_status)

    @group.command(name="refund", description="Record a (partial) refund of an order.")
    @app_commands.describe(
        number="Order number",
        amount="Amount to refund, e.g. 12.50",
        to_credit="Give the refund as store credit instead",
        reason="Optional internal reason",
    )
    @require_staff(staff_role_keys)
    async def refund(
        interaction: discord.Interaction[Any],
        number: OrderNumber,
        amount: app_commands.Range[str, 1, 24],
        to_credit: bool = False,
        reason: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        await order_refund(interaction, number, amount, to_credit, reason)

    @group.command(name="cancel", description="Cancel an order.")
    @app_commands.describe(number="Order number", reason="Optional internal reason")
    @require_staff(staff_role_keys)
    async def cancel(
        interaction: discord.Interaction[Any],
        number: OrderNumber,
        reason: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        await order_cancel(interaction, number, reason)

    @group.command(name="note", description="Add an internal note to an order's history.")
    @app_commands.describe(number="Order number", text="The note")
    @require_staff(staff_role_keys)
    async def note(
        interaction: discord.Interaction[Any],
        number: OrderNumber,
        text: app_commands.Range[str, 1, 500],
    ) -> None:
        await order_note(interaction, number, text)

    return group


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.bot.tree.add_command(build_group(), guild=app.guild_object)
