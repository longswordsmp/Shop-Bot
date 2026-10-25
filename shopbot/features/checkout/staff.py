"""Staff tooling shared by the staff order-control buttons and ``/order``: status changes, refunds, claims, notes,
correction requests, tracking details, rejection with a reason, lookups and the order list. Every reply here is
operator text (staff/admin only, always private); the live order record itself is business text built by
``staff_views.staff_messages``.

Every action re-checks the order's current state in its transaction (conditional writes), so a double click or two
staff members acting at once never apply anything twice; each one is audited and refreshes the live record.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.core.money import MoneyError, currency_exponent, format_amount, parse_amount
from shopbot.features.checkout import flow, repo, service, staff_keys, views
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.ui.limits import EMBED_FIELD_VALUE, EMBED_FIELDS, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE
from shopbot.ui.spec import EmbedField, EmbedSpec, MessageSpec
from shopbot.ui.style import chunk_lines

if TYPE_CHECKING:
    from shopbot.context import App

LIST_PAGE_SIZE = 10
LOOKUP_EVENTS = 10
LOOKUP_ACTIVITY = 15

# Banner of a staff reply by its tone: a refusal or warning shows the error banner, anything else the general one.
REPLY_BANNERS: Mapping[str, BannerId] = {"warning": "error", "danger": "error"}
# The full order record (details button, /order lookup and view, the /orders menu): on its first message only.
RECORD_DETAILS_BANNER: BannerId = "staff-dashboard"


@dataclass(frozen=True)
class OpReply:
    text: str
    tone: str = "info"


def reply_spec(app: App, reply: OpReply) -> MessageSpec:
    banner = app.ui.banner(REPLY_BANNERS.get(reply.tone, "general-response"))
    return MessageSpec(embeds=(app.ui.operator_embed(reply.text, tone=reply.tone),), banner=banner)


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


async def _already_handled(app: App, order: m.Order, current: m.Order | None) -> OpReply:
    """Another change came first: nothing was changed; the staff controls are brought up to date."""
    await service.refresh_staff_message(app, order.id)
    status = current.status if current is not None else "-"
    return OpReply(app.op("checkout.op.already_handled", number=order.number, status=status), "warning")


def _outstanding(app: App, order: m.Order) -> list[str]:
    """Operator lines: what of a paid order can still be refunded (money) or given back (applied store credit)."""
    lines: list[str] = []
    if order.refundable_minor > 0:
        lines.append(
            app.op("checkout.op.refund_remaining", remaining=amount(app, order.refundable_minor, order.currency))
        )
    if order.credit_returnable_minor > 0:
        lines.append(
            app.op("checkout.op.credit_returnable", amount=amount(app, order.credit_returnable_minor, order.currency))
        )
    return lines


async def change_status(
    app: App,
    order: m.Order,
    target: str,
    *,
    actor_id: int,
    note: str | None = None,
    rendered_status: str | None = None,
) -> OpReply:
    """A staff status change (button or command): only valid transitions, through the same state machine.

    ``rendered_status``: the status the clicked button was rendered for (buttons only; "" for a button rendered
    before buttons carried it). The change applies only while the order is still in that status - otherwise
    nothing changes, the reply says so and the controls are refreshed. Commands act on the current status."""
    number = order.number
    if target not in m.STAFF_TARGETS:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=number, status=order.status, target=target), "warning"
        )
    if target == m.PAID:
        provider = service.provider_for(order)
        if provider is not None and not provider.requires_staff_confirmation:
            return OpReply(app.op("checkout.op.provider_confirms", number=number, provider=provider.id), "warning")
    if rendered_status is not None and rendered_status != order.status:
        return await _already_handled(app, order, order)
    if order.status not in m.TRANSITIONS[target]:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=number, status=order.status, target=target), "warning"
        )
    allowed_from = (rendered_status,) if rendered_status is not None else None
    outcome = await service.change_status(
        app, order.id, target, actor_id=actor_id, note=note, allowed_from=allowed_from
    )
    if not outcome.applied:
        return await _already_handled(app, order, outcome.order)
    lines = [app.op("checkout.op.status_changed", number=number, status=target)]
    current = outcome.order
    if target == m.CANCELLED and outcome.previous != m.PENDING and current is not None and current.refunds_allowed:
        outstanding = _outstanding(app, current)
        if outstanding:
            lines += [app.op("checkout.op.paid_cancel", number=number), *outstanding]
    return OpReply("\n".join(lines), "success")


# -- rejection with a reason ------------------------------------------------------------------------------------


async def reject(app: App, order: m.Order, *, actor_id: int, reason: str, rendered_status: str) -> OpReply:
    """Reject an unpaid order with a reason for the customer (the rejection form). Acts only while the order is still
    in the status the reject button was rendered for; the reason goes into the customer's rejection notification."""
    number = order.number
    text = reason.strip()
    if not text or len(text) > h.TEXT_MAX:
        return OpReply(app.op("checkout.op.text_invalid", max=h.TEXT_MAX), "warning")
    if rendered_status != order.status:
        return await _already_handled(app, order, order)
    if order.status not in m.TRANSITIONS[m.REJECTED]:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=number, status=order.status, target=m.REJECTED), "warning"
        )
    result = await app.db.transaction(
        h.reject_with_reason,
        guild_id=app.env.guild_id,
        order_id=order.id,
        allowed_from=(rendered_status,),
        actor_id=actor_id,
        reason=text,
        now=app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
    )
    if result is None:
        return await _already_handled(app, order, await service.get_order(app, order.id))
    moved, _ = result
    await service.after_transition(app, moved, actor_id=actor_id)
    return OpReply(app.op("checkout.op.status_changed", number=number, status=m.REJECTED), "success")


