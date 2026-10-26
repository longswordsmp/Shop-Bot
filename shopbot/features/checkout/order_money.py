"""Writing the amounts stored in an order (``common.money_format``, in the order's own currency).

Kept apart from ``views`` so the payment providers can write amounts without importing the views (which import the
providers' instruction types)."""

from __future__ import annotations

from collections.abc import Callable

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as core
from shopbot.ui.style import Ui

# Writes an amount in minor units with common.money_format (raises MissingContent when that is not configured).
Money = Callable[[int], str]


def currency_digits(settings: Settings, currency: str) -> int:
    """Minor-unit digits of ``currency``: the configured override only applies to the configured shop currency."""
    c = settings.checkout
    return currency_exponent(currency, c.currency_exponent if currency == c.currency else -1)


def order_money(ui: Ui, settings: Settings, currency: str) -> Money:
    """Writes amounts stored in an order with the order's own currency (common.money_format)."""
    c = settings.checkout
    exponent = currency_digits(settings, currency)

    def write(minor: int) -> str:
        amount = format_amount(minor, exponent, c.decimal_separator, c.thousands_separator)
        text = ui.text(core.MONEY_FORMAT, amount=amount, currency=currency)
        if text is None:
            raise MissingContent(core.MONEY_FORMAT)
        return text

    return write
