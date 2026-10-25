"""The payment provider interface (see the package docstring for how to add a provider)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import Settings
from shopbot.features.checkout.models import Order

if TYPE_CHECKING:
    from shopbot.context import App


@dataclass(frozen=True)
class PaymentInstructions:
    """What the customer is shown while an order waits for payment.

    ``title_key``/``description_key`` are content keys with the placeholders ``{order_number}`` and ``{total}``.
    ``link_url`` (a provider's hosted payment page) is shown as a link button labelled by ``link_label_key``.
    """

    description_key: str
    title_key: str | None = None
    link_url: str | None = None
    link_label_key: str | None = None


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
    """A way to collect payment. Implementations never ask for or accept card details or credentials in
    Discord: either staff confirm a payment made elsewhere, or the customer pays on the provider's own hosted
    page and a verified webhook confirms it."""

    id: str
    # True: staff confirm receipt with the "mark paid" button. False: a verified webhook confirms payment.
    requires_staff_confirmation: bool
    # True: ``refund`` returns money through the provider. False: refunds are recorded only.
    supports_refunds: bool

    def content_keys(self, settings: Settings) -> list[ContentKey]:
        """Customer-facing text this provider shows (declared only while it is the configured provider)."""
        ...

    def instructions(self, order: Order, external_ref: str | None) -> PaymentInstructions:
        """Instructions for an order that is waiting for payment (used again after a restart)."""
        ...

    async def start(self, app: App, order: Order) -> PaymentStart:
        """Called once, after the order was committed (never inside a database transaction)."""
        ...

    async def refund(self, app: App, order: Order, amount_minor: int) -> RefundResult:
        """Return ``amount_minor`` to the customer, or report that it must be returned manually."""
        ...
