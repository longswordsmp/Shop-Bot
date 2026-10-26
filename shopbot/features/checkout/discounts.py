"""Discount codes: the rules, as pure functions (no database, no Discord), used for every display and for submission.

A customer applies one code per order on the review step: typed into a form, or with the one-click button of a coupon
they claimed. Rules:

- **The code** is written with letters, digits, ``-`` and ``_`` (3 to 32 characters). What the customer types is
  normalized first (``normalize``: surrounding spaces removed, upper case), so ``save10`` finds ``SAVE10``.
- **The items it counts:** every line of the cart, or only the lines of the code's products when it names any
  (``eligible_lines``). Delivery, extras, fees and store credit never count.
- **The amount** (``discount_amount``), always between 0 and the items subtotal:
  - ``percent`` (value in basis points, 100 = 1%): ``percent_of(eligible subtotal, value)``, rounded half up;
  - ``amount`` (value in minor units): the amount, at most the eligible subtotal;
  - ``bogo`` (buy one, get one free): every eligible unit is listed with its unit price, highest first, and every
    second one is free - the cheaper unit of each pair, i.e. the prices at positions 2, 4, 6 ... (a cart of $10, $8
    and $5 units saves $8; with a fourth unit of $3 it saves $8 + $3).
- **When it applies** (``evaluate``; the first rule that fails names the reason): the code exists (``UNKNOWN``), is not
  ended (``ENDED``), not expired (``EXPIRED``: ``expires_at`` must be later than now), has uses left (``USED_UP``:
  counted orders < ``max_uses``), the customer has not used it yet when it is once per customer (``ALREADY_USED``),
  the cart has eligible items (``NO_ELIGIBLE_ITEMS``), the items subtotal reaches the code's minimum (``MINIMUM``) and,
  for buy one get one free, there are at least two eligible units (``NEEDS_TWO_ITEMS``). Only items with a price count
  as eligible (a free item has nothing to take off), and a code that would take nothing off (e.g. 1% of a few cents)
  does not apply either (``NO_ELIGIBLE_ITEMS``): an order never stores, or counts a use of, a code that saved nothing.
- **Uses are counted from orders** (``repo``: ``orders.discount_code_id``), never kept as a counter that could
  drift: an order counts unless it ended without ever being paid (``paid_at`` is NULL and it is cancelled, expired or
  rejected); an order that was paid and refunded later still counts.

The totals (``totals.compute_totals``) take the amount: percent fees are charged on the items after the discount,
and store credit covers what is left.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from shopbot.core.money import percent_of

PERCENT: Final = "percent"
AMOUNT: Final = "amount"
BOGO: Final = "bogo"
KINDS: Final = (PERCENT, AMOUNT, BOGO)

CODE_MIN: Final = 3
CODE_MAX: Final = 32
_CODE_RE: Final = re.compile(r"^[A-Z0-9_-]{3,32}$")
_NOT_CODE: Final = re.compile(r"[^A-Z0-9_ -]")
# Percent codes: 1% to 100% (in basis points).
PERCENT_MIN_BP: Final = 100
PERCENT_MAX_BP: Final = 10_000

# Why a code does not apply (each has its own customer text, ``content_keys.discount_error_key``).
UNKNOWN: Final = "unknown"
ENDED: Final = "ended"
EXPIRED: Final = "expired"
USED_UP: Final = "used_up"
ALREADY_USED: Final = "already_used"
NO_ELIGIBLE_ITEMS: Final = "no_eligible_items"
MINIMUM: Final = "minimum"
NEEDS_TWO_ITEMS: Final = "needs_two_items"
REASONS: Final = (UNKNOWN, ENDED, EXPIRED, USED_UP, ALREADY_USED, NO_ELIGIBLE_ITEMS, MINIMUM, NEEDS_TWO_ITEMS)
# The reasons a code can never apply again (whatever the cart): it is not offered as a claimed coupon any more.
FINAL_REASONS: Final = (UNKNOWN, ENDED, EXPIRED, USED_UP, ALREADY_USED)


def normalize(text: str) -> str:
    """What the customer typed, as a code is stored: surrounding spaces removed, upper case."""
    return text.strip().upper()


def valid_format(code: str) -> bool:
    """A normalized code uses only A-Z, 0-9, '-' and '_' and has 3 to 32 characters."""
    return _CODE_RE.match(code) is not None


def shown(text: str) -> str:
    """What the customer typed, as the reply about a code that does not exist repeats it: only the characters a code
    can have (and spaces), at most ``CODE_MAX`` of them, so a typed mention or link is never shown as one."""
    return _NOT_CODE.sub("", text)[:CODE_MAX].strip() or "?"


@dataclass(frozen=True)
class Code:
    """A discount code (``discount_codes``) with the products it is limited to (empty: every item)."""

    id: int
    guild_id: int
    code: str
    kind: str
    value: int
    min_subtotal_minor: int
    max_uses: int | None
    once_per_customer: bool
    expires_at: int | None
    created_by: int | None
    created_at: int
    ended_at: int | None
    ended_by: int | None
    product_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def ended(self) -> bool:
        return self.ended_at is not None

    def expired(self, now: int) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def usable(self, now: int) -> bool:
        """Not ended and not expired (whether it fits a cart, or has uses left, is checked by ``evaluate``)."""
        return not self.ended and not self.expired(now)

    @property
    def limited(self) -> bool:
        """Only some products count (``product_ids``)."""
        return bool(self.product_ids)

    def counts(self, product_id: int) -> bool:
        return not self.product_ids or product_id in self.product_ids


@dataclass(frozen=True)
class Line:
    """One cart or order line: the product, its unit price and the quantity."""

    product_id: int
    unit_price_minor: int
    quantity: int


@dataclass(frozen=True)
class Usage:
    """How often a code was used (counted orders): by everyone, and by the customer placing this order."""

    uses: int = 0
    customer_uses: int = 0


@dataclass(frozen=True)
class Check:
    """The result of ``evaluate``: the amount taken off (0 when it does not apply) and why it does not apply (None
    when it does). ``minimum_minor``: the code's minimum, for the MINIMUM reason's text."""

    amount_minor: int
    reason: str | None = None
    minimum_minor: int = 0

    @property
    def applies(self) -> bool:
        return self.reason is None


def eligible_lines(code: Code, lines: Iterable[Line]) -> list[Line]:
    """The lines whose items the code counts (every line when it names no products)."""
    return [line for line in lines if code.counts(line.product_id)]


def subtotal(lines: Iterable[Line]) -> int:
    return sum(line.unit_price_minor * line.quantity for line in lines)


def bogo_free_minor(lines: Iterable[Line]) -> int:
    """Buy one, get one free: every unit listed by unit price, highest first; every second unit is free (the cheaper
    unit of each pair). Returns the sum of the free units' prices."""
    units = sorted((line.unit_price_minor for line in lines for _ in range(line.quantity)), reverse=True)
    return sum(units[1::2])


