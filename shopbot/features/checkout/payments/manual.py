"""The ``manual`` payment provider.

The customer is shown the business's own payment instructions (configured text). Staff confirm that the money
arrived with the "mark paid" button on the staff order controls (or ``/order status``). Refunds are recorded in
the order (``order_adjustments``); staff return the money outside the bot. Nothing payment-related is collected
in Discord.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.checkout.models import Order
from shopbot.features.checkout.payments.base import PaymentInstructions, PaymentStart, RefundResult
from shopbot.ui.spec import EmbedField

if TYPE_CHECKING:
    from shopbot.context import App

FEATURE = "checkout"
TITLE = "checkout.payment.manual.title"
INSTRUCTIONS = "checkout.payment.manual.instructions"
_PLACEHOLDERS = ("order_number", "total")


class ManualProvider:
    id = "manual"
    requires_staff_confirmation = True
    supports_refunds = False

    def content_keys(self, settings: Settings) -> list[ContentKey]:
        del settings
        group = "Checkout: payment instructions (manual payment)"
        return [
            ck(
                TITLE,
                Kind.TITLE,
                FEATURE,
                group,
                "Optional title of the payment instructions shown in the order channel after the customer "
                'submits an order (payments.provider = "manual"). {order_number} is the order number, {total} '
                "the amount to pay written with common.money_format.",
                required=False,
                placeholders=_PLACEHOLDERS,
            ),
            ck(
                INSTRUCTIONS,
                Kind.DESCRIPTION,
                FEATURE,
                group,
                "How the customer pays (your own payment instructions), shown in the order channel after the "
                "customer submits an order and in the payment reminder. Staff confirm receipt with the "
                "mark-paid button. Never ask customers to send card details or passwords in Discord. "
                "{order_number} is the order number, {total} the amount to pay written with common.money_format.",
                placeholders=_PLACEHOLDERS,
            ),
        ]

    def instructions(self, settings: Settings, order: Order, external_ref: str | None) -> PaymentInstructions:
        del settings, order, external_ref
        return PaymentInstructions(description_key=INSTRUCTIONS, title_key=TITLE)

    async def progress(self, app: App, order: Order) -> tuple[EmbedField, ...]:
        del app, order
        return ()

    async def start(self, app: App, order: Order) -> PaymentStart:
        return PaymentStart(external_ref=None, instructions=self.instructions(app.settings, order, None))

    async def refund(self, app: App, order: Order, amount_minor: int) -> RefundResult:
        del app, order, amount_minor
        return RefundResult(method="manual")
