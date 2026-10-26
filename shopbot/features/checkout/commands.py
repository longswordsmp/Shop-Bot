"""Staff order commands: ``/orders`` (browse orders, filter by status and member, paginated; open a record from the
list), ``/order`` (lookup, view, list, status, refund, cancel, note), ``/stats`` (sales and open orders) and
``/discount`` (create, list and end discount codes).

Hidden from non-staff by default permissions (manage_messages); authorized by ``require_staff`` with the checkout
staff roles on every invocation. Status changes, refunds and notes go through the same code path as the buttons of
the live order record. ``/discount`` is for catalog managers, like ``/product``: hidden by manage_guild, authorized by
``require_staff`` plus ``require_manager`` (admins, or members with a ``catalog.manager_roles`` role); its changes are
audited. Replies are private operator text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_staff
from shopbot.features.catalog.commands import require_manager
from shopbot.features.catalog.service import manager_role_keys
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_codes, gift_cards, service, staff, stats
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import gift_card

if TYPE_CHECKING:
    from shopbot.context import App

StatusChoice = Literal[
    "pending_payment",
    "payment_submitted",
    "paid",
    "processing",
    "shipped",
    "fulfilled",
    "cancelled",
    "rejected",
    "expired",
    "refunded",
]
TargetChoice = Literal["paid", "processing", "fulfilled", "rejected", "cancelled"]
DISCOUNT_TYPES = [
    app_commands.Choice(name="Percent off", value=d.PERCENT),
    app_commands.Choice(name="Amount off", value=d.AMOUNT),
    app_commands.Choice(name="Buy one, get one free", value=d.BOGO),
]
OrderNumber = app_commands.Range[int, 1, 2_000_000_000]
NOTE_MAX = h.NOTE_MAX


def staff_role_keys(app: App) -> tuple[str, ...]:
    return app.settings.checkout.staff_roles


async def _defer(interaction: discord.Interaction[Any]) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _ready_app(interaction: discord.Interaction[Any]) -> App | None:
    app = app_of(interaction)
    if not app.ready(k.FEATURE):
        await _send(interaction, app, staff.OpReply(app.op("command.feature_disabled", feature=k.FEATURE), "warning"))
        return None
    return app


async def _order(interaction: discord.Interaction[Any], app: App, number: int) -> m.Order | None:
    order = await service.get_order_by_number(app, number)
    if order is None:
        await _send(interaction, app, staff.OpReply(app.op("checkout.op.not_found", number=number), "warning"))
    return order


async def _send(interaction: discord.Interaction[Any], app: App, result: staff.OpReply) -> None:
    """A private staff reply (``staff.reply_spec``: refusals show the error banner, the rest the general one)."""
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
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    gift_card_order = order.payment_provider == gift_card.PROVIDER_ID
    if new_status == m.PAID and gift_card_order and not gift_cards.can_handle(app, member, interaction.user.id):
        await _send(interaction, app, staff.OpReply(app.op("checkout.gift.no_permission"), "warning"))
        return
    if new_status == m.PAID and gift_cards.awaits_decision(order):
        # Like Payment confirmed: the waiting cards were redeemed; paid only if they cover the total.
        reply = await gift_cards.decide(app, order, actor_id=interaction.user.id, redeemed=True, reason=None)
        await _send(interaction, app, reply)
        return
    await _send(interaction, app, await staff.change_status(app, order, new_status, actor_id=interaction.user.id))


async def order_ship(
    interaction: discord.Interaction[Any], number: int, carrier: str, tracking_number: str, link: str | None = None
) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    order = await _order(interaction, app, number)
    if order is None:
        return
    await _defer(interaction)
    result = await staff.ship(
        app,
        order,
        actor_id=interaction.user.id,
        carrier=carrier,
        tracking_number=tracking_number,
        tracking_url=link,
        rendered_status=None,
    )
    await _send(interaction, app, result)


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
    await _defer(interaction)
    await _send(interaction, app, await staff.note(app, order, actor_id=interaction.user.id, text=text))


def build_orders_command() -> app_commands.Command[Any, ..., None]:
    """A fresh ``/orders`` command (browse orders; the same list as ``/order list``)."""

    @app_commands.command(name="orders", description="Browse orders: filter by status or member, open a record.")
    @app_commands.describe(status="Only orders with this status", member="Only orders of this member")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @require_staff(staff_role_keys)
    async def orders(
        interaction: discord.Interaction[Any],
        status: StatusChoice | None = None,
        member: discord.Member | discord.User | None = None,
    ) -> None:
        await order_list(interaction, status, member)

    return orders


async def show_stats(interaction: discord.Interaction[Any], period: str) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    await _defer(interaction)
    await reply(interaction, await stats.stats_reply(app, period), ephemeral=True)


def build_stats_command() -> app_commands.Command[Any, ..., None]:
    """A fresh ``/stats`` command (sales of a period, the orders that need staff now, best sellers)."""

    @app_commands.command(name="stats", description="Sales, orders that need attention, and best sellers.")
    @app_commands.describe(period="Which orders count for sales and best sellers (default: the last 7 days)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @require_staff(staff_role_keys)
    async def stats_command(interaction: discord.Interaction[Any], period: stats.Period = "7d") -> None:
        await show_stats(interaction, period)

    return stats_command


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

    @group.command(name="view", description="Show everything about an order (same as lookup).")
    @app_commands.describe(number="Order number")
    @require_staff(staff_role_keys)
    async def view(interaction: discord.Interaction[Any], number: OrderNumber) -> None:
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

    @group.command(name="ship", description="Mark a paid order shipped and send the customer the tracking number.")
    @app_commands.describe(
        number="Order number",
        carrier="Carrier, e.g. USPS",
        tracking_number="Tracking number",
        link="Optional tracking page (https://...)",
    )
    @require_staff(staff_role_keys)
    async def ship(
        interaction: discord.Interaction[Any],
        number: OrderNumber,
        carrier: app_commands.Range[str, 1, h.CARRIER_MAX],
        tracking_number: app_commands.Range[str, 1, h.TRACKING_NUMBER_MAX],
        link: app_commands.Range[str, 1, h.TRACKING_URL_MAX] | None = None,
    ) -> None:
        await order_ship(interaction, number, carrier, tracking_number, link)

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
        text: app_commands.Range[str, 1, NOTE_MAX],
    ) -> None:
        await order_note(interaction, number, text)

    return group


async def discount_create(
    interaction: discord.Interaction[Any],
    *,
    code: str,
    kind: str,
    value: str | None,
    products: str | None,
    min_subtotal: str | None,
    max_uses: int | None,
    expires_in_days: int | None,
    once_per_customer: bool,
) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    await _defer(interaction)
    try:
        spec = await discount_codes.create(
            app,
            actor_id=interaction.user.id,
            code=code,
            kind=kind,
            value=value,
            products=products,
            min_subtotal=min_subtotal,
            max_uses=max_uses,
            expires_in_days=expires_in_days,
            once_per_customer=once_per_customer,
        )
    except discount_codes.Refused as exc:
        await _send(interaction, app, staff.OpReply(exc.text, "warning"))
        return
    await reply(interaction, spec, ephemeral=True)


async def discount_list(interaction: discord.Interaction[Any]) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    await _defer(interaction)
    try:
        specs = await discount_codes.list_specs(app)
    except discount_codes.Refused as exc:
        await _send(interaction, app, staff.OpReply(exc.text, "warning"))
        return
    for spec in specs:
        await reply(interaction, spec, ephemeral=True)


async def discount_end(interaction: discord.Interaction[Any], code: str) -> None:
    app = await _ready_app(interaction)
    if app is None:
        return
    await _defer(interaction)
    try:
        result = await discount_codes.end(app, actor_id=interaction.user.id, code=code)
    except discount_codes.Refused as exc:
        result = staff.OpReply(exc.text, "warning")
    await _send(interaction, app, result)


def build_discount_group() -> app_commands.Group:
    """A fresh ``/discount`` group (catalog managers and admins, like ``/product``)."""
    group = app_commands.Group(name="discount", description="Discount codes customers use at checkout.")
    app_commands.guild_only(group)
    app_commands.default_permissions(manage_guild=True)(group)

    @group.command(name="create", description="Create a discount code customers enter on the review step.")
    @app_commands.rename(kind="type")
    @app_commands.describe(
        code="The code customers type, e.g. SAVE10 (3-32 letters, digits, - or _)",
        kind="What the code gives",
        value="Percent (1-100) or amount, e.g. 5.00; leave empty for buy one, get one free",
        products="Only these product numbers, e.g. 12, 15 (empty: every item)",
        min_subtotal="Minimum items subtotal, e.g. 25.00",
        max_uses="How many orders can use it in total",
        expires_in_days="Ends after this many days (1-365; empty: no end date)",
        once_per_customer="Each customer can use it once (default: yes)",
    )
    @app_commands.choices(kind=DISCOUNT_TYPES)
    @require_staff(manager_role_keys)
    @require_manager()
    async def create(
        interaction: discord.Interaction[Any],
        code: app_commands.Range[str, 1, 40],
        kind: app_commands.Choice[str],
        value: app_commands.Range[str, 1, 24] | None = None,
        products: app_commands.Range[str, 1, 200] | None = None,
        min_subtotal: app_commands.Range[str, 1, 24] | None = None,
        max_uses: app_commands.Range[int, 1, 1_000_000] | None = None,
        expires_in_days: app_commands.Range[int, 1, discount_codes.MAX_DAYS] | None = None,
        once_per_customer: bool = True,
    ) -> None:
        await discount_create(
            interaction,
            code=code,
            kind=kind.value,
            value=value,
            products=products,
            min_subtotal=min_subtotal,
            max_uses=max_uses,
            expires_in_days=expires_in_days,
            once_per_customer=once_per_customer,
        )

    @group.command(name="list", description="Show the active discount codes, then the latest ended ones.")
    @require_staff(manager_role_keys)
    @require_manager()
    async def list_(interaction: discord.Interaction[Any]) -> None:
        await discount_list(interaction)

    @group.command(name="end", description="End a discount code (orders placed with it keep their discount).")
    @app_commands.describe(code="The code to end")
    @require_staff(manager_role_keys)
    @require_manager()
    async def end(interaction: discord.Interaction[Any], code: app_commands.Range[str, 1, 40]) -> None:
        await discount_end(interaction, code)

    return group


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.bot.tree.add_command(build_group(), guild=app.guild_object)
    app.bot.tree.add_command(build_discount_group(), guild=app.guild_object)
    app.bot.tree.add_command(build_orders_command(), guild=app.guild_object)
    app.bot.tree.add_command(build_stats_command(), guild=app.guild_object)