async def ship_refused(app: App, order: m.Order, rendered_status: str | None) -> OpReply | None:
    """Why the order cannot be marked shipped now (None: it can). ``rendered_status``: the status the Mark shipped
    button was rendered for (None from /order ship, which acts on the current status)."""
    if rendered_status is not None and rendered_status != order.status:
        return await _already_handled(app, order, order)
    if order.status not in m.TRANSITIONS[m.SHIPPED]:
        return OpReply(
            app.op("checkout.op.invalid_transition", number=order.number, status=order.status, target=m.SHIPPED),
            "warning",
        )
    return None


async def ship(
    app: App,
    order: m.Order,
    *,
    actor_id: int,
    carrier: str,
    tracking_number: str,
    tracking_url: str | None,
    rendered_status: str | None,
) -> OpReply:
    """Mark a paid order shipped with its carrier, tracking number and optional tracking link (the Mark shipped form,
    or /order ship with ``rendered_status`` None). The form acts only while the order is still in the status its
    button was rendered for; the customer is told by direct message (or in the ticket), with the tracking details."""
    number = order.number
    refused = await ship_refused(app, order, rendered_status)
    if refused is not None:
        return refused
    try:
        moved = await app.db.transaction(
            h.ship_order,
            guild_id=app.env.guild_id,
            order_id=order.id,
            allowed_from=(order.status if rendered_status is None else rendered_status,),
            actor_id=actor_id,
            carrier=carrier,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            now=app.clock.now(),
            task_attempts=app.scheduler.max_attempts,
        )
    except h.HandlingError as exc:
        if exc.reason == "url":
            return OpReply(app.op("checkout.op.tracking_url_invalid"), "warning")
        return _handling_refused(app, order, exc, state_key="checkout.op.tracking_state")
    if moved is None:
        return await _already_handled(app, order, await service.get_order(app, order.id))
    await service.after_transition(app, moved, actor_id=actor_id)
    return OpReply(app.op("checkout.op.status_changed", number=number, status=m.SHIPPED), "success")


# -- claims, notes, correction requests, tracking details -----------------------------------------------------------


def _staff_mention(user_id: int | None) -> str:
    return views.user_mention(user_id) if user_id else "-"


def _handling_refused(app: App, order: m.Order, exc: h.HandlingError, *, state_key: str) -> OpReply:
    current = exc.order or order
    number = current.number
    if exc.reason == "missing":
        return OpReply(app.op("checkout.op.order_missing"), "warning")
    if exc.reason == "claimed":
        return OpReply(
            app.op("checkout.op.claim_taken", number=number, staff=_staff_mention(exc.claimed_by)), "warning"
        )
    if exc.reason == "not_claimed":
        return OpReply(app.op("checkout.op.not_claimed", number=number), "warning")
    if exc.reason == "not_claimer":
        return OpReply(
            app.op("checkout.op.not_claimer", number=number, staff=_staff_mention(exc.claimed_by)), "warning"
        )
    if exc.reason == "text":
        return OpReply(app.op("checkout.op.text_invalid", max=h.TEXT_MAX), "warning")
    return OpReply(app.op(state_key, number=number, status=current.status), "warning")


async def claim(app: App, order: m.Order, *, actor_id: int) -> OpReply:
    """Claim an open order. A second claim (by anyone) is refused and changes nothing."""
    try:
        await service.record_activity(app, h.claim, order.id, actor_id=actor_id, audit_action="checkout.order_claimed")
    except h.HandlingError as exc:
        await service.refresh_staff_message(app, order.id)  # bring a stale record up to date
        return _handling_refused(app, order, exc, state_key="checkout.op.claim_state")
    return OpReply(app.op("checkout.op.claimed", number=order.number), "success")


