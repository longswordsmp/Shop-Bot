"""Pure builders for the staff side of orders: the live order record in the staff channel (#orders), its action
buttons, the staff-only forms (operator text), and the customer notifications staff actions send.

The live record always shows the order's unique reference, the customer, the current status, who claimed it, the
order summary (lines and totals), a TIMELINE of its stages (status changes from ``order_events`` plus claims,
correction requests and tracking details from ``order_activity``) and the latest internal notes. Field names and
stage names are content keys; values are data (numbers, mentions, Discord timestamps, staff-entered text escaped
with ``escape_markdown``). Discord's limits are budgeted: configured text is never cut; when the record does not fit,
the oldest notes and timeline entries are left out first (the details button shows everything), then the order lines
move to separate messages.

Like ``views``, nothing here calls Discord or the database. Missing required content raises ``MissingContent``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys as sk
from shopbot.features.checkout import views
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELD_VALUE
from shopbot.ui.spec import (
    ButtonSpec,
    ButtonStyle,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui, chunk_lines

OperatorText = views.OperatorText
Rows = views.Rows
STAFF_ROUTE = k.STAFF_ROUTE

NOTE_INPUT = "note"
MESSAGE_INPUT = "message"
TRACKING_INPUT = "tracking"
REASON_INPUT = "reason"

# At most this many timeline entries / internal notes are listed on the live record (latest first kept).
TIMELINE_MAX = 20
NOTES_MAX = 5

STATUS_TONES = {
    m.PENDING: "info",
    m.PAID: "success",
    m.PROCESSING: "info",
    m.FULFILLED: "success",
    m.CANCELLED: "neutral",
    m.REJECTED: "danger",
    m.EXPIRED: "neutral",
    m.REFUNDED: "neutral",
}

_STAGE_KEYS = {
    h.CLAIM: sk.STAGE_CLAIMED,
    h.UNCLAIM: sk.STAGE_UNCLAIMED,
    h.CORRECTION: sk.STAGE_CORRECTION,
    h.TRACKING: sk.STAGE_TRACKING,
}


# -- custom ids (every one carries only the order id; handlers reload the order) -----------------------------------


def claim_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "claim", order_id)


def unclaim_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "unclaim", order_id)


def note_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "note", order_id)


def note_form_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "noteform", order_id)


def correction_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "correct", order_id)


def correction_form_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "correctform", order_id)


def tracking_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "track", order_id)


def tracking_form_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "trackform", order_id)


def record_details_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "details", order_id)


def reject_form_id(order_id: int, status: str) -> str:
    """The rejection form; it carries the status the reject button was rendered for (a stale form changes nothing)."""
    return cid(STAFF_ROUTE, "rejectform", order_id, status)


# Management actions on the live record whose custom_id carries the order id first (see views.ORDER_CONTROL_ACTIONS).
MANAGEMENT_ACTIONS = views.MANAGEMENT_ACTIONS


# -- small helpers -------------------------------------------------------------------------------------------------


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _optional(ui: Ui, key: str, /, **values: str | int) -> str | None:
    if key not in ui.content.registry:
        return None
    return ui.text(key, **values)


def _button(ui: Ui, key: str, custom_id: str, *, style: ButtonStyle = "secondary") -> ButtonSpec:
    button = ui.button(key, custom_id, style=style)
    if button is None:
        raise MissingContent(key)
    return button


def _when(timestamp: int) -> str:
    return f"<t:{timestamp}:f>"


def _relative(timestamp: int) -> str:
    return f"<t:{timestamp}:R>"


def status_label(ui: Ui, status: str) -> str:
    return _text(ui, k.status_label_key(status))


def text_fields(name: str, text: str) -> list[EmbedField]:
    """Staff-entered text under ``name``, escaped and split into fields within Discord's field limit (nothing cut)."""
    return views.answer_fields(name, text)


