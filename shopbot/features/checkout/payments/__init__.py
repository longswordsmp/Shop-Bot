"""Payment providers: the extension point for how customers pay.

``PROVIDERS`` maps ``settings.payments.provider`` to an implementation of ``PaymentProvider``
(``shopbot.features.checkout.payments.base``):

- ``manual``: the customer sees the business's configured payment instructions, staff confirm receipt with a
  button, and refunds are recorded while staff return the money outside the bot;
- ``gift_card``: the customer submits gift cards for the total in a private form (``payments.gift_card``); codes are
  stored encrypted, revealed only to the payment staff and deleted once they decided (see ``gift_card``).

The bot never asks for or accepts bank or credit card details or passwords.

The payment methods customers choose from in their ticket (``[[payments.methods]]``: card, PayPal, gift card, ...,
each active or down; pressing a down one opens the private pop-up) are a layer above the provider: see
``checkout.payment_methods``. Bringing card payments back means adding a provider as below and offering its page as an
active method.

Adding an approved provider (hosted checkout page / payment link + verified webhook)
-----------------------------------------------------------------------------------
1. Create ``payments/<provider>.py`` with a class implementing ``PaymentProvider``:
   - ``id`` = the value operators put in ``settings.payments.provider``;
   - ``requires_staff_confirmation = False`` (the webhook confirms payment), ``supports_refunds = True`` if the
     provider's API can refund;
   - ``content_keys(settings)``: every text it shows (e.g. ``checkout.payment.<id>.title`` /
     ``.instructions`` / ``.link_label``), declared with docs and placeholders like the manual provider;
   - ``start(app, order)``: create a hosted checkout session / payment link for ``order.total_minor`` in
     ``order.currency`` through the provider's API and return ``PaymentStart(external_ref=<their id>,
     instructions=PaymentInstructions(..., link_url=<hosted page>, link_label_key=...))``. It runs after the
     order was committed and outside any database transaction; the reference is stored in
     ``payments.external_ref`` (unique per provider);
   - ``instructions(order, external_ref)``: rebuild the same instructions (used after a restart);
   - ``refund(app, order, amount)``: call the provider's refund API and return ``RefundResult("provider",
     refund_id)``; raise on failure (nothing is recorded then).
2. Register it in ``PROVIDERS`` below.
3. Add a webhook receiver (an HTTPS endpoint outside Discord) that VERIFIES the provider's signature with a secret
   from the environment (never from settings/content, never logged), looks the payment up by
   ``(provider, external_ref)``, and calls ``shopbot.features.checkout.service.change_status(app, order_id,
   "paid", actor_id=None, note=...)`` - the same state machine the staff buttons use, so duplicate webhook
   deliveries are harmless.
"""

from __future__ import annotations

from shopbot.features.checkout.payments.base import (
    PaymentInstructions,
    PaymentProvider,
    PaymentStart,
    RefundResult,
)
from shopbot.features.checkout.payments.gift_card import GiftCardProvider
from shopbot.features.checkout.payments.manual import ManualProvider

PROVIDERS: dict[str, PaymentProvider] = {ManualProvider.id: ManualProvider(), GiftCardProvider.id: GiftCardProvider()}


def get_provider(provider_id: str) -> PaymentProvider | None:
    return PROVIDERS.get(provider_id)


__all__ = [
    "PROVIDERS",
    "PaymentInstructions",
    "PaymentProvider",
    "PaymentStart",
    "RefundResult",
    "get_provider",
]
