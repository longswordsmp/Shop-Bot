"""Synchronous data access for discount codes (``discount_codes``, ``discount_code_products``, ``discount_claims``).

Every function takes the connection first and runs on the database thread, like ``repo``. Uses are counted from
orders (``orders.discount_code_id``; see ``discounts``): an order counts unless it ended without ever being paid. The
checks that must hold under concurrent submissions (``max_uses``, once per customer) are made again inside the
transaction that inserts the order (``repo.submit_order``), where SQLite's write lock serializes them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m

# The order statuses an order that was never paid ends in: such an order does not count as a use of its code.
UNPAID_ENDINGS = (m.CANCELLED, m.EXPIRED, m.REJECTED)
# How many claimed coupons the review step offers (newest first).
COUPONS_SHOWN = 2
# How many ended codes /discount list shows.
ENDED_LISTED = 10


class DiscountError(Exception):
    """Base class: the requested change to a discount code is not allowed. Nothing was written."""


class CodeTaken(DiscountError):
    """A live (not ended) code with this name exists already."""


class UnknownProducts(DiscountError):
    def __init__(self, product_ids: Sequence[int]) -> None:
        super().__init__(", ".join(str(p) for p in product_ids))
        self.product_ids = tuple(product_ids)


# -- reads -------------------------------------------------------------------------------------------------------


def _product_ids(conn: sqlite3.Connection, code_id: int) -> frozenset[int]:
    rows = conn.execute("SELECT product_id FROM discount_code_products WHERE code_id = ?", (code_id,))
    return frozenset(int(r["product_id"]) for r in rows)


def _code(conn: sqlite3.Connection, r: sqlite3.Row) -> d.Code:
    return d.Code(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        code=str(r["code"]),
        kind=str(r["kind"]),
        value=int(r["value"]),
        min_subtotal_minor=int(r["min_subtotal_minor"]),
        max_uses=None if r["max_uses"] is None else int(r["max_uses"]),
        once_per_customer=bool(r["once_per_customer"]),
        expires_at=None if r["expires_at"] is None else int(r["expires_at"]),
        created_by=None if r["created_by"] is None else int(r["created_by"]),
        created_at=int(r["created_at"]),
        ended_at=None if r["ended_at"] is None else int(r["ended_at"]),
        ended_by=None if r["ended_by"] is None else int(r["ended_by"]),
        product_ids=_product_ids(conn, int(r["id"])),
    )


def get_code(conn: sqlite3.Connection, guild_id: int, code_id: int) -> d.Code | None:
    row = conn.execute("SELECT * FROM discount_codes WHERE id = ? AND guild_id = ?", (code_id, guild_id)).fetchone()
    return None if row is None else _code(conn, row)


def find_code(conn: sqlite3.Connection, guild_id: int, code: str) -> d.Code | None:
    """The code with this (normalized) name: the live one, else the one that ended last (so the customer hears that
    it ended rather than that it does not exist); None when the shop never had it."""
    row = conn.execute(
        "SELECT * FROM discount_codes WHERE guild_id = ? AND code = ? ORDER BY ended_at IS NOT NULL, id DESC LIMIT 1",
        (guild_id, code),
    ).fetchone()
    return None if row is None else _code(conn, row)


def usable_code(conn: sqlite3.Connection, guild_id: int, code: str, now: int) -> d.Code | None:
    """The live code with this name when it can still be used now (not ended, not expired), else None."""
    found = find_code(conn, guild_id, d.normalize(code))
    return found if found is not None and found.usable(now) else None


def has_usable_codes(conn: sqlite3.Connection, guild_id: int, now: int) -> bool:
    """Whether the shop has at least one code that is not ended and not expired (the Add discount code button)."""
    row = conn.execute(
        "SELECT 1 FROM discount_codes WHERE guild_id = ? AND ended_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?) LIMIT 1",
        (guild_id, now),
    ).fetchone()
    return row is not None


def usage(conn: sqlite3.Connection, code_id: int, user_id: int) -> d.Usage:
    """How often the code was used, by everyone and by ``user_id``: its orders, except those that ended without ever
    being paid."""
    row = conn.execute(
        "SELECT COUNT(*) AS uses, COALESCE(SUM(user_id = ?), 0) AS mine FROM orders WHERE discount_code_id = ? "
        "AND NOT (paid_at IS NULL AND status IN (SELECT value FROM json_each(?)))",
        (user_id, code_id, json.dumps(list(UNPAID_ENDINGS))),
    ).fetchone()
    return d.Usage(uses=int(row["uses"]), customer_uses=int(row["mine"]))


def check(
    conn: sqlite3.Connection, guild_id: int, code: d.Code | None, user_id: int, lines: Sequence[d.Line], now: int
) -> d.Check:
    """``discounts.evaluate`` with the code's uses counted now."""
    counted = usage(conn, code.id, user_id) if code is not None else d.Usage()
    return d.evaluate(code, lines, now=now, usage=counted)


