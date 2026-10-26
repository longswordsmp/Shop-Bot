"""Migration 0013: discount codes, their products and claims, the session's applied code, and the orders table rebuilt
with the order's discount. A database at 0012 with realistic orders (paid with store credit, refunded, paid with gift
cards, cancelled) keeps every row and every reference unchanged; the old totals satisfy the new CHECK, which is
enforced for new rows."""

from __future__ import annotations

import sqlite3

import pytest

from shopbot.db.migrate import apply_migrations, discover

NEW_ORDER_COLUMNS = ("discount_code_id", "discount_code", "discount_kind", "discount_minor")
# Every table that refers to an order (and the order itself), compared row by row before and after.
TABLES = (
    "orders",
    "order_items",
    "order_fees",
    "order_events",
    "order_adjustments",
    "inventory_reservations",
    "payments",
    "credit_ledger",
    "gift_cards",
    "order_handling",
    "order_activity",
    "reviews",
    "problem_reports",
    "checkout_sessions",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _order(
    conn: sqlite3.Connection,
    number: int,
    status: str,
    *,
    subtotal: int,
    fee: int = 0,
    fees: int = 0,
    credits: int = 0,
    refunded: int = 0,
    paid_at: int | None = None,
    provider: str = "manual",
    session_id: int | None = None,
) -> int:
    total = subtotal + fee + fees - credits
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, session_id, ticket_id, status, currency, subtotal_minor, "
        "fulfillment_fee_minor, fees_minor, credits_applied_minor, total_minor, refunded_minor, fulfillment_option, "
        "details, payment_provider, version, created_at, updated_at, paid_at) "
        "VALUES (1, ?, 5, ?, 1, ?, 'USD', ?, ?, ?, ?, ?, ?, 'standard', '{\"a.country\": \"US\"}', ?, 3, ?, ?, ?)",
        (number, session_id, status, subtotal, fee, fees, credits, total, refunded, provider, number, number, paid_at),
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
        "INSERT INTO inventory_reservations(order_id, product_id, quantity, status, created_at, updated_at) "
        "VALUES (?, 1, 1, ?, 0, 0)",
        (order_id, "committed" if paid_at else "released"),
    )
    conn.execute(
        "INSERT INTO payments(order_id, provider, status, amount_minor, currency, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'USD', 0, 0)",
        (order_id, provider, "succeeded" if paid_at else "cancelled", total),
    )
    return order_id


def _before(conn: sqlite3.Connection) -> dict[str, int]:
    migrations = discover()
    position = [m.name for m in migrations].index("discounts")
    apply_migrations(conn, migrations=migrations[:position])
    conn.execute(
        "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
        "updated_at, last_activity_at) VALUES (1, 7, 'order', 'orders', 5, 900, 'open', 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
        "updated_at, last_activity_at) VALUES (1, 8, 'support', 'problem-report', 5, 901, 'open', 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO products(guild_id, name, price_minor, created_at, updated_at) VALUES (1, 'p', 1000, 0, 0)"
    )
    conn.execute(
        "INSERT INTO checkout_sessions(guild_id, user_id, ticket_id, status, step, details, apply_credits, "
        "created_at, updated_at) VALUES (1, 5, 1, 'submitted', 'done', '{}', 1, 0, 0)"
    )
    ids = {
        # Paid with store credit, then refunded in part and the credit given back.
        "credit": _order(
            conn, 1, "refunded", subtotal=3000, fee=500, fees=300, credits=800, refunded=3000, paid_at=10, session_id=1
        ),
        "gift": _order(conn, 2, "shipped", subtotal=2000, fee=1500, paid_at=20, provider="gift_card"),
        "cancelled": _order(conn, 3, "cancelled", subtotal=1000, credits=200),
        "pending": _order(conn, 4, "pending_payment", subtotal=4500, fee=500),
    }
    conn.execute("UPDATE checkout_sessions SET order_id = ? WHERE id = 1", (ids["credit"],))
    ledger = [
        (500, "admin_adjust", None),
        (-800, "order_debit", ids["credit"]),
        (800, "order_return", ids["credit"]),
        (1000, "refund_to_credit", ids["credit"]),
        (-200, "order_debit", ids["cancelled"]),
        (200, "order_return", ids["cancelled"]),
        (250, "gift_card", ids["gift"]),
    ]
    for delta, kind, order_id in ledger:
        conn.execute(
            "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, created_at) "
            "VALUES (1, 5, ?, ?, ?, 0)",
            (delta, kind, order_id),
        )
    conn.execute(
        "INSERT INTO order_adjustments(order_id, kind, method, amount_minor, reason, actor_id, created_at) "
        "VALUES (?, 'refund', 'manual', 2000, 'damaged', 9, 30)",
        (ids["credit"],),
    )
    conn.execute(
        "INSERT INTO order_adjustments(order_id, kind, method, amount_minor, actor_id, created_at) "
        "VALUES (?, 'credit_return', 'credits', 800, 9, 31)",
        (ids["credit"],),
    )
    conn.execute(
        "INSERT INTO gift_cards(guild_id, order_id, user_id, amount_minor, fingerprint, sealed, last4, status, "
        "submitted_at, decided_by, decided_at) VALUES (1, ?, 5, 3750, ?, NULL, 'AB12', 'redeemed', 15, 9, 19)",
        (ids["gift"], "f" * 64),
    )
    conn.execute(
        "INSERT INTO order_handling(order_id, claimed_by, claimed_at, tracking, tracking_at, updated_at, carrier, "
        "tracking_number, shipped_at) VALUES (?, 9, 21, 'UPS 1Z', 22, 22, 'UPS', '1Z999', 22)",
        (ids["gift"],),
    )
    conn.execute(
        "INSERT INTO order_activity(order_id, kind, actor_id, text, created_at) VALUES (?, 'note', 9, 'fragile', 23)",
        (ids["gift"],),
    )
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
        "VALUES (1, ?, 5, 'prompted', 0, 0)",
        (ids["credit"],),
    )
    conn.execute(
        "INSERT INTO problem_reports(guild_id, user_id, nonce, order_id, issue_type, ticket_id, created_at, "
        "updated_at) VALUES (1, 5, 'nonce-0001', ?, 'damaged', 2, 0, 0)",
        (ids["gift"],),
    )
    return ids


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    out: dict[str, list[tuple[object, ...]]] = {}
    for table in TABLES:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        kept = [c for c in columns if c not in (*NEW_ORDER_COLUMNS, "discount_code_id")]
        out[table] = [tuple(r) for r in conn.execute(f"SELECT {', '.join(kept)} FROM {table} ORDER BY rowid")]
    return out


