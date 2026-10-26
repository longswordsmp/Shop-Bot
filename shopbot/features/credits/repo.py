"""Credit ledger: append-only, synchronous, connection-first functions.

A member's balance is ``SUM(delta_minor)`` of their rows in ``credit_ledger`` (integer minor units of the shop
currency). Rows are never updated or deleted.

Every write function must run INSIDE a transaction (``app.db.transaction(fn, ...)``, or inside another
feature's transaction function that passes its ``conn`` through). Writes run their checks and the insert on
the same connection in the same transaction, so concurrent operations can never overdraw a balance or apply
an order debit/return twice (transactions are serialized by the database layer). A write called outside a
transaction raises ``RuntimeError``.

Invariants enforced by ``add_entry`` (the only insert) for every kind:
- ``delta_minor`` is a non-zero integer with ``abs(delta_minor) <= MAX_AMOUNT_MINOR``;
- the balance never becomes negative (``InsufficientCredit``);
- an ``order_id`` must reference an order of the same guild and the same member;
- ``admin_adjust``: positive or negative, ``order_id`` optional;
- ``order_debit``: negative, ``order_id`` required, at most one per order;
- ``order_return``: positive, ``order_id`` required, at most one per order, only after that order's debit and
  exactly the debited amount (for the debited member);
- ``refund_to_credit``: positive, ``order_id`` required (several partial refunds per order are allowed; the
  caller is responsible for not refunding more than was paid);
- ``gift_card``: positive, ``order_id`` required, at most one per order (the unused value of its gift cards).

Checkout API (call inside the transaction that creates/cancels the order):
- ``debit_for_order``: idempotent; at most one ``order_debit`` per order.
- ``return_for_order``: idempotent; at most one ``order_return`` per order, amount = the debit amount.
- ``refund_to_credit``: credit (part of) a refund to the member's balance.
- ``balance`` / ``applicable_amount``: how much credit can be applied to a total.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from shopbot.core.money import MAX_AMOUNT_MINOR

KIND_ADMIN_ADJUST: Final = "admin_adjust"
KIND_ORDER_DEBIT: Final = "order_debit"
KIND_ORDER_RETURN: Final = "order_return"
KIND_REFUND_TO_CREDIT: Final = "refund_to_credit"
# The value of gift cards the shop redeemed for an order but the order does not use (an overpayment, or the order was
# cancelled after a card was redeemed).
KIND_GIFT_CARD: Final = "gift_card"
KINDS: Final = (KIND_ADMIN_ADJUST, KIND_ORDER_DEBIT, KIND_ORDER_RETURN, KIND_REFUND_TO_CREDIT, KIND_GIFT_CARD)
# Notes are internal (shown only in the staff history). The limit keeps a full history page within Discord's
# embed limits even when every character needs markdown escaping.
NOTE_MAX_LENGTH: Final = 200

_SELECT = (
    "SELECT l.id, l.guild_id, l.user_id, l.delta_minor, l.kind, l.order_id, l.actor_id, l.note, l.created_at, "
    "o.number AS order_number FROM credit_ledger l LEFT JOIN orders o ON o.id = l.order_id"
)


class CreditError(Exception):
    """Base class: the requested ledger change violates a ledger invariant. Nothing was written."""


class InsufficientCredit(CreditError):
    """A debit would make the balance negative."""

    def __init__(self, *, balance_minor: int, requested_minor: int) -> None:
        super().__init__(f"balance {balance_minor} is lower than the requested {requested_minor}")
        self.balance_minor = balance_minor
        self.requested_minor = requested_minor


class OrderCreditConflict(CreditError):
    """An order debit/return conflicts with the order or with the order's existing ledger rows."""


@dataclass(frozen=True)
class Entry:
    id: int
    guild_id: int
    user_id: int
    delta_minor: int
    kind: str
    order_id: int | None
    actor_id: int | None
    note: str | None
    created_at: int
    # Customer-visible order number (orders.number) when the entry references an order.
    order_number: int | None = None


