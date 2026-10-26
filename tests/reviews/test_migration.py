"""Migration 0015 (reviews written with /review): the reviews table rebuilt with a nullable order, every row copied,
the id sequence kept, the new constraints, and the review_images table."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import pytest

from shopbot.db.migrate import apply_migrations, discover
from shopbot.features.reviews import images, repo

COLUMNS = (
    "id, guild_id, order_id, user_id, status, rating, comment, prompt_message_id, prompt_channel_id, "
    "published_message_id, approval, decided_by, decided_at, decision_reason, approval_channel_id, "
    "approval_message_id, created_at, updated_at"
)
# Realistic rows of a live shop: every review state, staff decisions, approval and published messages.
ROWS: list[dict[str, Any]] = [
    {"status": "published", "rating": 5, "comment": "Great", "published_message_id": 9001, "approval": "automatic"},
    {"status": "rated", "rating": 4, "comment": "", "approval": "automatic"},  # submitted, publication pending
    {"status": "rated", "rating": 2},  # comment form not sent yet
    {"status": "prompted", "prompt_message_id": 7001, "prompt_channel_id": 7000},
    {"status": "declined"},
    {
        "status": "pending_approval",
        "rating": 3,
        "comment": "Waiting",
        "approval_channel_id": 8000,
        "approval_message_id": 8001,
    },
    {"status": "pending_approval", "rating": 1, "comment": "Not posted yet"},  # approval message not posted yet
    {
        "status": "published",
        "rating": 5,
        "comment": "Approved",
        "published_message_id": 9002,
        "approval": "approved",
        "decided_by": 42,
        "decided_at": 100,
        "approval_channel_id": 8000,
        "approval_message_id": 8002,
    },
    {
        "status": "rejected",
        "rating": 1,
        "comment": "Spam",
        "approval": "rejected",
        "decided_by": 42,
        "decided_at": 101,
        "decision_reason": "Off topic",
        "approval_channel_id": 8000,
        "approval_message_id": 8003,
    },
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _order(conn: sqlite3.Connection, number: int, *, user_id: int = 5, status: str = "fulfilled") -> int:
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (1, ?, ?, ?, 'USD', 1, 1, 'manual', 0, 0)",
        (number, user_id, status),
    )
    return int(cursor.lastrowid or 0)


def _snapshot(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute(f"SELECT {COLUMNS} FROM reviews ORDER BY id")]


def _before(version: int) -> tuple[sqlite3.Connection, list[Any]]:
    """A database at ``version`` with the realistic rows (and a deleted newest review, so the sequence matters)."""
    migrations = discover()
    assert migrations[14].name == "reviews_command" and migrations[14].version == 15
    conn = _connect()
    apply_migrations(conn, migrations=migrations[:version])
    for number, row in enumerate(ROWS, start=1):
        values = {"guild_id": 1, "order_id": _order(conn, number), "user_id": 5, "created_at": number, **row}
        values["updated_at"] = number
        names = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO reviews({names}) VALUES ({marks})", tuple(values.values()))
    extra = _order(conn, 99)
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
        "VALUES (1, ?, 5, 'prompted', 0, 0)",
        (extra,),
    )
    conn.execute("DELETE FROM reviews WHERE order_id = ?", (extra,))  # id 10 was used once
    return conn, migrations


@pytest.mark.parametrize("version", [13, 14])
def test_every_review_is_copied_unchanged(version: int) -> None:
    conn, migrations = _before(version)
    before = _snapshot(conn)
    applied = apply_migrations(conn, migrations=migrations)
    assert 15 in applied  # later migrations may follow
    assert _snapshot(conn) == before
    assert {r["source"] for r in conn.execute("SELECT source FROM reviews")} == {repo.SOURCE_PROMPT}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_reviews_guild_status", "idx_reviews_unverified"} <= indexes
    # The repository reads the migrated rows exactly as before.
    assert repo.submitted_review_ids(conn, 1) == [2]
    assert repo.pending_approvals(conn, 1) == [(6, 8001), (7, None)]
    rejected = repo.get_review(conn, 9)
    assert rejected is not None and rejected.decision_reason == "Off topic" and rejected.order_number == 9
    assert rejected.verified and rejected.source == repo.SOURCE_PROMPT
    # Ids continue after every id ever used (10 was deleted).
    cursor = conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, source, created_at, updated_at) "
        "VALUES (1, NULL, 5, 'pending_approval', 4, 'Back again', 'command', 0, 0)"
    )
    assert cursor.lastrowid == 11


def _migrated() -> sqlite3.Connection:
    conn = _connect()
    apply_migrations(conn)
    return conn


def test_reviews_without_an_order() -> None:
    conn = _migrated()
    insert = (
        "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, approval, source, created_at, "
        "updated_at) VALUES (1, ?, 5, ?, 3, 'c', ?, ?, 0, 0)"
    )
    conn.execute(insert, (None, "pending_approval", None, "command"))
    conn.execute(insert, (None, "pending_approval", None, "command"))  # several reviews without an order
    for status, approval, source in (
        ("pending_approval", None, "prompt"),  # only /review writes reviews without an order
        ("prompted", None, "command"),  # never a review request
        ("rated", "automatic", "command"),  # never approved automatically
        ("pending_approval", None, "other"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (None, status, approval, source))
    order_id = _order(conn, 1)
    conn.execute(insert, (order_id, "rated", "automatic", "command"))  # a verified /review
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (order_id, "rated", "automatic", "prompt"))  # one review per order, as before


def test_review_images() -> None:
    conn = _migrated()
    conn.execute(
        "INSERT INTO reviews(guild_id, order_id, user_id, status, rating, comment, source, created_at, updated_at) "
        "VALUES (1, NULL, 5, 'pending_approval', 4, 'c', 'command', 0, 0)"
    )
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    picture = images.Picture(content_type="image/png", data=data, sha256=hashlib.sha256(data).hexdigest())
    images.store_image(conn, 1, guild_id=1, picture=picture, now=5)
    stored = images.get_image(conn, 1)
    assert stored is not None and stored.data == data and not stored.released
    assert images.release_image(conn, 1, now=6) is False  # still waiting for staff: kept
    conn.execute("UPDATE reviews SET status = 'declined' WHERE id = 1")
    assert images.release_image(conn, 1, now=7) is True
    released = images.get_image(conn, 1)
    assert released is not None and released.released and released.data is None and released.size == len(data)
    for update in (
        "UPDATE review_images SET released_at = NULL",  # no bytes without a release time
        "UPDATE review_images SET data = x'00', released_at = NULL",  # bytes must match the size
        "UPDATE review_images SET content_type = 'image/svg+xml'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(update)
    with pytest.raises(sqlite3.IntegrityError):
        images.store_image(conn, 99, guild_id=1, picture=picture, now=8)  # no such review
