"""Migration 0014: the tip of an unfinished order (checkout_sessions.tip_minor) and of an order (orders.tip_minor, its
part of fees_minor). A database at 0013 with realistic rows (orders with extras, a discount code, store credit, refunds,
gift cards; sessions with a discount code) keeps every row unchanged; every existing row gets no tip, and the new CHECKs
hold for new rows (a tip is never negative and never more than the order's fees, which include it)."""

from __future__ import annotations

import sqlite3

import pytest

from shopbot.db.migrate import Migration, apply_migrations, discover

TABLES = (
    "orders",
    "order_items",
    "order_fees",
    "order_events",
    "payments",
    "credit_ledger",
    "gift_cards",
    "checkout_sessions",
    "discount_codes",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrations(*, with_tips: bool) -> list[Migration]:
    """The migrations up to 0013 (``with_tips``: and 0014)."""
    migrations = discover()
    position = [m.name for m in migrations].index("tips")
    return migrations[: position + 1 if with_tips else position]


def _order(
    conn: sqlite3.Connection,
    number: int,
    status: str,
    *,
    subtotal: int,
    fee: int = 0,
    fees: int = 0,
    discount: int = 0,
    credits: int = 0,
    refunded: int = 0,
    paid_at: int | None = None,
    code_id: int | None = None,
    session_id: int | None = None,
) -> int:
    total = subtotal + fee + fees - discount - credits
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, session_id, ticket_id, status, currency, subtotal_minor, "
        "fulfillment_fee_minor, fees_minor, credits_applied_minor, total_minor, refunded_minor, fulfillment_option, "
        "details, payment_provider, version, created_at, updated_at, paid_at, discount_code_id, discount_code, "
        "discount_kind, discount_minor) VALUES (1, ?, 5, ?, 1, ?, 'USD', ?, ?, ?, ?, ?, ?, 'standard', "
        "'{\"a.country\": \"US\"}', 'gift_card', 2, ?, ?, ?, ?, ?, ?, ?)",
        (
            number,
            session_id,
            status,
            subtotal,
            fee,
            fees,
            credits,
            total,
            refunded,
            number,
            number,
            paid_at,
            code_id,
            "SAVE10" if code_id else None,
            "percent" if code_id else None,
            discount,
        ),
    )
    order_id = int(cursor.lastrowid or 0)
    conn.execute(
        "INSERT INTO order_items(order_id, product_id, name_snapshot, unit_price_minor, quantity, line_total_minor) "
        "VALUES (?, 1, 'p', ?, 1, ?)",
        (order_id, subtotal, subtotal),
    )
    if fees:
        conn.execute(
            "INSERT INTO order_fees(order_id, fee_id, amount_minor) VALUES (?, 'x.protection', ?)", (order_id, fees)
        )
    conn.execute(
        "INSERT INTO order_events(order_id, from_status, to_status, actor_id, created_at) VALUES (?, NULL, ?, 5, ?)",
        (order_id, status, number),
    )
    conn.execute(
        "INSERT INTO payments(order_id, provider, status, amount_minor, currency, created_at, updated_at) "
        "VALUES (?, 'gift_card', ?, ?, 'USD', 0, 0)",
        (order_id, "succeeded" if paid_at else "pending", total),
    )
    return order_id