def test_every_order_and_every_row_that_refers_to_one_is_kept() -> None:
    conn = _connect()
    ids = _before(conn)
    before = _snapshot(conn)
    assert all(before[table] for table in TABLES)
    apply_migrations(conn, migrations=discover())
    assert _snapshot(conn) == before  # nothing lost, nothing changed, nothing cascaded
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # Existing orders used no code; their totals satisfy the new CHECK (it now subtracts the discount).
    rows = conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
    assert [(r["discount_code_id"], r["discount_code"], r["discount_kind"], r["discount_minor"]) for r in rows] == [
        (None, None, None, 0)
    ] * 4
    for r in rows:
        assert r["total_minor"] == (
            r["subtotal_minor"]
            + r["fulfillment_fee_minor"]
            + r["fees_minor"]
            - r["discount_minor"]
            - r["credits_applied_minor"]
        )
    assert conn.execute("SELECT discount_code_id FROM checkout_sessions").fetchone()[0] is None
    # References are enforced against the rebuilt table, and deleting an order still cascades to its own rows.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO order_items(order_id, product_id, name_snapshot, unit_price_minor, quantity, "
            "line_total_minor) VALUES (999, 1, 'p', 1, 1, 1)"
        )
    conn.execute("DELETE FROM credit_ledger WHERE order_id = ?", (ids["pending"],))
    conn.execute("DELETE FROM orders WHERE id = ?", (ids["pending"],))
    assert conn.execute("SELECT COUNT(*) FROM order_items WHERE order_id = ?", (ids["pending"],)).fetchone()[0] == 0
    # New orders continue after the old ids (AUTOINCREMENT kept its sequence).
    new_id = _order(conn, 5, "pending_payment", subtotal=1000)
    assert new_id > max(ids.values())
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(orders)")}
    assert {"idx_orders_user", "idx_orders_status", "idx_orders_discount"} <= indexes