# -- the live order record ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEntry:
    created_at: int
    stage: str
    actor_id: int | None
    order: int  # tie-breaker: events before activity at the same second, then by id


def timeline_entries(ui: Ui, data: h.StaffData) -> list[TimelineEntry]:
    """Every stage of the order, oldest first: status changes and the staff activity that has a stage name."""
    entries: list[TimelineEntry] = []
    for event in data.events:
        if event.from_status is not None and event.from_status == event.to_status:
            continue  # a note without a status change (recorded by an older version)
        entries.append(TimelineEntry(event.created_at, status_label(ui, event.to_status), event.actor_id, event.id))
    for item in data.activity:
        key = _STAGE_KEYS.get(item.kind)
        if key is None:
            continue
        stage = _text(ui, key) if key != sk.STAGE_TRACKING else _optional(ui, key)
        if stage:
            entries.append(TimelineEntry(item.created_at, stage, item.actor_id, 1_000_000_000 + item.id))
    return sorted(entries, key=lambda e: (e.created_at, e.order))


def timeline_lines(ui: Ui, entries: Sequence[TimelineEntry]) -> list[str]:
    return [
        _text(
            ui,
            sk.TIMELINE_LINE,
            time=_when(e.created_at),
            stage=e.stage,
            actor=views.user_mention(e.actor_id) if e.actor_id else "",
        )
        for e in entries
    ]


def note_lines(ui: Ui, notes: Sequence[h.Activity]) -> list[str]:
    lines: list[str] = []
    for note in notes:
        line = _text(
            ui,
            sk.NOTE_LINE,
            time=_when(note.created_at),
            actor=views.user_mention(note.actor_id) if note.actor_id else "",
            note=views.escape(note.text or ""),
        )
        if len(line) <= EMBED_FIELD_VALUE:  # a note that cannot be listed whole is only in the details view
            lines.append(line)
    return lines


def _blocks(name: str, lines: Sequence[str]) -> list[EmbedField]:
    return [EmbedField(name=name, value=block) for block in chunk_lines(list(lines), EMBED_FIELD_VALUE)]


def _summary_fields(ui: Ui, settings: Settings, view: m.OrderView) -> tuple[list[EmbedField], list[EmbedField]]:
    """(order lines, totals) as stored in the order, in the order's currency."""
    order = view.order
    fmt = views.order_money(ui, settings, order.currency)
    lines = [
        views.line_text(ui, i.name, i.quantity, i.unit_price_minor, i.line_total_minor, fmt=fmt) for i in view.items
    ]
    cart_name = _text(ui, k.CART_FIELD)
    line_fields = _blocks(cart_name, lines) if lines else [EmbedField(name=cart_name, value=_text(ui, k.CART_EMPTY))]
    totals = [EmbedField(_text(ui, k.CART_SUBTOTAL_FIELD), fmt(order.subtotal_minor), inline=True)]
    option = order.fulfillment_option
    option_label = _optional(ui, k.option_key(option, "label")) if option else None
    chosen_name = _optional(ui, k.FULFILLMENT_CHOSEN_FIELD)
    if option_label and chosen_name:
        totals.append(EmbedField(chosen_name, option_label, inline=True))
    fee_name = _optional(ui, k.FULFILLMENT_FEE_FIELD)
    if order.fulfillment_fee_minor > 0 and fee_name:
        totals.append(EmbedField(fee_name, fmt(order.fulfillment_fee_minor), inline=True))
    for fee in view.fees:
        name = _optional(ui, k.fee_label_key(fee.fee_id))
        if name:
            totals.append(EmbedField(name, fmt(fee.amount_minor), inline=True))
    credits_name = _optional(ui, k.REVIEW_CREDITS_FIELD)
    if order.credits_applied_minor > 0 and credits_name:
        totals.append(EmbedField(credits_name, fmt(order.credits_applied_minor), inline=True))
    totals.append(EmbedField(_text(ui, k.REVIEW_TOTAL_FIELD), fmt(order.total_minor), inline=True))
    if order.refunded_minor > 0:
        totals.append(EmbedField(_text(ui, k.STAFF_REFUNDED_FIELD), fmt(order.refunded_minor), inline=True))
    returned_name = _optional(ui, k.STAFF_CREDIT_RETURNED_FIELD)
    if order.credit_returned_minor > 0 and returned_name:
        totals.append(EmbedField(returned_name, fmt(order.credit_returned_minor), inline=True))
    return line_fields, totals


