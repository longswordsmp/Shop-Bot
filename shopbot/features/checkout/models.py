"""Checkout records (sessions, carts, orders) and the order state machine.

Session statuses: ``active`` (the customer is building the order) -> ``submitted`` (an order exists) |
``cancelled`` (the customer cancelled) | ``expired`` (idle too long, or the order channel disappeared).

Order statuses and the only allowed transitions (``TRANSITIONS[to]`` lists the states ``to`` may be reached
from)::

    pending_payment -> paid | rejected | cancelled | expired
    paid            -> processing | fulfilled | cancelled | refunded
    processing      -> fulfilled | cancelled | refunded
    fulfilled       -> refunded
    cancelled       -> refunded        (only an order that was paid before it was cancelled)

``rejected``, ``expired`` and ``refunded`` are final. Every transition is one conditional UPDATE
(``WHERE status IN allowed_from``) plus an ``order_events`` row, so a second concurrent request finds the
state changed and applies nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from shopbot.features.catalog.repo import Product
from shopbot.features.checkout.totals import Totals

# -- session statuses and steps ---------------------------------------------------------------------------

ACTIVE: Final = "active"
SUBMITTED: Final = "submitted"
SESSION_CANCELLED: Final = "cancelled"
SESSION_EXPIRED: Final = "expired"

BROWSE: Final = "browse"
CART: Final = "cart"
FULFILLMENT: Final = "fulfillment"
DETAILS: Final = "details"
REVIEW: Final = "review"
DONE: Final = "done"
STEPS: Final = (BROWSE, CART, FULFILLMENT, DETAILS, REVIEW)

# -- order statuses -----------------------------------------------------------------------------------------

PENDING: Final = "pending_payment"
PAID: Final = "paid"
PROCESSING: Final = "processing"
FULFILLED: Final = "fulfilled"
CANCELLED: Final = "cancelled"
REJECTED: Final = "rejected"
EXPIRED: Final = "expired"
REFUNDED: Final = "refunded"
ORDER_STATUSES: Final = (PENDING, PAID, PROCESSING, FULFILLED, CANCELLED, REJECTED, EXPIRED, REFUNDED)
OPEN_STATUSES: Final = (PENDING, PAID, PROCESSING)
# Statuses after which the order channel is closed.
TERMINAL_STATUSES: Final = (FULFILLED, CANCELLED, REJECTED, EXPIRED, REFUNDED)
# Statuses shown with their own customer view / staff view / notification (pending_payment uses the
# submitted view with the payment instructions).
STATUS_VIEWS: Final = (PAID, PROCESSING, FULFILLED, CANCELLED, REJECTED, EXPIRED, REFUNDED)

TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    PAID: (PENDING,),
    PROCESSING: (PAID,),
    FULFILLED: (PAID, PROCESSING),
    REJECTED: (PENDING,),
    CANCELLED: (PENDING, PAID, PROCESSING),
    EXPIRED: (PENDING,),
    REFUNDED: (PAID, PROCESSING, FULFILLED, CANCELLED),
}
# Statuses that staff may set directly (buttons and /order status); expiry and refunds have their own paths.
STAFF_TARGETS: Final = (PAID, PROCESSING, FULFILLED, REJECTED, CANCELLED)

RESERVATION_HELD: Final = "held"
RESERVATION_COMMITTED: Final = "committed"
RESERVATION_RELEASED: Final = "released"

ORDER_COUNTER: Final = "order"


def allowed_targets(status: str) -> tuple[str, ...]:
    """Statuses reachable from ``status`` (in display order)."""
    return tuple(to for to in ORDER_STATUSES if status in TRANSITIONS.get(to, ()))


# -- records ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    id: int
    guild_id: int
    user_id: int
    ticket_id: int
    status: str
    step: str
    page: int
    fulfillment_option: str | None
    details: dict[str, str] = field(compare=False)
    apply_credits: bool
    message_id: int | None
    order_id: int | None
    version: int
    created_at: int
    updated_at: int
    expires_at: int | None

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


@dataclass(frozen=True)
class CartLine:
    """A cart line with the product as it is now (current name, price, stock)."""

    product: Product
    quantity: int

    @property
    def line_total_minor(self) -> int:
        return self.product.price_minor * self.quantity


@dataclass(frozen=True)
class Order:
    id: int
    guild_id: int
    number: int
    user_id: int
    session_id: int | None
    ticket_id: int | None
    status: str
    currency: str
    subtotal_minor: int
    fulfillment_fee_minor: int
    fees_minor: int
    credits_applied_minor: int
    total_minor: int
    refunded_minor: int
    fulfillment_option: str | None
    details: dict[str, str] = field(compare=False)
    payment_provider: str
    customer_message_id: int | None
    staff_channel_id: int | None
    staff_message_id: int | None
    version: int
    created_at: int
    updated_at: int
    paid_at: int | None
    fulfilled_at: int | None
    closed_at: int | None

    @property
    def refundable_minor(self) -> int:
        """Money that can still be refunded (never more than the total that was charged)."""
        return max(0, self.total_minor - self.refunded_minor)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def can_refund(self) -> bool:
        """Paid at some point, not fully refunded, and in a state that allows refunds."""
        if self.refundable_minor <= 0 or self.paid_at is None:
            return False
        return self.status in TRANSITIONS[REFUNDED]


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    name: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int


@dataclass(frozen=True)
class OrderFee:
    fee_id: str
    amount_minor: int


@dataclass(frozen=True)
class Payment:
    id: int
    order_id: int
    provider: str
    status: str
    amount_minor: int
    currency: str
    external_ref: str | None


@dataclass(frozen=True)
class Adjustment:
    id: int
    order_id: int
    kind: str
    method: str
    amount_minor: int
    reason: str | None
    actor_id: int | None
    external_ref: str | None
    created_at: int


@dataclass(frozen=True)
class OrderEvent:
    id: int
    order_id: int
    from_status: str | None
    to_status: str
    actor_id: int | None
    note: str | None
    created_at: int


@dataclass(frozen=True)
class OrderView:
    """Everything the customer and staff views of an order need (one consistent database snapshot)."""

    order: Order
    items: tuple[OrderItem, ...]
    fees: tuple[OrderFee, ...]
    channel_id: int | None = None


@dataclass(frozen=True)
class ProductPage:
    """One page of the product selection menu."""

    products: tuple[Product, ...]
    page: int
    pages: int
    total: int


@dataclass(frozen=True)
class WorkflowData:
    """Everything the workflow message of an active session needs (one consistent database snapshot).

    ``credits_enabled``: checkout may offer credits right now; ``credits_available_minor``: how much credit could
    be applied to this order (``min(balance, gross total)``).
    """

    session: Session
    lines: tuple[CartLine, ...]
    browse: ProductPage | None
    totals: Totals
    credits_enabled: bool
    credits_available_minor: int
