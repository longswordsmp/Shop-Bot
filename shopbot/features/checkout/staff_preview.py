"""Preview states of the staff side of orders: the live order record in every status and handling state (claimed,
tracking details, a long history), the staff-only forms, the full record (details button, ``/order view``), and the
customer notifications staff actions send. Built by the runtime view functions from synthetic sample data
(``[sample note N]``, ``[sample reason]``...). Called by ``preview.preview``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from shopbot.config.content import MissingContent
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff, staff_keys, staff_views, views
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_STAFF_ID = 100_000_000_000_000_002
SAMPLE_TIME = 1_700_000_000
SAMPLE_REASON = "[sample reason]"
SAMPLE_TRACKING = "[sample tracking details]"
SAMPLE_MESSAGE = "[sample message to the customer]"
SAMPLE_SHIPMENT = h.Shipment("[sample carrier]", "[sample tracking number]", "https://example.com/track")

Add = Callable[[str, str, Callable[[], MessageSpec | ModalSpec | None]], None]


class OrderFactory(Protocol):
    def __call__(
        self, status: str, lines: tuple[m.CartLine, ...], *, refunded: int | None = None, credits: int = 0
    ) -> m.OrderView: ...


# The stages an order went through to reach each status (submitted first).
_PATHS: dict[str, tuple[str, ...]] = {
    m.PENDING: (m.PENDING,),
    m.PAYMENT_SUBMITTED: (m.PENDING, m.PAYMENT_SUBMITTED),
    m.PAID: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID),
    m.PROCESSING: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING),
    m.SHIPPED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING, m.SHIPPED),
    m.FULFILLED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING, m.SHIPPED, m.FULFILLED),
    m.CANCELLED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.CANCELLED),
    m.REJECTED: (m.PENDING, m.PAYMENT_SUBMITTED, m.REJECTED),
    m.EXPIRED: (m.PENDING, m.EXPIRED),
    m.REFUNDED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.SHIPPED, m.FULFILLED, m.REFUNDED),
}


def sample_data(
    order: m.Order,
    *,
    claimed: bool = False,
    notes: int = 0,
    tracking: bool = False,
    correction: bool = False,
    claims: int = 0,
) -> h.StaffData:
    """Synthetic handling data: the status path of ``order``, optional claim, notes, correction request, tracking
    details and ``claims`` extra claim/release rounds (a long history)."""
    events: list[m.OrderEvent] = []
    previous: str | None = None
    for index, status in enumerate(_PATHS.get(order.status, (order.status,)), start=1):
        actor = order.user_id if previous is None else (None if status == m.EXPIRED else SAMPLE_STAFF_ID)
        events.append(
            m.OrderEvent(
                id=index,
                order_id=order.id,
                from_status=previous,
                to_status=status,
                actor_id=actor,
                note=SAMPLE_REASON if status == m.REJECTED else None,
                created_at=SAMPLE_TIME + index * 600,
            )
        )
        previous = status
    activity: list[h.Activity] = []
    moment = SAMPLE_TIME + 630  # after the submission

    def record(kind: str, text: str | None = None) -> None:
        nonlocal moment
        moment += 60
        activity.append(h.Activity(len(activity) + 1, order.id, kind, SAMPLE_STAFF_ID, text, moment))

    for _ in range(claims):
        record(h.CLAIM)
        record(h.UNCLAIM)
    claimed_at = None
    if claimed:
        record(h.CLAIM)
        claimed_at = moment
    if correction:
        record(h.CORRECTION, SAMPLE_MESSAGE)
    for index in range(1, notes + 1):
        record(h.NOTE, f"[sample note {index}]")
    if tracking:
        record(h.TRACKING, SAMPLE_TRACKING)
    handling = h.Handling(
        order_id=order.id,
        claimed_by=SAMPLE_STAFF_ID if claimed else None,
        claimed_at=claimed_at,
        tracking=SAMPLE_TRACKING if tracking else None,
        tracking_by=SAMPLE_STAFF_ID if tracking else None,
        tracking_at=moment if tracking else None,
        version=1,
    )
    return h.StaffData(handling=handling, events=tuple(events), activity=tuple(activity))


def add_states(
    app: App,
    add: Add,
    make: OrderFactory,
    *,
    lines: tuple[m.CartLine, ...],
    largest: tuple[m.CartLine, ...],
    staff_confirms: bool,
    credit_refunds: bool,
) -> None:
    ui, settings, op = app.ui, app.settings, app.op
    tracking = staff_keys.tracking_enabled(app.content)

    def record(view: m.OrderView, data: h.StaffData) -> Callable[[], MessageSpec]:
        return partial(
            staff_views.staff_message,
            ui,
            settings,
            view,
            data,
            staff_confirms_payment=staff_confirms,
            credit_refunds=credit_refunds,
            tracking=tracking,
        )

    # -- the live order record ---------------------------------------------------------------------------------
    for status in m.ORDER_STATUSES:
        view = make(status, lines)
        add(
            f"staff_{status}",
            f"Live order record in checkout.staff_channel for an order that is '{status}' (unclaimed).",
            record(view, sample_data(view.order)),
        )
    for status in (m.PENDING, m.PAID, m.PROCESSING):
        view = make(status, lines)
        add(
            f"staff_{status}_claimed",
            f"Live order record of a '{status}' order claimed by a staff member, with internal notes.",
            record(view, sample_data(view.order, claimed=True, notes=2, correction=status == m.PENDING)),
        )
    paid = make(m.PAID, lines)
    add(
        "staff_paid_tracking",
        "Live order record of a paid order with tracking details (shown when checkout.staff.button.tracking is set).",
        record(paid, sample_data(paid.order, claimed=True, tracking=True)),
    )
    fulfilled = make(m.FULFILLED, lines)
    add(
        "staff_fulfilled_long_history",
        "Live order record with a long history: the oldest notes and timeline entries are left out when they do not "
        "fit (the details button shows everything).",
        record(fulfilled, sample_data(fulfilled.order, claimed=True, notes=12, tracking=True, claims=10)),
    )
    partly_refunded = make(m.PAID, lines, refunded=1)
    add(
        "staff_paid_partially_refunded",
        "Live order record after a partial refund.",
        record(partly_refunded, sample_data(partly_refunded.order)),
    )
    with_credit = make(m.CANCELLED, lines, credits=1_500)
    add(
        "staff_cancelled_credit_applied",
        "Live order record of a paid order that was cancelled and had store credit applied (refund buttons and the "
        "button that gives the applied credit back).",
        record(with_credit, sample_data(with_credit.order)),
    )
    biggest = make(m.PENDING, largest)
    try:
        largest_staff = staff_views.staff_messages(
            ui,
            settings,
            biggest,
            sample_data(biggest.order, claimed=True, notes=5, correction=True),
            staff_confirms_payment=staff_confirms,
            credit_refunds=credit_refunds,
            tracking=tracking,
        )
    except MissingContent:  # the state is skipped like every other incomplete state
        largest_staff = []
    for index, spec in enumerate(largest_staff):
        add(
            "staff_pending_largest" if index == 0 else f"staff_pending_largest_part_{index}",
            "Live order record of the largest possible order (the lines follow as separate messages when they do "
            "not fit next to the configured text).",
            partial(_same, spec),
        )

    # -- staff-only forms and the full record (operator text) ---------------------------------------------------
    pending = make(m.PENDING, lines)
    add("staff_note_form", "Staff only: internal note form.", lambda: staff_views.note_modal(op, pending.order))
    add(
        "staff_correction_form",
        "Staff only: request-a-correction form (the message is sent to the customer).",
        lambda: staff_views.correction_modal(op, pending.order),
    )
    add(
        "staff_reject_form",
        "Staff only: rejection form (the reason is sent to the customer).",
        lambda: staff_views.reject_modal(op, pending.order, m.PENDING),
    )
    add(
        "staff_ship_form",
        "Staff only: Mark shipped form (carrier, tracking number and an optional tracking link for the customer).",
        lambda: staff_views.ship_modal(op, paid.order, m.PAID),
    )
    add(
        "staff_tracking_form",
        "Staff only: tracking details form (prefilled with the current details).",
        lambda: staff_views.tracking_modal(op, paid.order, SAMPLE_TRACKING),
    )
    detailed = sample_data(fulfilled.order, claimed=True, notes=3, tracking=True)
    try:
        record_specs = staff.lookup_specs(app, fulfilled, None, [], detailed)
    except MissingContent:
        record_specs = []
    for index, spec in enumerate(record_specs, start=1):
        add(
            f"staff_record_details_{index}",
            "Staff only: the full order record (details button, /order view, /order lookup, /orders menu).",
            partial(_same, spec),
        )

    # -- customer notifications sent by staff actions -----------------------------------------------------------
    rejected = make(m.REJECTED, lines)
    add(
        "notify_rejected_with_reason",
        "Customer notification: the order was rejected, with the reason staff entered.",
        lambda: staff_views.with_fields(
            _status_notification(app, rejected, m.REJECTED),
            staff_views.notification_fields(ui, m.REJECTED, h.NotificationData(reason=SAMPLE_REASON)),
        ),
    )
    add(
        "notify_fulfilled_with_tracking",
        "Customer notification: the order was completed, with the tracking details staff entered.",
        lambda: staff_views.with_fields(
            _status_notification(app, fulfilled, m.FULFILLED),
            staff_views.notification_fields(ui, m.FULFILLED, h.NotificationData(tracking=SAMPLE_TRACKING)),
        ),
    )
    shipped = make(m.SHIPPED, lines)
    shipped_data = h.NotificationData(tracking=SAMPLE_TRACKING, shipment=SAMPLE_SHIPMENT)
    add(
        "notify_shipped_with_tracking",
        "Customer notification: the order shipped, with the carrier, the tracking number and the Track package button "
        "(shown when staff entered a tracking link).",
        lambda: replace(
            staff_views.with_fields(
                _status_notification(app, shipped, m.SHIPPED),
                staff_views.notification_fields(ui, m.SHIPPED, shipped_data),
            ),
            rows=staff_views.notification_rows(ui, m.SHIPPED, shipped_data),
        ),
    )
    add(
        "notify_correction",
        "Customer notification: staff ask the customer to correct information (the staff message follows).",
        lambda: staff_views.correction_notification(ui, settings, pending, SAMPLE_MESSAGE),
    )
    add(
        "notify_tracking",
        "Customer notification: tracking details added after the order was completed.",
        lambda: staff_views.tracking_notification(ui, settings, fulfilled, SAMPLE_TRACKING),
    )


def _same(spec: MessageSpec) -> MessageSpec:
    return spec


def _status_notification(app: App, view: m.OrderView, status: str) -> MessageSpec:
    return views.status_notification(app.ui, app.settings, view, status)
