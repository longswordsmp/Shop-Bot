"""Pure builders for the staff side of orders: the live order record in the staff channel (#orders), its action
buttons, the staff-only forms (operator text), and the customer notifications staff actions send.

The live record always shows the order's number, total and status (its title), the customer, its ticket and who
claimed it, the order summary (lines, delivery and extras, destination, amounts), the payment, a TIMELINE of its
stages (status changes from ``order_events`` plus claims, correction requests and tracking details from
``order_activity``) and the latest internal notes. Field names and stage names are content keys; values are data
(numbers, mentions, Discord timestamps, staff-entered text escaped with ``escape_markdown``). Discord's limits are
budgeted: configured text is never cut; when the record does not fit, the oldest notes and timeline entries are left
out first (the details button shows everything), then the order lines move to separate messages.

Like ``views``, nothing here calls Discord or the database. Missing required content raises ``MissingContent``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, gift_card_store, views
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys as sk
from shopbot.features.checkout.payments import gift_card
from shopbot.features.checkout.payments.vault import CardSecret
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELD_VALUE
from shopbot.ui.spec import (
    ButtonSpec,
    ButtonStyle,
    Component,
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
CARRIER_INPUT = "carrier"
TRACKING_NUMBER_INPUT = "number"
TRACKING_URL_INPUT = "url"

# The name of a field that continues the list above it (an invisible character: Discord needs a name).
CONTINUED = "\u200b"

# At most this many timeline entries / internal notes are listed on the live record (latest first kept).
TIMELINE_MAX = 20
NOTES_MAX = 5

STATUS_TONES = {
    m.PENDING: "info",
    m.PAYMENT_SUBMITTED: "warning",
    m.PAID: "success",
    m.PROCESSING: "info",
    m.SHIPPED: "primary",
    m.FULFILLED: "success",
    m.CANCELLED: "neutral",
    m.REJECTED: "danger",
    m.EXPIRED: "neutral",
    m.REFUNDED: "neutral",
}

# The live record's banner follows the order's status (the same pictures as the customer's order view): a status
# change swaps it, a claim, note or tracking change keeps it without uploading it again.
RECORD_BANNERS: Mapping[str, BannerId] = {
    m.PENDING: "order-status",  # waiting for the payment, not under review yet
    m.PAYMENT_SUBMITTED: "pending-review",
    m.PAID: "successful-confirmation",
    m.PROCESSING: "submission-and-processing",
    m.SHIPPED: "completed-or-dispatched",
    m.FULFILLED: "successful-confirmation",  # delivered (the dispatched van is for shipped)
    m.CANCELLED: "cancellation",
    m.EXPIRED: "cancellation",
    m.REJECTED: "rejected-or-unsuccessful-submission",
    m.REFUNDED: "resolution",
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


def ship_form_id(order_id: int, status: str) -> str:
    """The Mark shipped form; it carries the status the button was rendered for (a stale form changes nothing)."""
    return cid(STAFF_ROUTE, "shipform", order_id, status)


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


def stage_name(ui: Ui, kind: str) -> str | None:
    """The stage name of a staff activity (Claimed, Claim released, Correction requested, Tracking details updated);
    None for an activity that is not a stage (a note, a rejection reason) or whose optional name is not set."""
    key = _STAGE_KEYS.get(kind)
    if key is None:
        return None
    return _text(ui, key) if key != sk.STAGE_TRACKING else _optional(ui, key)


def timeline_entries(ui: Ui, data: h.StaffData) -> list[TimelineEntry]:
    """Every stage of the order, oldest first: status changes and the staff activity that has a stage name."""
    entries: list[TimelineEntry] = []
    for event in data.events:
        if event.from_status is not None and event.from_status == event.to_status:
            continue  # a note without a status change (recorded by an older version)
        entries.append(TimelineEntry(event.created_at, status_label(ui, event.to_status), event.actor_id, event.id))
    for item in data.activity:
        stage = stage_name(ui, item.kind)
        if stage:
            entries.append(TimelineEntry(item.created_at, stage, item.actor_id, 1_000_000_000 + item.id))
    return sorted(entries, key=lambda e: (e.created_at, e.order))


def timeline_lines(ui: Ui, entries: Sequence[TimelineEntry]) -> list[str]:
    """One line per stage; an automatic stage has no actor (and no trailing space where the actor would be)."""
    return [
        _text(
            ui,
            sk.TIMELINE_LINE,
            time=_when(e.created_at),
            stage=e.stage,
            actor=views.user_mention(e.actor_id) if e.actor_id else "",
        ).rstrip()
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
    """``lines`` in fields within the field limit: the first one under the heading ``name``, the ones that continue it
    under a blank name, so a long list shows its heading (and its icon) once."""
    blocks = chunk_lines(list(lines), EMBED_FIELD_VALUE)
    return [EmbedField(name=name if index == 0 else CONTINUED, value=block) for index, block in enumerate(blocks)]


def _summary_fields(ui: Ui, settings: Settings, view: m.OrderView) -> tuple[list[EmbedField], list[EmbedField]]:
    """(order lines, then delivery + destination + amounts) as stored in the order, in the order's currency."""
    order = view.order
    fmt = views.order_money(ui, settings, order.currency)
    lines = [
        views.line_text(ui, i.name, i.quantity, i.unit_price_minor, i.line_total_minor, fmt=fmt) for i in view.items
    ]
    cart_name = _text(ui, k.CART_FIELD)
    line_fields = _blocks(cart_name, lines) if lines else [EmbedField(name=cart_name, value=_text(ui, k.CART_EMPTY))]
    after: list[EmbedField] = []
    option = order.fulfillment_option
    option_label = _optional(ui, k.option_key(option, "label")) if option else None
    extras = [
        label
        for fee in view.fees
        if flow.extra_id_of_fee(fee.fee_id) is not None and (label := _optional(ui, k.fee_label_key(fee.fee_id)))
    ]
    delivery = [label for label in (option_label, *extras) if label]
    if delivery:
        delivery_name = _text(ui, sk.DELIVERY_FIELD)
        after += [EmbedField(delivery_name, value) for value in _joined(delivery)]
    destination = _destination(ui, settings, order)
    if destination is not None:
        after.append(EmbedField(_text(ui, sk.SHIP_TO_FIELD), destination))
    after += _blocks(_text(ui, sk.AMOUNT_FIELD), amount_lines(ui, view, fmt))
    return line_fields, after


