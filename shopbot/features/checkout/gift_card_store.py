"""Synchronous data access for the gift cards customers submit to pay an order (table ``gift_cards``, migration 0012).

Plain reads and conditional writes on one connection; the order state machine (``repo.transition``) calls the writes
for the cards of an order that is paid or ends unpaid, and ``gift_card_repo`` composes them into the customer's and
staff's transactions. A card's code is only ever held as its sealed token (``payments.vault``); every decision on a
card deletes the token in the same statement (the schema refuses a decided card that still holds one).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

SUBMITTED: Final = "submitted"
REDEEMED: Final = "redeemed"
REFUSED: Final = "refused"
WITHDRAWN: Final = "withdrawn"
STATUSES: Final = (SUBMITTED, REDEEMED, REFUSED, WITHDRAWN)
# Cards that count toward an order's payment (and whose fingerprint may not be submitted again).
ACTIVE: Final = (SUBMITTED, REDEEMED)

# Gift card codes are long; a short minimum would make the last characters shown to tell cards apart a large part
# of the code.
CODE_MIN: Final = 10
CODE_MAX: Final = 64
PIN_MAX: Final = 16
# Refusal reasons are shown escaped (at most twice as long) in one line of a field (1024 characters).
REASON_MAX: Final = 400
LAST_CHARS: Final = 4

_COLUMNS = (
    "id, guild_id, order_id, user_id, amount_minor, last4, status, reason, submitted_at, reveal_count, revealed_by, "
    "revealed_at, decided_by, decided_at, erased_at, sealed IS NOT NULL AS stored"
)


@dataclass(frozen=True)
class GiftCard:
    """A submitted card without its code (``stored``: the sealed code is still kept)."""

    id: int
    guild_id: int
    order_id: int
    user_id: int
    amount_minor: int
    last4: str
    status: str
    reason: str | None
    submitted_at: int
    reveal_count: int
    revealed_by: int | None
    revealed_at: int | None
    decided_by: int | None
    decided_at: int | None
    erased_at: int | None
    stored: bool


@dataclass(frozen=True)
class CardTotals:
    """``waiting_minor``: cards staff have not decided on; ``redeemed_minor``: cards staff redeemed."""

    waiting_minor: int = 0
    redeemed_minor: int = 0
    waiting: int = 0
    active: int = 0

    @property
    def covered_minor(self) -> int:
        return self.waiting_minor + self.redeemed_minor


def _card(row: sqlite3.Row) -> GiftCard:
    return GiftCard(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        order_id=int(row["order_id"]),
        user_id=int(row["user_id"]),
        amount_minor=int(row["amount_minor"]),
        last4=str(row["last4"]),
        status=str(row["status"]),
        reason=row["reason"],
        submitted_at=int(row["submitted_at"]),
        reveal_count=int(row["reveal_count"]),
        revealed_by=row["revealed_by"],
        revealed_at=row["revealed_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        erased_at=row["erased_at"],
        stored=bool(row["stored"]),
    )


def cards(conn: sqlite3.Connection, order_id: int) -> list[GiftCard]:
    """Every card submitted for the order, oldest first."""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM gift_cards WHERE order_id = ? ORDER BY id", (order_id,))  # noqa: S608
    return [_card(r) for r in rows]


def get_card(conn: sqlite3.Connection, card_id: int) -> GiftCard | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM gift_cards WHERE id = ?", (card_id,)).fetchone()  # noqa: S608
    return None if row is None else _card(row)


def totals(conn: sqlite3.Connection, order_id: int) -> CardTotals:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN status = 'submitted' THEN amount_minor END), 0) AS waiting_minor, "
        "COALESCE(SUM(CASE WHEN status = 'redeemed' THEN amount_minor END), 0) AS redeemed_minor, "
        "COUNT(CASE WHEN status = 'submitted' THEN 1 END) AS waiting, "
        "COUNT(CASE WHEN status IN ('submitted', 'redeemed') THEN 1 END) AS active "
        "FROM gift_cards WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    return CardTotals(
        waiting_minor=int(row["waiting_minor"]),
        redeemed_minor=int(row["redeemed_minor"]),
        waiting=int(row["waiting"]),
        active=int(row["active"]),
    )


def fingerprint_in_use(conn: sqlite3.Connection, guild_id: int, fingerprint: str) -> bool:
    """The card is waiting for a decision or was redeemed (for any order)."""
    row = conn.execute(
        "SELECT 1 FROM gift_cards WHERE guild_id = ? AND fingerprint = ? AND status IN ('submitted', 'redeemed')",
        (guild_id, fingerprint),
    ).fetchone()
    return row is not None


def insert(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    user_id: int,
    amount_minor: int,
    fingerprint: str,
    sealed: bytes,
    last4: str,
    now: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO gift_cards(guild_id, order_id, user_id, amount_minor, fingerprint, sealed, last4, status, "
        "submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?)",
        (guild_id, order_id, user_id, amount_minor, fingerprint, sealed, last4, now),
    )
    return int(cursor.lastrowid or 0)


def sealed_waiting(conn: sqlite3.Connection, order_id: int) -> list[tuple[GiftCard, bytes]]:
    """The cards staff have not decided on that still hold their sealed code."""
    rows = conn.execute(
        f"SELECT {_COLUMNS}, sealed FROM gift_cards WHERE order_id = ? AND status = 'submitted' "  # noqa: S608
        "AND sealed IS NOT NULL ORDER BY id",
        (order_id,),
    )
    return [(_card(r), bytes(r["sealed"])) for r in rows]


def mark_revealed(conn: sqlite3.Connection, card_ids: Sequence[int], actor_id: int, now: int) -> None:
    conn.execute(
        "UPDATE gift_cards SET reveal_count = reveal_count + 1, revealed_by = ?, revealed_at = ? "
        "WHERE id IN (SELECT value FROM json_each(?))",
        (actor_id, now, json.dumps(list(card_ids))),
    )


def decide(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    card_ids: Sequence[int] | None,
    status: str,
    reason: str | None,
    actor_id: int | None,
    now: int,
) -> int:
    """Decide on the order's waiting cards (``card_ids`` None: all of them): redeemed, refused (with the reason the
    customer is shown) or withdrawn. Deletes their codes. Returns how many cards changed (cards decided before are
    left as they are)."""
    if status not in (REDEEMED, REFUSED, WITHDRAWN):
        raise ValueError(f"not a decision: {status!r}")
    ids = None if card_ids is None else json.dumps(list(card_ids))
    cur = conn.execute(
        "UPDATE gift_cards SET status = ?, reason = ?, decided_by = ?, decided_at = ?, sealed = NULL, "
        "erased_at = COALESCE(erased_at, ?) WHERE order_id = ? AND status = 'submitted' "
        "AND (? IS NULL OR id IN (SELECT value FROM json_each(?)))",
        (status, reason, actor_id, now, now, order_id, ids, ids),
    )
    return cur.rowcount


def erase(conn: sqlite3.Connection, card_id: int, now: int) -> bool:
    """Delete a card's sealed code (kept too long). The card itself stays, so staff still see what was submitted."""
    cur = conn.execute(
        "UPDATE gift_cards SET sealed = NULL, erased_at = ? WHERE id = ? AND sealed IS NOT NULL", (now, card_id)
    )
    return cur.rowcount == 1


def refused_since(conn: sqlite3.Connection, order_id: int, since: int) -> list[GiftCard]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM gift_cards WHERE order_id = ? AND status = 'refused' AND decided_at >= ? "  # noqa: S608
        "ORDER BY id",
        (order_id, since),
    )
    return [_card(r) for r in rows]
