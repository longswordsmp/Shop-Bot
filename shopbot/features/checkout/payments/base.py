"""The payment provider interface (see the package docstring for how to add a provider)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import Settings
from shopbot.features.checkout.models import Order
from shopbot.ui.spec import EmbedField

if TYPE_CHECKING:
    from shopbot.context import App


@dataclass(frozen=True)
class PaymentInstructions:
    """What the customer is shown while an order waits for payment.

    ``title_key``/``description_key`` are content keys with the placeholders ``{order_number}`` and ``{total}``.
    ``link_url`` (a provider's hosted payment page) is shown as a link button labelled by ``link_label_key``.
    ``form_label_key``: a button that opens the provider's private payment form (the gift card form). ``fields``:
    what the customer has paid so far (rendered by the provider's ``progress``); ``amount_due``: what is left to pay.
    """

    description_key: str
    title_key: str | None = None
    link_url: str | None = None
    link_label_key: str | None = None
    form_label_key: str | None = None
    fields: tuple[EmbedField, ...] = ()
    # What is still to pay (formatted) once part of the total was paid, e.g. one gift card of two: the instructions'
    # {total} shows it, so the customer pays the rest and not the whole total again. None: nothing paid yet.
    amount_due: str | None = None


@dataclass(frozen=True)
class PaymentStart:
    """Result of starting a payment: the provider's reference (stored in ``payments.external_ref``) and the
    instructions to show."""

    external_ref: str | None
    instructions: PaymentInstructions


@dataclass(frozen=True)
class RefundResult:
    """``method='provider'``: the provider returned the money (``external_ref`` is its refund id).
    ``method='manual'``: nothing left the bot; staff must return the money outside the bot."""

    method: Literal["provider", "manual"]
    external_ref: str | None = None

    @property
    def returned_by_provider(self) -> bool:
        return self.method == "provider"


class PaymentProvider(Protocol):
    """A way to collect payment. Implementations never ask for bank or credit card details or passwords in Discord:
    staff confirm a payment made elsewhere, the customer pays on the provider's own hosted page and a verified webhook
    confirms it, or (``gift_card``) the customer submits a gift card in a private form, which is stored encrypted,
    shown only to the payment staff and deleted once they decided on it."""

    id: str
    # True: staff confirm receipt with the "mark paid" button. False: a verified webhook confirms payment.
    requires_staff_confirmation: bool
    # True: ``refund`` returns money through the provider. False: refunds are recorded only.
    supports_refunds: bool

    def content_keys(self, settings: Settings) -> list[ContentKey]:
        """Customer-facing text this provider shows (declared only while it is the configured provider)."""
        ...

    def instructions(self, settings: Settings, order: Order, external_ref: str | None) -> PaymentInstructions:
        """Instructions for an order that is waiting for payment (used again after a restart)."""
        ...

    async def progress(self, app: App, order: Order) -> tuple[EmbedField, ...]:
        """What the customer has paid so far for an order waiting for payment (shown with the instructions)."""
        ...

    async def start(self, app: App, order: Order) -> PaymentStart:
        """Called once, after the order was committed (never inside a database transaction)."""
        ...

    async def refund(self, app: App, order: Order, amount_minor: int) -> RefundResult:
        """Return ``amount_minor`` to the customer, or report that it must be returned manually."""
        ...
