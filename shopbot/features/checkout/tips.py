"""Tips for our team: the rules, as pure functions (no database, no Discord), used for every display and for submission.

The customer may add a tip on the review step (``[checkout.tips]``): one of the listed ``amounts``, or with
``allow_custom`` an amount they type (Other amount), from one whole unit of the shop currency (1.00) up to
``max_minor``. The tip is added to the order and paid with it; 100% of it goes to the team. Rules:

- **Which tip counts** (``allowed``, ``effective``): 0 (no tip) always; otherwise only while tips are on, and only a
  listed amount or a typed amount within the limits. A tip is checked again whenever the order's totals are computed:
  every display of the unfinished order and the transaction that creates the order. A tip that is no longer allowed
  (tips switched off, the amount no longer listed or above the maximum) counts as no tip, so the total changes and
  Submit gives the "the total changed" review instead of an order at a price nobody saw. The unfinished order keeps
  the amount the customer chose: it counts again (and is shown again) if the setting comes back before they confirm.
- **The totals** (``totals.compute_totals``) add it as it is: a discount code never applies to it, percent fees are not
  charged on it, and store credit may cover it like the rest of the order.
- **Stored inside the order's fees**: ``orders.fees_minor`` holds the extras and fees plus the tip and
  ``orders.tip_minor`` the tip's part of them, so the total CHECK of the orders table holds. Displays list the tip on
  its own line ("Tip for our team"), never as a fee.
- **Refunds** treat the tip as part of what was paid (it can be refunded like the rest). ``/stats`` shows the tips
  paid orders kept on their own line and leaves them out of the revenue (``stats``).
- **What the customer types** (``parse``): the amount in the shop's currency format (``12``, ``7.50``), surrounding
  spaces and the shop currency's own sign or code allowed (``$7.50``, ``7.50 USD``); another currency's sign (``€5``)
  is refused. Too low or not an amount: ``INVALID``; above ``max_minor``: ``TOO_HIGH``.
"""

from __future__ import annotations

from typing import Final

from shopbot.config.settings import CheckoutSettings
from shopbot.core.money import MoneyError, currency_exponent, parse_amount

# Why a typed amount cannot be used (each has its own customer text, ``content_keys``).
INVALID: Final = "invalid"
TOO_HIGH: Final = "too_high"
# The sign a customer may write before or after a typed amount, by currency code (the code itself is allowed too, e.g.
# "7.50 USD"). Any other sign is refused (INVALID), so "€5" in a USD shop is never read as $5.00.
CURRENCY_SIGNS: Final = {
    "USD": "$",
    "CAD": "$",
    "AUD": "$",
    "NZD": "$",
    "SGD": "$",
    "HKD": "$",
    "MXN": "$",
    "BRL": "R$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "KRW": "₩",
}


class TipRefused(ValueError):
    """A typed amount that cannot be a tip: ``reason`` is ``INVALID`` or ``TOO_HIGH``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def exponent(c: CheckoutSettings) -> int:
    """Minor-unit digits of the shop currency."""
    return currency_exponent(c.currency, c.currency_exponent)


def custom_minimum(c: CheckoutSettings) -> int:
    """The smallest amount a customer may type: one whole unit of the shop currency (1.00)."""
    return 10 ** exponent(c)


def allowed(c: CheckoutSettings, tip_minor: int) -> bool:
    """Whether ``tip_minor`` can be the tip of an order now (see the module docstring)."""
    tips = c.tips
    if tip_minor == 0:
        return True
    if not tips.enabled or tip_minor < 0:
        return False
    if tip_minor in tips.amounts:
        return True
    return tips.allow_custom and custom_minimum(c) <= tip_minor <= tips.max_minor


def effective(c: CheckoutSettings, tip_minor: int) -> int:
    """The tip that counts: ``tip_minor`` while it is allowed, otherwise 0."""
    return tip_minor if allowed(c, tip_minor) else 0


def parse(c: CheckoutSettings, text: str) -> int:
    """The amount the customer typed (Other amount), in minor units: only the shop currency's sign and code may
    surround it (``CURRENCY_SIGNS``). Raises ``TipRefused`` when it cannot be a tip."""
    raw = text.strip()
    remaining = [affix for affix in (CURRENCY_SIGNS.get(c.currency, ""), c.currency) if affix]
    found = True
    while found:  # each of them once, before or after the amount, in any order ("$7.50 USD", "USD $7.50")
        found = False
        for affix in remaining:
            if raw.upper().startswith(affix):
                raw = raw[len(affix) :].strip()
            elif raw.upper().endswith(affix):
                raw = raw[: -len(affix)].strip()
            else:
                continue
            remaining.remove(affix)
            found = True
            break
    try:
        amount = parse_amount(raw, exponent(c), c.decimal_separator, c.thousands_separator)
    except MoneyError:
        raise TipRefused(INVALID) from None
    if amount < custom_minimum(c):
        raise TipRefused(INVALID)
    if amount > c.tips.max_minor:
        raise TipRefused(TOO_HIGH)
    return amount