def _destination(ui: Ui, settings: Settings, order: m.Order) -> str | None:
    """Where the order goes: only the country (picked from a menu) unless ``operations.answers_in_orders_channel``
    lets the record show what the customer typed (then ``checkout.address.ship_to``: name and city, never the
    street). The details button shows the full address privately either way."""
    country = order.details.get(flow.ADDRESS_COUNTRY)
    if not country:
        return None
    if settings.operations.answers_in_orders_channel:
        return views.ship_to(ui, settings, order.details)
    return views.escape(settings.shipping.country_name(country))


def _joined(parts: Sequence[str], limit: int = EMBED_FIELD_VALUE) -> list[str]:
    """``parts`` joined with " · ", continued in another value whenever one would pass ``limit`` characters."""
    values: list[str] = []
    for part in parts:
        if values and len(values[-1]) + 3 + len(part) <= limit:
            values[-1] = f"{values[-1]} · {part}"
        else:
            values.append(part)
    return values


def amount_lines(ui: Ui, view: m.OrderView, fmt: views.Money) -> list[str]:
    """Every amount of the order, one per line like the customer's summary ("Subtotal · $12.97", the discount code's
    "Discount (SAVE10) · -$1.30", "Standard shipping · $5.00", the extras, the store credit applied, "**Total ·
    $17.97**"), then what was refunded and what applied store credit was given back, each on its own line (the live
    record and the full record)."""
    order = view.order

    def line(label: str, minor: int) -> str:
        return _text(ui, k.SUMMARY_LINE, label=label, amount=fmt(minor))

    lines = [line(_text(ui, k.CART_SUBTOTAL_FIELD), order.subtotal_minor)]
    if order.discount_minor > 0 and order.discount_code:
        lines.append(views.discount_line(ui, order.discount_kind, order.discount_code, fmt(order.discount_minor)))
    option = order.fulfillment_option
    if order.fulfillment_fee_minor > 0:
        name = (_optional(ui, k.option_key(option, "label")) if option else None) or _optional(
            ui, k.FULFILLMENT_FEE_FIELD
        )
        if name:
            lines.append(line(name, order.fulfillment_fee_minor))
    # A fee whose label is no longer configured is left out, as it is everywhere else (the total includes it).
    for fee in view.fees:
        label = _optional(ui, k.fee_label_key(fee.fee_id))
        if label:
            lines.append(line(label, fee.amount_minor))
    credit_name = _optional(ui, k.REVIEW_CREDITS_FIELD) if order.credits_applied_minor > 0 else None
    if credit_name:
        credit = _optional(ui, k.SUMMARY_CREDIT_LINE, label=credit_name, amount=fmt(order.credits_applied_minor))
        lines.append(credit or line(credit_name, order.credits_applied_minor))
    lines.append(_text(ui, k.SUMMARY_TOTAL_LINE, label=_text(ui, k.REVIEW_TOTAL_FIELD), amount=fmt(order.total_minor)))
    if order.refunded_minor > 0:
        lines.append(line(_text(ui, k.STAFF_REFUNDED_FIELD), order.refunded_minor))
    returned_name = _optional(ui, k.STAFF_CREDIT_RETURNED_FIELD)
    if order.credit_returned_minor > 0 and returned_name:
        lines.append(line(returned_name, order.credit_returned_minor))
    return lines


