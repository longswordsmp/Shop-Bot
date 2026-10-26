"""Discount codes, the pure rules (``discounts``) and the totals with a discount: code format, percent (rounded half
up), amount (capped), buy one get one free (the cheaper unit of each pair is free), product restriction, minimum,
every reason a code does not apply, and percent fees charged on the items after the discount."""

from __future__ import annotations

from dataclasses import replace

import pytest

from shopbot.config.settings import FeeDef
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout.totals import compute_totals

NOW = 1_700_000_000


def code(kind: str = d.PERCENT, value: int = 1000, **changes: object) -> d.Code:
    base = d.Code(
        id=1,
        guild_id=1,
        code="SAVE10",
        kind=kind,
        value=value,
        min_subtotal_minor=0,
        max_uses=None,
        once_per_customer=False,
        expires_at=None,
        created_by=None,
        created_at=0,
        ended_at=None,
        ended_by=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def line(product_id: int, price: int, quantity: int = 1) -> d.Line:
    return d.Line(product_id, price, quantity)


# -- the code itself --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "normalized", "valid"),
    [
        ("save10", "SAVE10", True),
        ("  Spring-Sale_2  ", "SPRING-SALE_2", True),
        ("ab", "AB", False),  # too short
        ("A" * 32, "A" * 32, True),
        ("A" * 33, "A" * 33, False),  # too long
        ("save 10", "SAVE 10", False),  # a space inside
        ("café", "CAFÉ", False),  # letters outside A-Z
        ("50%OFF", "50%OFF", False),
    ],
)
def test_codes_are_normalized_and_checked(typed: str, normalized: str, valid: bool) -> None:
    assert d.normalize(typed) == normalized
    assert d.valid_format(d.normalize(typed)) is valid


def test_a_code_that_does_not_exist_is_repeated_safely() -> None:
    assert d.shown("SAVE 10!") == "SAVE 10"
    assert d.shown("<@123456789012345678>") == "123456789012345678"
    assert d.shown("!!!") == "?"
    assert len(d.shown("X" * 40)) == d.CODE_MAX


# -- amounts ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subtotal", "basis_points", "expected"),
    [
        (995, 1000, 100),  # 99.5 -> 100 (half up)
        (994, 1000, 99),  # 99.4 -> 99
        (999, 1250, 125),  # 124.875 -> 125
        (5, 1000, 1),  # 0.5 -> 1
        (4, 1000, 0),  # 0.4 -> 0
        (2000, 10_000, 2000),  # 100 %
        (2000, 100, 20),  # 1 %
    ],
)
def test_percent_off_rounds_half_up(subtotal: int, basis_points: int, expected: int) -> None:
    assert d.discount_amount(code(d.PERCENT, basis_points), [line(1, subtotal)]) == expected


def test_amount_off_is_capped_at_the_eligible_items() -> None:
    assert d.discount_amount(code(d.AMOUNT, 500), [line(1, 1200)]) == 500
    assert d.discount_amount(code(d.AMOUNT, 5000), [line(1, 1200), line(2, 300)]) == 1500
    limited = code(d.AMOUNT, 5000, product_ids=frozenset({2}))
    assert d.discount_amount(limited, [line(1, 1200), line(2, 300, 2)]) == 600  # only product 2 counts


@pytest.mark.parametrize(
    ("lines", "free"),
    [
        ([line(1, 1000), line(2, 800), line(3, 500)], 800),  # pairs (10, 8), (5): the $8 unit is free
        ([line(1, 1000), line(2, 800), line(3, 500), line(4, 300)], 1100),  # (10, 8), (5, 3)
        ([line(1, 1000, 3)], 1000),  # three of one item: one free
        ([line(1, 1000, 4)], 2000),  # four: two free
        ([line(1, 1000, 2), line(2, 500, 3)], 1500),  # 10, 10, 5, 5, 5: one $10 and one $5 free
        ([line(1, 500, 3), line(2, 1000, 2)], 1500),  # the order of the lines does not matter
        ([line(1, 999)], 0),  # one unit: nothing free
    ],
)
def test_buy_one_get_one_free_makes_the_cheaper_unit_of_each_pair_free(lines: list[d.Line], free: int) -> None:
    assert d.bogo_free_minor(lines) == free
    assert d.discount_amount(code(d.BOGO, 0), lines) == free