def _record_fields(ui: Ui, view: m.OrderView, data: h.StaffData, status: str) -> list[EmbedField]:
    """Reference, customer, order channel, status, claim and tracking details."""
    order = view.order
    fields = [
        EmbedField(_text(ui, sk.REFERENCE_FIELD), str(order.number), inline=True),
        EmbedField(_text(ui, sk.CUSTOMER_FIELD), views.user_mention(order.user_id), inline=True),
    ]
    channel_name = _optional(ui, sk.CHANNEL_FIELD)
    if channel_name and view.channel_id:
        fields.append(EmbedField(channel_name, views.channel_mention(view.channel_id), inline=True))
    fields.append(EmbedField(_text(ui, sk.STATUS_FIELD), status, inline=True))
    handling = data.handling
    if handling.claimed_by is not None and handling.claimed_at is not None:
        claimed = f"{views.user_mention(handling.claimed_by)} {_relative(handling.claimed_at)}"
        fields.append(EmbedField(_text(ui, sk.CLAIMED_FIELD), claimed, inline=True))
    tracking_name = _optional(ui, sk.TRACKING_FIELD)
    if handling.tracking and tracking_name:
        fields += text_fields(tracking_name, handling.tracking)
    return fields


def management_buttons(ui: Ui, order: m.Order, handling: h.Handling, *, tracking: bool) -> list[ButtonSpec]:
    """Claim (or release the claim) while the order is open, request a correction while it waits for payment, tracking
    details once it is paid (when tracking is used), add an internal note, show the full record."""
    buttons: list[ButtonSpec] = []
    if order.status in h.CLAIMABLE_STATUSES:
        if handling.claimed_by is None:
            buttons.append(_button(ui, sk.BUTTON_CLAIM, claim_id(order.id), style="primary"))
        else:
            buttons.append(_button(ui, sk.BUTTON_UNCLAIM, unclaim_id(order.id)))
    if order.status in h.CORRECTION_STATUSES:
        buttons.append(_button(ui, sk.BUTTON_CORRECTION, correction_id(order.id)))
    if tracking and order.status in h.TRACKING_STATUSES:
        buttons.append(_button(ui, sk.BUTTON_TRACKING, tracking_id(order.id)))
    buttons.append(_button(ui, sk.BUTTON_NOTE, note_id(order.id)))
    buttons.append(_button(ui, sk.BUTTON_DETAILS, record_details_id(order.id)))
    return buttons


def staff_rows(
    ui: Ui,
    order: m.Order,
    handling: h.Handling,
    *,
    staff_confirms_payment: bool,
    credit_refunds: bool,
    tracking: bool,
) -> Rows:
    """The status and refund controls for the order's current status (``views.staff_buttons``: each carries that
    status) on the first rows, the management buttons on the row below (at most 3 rows)."""
    status_buttons = views.staff_buttons(
        ui, order, staff_confirms_payment=staff_confirms_payment, credit_refunds=credit_refunds
    )
    return rows_of(*status_buttons) + rows_of(*management_buttons(ui, order, handling, tracking=tracking))