def cart_lines(lines: Iterable[m.CartLine]) -> list[d.Line]:
    """Cart lines as the discount rules read them (the products' current prices)."""
    return [d.Line(line.product.id, line.product.price_minor, line.quantity) for line in lines]


def session_discount(
    conn: sqlite3.Connection, guild_id: int, session: m.Session, lines: Sequence[m.CartLine], now: int
) -> m.AppliedDiscount | None:
    """The code stored on the session, checked against its cart now (None when it has none)."""
    if session.discount_code_id is None:
        return None
    code = get_code(conn, guild_id, session.discount_code_id)
    if code is None:
        return None
    result = check(conn, guild_id, code, session.user_id, cart_lines(lines), now)
    return m.AppliedDiscount(
        code_id=code.id,
        code=code.code,
        kind=code.kind,
        amount_minor=result.amount_minor,
        reason=result.reason,
        minimum_minor=result.minimum_minor,
    )


def claimed_coupons(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    lines: Sequence[m.CartLine],
    now: int,
    limit: int = COUPONS_SHOWN,
) -> list[d.Code]:
    """The coupons the customer claimed that can still be used, newest claim first (at most ``limit``): not ended,
    not expired, with uses left and not used by them yet. A coupon that does not fit this cart (a minimum, other
    items) is still offered: its button explains what is missing."""
    rows = conn.execute(
        "SELECT c.* FROM discount_claims k JOIN discount_codes c ON c.id = k.code_id WHERE k.guild_id = ? "
        "AND k.user_id = ? AND c.ended_at IS NULL AND (c.expires_at IS NULL OR c.expires_at > ?) "
        "ORDER BY k.claimed_at DESC, c.id DESC",
        (guild_id, user_id, now),
    ).fetchall()
    found: list[d.Code] = []
    items = cart_lines(lines)
    for row in rows:
        code = _code(conn, row)
        if check(conn, guild_id, code, user_id, items, now).reason in d.FINAL_REASONS:
            continue
        found.append(code)
        if len(found) >= limit:
            break
    return found


def offer(
    conn: sqlite3.Connection, guild_id: int, user_id: int, lines: Sequence[m.CartLine], now: int
) -> m.DiscountOffer:
    """The discount buttons of the review step (see ``models.DiscountOffer``); call only while codes are on."""
    coupons = tuple((c.id, c.code) for c in claimed_coupons(conn, guild_id, user_id, lines, now))
    return m.DiscountOffer(codes=has_usable_codes(conn, guild_id, now), coupons=coupons)


@dataclass(frozen=True)
class CodeStats:
    """A code with how often it was used (counted orders) and claimed, and the names of its products."""

    code: d.Code
    uses: int
    claims: int
    product_names: tuple[str, ...]


def _stats(conn: sqlite3.Connection, code: d.Code) -> CodeStats:
    uses = usage(conn, code.id, 0).uses
    claims = conn.execute("SELECT COUNT(*) AS n FROM discount_claims WHERE code_id = ?", (code.id,)).fetchone()["n"]
    names = [
        str(r["name"])
        for r in conn.execute(
            "SELECT p.name FROM discount_code_products x JOIN products p ON p.id = x.product_id WHERE x.code_id = ? "
            "ORDER BY p.sort_order, p.id",
            (code.id,),
        )
    ]
    return CodeStats(code=code, uses=uses, claims=int(claims), product_names=tuple(names))