def _record_fields(ui: Ui, view: m.OrderView, data: h.StaffData) -> list[EmbedField]:
    """Customer and order ticket side by side (two short mentions), the claim full width under them, so no fact is
    squeezed into a third of the card (the number and status are in the title)."""
    order = view.order
    fields = [EmbedField(_text(ui, sk.CUSTOMER_FIELD), views.user_mention(order.user_id), inline=True)]
    channel_name = _optional(ui, sk.CHANNEL_FIELD)
    if channel_name and view.channel_id:
        fields.append(EmbedField(channel_name, views.channel_mention(view.channel_id), inline=True))
    handling = data.handling
    if handling.claimed_by is not None and handling.claimed_at is not None:
        claimed = f"{views.user_mention(handling.claimed_by)} {_relative(handling.claimed_at)}"
        fields.append(EmbedField(_text(ui, sk.CLAIMED_FIELD), claimed))
    elif order.status in h.CLAIMABLE_STATUSES:
        fields.append(EmbedField(_text(ui, sk.CLAIMED_FIELD), _text(ui, sk.NOT_CLAIMED)))
    return fields


def _handling_fields(ui: Ui, settings: Settings, order: m.Order, data: h.StaffData) -> list[EmbedField]:
    """The submitted gift cards and the tracking details."""
    fields = gift_card.record_fields(ui, settings, order, data.gift_cards)
    tracking_name = _optional(ui, sk.TRACKING_FIELD)
    if data.handling.tracking and tracking_name:
        fields += text_fields(tracking_name, data.handling.tracking)
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
    configured texts alone exceed Discord's limits fails the limits check and is reported, never sent cut).
    Only the record shows a banner (``RECORD_BANNERS``, by status); the separate line messages show none."""
    order = view.order
    banner = ui.banner(RECORD_BANNERS.get(order.status, "order-status"))
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
    record = _record_fields(ui, view, data)
    line_fields, total_fields = _summary_fields(ui, settings, view)
    total_fields += _handling_fields(ui, settings, order, data)
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
        return [MessageSpec(embeds=tuple(complete), rows=rows, banner=banner)]
    # Too much for one message: the order lines move to separate messages; if the rest still does not fit, the
    # oldest notes are left out first, then the oldest timeline entries (the latest stage is always kept).
    n_timeline, n_notes = len(timeline), len(notes)
    while True:
        candidate = embeds(False, n_timeline, n_notes)
        if views._fits(candidate) or (n_notes == 0 and n_timeline <= 1):
            record_spec = MessageSpec(embeds=tuple(candidate), rows=rows, banner=banner)
            return [record_spec, *views._pack_fields(line_fields, head.color)]
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
    """Private notification asking the customer to correct information, with the staff member's message (banner:
    the customer is asked to fix their order details)."""
    spec = _notification(ui, settings, view, sk.CORRECTION_TITLE, sk.CORRECTION_DESCRIPTION, "warning")
    spec = replace(spec, banner=ui.banner("details-and-options"))
    return with_fields(spec, text_fields(_text(ui, sk.CORRECTION_MESSAGE_FIELD), message))


def tracking_notification(ui: Ui, settings: Settings, view: m.OrderView, tracking: str) -> MessageSpec:
    """Private notification with new or changed tracking details of a completed order (banner: order status)."""
    spec = _notification(ui, settings, view, sk.TRACKING_TITLE, sk.TRACKING_DESCRIPTION, "info")
    spec = replace(spec, banner=ui.banner("order-status"))
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


def ship_modal(op: OperatorText, order: m.Order, status: str) -> ModalSpec:
    """Mark shipped: the carrier, the tracking number and an optional tracking link the customer receives."""
    return ModalSpec(
        custom_id=ship_form_id(order.id, status),
        title=op("checkout.ship.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=CARRIER_INPUT,
                label=op("checkout.ship.carrier"),
                placeholder=op("checkout.ship.carrier_hint"),
                required=True,
                min_length=1,
                max_length=h.CARRIER_MAX,
            ),
            TextInputSpec(
                custom_id=TRACKING_NUMBER_INPUT,
                label=op("checkout.ship.number"),
                required=True,
                min_length=1,
                max_length=h.TRACKING_NUMBER_MAX,
            ),
            TextInputSpec(
                custom_id=TRACKING_URL_INPUT,
                label=op("checkout.ship.url"),
                description=op("checkout.ship.url_hint"),
                required=False,
                max_length=h.TRACKING_URL_MAX,
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


def gift_card_problem_modal(op: OperatorText, order: m.Order, card_id: int = 0) -> ModalSpec:
    """Card problem: the reason the customer is shown (for every waiting card, or for card ``card_id``)."""
    return ModalSpec(
        custom_id=views.gift_card_problem_form_id(order.id, card_id),
        title=op("checkout.gift.problem.title", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=REASON_INPUT,
                label=op("checkout.gift.problem.reason"),
                style="paragraph",
                placeholder=op("checkout.gift.problem.reason_hint"),
                required=True,
                min_length=1,
                max_length=gift_card_store.REASON_MAX,
            ),
        ),
    )


@dataclass(frozen=True)
class RevealedCard:
    """A card in the reveal reply: its number among the order's cards, and its code (None: unreadable or deleted)."""

    index: int
    card: gift_card_store.GiftCard
    secret: CardSecret | None