def staff_messages(
    ui: Ui,
    settings: Settings,
    view: m.OrderView,
    data: h.StaffData,
    *,
    staff_confirms_payment: bool,
    credit_refunds: bool,
    tracking: bool,
) -> list[MessageSpec]:
    """The live order record in the staff channel (edited in place on every change), then - only when the order lines
    do not fit next to everything else - the lines as separate messages. Nothing configured is cut (a record whose
    configured texts alone exceed Discord's limits fails the limits check and is reported, never sent cut)."""
    order = view.order
    fmt = views.order_money(ui, settings, order.currency)
    status = status_label(ui, order.status)
    total = fmt(order.total_minor)
    title = _text(ui, k.STAFF_TITLE, order_number=str(order.number), total=total, status=status)
    description = _text(
        ui,
        k.STAFF_DESCRIPTION,
        order_number=str(order.number),
        user_mention=views.user_mention(order.user_id),
        channel_mention=views.channel_mention(view.channel_id),
        total=total,
        status=status,
    )
    head = ui.embed(title=title, description=description, tone=STATUS_TONES.get(order.status, "info"), timestamp=True)
    record = _record_fields(ui, view, data, status)
    line_fields, total_fields = _summary_fields(ui, settings, view)
    timeline_name = _text(ui, sk.TIMELINE_FIELD)
    notes_name = _text(ui, sk.NOTES_FIELD)
    timeline = timeline_lines(ui, timeline_entries(ui, data))[-TIMELINE_MAX:]
    notes = note_lines(ui, data.notes)[-NOTES_MAX:]
    rows = staff_rows(
        ui,
        order,
        data.handling,
        staff_confirms_payment=staff_confirms_payment,
        credit_refunds=credit_refunds,
        tracking=tracking,
    )

    def embeds(with_lines: bool, n_timeline: int, n_notes: int) -> list[EmbedSpec]:
        fields = record + (line_fields if with_lines else []) + total_fields
        if n_timeline:
            fields += _blocks(timeline_name, timeline[-n_timeline:])
        if n_notes:
            fields += _blocks(notes_name, notes[-n_notes:])
        return views._spread(head, fields)

    complete = embeds(True, len(timeline), len(notes))
    if views._fits(complete):
        return [MessageSpec(embeds=tuple(complete), rows=rows)]
    # Too much for one message: the order lines move to separate messages; if the rest still does not fit, the
    # oldest notes are left out first, then the oldest timeline entries (the latest stage is always kept).
    n_timeline, n_notes = len(timeline), len(notes)
    while True:
        candidate = embeds(False, n_timeline, n_notes)
        if views._fits(candidate) or (n_notes == 0 and n_timeline <= 1):
            return [MessageSpec(embeds=tuple(candidate), rows=rows), *views._pack_fields(line_fields, head.color)]
        if n_notes > 0:
            n_notes -= 1
        else:
            n_timeline -= 1


def staff_message(
    ui: Ui,
    settings: Settings,
    view: m.OrderView,
    data: h.StaffData,
    *,
    staff_confirms_payment: bool,
    credit_refunds: bool,
    tracking: bool,
) -> MessageSpec:
    """The live order record (see ``staff_messages``)."""
    return staff_messages(
        ui,
        settings,
        view,
        data,
        staff_confirms_payment=staff_confirms_payment,
        credit_refunds=credit_refunds,
        tracking=tracking,
    )[0]


# -- customer notifications sent by staff actions ----------------------------------------------------------------


def _notify_values(ui: Ui, settings: Settings, view: m.OrderView) -> dict[str, str | int]:
    return {**views.order_values(ui, settings, view.order), "channel_mention": views.channel_mention(view.channel_id)}


def with_fields(spec: MessageSpec, fields: Sequence[EmbedField]) -> MessageSpec:
    """``spec`` with ``fields`` added under its first card (further cards when a card would hold more than 25)."""
    if not fields or not spec.embeds:
        return spec
    first, *rest = spec.embeds
    head = replace(first, fields=())
    return replace(spec, embeds=(*views._spread(head, [*first.fields, *fields]), *rest))


