"""Staff tooling shared by the staff order-control buttons and ``/order``: status changes, refunds, lookups and
the order list. Every reply here is operator text (staff/admin only, always private); the staff order-control
message itself is business text built by ``views.staff_message``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discord.utils import escape_markdown

from shopbot.core.money import MoneyError, currency_exponent, format_amount, parse_amount
from shopbot.features.checkout import flow, repo, service, views
from shopbot.features.checkout import models as m
from shopbot.ui.limits import EMBED_FIELD_VALUE, EMBED_FIELDS, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE
from shopbot.ui.spec import EmbedField, EmbedSpec, MessageSpec
from shopbot.ui.style import chunk_lines

if TYPE_CHECKING:
    from shopbot.context import App

LIST_PAGE_SIZE = 10
LOOKUP_EVENTS = 10


@dataclass(frozen=True)
class OpReply:
    text: str
    tone: str = "info"


def reply_spec(app: App, reply: OpReply) -> MessageSpec:
    return MessageSpec(embeds=(app.ui.operator_embed(reply.text, tone=reply.tone),))


# -- amounts (operator wording: separators from settings + the currency code) ----------------------------------------


def _exponent(app: App, currency: str) -> int:
    c = app.settings.checkout
    return currency_exponent(currency, c.currency_exponent if currency == c.currency else -1)


def plain_amount(app: App, minor: int, currency: str) -> str:
    c = app.settings.checkout
    return format_amount(minor, _exponent(app, currency), c.decimal_separator, c.thousands_separator)


def amount(app: App, minor: int, currency: str) -> str:
    return app.op("checkout.amount", amount=plain_amount(app, minor, currency), currency=currency)


def example_amount(app: App, currency: str) -> str:
    exponent = _exponent(app, currency)
    minor = 1250 * 10 ** (exponent - 2) if exponent >= 2 else 12 * 10**exponent
    return plain_amount(app, minor, currency)


def parse_staff_amount(app: App, text: str, currency: str) -> int:
    c = app.settings.checkout
    return parse_amount(text, _exponent(app, currency), c.decimal_separator, c.thousands_separator)


# -- status changes ----------------------------------------------------------------------------------------------


async def change_status(app: App, order: m.Order, target: str, *, actor_id: int, note: str | None = None) -> OpReply:
    """A staff status change (button or command): only valid transitions, through the same state machine."""
    number = order.number
    if target not in m.STAFF_TARGETS:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=number, status=order.status, target=target), "warning"
        )
    if target == m.PAID:
        provider = service.provider_for(order)
        if provider is not None and not provider.requires_staff_confirmation:
            return OpReply(app.op("checkout.op.provider_confirms", number=number, provider=provider.id), "warning")
    if order.status not in m.TRANSITIONS[target]:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=number, status=order.status, target=target), "warning"
        )
    outcome = await service.change_status(app, order.id, target, actor_id=actor_id, note=note)
    if not outcome.applied:
        current = outcome.order
        await service.refresh_staff_message(app, order.id)
        status = current.status if current is not None else "-"
        return OpReply(app.op("checkout.op.already_handled", number=number, status=status), "warning")
    return OpReply(app.op("checkout.op.status_changed", number=number, status=target), "success")


# -- refunds --------------------------------------------------------------------------------------------------------


async def refund(
    app: App, order: m.Order, *, amount_text: str, to_credit: bool, reason: str | None, actor_id: int
) -> OpReply:
    number = order.number
    try:
        minor = parse_staff_amount(app, amount_text, order.currency)
    except MoneyError as exc:
        text = app.op("checkout.op.refund_invalid_amount", reason=str(exc), example=example_amount(app, order.currency))
        return OpReply(text, "warning")
    try:
        outcome = await service.refund(
            app, order.id, amount_minor=minor, to_credit=to_credit, reason=reason, actor_id=actor_id
        )
    except service.RefundError as exc:
        current = exc.order or order
        if exc.reason == "amount":
            text = app.op(
                "checkout.op.refund_too_much",
                number=number,
                max=amount(app, current.refundable_minor, current.currency),
            )
        elif exc.reason == "credits_unavailable":
            text = app.op("checkout.op.credits_unavailable")
        elif exc.reason == "reason":
            text = app.op("checkout.op.reason_too_long", max=service.REFUND_REASON_MAX)
        elif exc.reason == "provider_failed":
            text = app.op("checkout.op.refund_failed", number=number, error_id=exc.error_id or "-")
        elif exc.reason == "missing":
            text = app.op("checkout.op.not_found", number=number)
        else:
            text = app.op("checkout.op.refund_not_allowed", number=number, status=current.status)
        return OpReply(text, "warning")
    record = outcome.record
    shown = amount(app, minor, order.currency)
    if to_credit:
        key = "checkout.op.refund_credit"
    elif outcome.result is not None and outcome.result.returned_by_provider:
        key = "checkout.op.refund_provider"
    else:
        key = "checkout.op.refund_manual"
    lines = [app.op(key, number=number, amount=shown)]
    if record.transition is not None:
        lines.append(app.op("checkout.op.fully_refunded", number=number))
    else:
        lines.append(
            app.op(
                "checkout.op.refund_remaining",
                remaining=amount(app, record.order.refundable_minor, record.order.currency),
            )
        )
    return OpReply("\n".join(lines), "success")


# -- order list --------------------------------------------------------------------------------------------------


async def list_view(app: App, page: int, status: str, user_id: int) -> MessageSpec:
    guild_id = app.env.guild_id

    def work(conn: sqlite3.Connection) -> tuple[int, list[m.Order], int]:
        total = repo.count_orders(conn, guild_id, status=status, user_id=user_id)
        pages = max(1, -(-total // LIST_PAGE_SIZE))
        current = min(max(0, page), pages - 1)
        orders = repo.list_orders(
            conn, guild_id, status=status, user_id=user_id, limit=LIST_PAGE_SIZE, offset=current * LIST_PAGE_SIZE
        )
        return total, orders, current

    total, orders, current = await app.db.run(work)
    pages = max(1, -(-total // LIST_PAGE_SIZE))
    return views.order_list_page(
        app.ui,
        app.op,
        lambda minor, currency: amount(app, minor, currency),
        orders,
        page=current,
        pages=pages,
        total=total,
        status=status,
        user_id=user_id,
    )


# -- lookup --------------------------------------------------------------------------------------------------------


def _when(timestamp: int | None) -> str:
    return f"<t:{timestamp}:f>" if timestamp else "-"


def _label(app: App, answer_key: str) -> str:
    """The configured label of an answer, or (operator view only) the field key when it has none."""
    label_key = flow.label_key_for_answer(answer_key)
    if label_key is not None and label_key in app.content.registry:
        text = app.content.get(label_key)
        if text:
            return text
    return app.op("checkout.lookup.unlabelled", key=answer_key)


def _blocks(name: str, lines: list[str]) -> list[EmbedField]:
    return [EmbedField(name=name, value=block) for block in chunk_lines(lines, EMBED_FIELD_VALUE)] if lines else []


def _pack(app: App, title: str, fields: list[EmbedField]) -> list[MessageSpec]:
    """Operator embeds holding every field, split into messages within Discord's limits (nothing is cut)."""
    messages: list[MessageSpec] = []
    embeds: list[EmbedSpec] = []
    message_used = 0
    current: list[EmbedField] = []
    current_used = len(title)
    for item in fields:
        size = len(item.name) + len(item.value)
        if current and (len(current) >= EMBED_FIELDS or message_used + current_used + size > EMBED_TOTAL_CHARS):
            embeds.append(app.ui.operator_embed(title, fields=current))
            message_used += current_used
            current, current_used = [], len(title)
            if message_used + current_used + size > EMBED_TOTAL_CHARS or len(embeds) >= EMBEDS_PER_MESSAGE:
                messages.append(MessageSpec(embeds=tuple(embeds)))
                embeds, message_used = [], 0
        current.append(item)
        current_used += size
    if current:
        embeds.append(app.ui.operator_embed(title, fields=current))
    if embeds:
        messages.append(MessageSpec(embeds=tuple(embeds)))
    if not messages:
        messages.append(MessageSpec(embeds=(app.ui.operator_embed(title),)))
    return messages