def test_buy_one_get_one_free_counts_only_the_codes_products() -> None:
    bogo = code(d.BOGO, 0, product_ids=frozenset({2, 3}))
    lines = [line(1, 5000, 2), line(2, 800), line(3, 600, 2)]
    # Eligible units: 8, 6, 6 -> pairs (8, 6), (6): one $6 unit free. Product 1 never counts, however expensive.
    assert d.discount_amount(bogo, lines) == 600


def test_percent_off_counts_only_the_codes_products() -> None:
    limited = code(d.PERCENT, 2000, product_ids=frozenset({1}))
    assert d.discount_amount(limited, [line(1, 1000, 2), line(2, 5000)]) == 400  # 20% of $20, not of $70


def test_the_discount_never_exceeds_the_subtotal() -> None:
    for kind, value in ((d.PERCENT, 10_000), (d.AMOUNT, 10**12), (d.BOGO, 0)):
        lines = [line(1, 700, 3), line(2, 1)]
        assert 0 <= d.discount_amount(code(kind, value), lines) <= d.subtotal(lines)


# -- when a code applies -----------------------------------------------------------------------------------------------


def evaluate(found: d.Code | None, lines: list[d.Line], *, uses: int = 0, mine: int = 0) -> d.Check:
    return d.evaluate(found, lines, now=NOW, usage=d.Usage(uses=uses, customer_uses=mine))


def test_a_code_that_applies_gives_its_amount() -> None:
    result = evaluate(code(d.PERCENT, 1000), [line(1, 2000)])
    assert result.applies and result.amount_minor == 200 and result.reason is None


@pytest.mark.parametrize(
    ("found", "lines", "uses", "mine", "reason"),
    [
        (None, [line(1, 2000)], 0, 0, d.UNKNOWN),
        (code(ended_at=NOW - 1), [line(1, 2000)], 0, 0, d.ENDED),
        (code(expires_at=NOW), [line(1, 2000)], 0, 0, d.EXPIRED),  # expires_at must be later than now
        (code(expires_at=NOW - 60), [line(1, 2000)], 0, 0, d.EXPIRED),
        (code(max_uses=3), [line(1, 2000)], 3, 0, d.USED_UP),
        (code(once_per_customer=True), [line(1, 2000)], 1, 1, d.ALREADY_USED),
        (code(product_ids=frozenset({9})), [line(1, 2000)], 0, 0, d.NO_ELIGIBLE_ITEMS),
        (code(min_subtotal_minor=2500), [line(1, 2000)], 0, 0, d.MINIMUM),
        (code(d.BOGO, 0), [line(1, 2000)], 0, 0, d.NEEDS_TWO_ITEMS),
        (code(d.BOGO, 0, product_ids=frozenset({1})), [line(1, 2000), line(2, 900)], 0, 0, d.NEEDS_TWO_ITEMS),
    ],
)
def test_each_reason_a_code_does_not_apply(
    found: d.Code | None, lines: list[d.Line], uses: int, mine: int, reason: str
) -> None:
    result = evaluate(found, lines, uses=uses, mine=mine)
    assert result.reason == reason and result.amount_minor == 0 and not result.applies


def test_the_minimum_reason_names_the_codes_minimum() -> None:
    result = evaluate(code(min_subtotal_minor=2500), [line(1, 2000)])
    assert result.minimum_minor == 2500
    # The minimum is the items subtotal of the whole cart (other items count toward it).
    limited = code(min_subtotal_minor=2500, product_ids=frozenset({1}))
    assert evaluate(limited, [line(1, 2000), line(2, 600)]).applies