@dataclass(frozen=True)
class OrderCredit:
    """Result of an idempotent order helper: the ledger row, and whether this call created it."""

    entry: Entry
    created: bool


def _row(r: sqlite3.Row) -> Entry:
    return Entry(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        user_id=int(r["user_id"]),
        delta_minor=int(r["delta_minor"]),
        kind=str(r["kind"]),
        order_id=None if r["order_id"] is None else int(r["order_id"]),
        actor_id=None if r["actor_id"] is None else int(r["actor_id"]),
        note=r["note"],
        created_at=int(r["created_at"]),
        order_number=None if r["order_number"] is None else int(r["order_number"]),
    )


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("credit ledger writes must run inside app.db.transaction")


# -- reads ---------------------------------------------------------------------------------------


def balance(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    """Current balance in minor units (0 for a member without ledger rows)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(delta_minor), 0) AS total FROM credit_ledger WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return int(row["total"])


def applicable_amount(conn: sqlite3.Connection, guild_id: int, user_id: int, total_minor: int) -> int:
    """How much credit can be applied to an order total: ``min(balance, total)`` (never negative)."""
    return max(0, min(balance(conn, guild_id, user_id), total_minor))


def history(conn: sqlite3.Connection, guild_id: int, user_id: int, *, limit: int, offset: int) -> list[Entry]:
    """A member's ledger rows, newest first."""
    rows = conn.execute(
        f"{_SELECT} WHERE l.guild_id = ? AND l.user_id = ? ORDER BY l.id DESC LIMIT ? OFFSET ?",
        (guild_id, user_id, max(0, limit), max(0, offset)),
    ).fetchall()
    return [_row(r) for r in rows]


def count_entries(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM credit_ledger WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()
    return int(row["n"])


def get_entry(conn: sqlite3.Connection, guild_id: int, entry_id: int) -> Entry | None:
    row = conn.execute(f"{_SELECT} WHERE l.guild_id = ? AND l.id = ?", (guild_id, entry_id)).fetchone()
    return None if row is None else _row(row)


def order_entry(conn: sqlite3.Connection, guild_id: int, order_id: int, kind: str) -> Entry | None:
    """The (first) row of ``kind`` for an order, e.g. its ``order_debit``; None if there is none."""
    row = conn.execute(
        f"{_SELECT} WHERE l.guild_id = ? AND l.order_id = ? AND l.kind = ? ORDER BY l.id LIMIT 1",
        (guild_id, order_id, kind),
    ).fetchone()
    return None if row is None else _row(row)


def order_entries(conn: sqlite3.Connection, guild_id: int, order_id: int) -> list[Entry]:
    """Every ledger row that references an order, oldest first."""
    rows = conn.execute(
        f"{_SELECT} WHERE l.guild_id = ? AND l.order_id = ? ORDER BY l.id",
        (guild_id, order_id),
    ).fetchall()
    return [_row(r) for r in rows]


# -- writes --------------------------------------------------------------------------------------


def _check_order(conn: sqlite3.Connection, guild_id: int, user_id: int, order_id: int) -> None:
    row = conn.execute("SELECT guild_id, user_id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None or int(row["guild_id"]) != guild_id:
        raise OrderCreditConflict(f"order {order_id} does not exist")
    if int(row["user_id"]) != user_id:
        raise OrderCreditConflict(f"order {order_id} belongs to another member")


def _check_kind(
    conn: sqlite3.Connection, *, guild_id: int, user_id: int, delta_minor: int, kind: str, order_id: int | None
) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown credit entry kind {kind!r}")
    if kind != KIND_ADMIN_ADJUST and order_id is None:
        raise ValueError(f"{kind} entries require an order_id")
    if kind == KIND_ORDER_DEBIT and delta_minor > 0:
        raise ValueError("order_debit entries must be negative")
    if kind in (KIND_ORDER_RETURN, KIND_REFUND_TO_CREDIT, KIND_GIFT_CARD) and delta_minor < 0:
        raise ValueError(f"{kind} entries must be positive")
    if order_id is None:
        return
    _check_order(conn, guild_id, user_id, order_id)
    if kind == KIND_ORDER_DEBIT and order_entry(conn, guild_id, order_id, KIND_ORDER_DEBIT) is not None:
        raise OrderCreditConflict(f"order {order_id} already has a credit debit")
    if kind == KIND_GIFT_CARD and order_entry(conn, guild_id, order_id, KIND_GIFT_CARD) is not None:
        raise OrderCreditConflict(f"order {order_id} already credited its unused gift card value")
    if kind == KIND_ORDER_RETURN:
        debit = order_entry(conn, guild_id, order_id, KIND_ORDER_DEBIT)
        if debit is None:
            raise OrderCreditConflict(f"order {order_id} has no credit debit to return")
        if order_entry(conn, guild_id, order_id, KIND_ORDER_RETURN) is not None:
            raise OrderCreditConflict(f"the credit debit of order {order_id} was already returned")
        if debit.user_id != user_id or delta_minor != -debit.delta_minor:
            raise OrderCreditConflict(f"a return must give back exactly the debit of order {order_id}")


def add_entry(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    delta_minor: int,
    kind: str,
    order_id: int | None,
    actor_id: int | None,
    note: str | None,
    now: int,
) -> int:
    """Append one ledger row and return its id. Must run inside a transaction.

    Raises ``ValueError`` for malformed input (zero delta, unknown kind, wrong sign, note too long),
    ``InsufficientCredit`` when a negative delta would overdraw the balance, and ``OrderCreditConflict`` when an
    order reference is invalid or would duplicate an order debit/return. Nothing is written when it raises.
    """
    _require_transaction(conn)
    if isinstance(delta_minor, bool) or not isinstance(delta_minor, int):
        raise ValueError("delta_minor must be an integer number of minor units")
    if delta_minor == 0:
        raise ValueError("delta_minor must not be 0")
    if abs(delta_minor) > MAX_AMOUNT_MINOR:
        raise ValueError(f"abs(delta_minor) must be at most {MAX_AMOUNT_MINOR}")
    if note is not None and len(note) > NOTE_MAX_LENGTH:
        raise ValueError(f"note must be at most {NOTE_MAX_LENGTH} characters")
    _check_kind(conn, guild_id=guild_id, user_id=user_id, delta_minor=delta_minor, kind=kind, order_id=order_id)
    if delta_minor < 0:
        current = balance(conn, guild_id, user_id)
        if current + delta_minor < 0:
            raise InsufficientCredit(balance_minor=current, requested_minor=-delta_minor)
    cursor = conn.execute(
        "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, actor_id, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, delta_minor, kind, order_id, actor_id, note or None, now),
    )
    return int(cursor.lastrowid or 0)


def _created(conn: sqlite3.Connection, guild_id: int, entry_id: int) -> Entry:
    entry = get_entry(conn, guild_id, entry_id)
    if entry is None:  # pragma: no cover - the row was inserted in this transaction
        raise RuntimeError(f"credit entry {entry_id} vanished")
    return entry


def debit_for_order(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    order_id: int,
    amount_minor: int,
    actor_id: int | None = None,
    note: str | None = None,
    now: int,
) -> OrderCredit:
    """Apply ``amount_minor`` (> 0) of the member's credit to an order. Idempotent per order.

    Call inside the transaction that inserted the order row (with ``credits_applied_minor = amount_minor``). If
    the order already has a debit with the same member and amount, that row is returned with
    ``created=False`` and nothing is written; a different member/amount raises ``OrderCreditConflict``, and so does
    a debit for an order whose debit was already returned (credit cannot be re-applied to the same order).
    Raises ``InsufficientCredit`` when the balance is lower than ``amount_minor`` (the caller's transaction should
    then roll back, e.g. by letting the exception propagate).
    """
    _require_transaction(conn)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise ValueError("amount_minor must be a positive integer")
    existing = order_entry(conn, guild_id, order_id, KIND_ORDER_DEBIT)
    if existing is not None:
        if existing.user_id != user_id or existing.delta_minor != -amount_minor:
            raise OrderCreditConflict(f"order {order_id} already has a different credit debit")
        if order_entry(conn, guild_id, order_id, KIND_ORDER_RETURN) is not None:
            raise OrderCreditConflict(f"the credit debit of order {order_id} was already returned")
        return OrderCredit(existing, created=False)
    entry_id = add_entry(
        conn,
        guild_id=guild_id,
        user_id=user_id,
        delta_minor=-amount_minor,
        kind=KIND_ORDER_DEBIT,
        order_id=order_id,
        actor_id=actor_id,
        note=note,
        now=now,
    )
    return OrderCredit(_created(conn, guild_id, entry_id), created=True)


def return_for_order(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int | None = None,
    note: str | None = None,
    now: int,
) -> OrderCredit | None:
    """Give back the credit debited for an order (cancelled/expired/rejected order). Idempotent per order.

    Returns None when the order has no credit debit (nothing to return). If the debit was already returned,
    the existing return row is returned with ``created=False`` and nothing is written.
    """
    _require_transaction(conn)
    debit = order_entry(conn, guild_id, order_id, KIND_ORDER_DEBIT)
    if debit is None:
        return None
    existing = order_entry(conn, guild_id, order_id, KIND_ORDER_RETURN)
    if existing is not None:
        return OrderCredit(existing, created=False)
    entry_id = add_entry(
        conn,
        guild_id=guild_id,
        user_id=debit.user_id,
        delta_minor=-debit.delta_minor,
        kind=KIND_ORDER_RETURN,
        order_id=order_id,
        actor_id=actor_id,
        note=note,
        now=now,
    )
    return OrderCredit(_created(conn, guild_id, entry_id), created=True)


def refund_to_credit(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    order_id: int,
    amount_minor: int,
    actor_id: int | None,
    note: str | None = None,
    now: int,
) -> Entry:
    """Credit (part of) an order refund to the member's balance. NOT idempotent by itself: record the refund
    (e.g. an ``order_adjustments`` row) in the same transaction so a repeated request can be detected."""
    _require_transaction(conn)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise ValueError("amount_minor must be a positive integer")
    entry_id = add_entry(
        conn,
        guild_id=guild_id,
        user_id=user_id,
        delta_minor=amount_minor,
        kind=KIND_REFUND_TO_CREDIT,
        order_id=order_id,
        actor_id=actor_id,
        note=note,
        now=now,
    )
    return _created(conn, guild_id, entry_id)


def credit_gift_cards(
    conn: sqlite3.Connection, *, guild_id: int, user_id: int, order_id: int, amount_minor: int, now: int
) -> OrderCredit:
    """Credit the unused value of an order's redeemed gift cards to the customer. Idempotent per order: a second call
    returns the existing row (``created=False``) and writes nothing."""
    _require_transaction(conn)
    existing = order_entry(conn, guild_id, order_id, KIND_GIFT_CARD)
    if existing is not None:
        return OrderCredit(existing, created=False)
    entry_id = add_entry(
        conn,
        guild_id=guild_id,
        user_id=user_id,
        delta_minor=amount_minor,
        kind=KIND_GIFT_CARD,
        order_id=order_id,
        actor_id=None,
        note=None,
        now=now,
    )
    return OrderCredit(_created(conn, guild_id, entry_id), created=True)


@dataclass(frozen=True)
class Adjustment:
    entry: Entry
    balance_minor: int


def adjust(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    delta_minor: int,
    actor_id: int,
    note: str | None,
    now: int,
) -> Adjustment:
    """Staff adjustment (``admin_adjust``): append the row and return it with the resulting balance."""
    entry_id = add_entry(
        conn,
        guild_id=guild_id,
        user_id=user_id,
        delta_minor=delta_minor,
        kind=KIND_ADMIN_ADJUST,
        order_id=None,
        actor_id=actor_id,
        note=note,
        now=now,
    )
    return Adjustment(_created(conn, guild_id, entry_id), balance(conn, guild_id, user_id))
