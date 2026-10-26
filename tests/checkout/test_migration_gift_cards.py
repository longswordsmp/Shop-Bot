"""Migration 0012: the gift_cards table, and the credit ledger rebuilt with the 'gift_card' kind. Every ledger row
survives unchanged (same ids, new rows continue after them), the per-order uniqueness still holds, and a decided card
can never keep its code."""

from __future__ import annotations

import sqlite3

import pytest

from shopbot.db.migrate import apply_migrations, discover


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _before(conn: sqlite3.Connection) -> None:
    migrations = discover()
    position = [m.name for m in migrations].index("gift_cards")
    apply_migrations(conn, migrations=migrations[:position])
    conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, credits_applied_minor, "
        "total_minor, payment_provider, created_at, updated_at) "
        "VALUES (1, 1, 5, 'pending_payment', 'USD', 1000, 100, 900, 'manual', 0, 0)"
    )
    rows = [
        (5, 300, "admin_adjust", None, "welcome"),
        (5, -100, "order_debit", 1, None),
        (5, 100, "order_return", 1, None),
    ]
    for user_id, delta, kind, order_id, note in rows:
        conn.execute(
            "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, note, created_at) "
            "VALUES (1, ?, ?, ?, ?, ?, 0)",
            (user_id, delta, kind, order_id, note),
        )


def _ledger(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [tuple(r) for r in conn.execute("SELECT * FROM credit_ledger ORDER BY id")]


def test_the_ledger_keeps_every_row_and_accepts_gift_card_credit_once_per_order() -> None:
    conn = _connect()
    _before(conn)
    before = _ledger(conn)
    apply_migrations(conn, migrations=discover())
    assert _ledger(conn) == before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute(
        "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, created_at) "
        "VALUES (1, 5, 250, 'gift_card', 1, 1)"
    )
    assert conn.execute("SELECT MAX(id) FROM credit_ledger").fetchone()[0] == len(before) + 1
    with pytest.raises(sqlite3.IntegrityError):  # once per order
        conn.execute(
            "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, created_at) "
            "VALUES (1, 5, 250, 'gift_card', 1, 2)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # a second order debit is still refused
        conn.execute(
            "INSERT INTO credit_ledger(guild_id, user_id, delta_minor, kind, order_id, created_at) "
            "VALUES (1, 5, -100, 'order_debit', 1, 2)"
        )
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(credit_ledger)")}
    assert {"idx_credit_ledger_user", "uq_credit_ledger_order_kind"} <= indexes


def test_a_card_is_accepted_once_and_a_decided_card_keeps_no_code() -> None:
    conn = _connect()
    _before(conn)
    apply_migrations(conn, migrations=discover())

    def add(fingerprint: str, status: str = "submitted", sealed: bytes | None = b"token") -> None:
        conn.execute(
            "INSERT INTO gift_cards(guild_id, order_id, user_id, amount_minor, fingerprint, sealed, last4, status, "
            "submitted_at) VALUES (1, 1, 5, 500, ?, ?, '1234', ?, 0)",
            (fingerprint, sealed, status),
        )

    add("a" * 64)
    with pytest.raises(sqlite3.IntegrityError):  # the same card while it is waiting
        add("a" * 64)
    with pytest.raises(sqlite3.IntegrityError):  # a decided card that still holds its code
        add("b" * 64, status="redeemed")
    add("c" * 64, status="refused", sealed=None)
    add("c" * 64)  # a refused card may be submitted again
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE gift_cards SET status = 'redeemed' WHERE fingerprint = ?", ("a" * 64,))
    conn.execute("UPDATE gift_cards SET status = 'redeemed', sealed = NULL WHERE fingerprint = ?", ("a" * 64,))