def test_uses_left_and_other_customers() -> None:
    assert evaluate(code(max_uses=3), [line(1, 2000)], uses=2).applies
    # Not once per customer: the same customer may use it again (while uses are left).
    assert evaluate(code(max_uses=5), [line(1, 2000)], uses=2, mine=2).applies
    # Once per customer: other customers' uses do not count against this customer.
    assert evaluate(code(once_per_customer=True), [line(1, 2000)], uses=7, mine=0).applies


def test_a_code_expiring_later_applies() -> None:
    assert evaluate(code(expires_at=NOW + 1), [line(1, 2000)]).applies
    assert code(expires_at=NOW + 1).usable(NOW) and not code(expires_at=NOW).usable(NOW)
    assert not code(ended_at=1).usable(NOW)


def test_buy_one_get_one_free_needs_two_eligible_units() -> None:
    assert evaluate(code(d.BOGO, 0), [line(1, 2000, 2)]).amount_minor == 2000
    assert evaluate(code(d.BOGO, 0), [line(1, 2000), line(2, 1500)]).amount_minor == 1500


def test_a_code_that_would_take_nothing_off_does_not_apply() -> None:
    # A free item is not an eligible unit: buy one get one free with a $10 item and a $0 item would save $0, and the
    # order would store the code and count a use (once per customer: the customer could never use it again).
    free_pair = evaluate(code(d.BOGO, 0, once_per_customer=True), [line(1, 1000), line(2, 0)])
    assert free_pair.reason == d.NEEDS_TWO_ITEMS and free_pair.amount_minor == 0
    assert evaluate(code(d.PERCENT, 1000), [line(1, 0, 3)]).reason == d.NO_ELIGIBLE_ITEMS
    # 1% of $0.49 rounds to $0.00.
    assert evaluate(code(d.PERCENT, 100), [line(1, 49)]).reason == d.NO_ELIGIBLE_ITEMS
    # Free items next to priced ones change nothing.
    assert evaluate(code(d.BOGO, 0), [line(1, 1000), line(2, 800), line(3, 0)]).amount_minor == 800


# -- totals with a discount -------------------------------------------------------------------------------------------

FLAT = FeeDef(id="service", kind="flat", amount_minor=150)
TAX = FeeDef(id="tax", kind="percent", basis_points=1000)


def test_percent_fees_are_charged_on_the_items_after_the_discount() -> None:
    totals = compute_totals([(1000, 3)], fulfillment_fee_minor=500, fees=[FLAT, TAX], discount_minor=1000)
    assert totals.subtotal_minor == 3000 and totals.discount_minor == 1000
    assert [f.amount_minor for f in totals.fees] == [150, 200]  # 10% of $20, not of $30
    assert totals.gross_minor == 3000 - 1000 + 500 + 150 + 200
    assert totals.total_minor == totals.gross_minor


def test_store_credit_covers_what_is_left_after_the_discount() -> None:
    totals = compute_totals(
        [(2000, 1)], fulfillment_fee_minor=500, credits_available_minor=5000, apply_credits=True, discount_minor=500
    )
    assert totals.gross_minor == 2000
    assert totals.credits_applied_minor == 2000 and totals.total_minor == 0
    partial = compute_totals([(2000, 1)], credits_available_minor=300, apply_credits=True, discount_minor=500)
    assert partial.credits_applied_minor == 300 and partial.total_minor == 1200


def test_a_full_discount_leaves_only_delivery_and_flat_fees() -> None:
    totals = compute_totals([(1000, 2)], fulfillment_fee_minor=500, fees=[FLAT, TAX], discount_minor=2000)
    assert totals.fees_minor == 150 and totals.total_minor == 650
    free = compute_totals([(1000, 2)], discount_minor=2000)
    assert free.total_minor == 0 and free.gross_minor == 0


def test_the_discount_must_fit_the_subtotal() -> None:
    with pytest.raises(ValueError):
        compute_totals([(1000, 1)], discount_minor=1001)
    with pytest.raises(ValueError):
        compute_totals([(1000, 1)], discount_minor=-1)
    assert compute_totals([(1000, 1)]).discount_minor == 0
