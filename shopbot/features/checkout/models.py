"""Checkout records (sessions, carts, orders) and the order state machine.

Session statuses: ``active`` (the customer is building the order) -> ``submitted`` (an order exists) |
``cancelled`` (the customer cancelled) | ``expired`` (idle too long, or the order channel disappeared).

Order statuses and the only allowed transitions (``TRANSITIONS[to]`` lists the states ``to`` may be reached
from)::

    pending_payment   -> payment_submitted | paid | rejected | cancelled | expired
    payment_submitted -> pending_payment | paid
                         (the customer submitted gift cards for the total; it leaves only through a decision on its
                         cards: redeemed cards covering the total make it paid, otherwise it waits for payment
                         again. Staff may have redeemed a revealed card, so it is never rejected or cancelled with
                         cards waiting: Card problem first.)
    paid              -> processing | shipped | fulfilled | cancelled | refunded
    processing        -> shipped | fulfilled | cancelled | refunded
    shipped           -> fulfilled | refunded                 (carrier and tracking number in order_handling)
    fulfilled         -> refunded                             (delivered)
    cancelled         -> refunded        (only an order that was paid before it was cancelled)

``rejected``, ``expired`` and ``refunded`` are final. Every transition is one conditional UPDATE
(``WHERE status IN allowed_from``) plus an ``order_events`` row, so a second concurrent request finds the
state changed and applies nothing.

Refunds of a paid order have two parts: money (``refunded_minor``, at most ``total_minor``) and the store credit
the customer applied (``credits_applied_minor``, returned all at once; ``credit_returned_minor`` is read from the
credit ledger). An order is fully refunded - and moves to ``refunded`` - once both are covered. A discount
(``discount_minor``, from a discount code) was never paid, so it is never refunded: it is already taken off
``total_minor``.
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
PAYMENT_SUBMITTED: Final = "payment_submitted"
PAID: Final = "paid"
PROCESSING: Final = "processing"
SHIPPED: Final = "shipped"
FULFILLED: Final = "fulfilled"  # delivered
CANCELLED: Final = "cancelled"
REJECTED: Final = "rejected"
EXPIRED: Final = "expired"
REFUNDED: Final = "refunded"
ORDER_STATUSES: Final = (
    PENDING,
    PAYMENT_SUBMITTED,
    PAID,
    PROCESSING,
    SHIPPED,
    FULFILLED,
    CANCELLED,
    REJECTED,
    EXPIRED,
    REFUNDED,
)
OPEN_STATUSES: Final = (PENDING, PAYMENT_SUBMITTED, PAID, PROCESSING, SHIPPED)
# Not paid yet: stock is held, the applied credit is returned and the payment cancelled if the order ends here.
UNPAID_STATUSES: Final = (PENDING, PAYMENT_SUBMITTED)
# Paid and waiting to be sent: what the "paid but not shipped" staff reminder watches.
AWAITING_SHIPMENT: Final = (PAID, PROCESSING)
# Statuses after which the order channel is closed.
TERMINAL_STATUSES: Final = (FULFILLED, CANCELLED, REJECTED, EXPIRED, REFUNDED)
# Statuses shown with their own customer view / staff view / notification (pending_payment uses the
# submitted view with the payment instructions).
STATUS_VIEWS: Final = (PAYMENT_SUBMITTED, PAID, PROCESSING, SHIPPED, FULFILLED, CANCELLED, REJECTED, EXPIRED, REFUNDED)

TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    PENDING: (PAYMENT_SUBMITTED,),
    PAYMENT_SUBMITTED: (PENDING,),
    PAID: (PENDING, PAYMENT_SUBMITTED),
    PROCESSING: (PAID,),
    SHIPPED: (PAID, PROCESSING),
    FULFILLED: (PAID, PROCESSING, SHIPPED),
    REJECTED: (PENDING,),
    CANCELLED: (PENDING, PAID, PROCESSING),
    EXPIRED: (PENDING,),
    REFUNDED: (PAID, PROCESSING, SHIPPED, FULFILLED, CANCELLED),
}
# Statuses that staff may set directly (buttons and /order status). Expiry, refunds and the customer's payment
# submission have their own paths, and so does shipped: it always comes with the carrier and tracking number (the
# Mark shipped form or /order ship).
STAFF_TARGETS: Final = (PAID, PROCESSING, FULFILLED, REJECTED, CANCELLED)
# Statuses in which the store credit applied to a paid order can be given back. 'refunded' is included for orders
# whose money was refunded in full before the applied credit counted toward a full refund.
CREDIT_RETURN_STATUSES: Final = (*TRANSITIONS[REFUNDED], REFUNDED)

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
    # The discount code the customer applied (checked again whenever the review is shown and at submission).
    discount_code_id: int | None = None

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
    # Store credit given back to the customer for this order (its ``order_return`` ledger row; 0 when none).
    credit_returned_minor: int = 0
    # The discount code the order used: its id, the code and kind as they were when the order was placed, and the
    # amount it took off the items (0 without a code).
    discount_code_id: int | None = None
    discount_code: str | None = None
    discount_kind: str | None = None
    discount_minor: int = 0

    @property
    def refundable_minor(self) -> int:
        """Money that can still be refunded (never more than the total that was charged)."""
        return max(0, self.total_minor - self.refunded_minor)

    @property
    def credit_returnable_minor(self) -> int:
        """Applied store credit that was not given back yet."""
        return max(0, self.credits_applied_minor - self.credit_returned_minor)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def submission_answers(self) -> dict[str, str]:
        """The answers to ``checkout.submission_form`` (asked when the customer pressed Submit), by answer key
        (``s.<field>``); also part of ``details``, which staff views show with their configured labels."""
        return {key: value for key, value in self.details.items() if key.startswith("s.")}

    @property
    def refunds_allowed(self) -> bool:
        """Paid at some point and in a state that allows refunds (money or the applied credit)."""
        return self.paid_at is not None and self.status in TRANSITIONS[REFUNDED]

    @property
    def can_refund(self) -> bool:
        """Money can still be refunded: paid at some point, not fully refunded, and in a state that allows refunds."""
        return self.refundable_minor > 0 and self.refunds_allowed

    @property
    def can_return_credit(self) -> bool:
        """The store credit applied to this paid order can still be given back (all of it at once)."""
        return self.credit_returnable_minor > 0 and self.paid_at is not None and self.status in CREDIT_RETURN_STATUSES

    @property
    def fully_refunded(self) -> bool:
        """Money refunds plus returned credit cover everything the customer paid (total + applied credit)."""
        paid = self.total_minor + self.credits_applied_minor
        return self.refunded_minor + self.credit_returned_minor >= paid


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
    """Everything the customer and staff views of an order need (one consistent database snapshot).

    ``ticket_number``: the order ticket's number (its unique reference); ``timeline``: the order's status changes
    in order (the submission first). Staff notes (events without a status change) are never part of it."""

    order: Order
    items: tuple[OrderItem, ...]
    fees: tuple[OrderFee, ...]
    channel_id: int | None = None
    ticket_number: int | None = None
    timeline: tuple[OrderEvent, ...] = ()


@dataclass(frozen=True)
class ProductPage:
    """One page of the product selection menu."""

    products: tuple[Product, ...]
    page: int
    pages: int
    total: int


@dataclass(frozen=True)
class AppliedDiscount:
    """The discount code of an unfinished order as checked now (``discounts.evaluate``): ``amount_minor`` is what it
    takes off the items, 0 when it does not apply; ``reason`` is why it does not apply (``discounts.REASONS``; None
    when it does) and ``minimum_minor`` the code's minimum (for that reason's text)."""

    code_id: int
    code: str
    kind: str
    amount_minor: int
    reason: str | None = None
    minimum_minor: int = 0

    @property
    def applies(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class DiscountOffer:
    """What the review step offers: ``codes`` - discount codes are on and the shop has at least one usable code (the
    Add discount code button); ``coupons`` - (code id, code) of coupons the customer claimed that can still be used,
    newest first (one Use coupon button each)."""

    codes: bool = False
    coupons: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class WorkflowData:
    """Everything the workflow message of an active session needs (one consistent database snapshot).

    ``credits_enabled``: checkout may offer credits right now; ``credits_available_minor``: how much credit could
    be applied to this order (``min(balance, gross total)``); ``ticket_number``: the order ticket's number (the
    reference the live panel shows); ``discount``: the discount code the customer applied, as checked now (None
    without one, or while discount codes are off); ``offer``: the discount buttons the review step shows.
    """

    session: Session
    lines: tuple[CartLine, ...]
    browse: ProductPage | None
    totals: Totals
    credits_enabled: bool
    credits_available_minor: int
    ticket_number: int | None = None
    discount: AppliedDiscount | None = None
    offer: DiscountOffer = field(default_factory=DiscountOffer)
