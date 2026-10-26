"""Order totals: ONE pure function used for every display and for submission.

All amounts are integer minor units. Rules:
- subtotal = sum(unit price x quantity) of the cart lines;
- the discount is what the order's discount code takes off the items (``discounts.discount_amount``; 0 without a
  code), never more than the subtotal;
- the fulfillment fee is the chosen option's ``fee_minor`` (0 without options);
- each configured fee is either flat (``amount_minor``) or a percentage of the items AFTER the discount
  (``percent_of(subtotal - discount, basis_points)``, rounded half up);
- gross = subtotal + fulfillment fee + fees - discount;
- credits applied = min(credits available, gross) when the customer chose to apply credits (never negative);
- total = gross - credits applied (never negative), which matches the ``orders`` table CHECK constraint.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shopbot.config.settings import FeeDef
from shopbot.core.money import percent_of


@dataclass(frozen=True)
class FeeAmount:
    fee_id: str
    amount_minor: int


@dataclass(frozen=True)
class Totals:
    subtotal_minor: int
    fulfillment_fee_minor: int
    fees: tuple[FeeAmount, ...]
    fees_minor: int
    gross_minor: int
    credits_applied_minor: int
    total_minor: int
    discount_minor: int = 0


def fee_amount(fee: FeeDef, items_minor: int) -> int:
    """A fee's amount; a percent fee is charged on ``items_minor`` (the items after the discount)."""
    if fee.kind == "percent":
        return percent_of(items_minor, fee.basis_points)
    return fee.amount_minor


def compute_totals(
    lines: Sequence[tuple[int, int]],
    *,
    fulfillment_fee_minor: int = 0,
    fees: Sequence[FeeDef] = (),
    credits_available_minor: int = 0,
    apply_credits: bool = False,
    discount_minor: int = 0,
) -> Totals:
    """``lines`` are (unit price, quantity) pairs. See the module docstring for the rules."""
    for price, quantity in lines:
        if price < 0 or quantity <= 0:
            raise ValueError("cart lines need a non-negative price and a positive quantity")
    if fulfillment_fee_minor < 0:
        raise ValueError("the fulfillment fee must not be negative")
    subtotal = sum(price * quantity for price, quantity in lines)
    if not 0 <= discount_minor <= subtotal:
        raise ValueError("the discount must be between 0 and the subtotal")
    items = subtotal - discount_minor
    fee_amounts = tuple(FeeAmount(fee.id, fee_amount(fee, items)) for fee in fees)
    fees_total = sum(f.amount_minor for f in fee_amounts)
    gross = items + fulfillment_fee_minor + fees_total
    credits = min(max(0, credits_available_minor), gross) if apply_credits else 0
    return Totals(
        subtotal_minor=subtotal,
        fulfillment_fee_minor=fulfillment_fee_minor,
        fees=fee_amounts,
        fees_minor=fees_total,
        gross_minor=gross,
        credits_applied_minor=credits,
        total_minor=gross - credits,
        discount_minor=discount_minor,
    )