async def unclaim(app: App, order: m.Order, *, actor_id: int, override: bool) -> OpReply:
    """Release the claim (the member holding it, or an admin with ``override``)."""
    try:
        await service.record_activity(
            app, h.unclaim, order.id, actor_id=actor_id, audit_action="checkout.order_unclaimed", override=override
        )
    except h.HandlingError as exc:
        await service.refresh_staff_message(app, order.id)
        return _handling_refused(app, order, exc, state_key="checkout.op.claim_state")
    return OpReply(app.op("checkout.op.unclaimed", number=order.number), "success")


async def note(app: App, order: m.Order, *, actor_id: int, text: str) -> OpReply:
    """An internal note (never shown to the customer)."""
    if not text.strip() or len(text.strip()) > h.NOTE_MAX:
        return OpReply(app.op("checkout.op.text_invalid", max=h.NOTE_MAX), "warning")
    try:
        await service.record_activity(
            app, h.add_note, order.id, actor_id=actor_id, audit_action="checkout.order_note", text=text
        )
    except h.HandlingError as exc:
        return _handling_refused(app, order, exc, state_key="checkout.op.claim_state")
    return OpReply(app.op("checkout.op.note_added", number=order.number), "success")


_DELIVERY_TEXT = {
    "dm": "checkout.op.delivery.dm",
    "channel": "checkout.op.delivery.channel",
    "duplicate": "checkout.op.delivery.duplicate",
}


def _delivery(app: App, delivery: str) -> str:
    return app.op(_DELIVERY_TEXT.get(delivery, "checkout.op.delivery.none"))


async def request_correction(app: App, order: m.Order, *, actor_id: int, text: str) -> OpReply:
    """Ask the customer of an order waiting for payment to correct information; the order keeps waiting."""
    try:
        change = await service.record_activity(
            app,
            h.request_correction,
            order.id,
            actor_id=actor_id,
            audit_action="checkout.order_correction_requested",
            text=text,
        )
    except h.HandlingError as exc:
        return _handling_refused(app, order, exc, state_key="checkout.op.correction_state")
    delivery = await app.db.run(_delivery_of, service.notify_key(order.id, f"correction:{change.activity.id}"))
    tone = "success" if delivery in ("dm", "channel") else "warning"
    return OpReply(app.op("checkout.op.correction_sent", number=order.number, delivery=_delivery(app, delivery)), tone)


async def set_tracking(app: App, order: m.Order, *, actor_id: int, text: str) -> OpReply:
    """Store tracking details; the customer of a shipped or delivered order is told right away (before that, Mark
    shipped sends the shipment)."""
    if not staff_keys.tracking_enabled(app.content):
        return OpReply(app.op("checkout.op.tracking_unavailable"), "warning")
    try:
        change = await service.record_activity(
            app, h.set_tracking, order.id, actor_id=actor_id, audit_action="checkout.order_tracking_set", text=text
        )
    except h.HandlingError as exc:
        return _handling_refused(app, order, exc, state_key="checkout.op.tracking_state")
    if change.order.status not in (m.SHIPPED, m.FULFILLED):
        return OpReply(app.op("checkout.op.tracking_saved", number=order.number), "success")
    delivery = await app.db.run(_delivery_of, service.notify_key(order.id, f"tracking:{change.activity.id}"))
    tone = "success" if delivery in ("dm", "channel") else "warning"
    return OpReply(app.op("checkout.op.tracking_sent", number=order.number, delivery=_delivery(app, delivery)), tone)


