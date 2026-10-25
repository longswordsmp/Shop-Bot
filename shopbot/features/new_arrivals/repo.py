"""Announcement records: synchronous ``fn(conn, ...)`` functions over ``new_arrival_announcements``.

One row per qualifying product event, unique per (guild, product, event, product version):

- ``claim`` is called before an update is posted. It returns ``"fresh"`` for a new claim, ``"posted"`` when the
  update was already posted (nothing to do), or ``"stale"`` when an earlier attempt claimed it and never recorded a
  message (the process stopped, or Discord's answer was lost): the caller then looks for that earlier post before
  posting again.
- ``record_posted`` stores the message once it was posted (only for a claimed row, so it applies once).
- ``release`` forgets a claim whose post certainly did not happen (e.g. Discord refused it), so a retry is fresh.

Write functions must run inside ``app.db.transaction``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

ClaimResult = Literal["fresh", "stale", "posted"]


@dataclass(frozen=True)
class Announcement:
    id: int
    guild_id: int
    product_id: int
    event: str
    product_version: int
    status: str
    channel_id: int | None
    message_id: int | None
    mentioned: bool
    created_at: int
    updated_at: int

    @property
    def posted(self) -> bool:
        return self.status == "posted"


def _row(r: sqlite3.Row) -> Announcement:
    return Announcement(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        product_id=int(r["product_id"]),
        event=str(r["event"]),
        product_version=int(r["product_version"]),
        status=str(r["status"]),
        channel_id=None if r["channel_id"] is None else int(r["channel_id"]),
        message_id=None if r["message_id"] is None else int(r["message_id"]),
        mentioned=bool(r["mentioned"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
    )


def get(conn: sqlite3.Connection, guild_id: int, product_id: int, event: str, version: int) -> Announcement | None:
    row = conn.execute(
        "SELECT * FROM new_arrival_announcements WHERE guild_id = ? AND product_id = ? AND event = ? "
        "AND product_version = ?",
        (guild_id, product_id, event, version),
    ).fetchone()
    return None if row is None else _row(row)


def list_for_product(conn: sqlite3.Connection, guild_id: int, product_id: int) -> list[Announcement]:
    rows = conn.execute(
        "SELECT * FROM new_arrival_announcements WHERE guild_id = ? AND product_id = ? ORDER BY id",
        (guild_id, product_id),
    )
    return [_row(r) for r in rows]


def claim(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    product_id: int,
    event: str,
    version: int,
    channel_id: int,
    now: int,
) -> tuple[ClaimResult, Announcement]:
    existing = get(conn, guild_id, product_id, event, version)
    if existing is not None:
        if existing.posted:
            return "posted", existing
        conn.execute(
            "UPDATE new_arrival_announcements SET channel_id = ?, updated_at = ? WHERE id = ? AND status = 'claimed'",
            (channel_id, now, existing.id),
        )
        refreshed = get(conn, guild_id, product_id, event, version)
        assert refreshed is not None
        return "stale", refreshed
    conn.execute(
        "INSERT INTO new_arrival_announcements(guild_id, product_id, event, product_version, status, channel_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'claimed', ?, ?, ?)",
        (guild_id, product_id, event, version, channel_id, now, now),
    )
    created = get(conn, guild_id, product_id, event, version)
    assert created is not None
    return "fresh", created


def record_posted(
    conn: sqlite3.Connection,
    announcement_id: int,
    *,
    channel_id: int,
    message_id: int,
    mentioned: bool,
    now: int,
) -> bool:
    """Mark a claimed announcement as posted with its message. False if it was not claimed (already recorded)."""
    cursor = conn.execute(
        "UPDATE new_arrival_announcements SET status = 'posted', channel_id = ?, message_id = ?, mentioned = ?, "
        "updated_at = ? WHERE id = ? AND status = 'claimed'",
        (channel_id, message_id, int(mentioned), now, announcement_id),
    )
    return cursor.rowcount == 1


def release(conn: sqlite3.Connection, announcement_id: int) -> bool:
    """Forget a claim whose update was certainly not posted (only claimed rows; a posted row is never removed)."""
    cursor = conn.execute(
        "DELETE FROM new_arrival_announcements WHERE id = ? AND status = 'claimed'", (announcement_id,)
    )
    return cursor.rowcount == 1


def recorded_message_ids(conn: sqlite3.Connection, guild_id: int, channel_id: int) -> set[int]:
    """Message ids of the updates already recorded in ``channel_id`` (never adopted for another announcement)."""
    rows = conn.execute(
        "SELECT message_id FROM new_arrival_announcements WHERE guild_id = ? AND channel_id = ? "
        "AND message_id IS NOT NULL",
        (guild_id, channel_id),
    )
    return {int(r["message_id"]) for r in rows}