def _at_0013(conn: sqlite3.Connection) -> dict[str, int]:
    apply_migrations(conn, migrations=_migrations(with_tips=False))
    conn.execute(
        "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
        "updated_at, last_activity_at) VALUES (1, 7, 'order', 'orders', 5, 900, 'open', 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO products(guild_id, name, price_minor, created_at, updated_at) VALUES (1, 'p', 1000, 0, 0)"
    )
    code_id = int(
        conn.execute(
            "INSERT INTO discount_codes(guild_id, code, kind, value, created_at, updated_at) "
            "VALUES (1, 'SAVE10', 'percent', 1000, 0, 0)"
        ).lastrowid
        or 0
    )
    # A submitted session (its order below) and an unfinished one with the code applied, on the review step.
    for status, step in (("submitted", "done"), ("active", "review")):
        conn.execute(
            "INSERT INTO checkout_sessions(guild_id, user_id, ticket_id, status, step, details, apply_credits, "
            "created_at, updated_at, discount_code_id) VALUES (1, 5, 1, ?, ?, '{}', 1, 0, 0, ?)",
            (status, step, code_id),
        )
    ids = {
        # Extras, a discount code and store credit, paid, then refunded in full.
        "refunded": _order(
            conn,
            1,
            "refunded",
            subtotal=3000,
            fee=500,
            fees=300,
            discount=300,
            credits=800,
            refunded=2700,
            paid_at=10,
            code_id=code_id,
            session_id=1,
        ),
        "shipped": _order(conn, 2, "shipped", subtotal=2000, fee=1500, fees=500, paid_at=20),
        "cancelled": _order(conn, 3, "cancelled", subtotal=1000, credits=200),
        "pending": _order(conn, 4, "pending_payment", subtotal=4500, fee=500, discount=450, code_id=code_id),
    }
    conn.execute("UPDATE checkout_sessions SET order_id = ? WHERE id = 1", (ids["refunded"],))
    for delta, kind, order_id in (
        (500, "admin_adjust", None),
        (-800, "order_debit", ids["refunded"]),
        (800, "order_return", ids["refunded"]),
        (-200, "order_debit", ids["cancelled"]),
        (200, "order_return", ids["cancelled"]),
    ):
        conn.execute(
            "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, created_at) "
            "VALUES (1, 5, ?, ?, ?, 0)",
            (delta, kind, order_id),
        )
    conn.execute(
        "INSERT INTO gift_cards(guild_id, order_id, user_id, amount_minor, fingerprint, sealed, last4, status, "
        "submitted_at, decided_by, decided_at) VALUES (1, ?, 5, 4000, ?, NULL, 'AB12', 'redeemed', 15, 9, 19)",
        (ids["shipped"], "f" * 64),
    )
    return ids


def _columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {table: [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] for table in TABLES}


def _snapshot(conn: sqlite3.Connection, columns: dict[str, list[str]]) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT {', '.join(columns[table])} FROM {table} ORDER BY rowid")]
        for table in TABLES
    }


def test_every_row_is_kept_and_has_no_tip() -> None:
    conn = _connect()
    _at_0013(conn)
    columns = _columns(conn)
    before = _snapshot(conn, columns)
    assert all(before[table] for table in TABLES)
    apply_migrations(conn, migrations=_migrations(with_tips=True))
    assert _snapshot(conn, columns) == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [r[0] for r in conn.execute("SELECT tip_minor FROM orders ORDER BY id")] == [0, 0, 0, 0]
    assert [r[0] for r in conn.execute("SELECT tip_minor FROM checkout_sessions ORDER BY id")] == [0, 0]
    added = {r[1]: r for r in conn.execute("PRAGMA table_info(orders)")}["tip_minor"]
    assert (added["notnull"], added["dflt_value"]) == (1, "0")


def test_the_tip_is_part_of_the_fees_and_never_negative() -> None:
    conn = _connect()
    ids = _at_0013(conn)
    apply_migrations(conn, migrations=_migrations(with_tips=True))

    def insert(number: int, *, fees: int, tip: int, total: int) -> None:
        conn.execute(
            "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, fulfillment_fee_minor, "
            "fees_minor, total_minor, payment_provider, created_at, updated_at, tip_minor) "
            "VALUES (1, ?, 5, 'pending_payment', 'USD', 2000, 500, ?, ?, 'gift_card', 0, 0, ?)",
            (number, fees, total, tip),
        )

    insert(10, fees=800, tip=500, total=3300)  # $3 of extras + a $5 tip, inside the fees
    insert(11, fees=500, tip=500, total=3000)  # a tip alone
    with pytest.raises(sqlite3.IntegrityError):
        insert(12, fees=300, tip=500, total=2800)  # more tip than fees: the tip is not counted in the total
    with pytest.raises(sqlite3.IntegrityError):
        insert(13, fees=800, tip=500, total=2800)  # the 0013 total CHECK still holds (the tip is in the fees)
    with pytest.raises(sqlite3.IntegrityError):
        insert(14, fees=0, tip=-100, total=2400)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE orders SET tip_minor = 301 WHERE id = ?", (ids["refunded"],))  # its fees are 300
    conn.execute("UPDATE orders SET tip_minor = 300 WHERE id = ?", (ids["refunded"],))
    conn.execute("UPDATE checkout_sessions SET tip_minor = 750 WHERE id = 2")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE checkout_sessions SET tip_minor = -1 WHERE id = 2")
