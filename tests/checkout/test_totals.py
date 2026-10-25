"""The one pure totals function: subtotal, fulfillment fee, fees (flat and percent, half up), credits, total."""

from __future__ import annotations

import itertools

import pytest

from shopbot.config.settings import FeeDef
from shopbot.features.checkout.totals import FeeAmount, compute_totals, fee_amount

FLAT = FeeDef(id="service", kind="flat", amount_minor=150)
TAX = FeeDef(id="tax", kind="percent", basis_points=1000)


def test_empty_cart_is_all_zero() -> None:
    totals = compute_totals([])
    assert (totals.subtotal_minor, totals.fees_minor, totals.gross_minor, totals.total_minor) == (0, 0, 0, 0)
    assert totals.fees == () and totals.credits_applied_minor == 0


def test_subtotal_fees_and_fulfillment_fee() -> None:
    totals = compute_totals([(1250, 2), (300, 1)], fulfillment_fee_minor=500, fees=[FLAT, TAX])
    assert totals.subtotal_minor == 2800
    assert totals.fees == (FeeAmount("service", 150), FeeAmount("tax", 280))
    assert totals.fees_minor == 430
    assert totals.gross_minor == 2800 + 500 + 430
    assert totals.total_minor == totals.gross_minor


@pytest.mark.parametrize(
    ("subtotal", "basis_points", "expected"),
    [
        (105, 1000, 11),  # 10.5 -> 11 (half up)
        (104, 1000, 10),  # 10.4 -> 10
        (125, 250, 3),  # 3.125 -> 3
        (5, 1000, 1),  # 0.5 -> 1
        (4, 1000, 0),  # 0.4 -> 0
        (0, 1000, 0),
        (999_999, 1, 100),  # 99.9999 -> 100
        (10_000, 100_000, 100_000),  # 1000 %
    ],
)
def test_percent_fees_round_half_up_on_the_subtotal(subtotal: int, basis_points: int, expected: int) -> None:
    fee = FeeDef(id="pct", kind="percent", basis_points=basis_points)
    assert fee_amount(fee, subtotal) == expected
    totals = compute_totals([(subtotal, 1)] if subtotal else [], fulfillment_fee_minor=700, fees=[fee])
    # Percent fees use the subtotal only, never the fulfillment fee.
    assert totals.fees_minor == expected


def test_credits_are_applied_only_when_chosen_and_capped_at_the_total() -> None:
    lines = [(1000, 1)]
    assert compute_totals(lines, credits_available_minor=300).credits_applied_minor == 0
    applied = compute_totals(lines, fees=[FLAT], credits_available_minor=300, apply_credits=True)
    assert applied.credits_applied_minor == 300 and applied.total_minor == 1150 - 300
    capped = compute_totals(lines, fees=[FLAT], credits_available_minor=99_999, apply_credits=True)
    assert capped.credits_applied_minor == 1150 and capped.total_minor == 0
    negative = compute_totals(lines, credits_available_minor=-50, apply_credits=True)
    assert negative.credits_applied_minor == 0 and negative.total_minor == 1000


def test_zero_priced_products_and_zero_fees() -> None:
    totals = compute_totals([(0, 3)], fees=[TAX])
    assert totals.subtotal_minor == 0 and totals.fees == (FeeAmount("tax", 0),) and totals.total_minor == 0


def test_invalid_lines_are_rejected() -> None:
    with pytest.raises(ValueError):
        compute_totals([(100, 0)])
    with pytest.raises(ValueError):
        compute_totals([(-1, 1)])
    with pytest.raises(ValueError):
        compute_totals([(100, 1)], fulfillment_fee_minor=-1)


def test_invariants_hold_for_many_combinations() -> None:
    prices = (0, 1, 99, 1250, 10**9)
    quantities = (1, 3, 1000)
    fees = ([], [FLAT], [TAX], [FLAT, TAX])
    for price, quantity, fee_set, fulfillment, credits, apply in itertools.product(
        prices, quantities, fees, (0, 500), (0, 1, 10**12), (False, True)
    ):
        totals = compute_totals(
            [(price, quantity), (250, 2)],
            fulfillment_fee_minor=fulfillment,
            fees=fee_set,
            credits_available_minor=credits,
            apply_credits=apply,
        )
        assert totals.subtotal_minor == price * quantity + 500
        assert totals.gross_minor == totals.subtotal_minor + fulfillment + totals.fees_minor
        assert totals.total_minor == totals.gross_minor - totals.credits_applied_minor  # the orders CHECK constraint
        assert 0 <= totals.credits_applied_minor <= totals.gross_minor
        assert totals.total_minor >= 0
        assert isinstance(totals.total_minor, int)