def _delivery_of(conn: sqlite3.Connection, dedupe_key: str) -> str:
    """How a notification was delivered ('dm', 'channel', 'none'), from its delivery record."""
    row = conn.execute("SELECT delivery, status FROM notifications WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    if row is None or str(row["status"]) != "sent":
        return "none"
    return str(row["delivery"])


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
        elif exc.reason == "currency":
            text = currency_refused(app, current)
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
    return OpReply("\n".join([app.op(key, number=number, amount=shown), *_after_refund_lines(app, record)]), "success")


def _after_refund_lines(app: App, record: repo.RefundRecord) -> list[str]:
    if record.transition is not None:
        return [app.op("checkout.op.fully_refunded", number=record.order.number)]
    return _outstanding(app, record.order)


def currency_refused(app: App, order: m.Order) -> str:
    """Operator text: this order cannot be refunded to store credit (paid in another currency than the shop's)."""
    return app.op(
        "checkout.op.refund_currency",
        number=order.number,
        currency=order.currency,
        shop_currency=app.settings.checkout.currency or "-",
    )


async def return_credit(app: App, order: m.Order, *, reason: str | None, actor_id: int) -> OpReply:
    """Give the store credit applied to a paid order back to the customer (all of it, once)."""
    number = order.number
    try:
        outcome = await service.return_credit(app, order.id, reason=reason, actor_id=actor_id)
    except service.RefundError as exc:
        current = exc.order or order
        if exc.reason == "reason":
            text = app.op("checkout.op.reason_too_long", max=service.REFUND_REASON_MAX)
        elif exc.reason == "missing":
            text = app.op("checkout.op.not_found", number=number)
        else:
            text = app.op("checkout.op.credit_return_not_allowed", number=number, status=current.status)
        return OpReply(text, "warning")
    record = outcome.record
    shown = amount(app, record.adjustment.amount_minor, record.order.currency)
    lines = [app.op("checkout.op.credit_returned", number=number, amount=shown), *_after_refund_lines(app, record)]
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


def _pieces(line: str) -> list[str]:
    """``line`` in pieces that each fit an embed field (operator display of staff-entered text; nothing is lost)."""
    return [line[i : i + EMBED_FIELD_VALUE] for i in range(0, len(line), EMBED_FIELD_VALUE)] or [line]


def _blocks(name: str, lines: list[str]) -> list[EmbedField]:
    return [EmbedField(name=name, value=block) for block in chunk_lines(lines, EMBED_FIELD_VALUE)] if lines else []


def _pack(app: App, title: str, fields: list[EmbedField]) -> list[MessageSpec]:
    """Operator embeds holding every field, split into messages within Discord's limits (nothing is cut). The first
    message shows the staff banner; the messages after it continue the same record and show none."""
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
    messages[0] = replace(messages[0], banner=app.ui.banner(RECORD_DETAILS_BANNER))
    return messages


async def lookup_messages(app: App, order: m.Order) -> list[MessageSpec]:
    """Everything staff need about an order (operator text), split into as many messages as needed."""
    view = await service.order_view(app, order.id)
    if view is None:
        return [reply_spec(app, OpReply(app.op("checkout.op.not_found", number=order.number), "warning"))]
    payment = await app.db.run(repo.payment_for, order.id)
    adjustments = await app.db.run(repo.adjustments, order.id)
    data = await service.staff_data(app, order.id)
    return lookup_specs(app, view, payment, adjustments, data)


def lookup_specs(
    app: App,
    view: m.OrderView,
    payment: m.Payment | None,
    adjustments: list[m.Adjustment],
    data: h.StaffData,
) -> list[MessageSpec]:
    """The full order record (``/order lookup``/``view``, the details button) from loaded data (pure; the preview
    builds it from sample data)."""
    order = view.order
    history = list(data.events)
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
    if order.credits_applied_minor > 0:
        fields.append(
            EmbedField(op("checkout.lookup.credit_returned"), money(order.credit_returned_minor), inline=True)
        )
    handling = data.handling
    if handling.claimed_by is not None:
        claimed = op(
            "checkout.lookup.claimed_value",
            staff=views.user_mention(handling.claimed_by),
            when=_when(handling.claimed_at),
        )
        fields.append(EmbedField(op("checkout.lookup.claimed"), claimed, inline=True))
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
            "checkout.lookup.credit_return_line" if a.kind == "credit_return" else "checkout.lookup.refund_line",
            amount=money(a.amount_minor),
            method=a.method,
            actor=views.user_mention(a.actor_id) if a.actor_id else "-",
            when=_when(a.created_at),
            reason=escape_markdown(a.reason) if a.reason else "-",
        )
        for a in adjustments
    ]
    # Free text (refund reasons, rejection reasons, notes) may be long: split, never cut.
    fields += _blocks(op("checkout.lookup.refunds"), [piece for line in refund_lines for piece in _pieces(line)])
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
    fields += _blocks(op("checkout.lookup.history"), [piece for line in event_lines for piece in _pieces(line)])
    if handling.tracking:
        fields += views.answer_fields(op("checkout.lookup.tracking"), handling.tracking)
    activity_lines = [
        op(
            "checkout.lookup.activity_line",
            when=_when(a.created_at),
            kind=a.kind,
            actor=views.user_mention(a.actor_id) if a.actor_id else op("checkout.lookup.automatic"),
            text=escape_markdown(a.text) if a.text else "-",
        )
        for a in data.activity[-LOOKUP_ACTIVITY:]
    ]
    # A long note or message is split over several lines (never cut).
    fields += _blocks(op("checkout.lookup.activity"), [piece for line in activity_lines for piece in _pieces(line)])
    return _pack(app, op("checkout.lookup.title", number=order.number), fields)