def rejection_fields(ui: Ui, reason: str | None) -> list[EmbedField]:
    """The staff's rejection reason for the customer's rejection notification (none when no reason was given)."""
    if not reason or not reason.strip():
        return []
    return text_fields(_text(ui, sk.REJECTION_REASON_FIELD), reason)


def tracking_fields(ui: Ui, tracking: str | None) -> list[EmbedField]:
    """Tracking details for the customer's completion notification (none when tracking is not configured/set)."""
    if not tracking or not tracking.strip():
        return []
    name = _optional(ui, sk.TRACKING_DETAILS_FIELD)
    return text_fields(name, tracking) if name else []


def _notification(
    ui: Ui, settings: Settings, view: m.OrderView, title_key: str, description_key: str, tone: str
) -> MessageSpec:
    embed = views._content_embed(
        ui,
        title_key,
        description_key,
        required=(description_key,),
        values=_notify_values(ui, settings, view),
        tone=tone,
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,))


def correction_notification(ui: Ui, settings: Settings, view: m.OrderView, message: str) -> MessageSpec:
    """Private notification asking the customer to correct information, with the staff member's message."""
    spec = _notification(ui, settings, view, sk.CORRECTION_TITLE, sk.CORRECTION_DESCRIPTION, "warning")
    return with_fields(spec, text_fields(_text(ui, sk.CORRECTION_MESSAGE_FIELD), message))


def tracking_notification(ui: Ui, settings: Settings, view: m.OrderView, tracking: str) -> MessageSpec:
    """Private notification with new or changed tracking details of a completed order."""
    spec = _notification(ui, settings, view, sk.TRACKING_TITLE, sk.TRACKING_DESCRIPTION, "info")
    return with_fields(spec, text_fields(_text(ui, sk.TRACKING_DETAILS_FIELD), tracking))


# -- staff-only forms (operator text) -------------------------------------------------------------------------------


def note_modal(op: OperatorText, order: m.Order) -> ModalSpec:
    return ModalSpec(
        custom_id=note_form_id(order.id),
        title=op("checkout.note.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=NOTE_INPUT,
                label=op("checkout.note.label"),
                style="paragraph",
                description=op("checkout.note.hint"),
                required=True,
                min_length=1,
                max_length=h.NOTE_MAX,
            ),
        ),
    )


def correction_modal(op: OperatorText, order: m.Order) -> ModalSpec:
    return ModalSpec(
        custom_id=correction_form_id(order.id),
        title=op("checkout.correction.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=MESSAGE_INPUT,
                label=op("checkout.correction.label"),
                style="paragraph",
                description=op("checkout.correction.hint"),
                required=True,
                min_length=1,
                max_length=h.TEXT_MAX,
            ),
        ),
    )


def tracking_modal(op: OperatorText, order: m.Order, current: str | None) -> ModalSpec:
    return ModalSpec(
        custom_id=tracking_form_id(order.id),
        title=op("checkout.tracking.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=TRACKING_INPUT,
                label=op("checkout.tracking.label"),
                style="paragraph",
                description=op("checkout.tracking.hint"),
                default=current or None,
                required=True,
                min_length=1,
                max_length=h.TEXT_MAX,
            ),
        ),
    )


def reject_modal(op: OperatorText, order: m.Order, status: str) -> ModalSpec:
    return ModalSpec(
        custom_id=reject_form_id(order.id, status),
        title=op("checkout.reject.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=REASON_INPUT,
                label=op("checkout.reject.label"),
                style="paragraph",
                description=op("checkout.reject.hint"),
                required=True,
                min_length=1,
                max_length=h.TEXT_MAX,
            ),
        ),
    )


def notification_fields(ui: Ui, status: str, data: h.NotificationData) -> list[EmbedField]:
    """Fields added to a status notification: the rejection reason, or the tracking details of a completed order."""
    if status == m.REJECTED:
        return rejection_fields(ui, data.reason)
    if status == m.FULFILLED:
        return tracking_fields(ui, data.tracking)
    return []