def discount_amount(code: Code, lines: Sequence[Line]) -> int:
    """What ``code`` takes off these lines (see the module docstring), between 0 and their subtotal."""
    eligible = eligible_lines(code, lines)
    base = subtotal(eligible)
    if code.kind == PERCENT:
        amount = percent_of(base, code.value)
    elif code.kind == AMOUNT:
        amount = min(code.value, base)
    elif code.kind == BOGO:
        amount = bogo_free_minor(eligible)
    else:
        raise ValueError(f"unknown discount kind {code.kind!r}")
    return max(0, min(amount, base, subtotal(lines)))


def evaluate(code: Code | None, lines: Sequence[Line], *, now: int, usage: Usage) -> Check:
    """Whether ``code`` applies to a cart of ``lines`` placed now, and the amount it takes off (see the module
    docstring for the rules, in the order they are checked)."""
    if code is None:
        return Check(0, UNKNOWN)
    if code.ended:
        return Check(0, ENDED)
    if code.expired(now):
        return Check(0, EXPIRED)
    if code.max_uses is not None and usage.uses >= code.max_uses:
        return Check(0, USED_UP)
    if code.once_per_customer and usage.customer_uses > 0:
        return Check(0, ALREADY_USED)
    eligible = [line for line in eligible_lines(code, lines) if line.unit_price_minor > 0]
    if not eligible:
        return Check(0, NO_ELIGIBLE_ITEMS)
    if subtotal(lines) < code.min_subtotal_minor:
        return Check(0, MINIMUM, minimum_minor=code.min_subtotal_minor)
    if code.kind == BOGO and sum(line.quantity for line in eligible) < 2:
        return Check(0, NEEDS_TWO_ITEMS)
    amount = discount_amount(code, lines)
    if amount <= 0:
        return Check(0, NO_ELIGIBLE_ITEMS)
    return Check(amount)