def list_codes(conn: sqlite3.Connection, guild_id: int, now: int) -> tuple[list[CodeStats], list[CodeStats]]:
    """(the codes that can be used now, newest first; then the ``ENDED_LISTED`` codes that ended or expired most
    recently)."""
    rows = conn.execute("SELECT * FROM discount_codes WHERE guild_id = ? ORDER BY id DESC", (guild_id,)).fetchall()
    codes = [_code(conn, r) for r in rows]
    usable = [c for c in codes if c.usable(now)]
    over = sorted(
        (c for c in codes if not c.usable(now)),
        key=lambda c: (c.ended_at if c.ended_at is not None else c.expires_at or 0, c.id),
        reverse=True,
    )[:ENDED_LISTED]
    return [_stats(conn, c) for c in usable], [_stats(conn, c) for c in over]


def product_names(conn: sqlite3.Connection, guild_id: int, product_ids: Iterable[int]) -> dict[int, str]:
    """Names of this guild's products with these ids (archived ones too)."""
    ids = json.dumps(sorted(set(product_ids)))
    rows = conn.execute(
        "SELECT id, name FROM products WHERE guild_id = ? AND id IN (SELECT value FROM json_each(?))", (guild_id, ids)
    )
    return {int(r["id"]): str(r["name"]) for r in rows}


# -- writes ------------------------------------------------------------------------------------------------------


def create_code(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    code: str,
    kind: str,
    value: int,
    min_subtotal_minor: int,
    max_uses: int | None,
    once_per_customer: bool,
    expires_at: int | None,
    product_ids: Sequence[int],
    actor_id: int | None,
    now: int,
) -> d.Code:
    """A new code (``code`` normalized). Raises ``CodeTaken`` when a live code has this name and ``UnknownProducts``
    for product ids this guild does not have (archived products count: they cannot be bought anyway)."""
    if conn.execute(
        "SELECT 1 FROM discount_codes WHERE guild_id = ? AND code = ? AND ended_at IS NULL", (guild_id, code)
    ).fetchone():
        raise CodeTaken(code)
    known = product_names(conn, guild_id, product_ids)
    missing = sorted(set(product_ids) - set(known))
    if missing:
        raise UnknownProducts(missing)
    cursor = conn.execute(
        "INSERT INTO discount_codes(guild_id, code, kind, value, min_subtotal_minor, max_uses, once_per_customer, "
        "expires_at, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            code,
            kind,
            value,
            min_subtotal_minor,
            max_uses,
            int(once_per_customer),
            expires_at,
            actor_id,
            now,
            now,
        ),
    )
    code_id = int(cursor.lastrowid or 0)
    conn.executemany(
        "INSERT INTO discount_code_products(code_id, product_id) VALUES (?, ?)",
        [(code_id, product_id) for product_id in sorted(set(product_ids))],
    )
    created = get_code(conn, guild_id, code_id)
    assert created is not None
    return created


def end_code(conn: sqlite3.Connection, *, guild_id: int, code: str, actor_id: int | None, now: int) -> d.Code | None:
    """End the live code with this name (None when there is none). Orders that used it keep it; an unfinished order
    that applied it shows that it ended."""
    cur = conn.execute(
        "UPDATE discount_codes SET ended_at = ?, ended_by = ?, updated_at = ? WHERE guild_id = ? AND code = ? "
        "AND ended_at IS NULL",
        (now, actor_id, now, guild_id, code),
    )
    if cur.rowcount != 1:
        return None
    row = conn.execute(
        "SELECT * FROM discount_codes WHERE guild_id = ? AND code = ? AND ended_at = ? ORDER BY id DESC LIMIT 1",
        (guild_id, code, now),
    ).fetchone()
    return _code(conn, row)


def claimed(conn: sqlite3.Connection, code_id: int, user_id: int) -> bool:
    """Whether the customer claimed this code's coupon."""
    row = conn.execute("SELECT 1 FROM discount_claims WHERE code_id = ? AND user_id = ?", (code_id, user_id)).fetchone()
    return row is not None


def claim(conn: sqlite3.Connection, *, guild_id: int, code_id: int, user_id: int, now: int) -> bool:
    """Record the customer's claim of a coupon (once; a repeated claim keeps the first). Returns whether it is new."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO discount_claims(code_id, guild_id, user_id, claimed_at) VALUES (?, ?, ?, ?)",
        (code_id, guild_id, user_id, now),
    )
    return cur.rowcount == 1