def gift_card_reveal(
    op: OperatorText, ui: Ui, settings: Settings, order: m.Order, cards: Sequence[RevealedCard], *, keep_days: int
) -> MessageSpec:
    """The private reply with the waiting cards' codes (spoiler-hidden) under the three steps to take. With more than
    one waiting card, each gets its own row of redeemed / problem buttons (only the problem button for a card whose
    code was deleted unread); the record's Payment confirmed and Card problem act on all of them. Its operator text
    shows the bot's icons."""
    fmt = views.order_money(ui, settings, order.currency)
    fields: list[EmbedField] = []
    for item in cards:
        name = op(
            "checkout.gift.reveal.card",
            index=item.index,
            amount=fmt(item.card.amount_minor),
            last4=views.escape(item.card.last4),
        )
        if item.secret is not None:
            lines = [op("checkout.gift.reveal.code", code=_spoiler_safe(item.secret.code))]
            pin = item.secret.pin
            lines.append(
                op("checkout.gift.reveal.pin", pin=_spoiler_safe(pin)) if pin else op("checkout.gift.reveal.no_pin")
            )
            value = "\n".join(lines)
        elif not item.card.stored:
            value = op("checkout.gift.reveal.erased", days=keep_days)
        else:
            value = op("checkout.gift.reveal.unreadable")
        fields.append(EmbedField(name, value))
    embed = ui.operator_embed(
        op("checkout.gift.reveal.title", number=order.number),
        ui.with_icons(op("checkout.gift.reveal.intro")),
        tone="warning",
        fields=fields,
    )
    rows: list[tuple[Component, ...]] = []
    if len(cards) > 1:
        for item in cards:
            # A card whose code was deleted unread cannot be redeemed any more: only its problem button.
            erased = item.secret is None and not item.card.stored
            redeemed = ButtonSpec(
                label=ui.with_icons(op("checkout.gift.reveal.redeemed_button", index=item.index)),
                custom_id=views.gift_card_redeemed_id(order.id, item.card.id),
                style="success",
            )
            problem = ButtonSpec(
                label=ui.with_icons(op("checkout.gift.reveal.problem_button", index=item.index)),
                custom_id=views.gift_card_problem_id(order.id, item.card.id),
                style="danger",
            )
            rows.append((problem,) if erased else (redeemed, problem))  # one row per card
    return MessageSpec(embeds=(embed,), rows=tuple(rows), banner=ui.banner("staff-dashboard"))


def _spoiler_safe(text: str) -> str:
    """A customer-typed code inside ``||`...`||``: backticks and pipes cannot end the markup early."""
    return text.replace("`", "'").replace("|", "/")


def notification_fields(ui: Ui, status: str, data: h.NotificationData) -> list[EmbedField]:
    """Fields added to a status notification: the rejection reason, the shipment of a shipped order, or the tracking
    details of a delivered one."""
    if status == m.REJECTED:
        return rejection_fields(ui, data.reason)
    if status == m.SHIPPED:
        return shipment_fields(ui, data.shipment) if data.shipment is not None else tracking_fields(ui, data.tracking)
    if status == m.FULFILLED:
        return tracking_fields(ui, data.tracking)
    return []


def shipment_fields(ui: Ui, shipment: h.Shipment) -> list[EmbedField]:
    """The carrier and tracking number (staff-entered data, escaped), side by side."""
    return [
        EmbedField(_text(ui, sk.SHIPMENT_CARRIER_FIELD), views.escape(shipment.carrier), inline=True),
        EmbedField(_text(ui, sk.SHIPMENT_NUMBER_FIELD), views.escape(shipment.tracking_number), inline=True),
    ]


def notification_rows(ui: Ui, status: str, data: h.NotificationData) -> tuple[tuple[Component, ...], ...]:
    """Buttons under a status notification: Track package (the carrier's page) once a shipped order has a link."""
    shipment = data.shipment
    if status != m.SHIPPED or shipment is None or not shipment.tracking_url:
        return ()
    return rows_of(ButtonSpec(label=_text(ui, sk.SHIPMENT_TRACK_BUTTON), style="link", url=shipment.tracking_url))
