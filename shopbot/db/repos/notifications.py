"""Notification delivery records. ``dedupe_key`` guarantees a notification is delivered at most once."""

from __future__ import annotations

import sqlite3


def already_sent(conn: sqlite3.Connection, dedupe_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE dedupe_key = ? AND status IN ('sent', 'skipped')", (dedupe_key,)
    ).fetchone()
    return row is not None


def record(
    conn: sqlite3.Connection,
    *,
    dedupe_key: str,
    user_id: int | None,
    channel_id: int | None,
    kind: str,
    delivery: str,
    status: str,
    error: str | None,
    now: int,
) -> None:
    conn.execute(
        "INSERT INTO notifications(dedupe_key, user_id, channel_id, kind, delivery, status, error, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(dedupe_key) DO UPDATE SET "
        "channel_id = excluded.channel_id, delivery = excluded.delivery, status = excluded.status, "
        "error = excluded.error, created_at = excluded.created_at",
        (dedupe_key, user_id, channel_id, kind, delivery, status, None if error is None else error[:500], now),
    )