async def lookup_messages(app: App, order: m.Order) -> list[MessageSpec]:
    """Everything staff need about an order (operator text), split into as many messages as needed."""
    view = await service.order_view(app, order.id)
    if view is None:
        return [reply_spec(app, OpReply(app.op("checkout.op.not_found", number=order.number), "warning"))]
    order = view.order
    payment = await app.db.run(repo.payment_for, order.id)
    adjustments = await app.db.run(repo.adjustments, order.id)
    history = await app.db.run(repo.events, order.id)
    op = app.op
    cur = order.currency

    def money(minor: int) -> str:
        return amount(app, minor, cur)

    fields = [
        EmbedField(op("checkout.lookup.status"), order.status, inline=True),
        EmbedField(op("checkout.lookup.customer"), views.user_mention(order.user_id), inline=True),
        EmbedField(op("checkout.lookup.channel"), views.channel_mention(view.channel_id) or "-", inline=True),
        EmbedField(op("checkout.lookup.created"), _when(order.created_at), inline=True),
        EmbedField(op("checkout.lookup.paid"), _when(order.paid_at), inline=True),
        EmbedField(op("checkout.lookup.fulfilled"), _when(order.fulfilled_at), inline=True),
        EmbedField(op("checkout.lookup.subtotal"), money(order.subtotal_minor), inline=True),
        EmbedField(
            op("checkout.lookup.fulfillment"),
            op(
                "checkout.lookup.fulfillment_value",
                option=order.fulfillment_option or "-",
                fee=money(order.fulfillment_fee_minor),
            ),
            inline=True,
        ),
        EmbedField(op("checkout.lookup.fees"), money(order.fees_minor), inline=True),
        EmbedField(op("checkout.lookup.credits"), money(order.credits_applied_minor), inline=True),
        EmbedField(op("checkout.lookup.total"), money(order.total_minor), inline=True),
        EmbedField(op("checkout.lookup.refunded"), money(order.refunded_minor), inline=True),
    ]
    if payment is not None:
        fields.append(
            EmbedField(
                op("checkout.lookup.payment"),
                op(
                    "checkout.lookup.payment_value",
                    provider=payment.provider,
                    status=payment.status,
                    reference=escape_markdown(payment.external_ref) if payment.external_ref else "-",
                ),
            )
        )
    item_lines = [
        op(
            "checkout.lookup.item",
            quantity=i.quantity,
            name=escape_markdown(i.name),
            product_id=i.product_id,
            unit=money(i.unit_price_minor),
            total=money(i.line_total_minor),
        )
        for i in view.items
    ]
    fields += _blocks(op("checkout.lookup.items"), item_lines)
    fee_lines = [op("checkout.lookup.fee_line", fee=f.fee_id, amount=money(f.amount_minor)) for f in view.fees]
    fields += _blocks(op("checkout.lookup.fee_lines"), fee_lines)
    for answer_key, answer in views.ordered_answers(app.settings, order):
        fields += views.answer_fields(_label(app, answer_key), answer)
    refund_lines = [
        op(
            "checkout.lookup.refund_line",
            amount=money(a.amount_minor),
            method=a.method,
            actor=views.user_mention(a.actor_id) if a.actor_id else "-",
            when=_when(a.created_at),
            reason=escape_markdown(a.reason) if a.reason else "-",
        )
        for a in adjustments
    ]
    fields += _blocks(op("checkout.lookup.refunds"), refund_lines)
    event_lines = [
        op(
            "checkout.lookup.event",
            when=_when(e.created_at),
            old=e.from_status or "-",
            new=e.to_status,
            actor=views.user_mention(e.actor_id) if e.actor_id else op("checkout.lookup.automatic"),
            note=escape_markdown(e.note) if e.note else "-",
        )
        for e in history[-LOOKUP_EVENTS:]
    ]
    fields += _blocks(op("checkout.lookup.history"), event_lines)
    return _pack(app, op("checkout.lookup.title", number=order.number), fields)