@pytest.mark.parametrize("deleted", ["newest", "all"])
def test_order_ids_are_never_reused_after_the_rebuild(deleted: str) -> None:
    # The newest orders (or all of them, as /reset wipe_shop_data does) were deleted at 0012: the rebuild keeps the
    # id sequence, so a new order does not take an id that audit entries and kept records may still refer to.
    conn = _connect()
    ids = _before(conn)
    gone = [ids["cancelled"], ids["pending"]] if deleted == "newest" else list(ids.values())
    conn.execute("UPDATE checkout_sessions SET order_id = NULL")
    for order_id in gone:
        for table in ("credit_ledger", "gift_cards", "reviews", "problem_reports", "order_adjustments"):
            conn.execute(f"DELETE FROM {table} WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    apply_migrations(conn, migrations=discover())
    assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'orders'").fetchone()[0] == max(ids.values())
    assert conn.execute("SELECT COUNT(*) FROM sqlite_sequence WHERE name = 'orders_v3'").fetchone()[0] == 0
    assert _order(conn, 9, "pending_payment", subtotal=1000) == max(ids.values()) + 1


def test_a_database_that_never_had_an_order_starts_at_one() -> None:
    conn = _connect()
    migrations = discover()
    apply_migrations(conn, migrations=migrations[: [m.name for m in migrations].index("discounts")])
    apply_migrations(conn, migrations=migrations)
    conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (1, 1, 5, 'pending_payment', 'USD', 0, 0, 'manual', 0, 0)"
    )
    assert conn.execute("SELECT MAX(id) FROM orders").fetchone()[0] == 1


def _code(conn: sqlite3.Connection, code: str = "SAVE10", kind: str = "percent", value: int = 1000) -> int:
    cursor = conn.execute(
        "INSERT INTO discount_codes(guild_id, code, kind, value, created_at, updated_at) VALUES (1, ?, ?, ?, 0, 0)",
        (code, kind, value),
    )
    return int(cursor.lastrowid or 0)


def test_an_order_with_a_discount_and_the_new_check() -> None:
    conn = _connect()
    _before(conn)
    apply_migrations(conn, migrations=discover())
    code_id = _code(conn)

    def insert(number: int, subtotal: int, discount: int, total: int, code: str | None = "SAVE10") -> None:
        conn.execute(
            "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, fulfillment_fee_minor, "
            "total_minor, payment_provider, created_at, updated_at, discount_code_id, discount_code, discount_kind, "
            "discount_minor) VALUES (1, ?, 5, 'pending_payment', 'USD', ?, 500, ?, 'manual', 0, 0, ?, ?, ?, ?)",
            (number, subtotal, total, code_id if code else None, code, "percent" if code else None, discount),
        )

    insert(10, 2000, 200, 2300)  # 2000 + 500 - 200
    with pytest.raises(sqlite3.IntegrityError):
        insert(11, 2000, 200, 2500)  # the total ignores the discount
    with pytest.raises(sqlite3.IntegrityError):
        insert(12, 2000, 2001, 499)  # more than the subtotal
    with pytest.raises(sqlite3.IntegrityError):
        insert(13, 2000, 200, 2300, code=None)  # a discount without its code
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE orders SET discount_kind = 'free' WHERE number = 10")
    # A code that orders used cannot disappear from under them.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM discount_codes WHERE id = ?", (code_id,))


def test_codes_are_checked_and_unique_while_live() -> None:
    conn = _connect()
    _before(conn)
    apply_migrations(conn, migrations=discover())
    first = _code(conn, "SAVE10")
    with pytest.raises(sqlite3.IntegrityError):
        _code(conn, "SAVE10")  # one live code per name
    conn.execute("UPDATE discount_codes SET ended_at = 5, ended_by = 9 WHERE id = ?", (first,))
    assert _code(conn, "SAVE10") > first  # the name can be used again once the old code ended
    for code, kind, value in (
        ("save10", "percent", 1000),  # not normalized
        ("AB", "percent", 1000),  # too short
        ("SAVE 10", "percent", 1000),
        ("PCT0", "percent", 99),  # below 1 %
        ("PCT200", "percent", 10_001),  # above 100 %
        ("AMT0", "amount", 0),
        ("BOGO1", "bogo", 1),  # buy one get one free has no value
        ("OTHER", "free_shipping", 0),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            _code(conn, code, kind, value)
    bogo = _code(conn, "BOGO", "bogo", 0)
    conn.execute("INSERT INTO discount_code_products(code_id, product_id) VALUES (?, 1)", (bogo,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO discount_code_products(code_id, product_id) VALUES (?, 99)", (bogo,))
    conn.execute("INSERT INTO discount_claims(code_id, guild_id, user_id, claimed_at) VALUES (?, 1, 5, 0)", (bogo,))
    with pytest.raises(sqlite3.IntegrityError):  # one claim per customer
        conn.execute("INSERT INTO discount_claims(code_id, guild_id, user_id, claimed_at) VALUES (?, 1, 5, 1)", (bogo,))
    conn.execute("UPDATE checkout_sessions SET discount_code_id = ? WHERE id = 1", (bogo,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE checkout_sessions SET discount_code_id = 999 WHERE id = 1")
