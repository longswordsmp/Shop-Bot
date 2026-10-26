"""Migration 0011: the orders table is rebuilt with the 'payment_submitted' and 'shipped' statuses. Every order and
every row referring to one (items, events, payments, handling, reviews) survives unchanged, nothing cascades."""

from __future__ import annotations

import sqlite3

import pytest

from shopbot.db.migrate import FOREIGN_KEYS_OFF, apply_migrations, discover


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _before(conn: sqlite3.Connection) -> None:
    migrations = discover()
    position = [m.name for m in migrations].index("order_shipping_statuses")
    apply_migrations(conn, migrations=migrations[:position])
    conn.execute(
        "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
        "updated_at, last_activity_at) VALUES (1, 7, 'order', 'orders', 5, 900, 'open', 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO products(guild_id, name, price_minor, created_at, updated_at) VALUES (1, 'p', 1000, 0, 0)"
    )
    for number, status in ((1, "pending_payment"), (2, "paid"), (3, "fulfilled")):
        conn.execute(
            "INSERT INTO orders(guild_id, number, user_id, ticket_id, status, currency, subtotal_minor, total_minor, "
            "payment_provider, created_at, updated_at) VALUES (1, ?, 5, 1, ?, 'USD', 1000, 1000, 'manual', 0, 0)",
            (number, status),
        )
        order_id = conn.execute("SELECT id FROM orders WHERE number = ?", (number,)).fetchone()[0]
        conn.execute(
            "INSERT INTO order_items(order_id, product_id, name_snapshot, unit_price_minor, quantity, "
            "line_total_minor) VALUES (?, 1, 'p', 1000, 1, 1000)",
            (order_id,),
        )
        conn.execute("INSERT INTO order_events(order_id, to_status, created_at) VALUES (?, ?, 0)", (order_id, status))
        conn.execute(
            "INSERT INTO order_handling(order_id, tracking, tracking_at, updated_at) VALUES (?, 'UPS 1Z', 1, 1)",
            (order_id,),
        )
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
        "VALUES (1, 3, 5, 'prompted', 0, 0)"
    )


TABLES = ("orders", "order_items", "order_events", "order_handling", "reviews")


def _columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """The columns of each table now: the snapshot after the migrations compares only these (this migration and later
    ones add columns, e.g. the shipment, the discount code and the tip of an order)."""
    return {table: [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] for table in TABLES}


def _snapshot(conn: sqlite3.Connection, columns: dict[str, list[str]]) -> dict[str, list[tuple[object, ...]]]:
    out: dict[str, list[tuple[object, ...]]] = {}
    for table in TABLES:
        rows = conn.execute(f"SELECT {', '.join(columns[table])} FROM {table} ORDER BY rowid")
        out[table] = [tuple(r) for r in rows]
    return out


def test_the_rebuild_keeps_every_order_and_every_row_that_refers_to_one() -> None:
    conn = _connect()
    _before(conn)
    columns = _columns(conn)
    before = _snapshot(conn, columns)
    assert all(before[table] for table in TABLES)
    apply_migrations(conn, migrations=discover())
    assert _snapshot(conn, columns) == before  # nothing lost, nothing changed, nothing cascaded
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on
    # The new statuses are accepted, an unknown one still is not.
    conn.execute("UPDATE orders SET status = 'payment_submitted' WHERE number = 1")
    conn.execute("UPDATE orders SET status = 'shipped' WHERE number = 2")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE orders SET status = 'lost' WHERE number = 1")
    # References are enforced against the new table (an order item of an order that does not exist is refused).
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO order_items(order_id, product_id, name_snapshot, unit_price_minor, quantity, "
            "line_total_minor) VALUES (999, 1, 'p', 1, 1, 1)"
        )
    # Deleting an order still cascades to its items (the references point at the rebuilt table).
    conn.execute("DELETE FROM reviews")
    conn.execute("DELETE FROM orders WHERE number = 1")
    assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 2
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(orders)")}
    assert {"idx_orders_user", "idx_orders_status"} <= indexes


def test_the_shipment_columns_are_checked() -> None:
    conn = _connect()
    _before(conn)
    apply_migrations(conn, migrations=discover())
    conn.execute(
        "UPDATE order_handling SET carrier = 'UPS', tracking_number = '1Z999', tracking_url = 'https://ups.com/t', "
        "shipped_at = 5 WHERE order_id = 2"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE order_handling SET tracking_url = 'http://insecure.example' WHERE order_id = 2")


def test_only_marked_migrations_run_with_foreign_keys_off() -> None:
    marked = [m.name for m in discover() if FOREIGN_KEYS_OFF in m.path.read_text(encoding="utf-8").splitlines()[:5]]
    # 0015 rebuilds the reviews table (/review writes reviews without an order); 0014 (tips) only adds columns.
    assert marked == ["order_shipping_statuses", "gift_cards", "discounts", "reviews_command"]
