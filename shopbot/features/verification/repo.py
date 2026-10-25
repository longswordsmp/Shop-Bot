"""Acceptance history (rows of ``verification_acceptances``).

Synchronous, connection-first functions run with ``app.db.run`` / ``app.db.transaction``. The Member role in
Discord is the source of truth for "is verified"; these rows only record when a member accepted the rules and how
many times the role had to be granted (the counter keys the optional confirmation DM, so it is sent at most once
per acceptance). ``(guild_id, user_id)`` is the primary key, so a member can never have two rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Acceptance:
    guild_id: int
    user_id: int
    first_accepted_at: int
    last_accepted_at: int
    acceptances: int


def _row(row: sqlite3.Row) -> Acceptance:
    return Acceptance(
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        first_accepted_at=int(row["first_accepted_at"]),
        last_accepted_at=int(row["last_accepted_at"]),
        acceptances=int(row["acceptances"]),
    )


def get_acceptance(conn: sqlite3.Connection, guild_id: int, user_id: int) -> Acceptance | None:
    row = conn.execute(
        "SELECT guild_id, user_id, first_accepted_at, last_accepted_at, acceptances FROM verification_acceptances "
        "WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return None if row is None else _row(row)


def record_acceptance(conn: sqlite3.Connection, guild_id: int, user_id: int, now: int) -> Acceptance:
    """Record that the member was granted the Member role now. Must run inside a transaction."""
    conn.execute(
        "INSERT INTO verification_acceptances (guild_id, user_id, first_accepted_at, last_accepted_at, acceptances) "
        "VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT (guild_id, user_id) DO UPDATE SET "
        "last_accepted_at = MAX(last_accepted_at, excluded.last_accepted_at), acceptances = acceptances + 1",
        (guild_id, user_id, now, now),
    )
    acceptance = get_acceptance(conn, guild_id, user_id)
    if acceptance is None:  # pragma: no cover - the row was written by the statement above
        raise RuntimeError("acceptance row missing after insert")
    return acceptance


def count_acceptances(conn: sqlite3.Connection, guild_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM verification_acceptances WHERE guild_id = ?", (guild_id,)).fetchone()
    return int(row["n"])
