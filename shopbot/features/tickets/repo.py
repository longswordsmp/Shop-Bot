"""Synchronous data access for tickets. Every function takes the connection first and runs on the database
thread (``app.db.run`` for reads, ``app.db.transaction`` for writes). State changes are conditional
UPDATEs (``WHERE status = ...``) so concurrent or repeated requests can never apply twice. Per-user limits
are checked in the same transaction as the change they guard and refuse it with ``LimitReached``."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from shopbot.db.repos import state
from shopbot.features.tickets.models import (
    ACTIVE_STATUSES,
    CLOSED,
    CREATING,
    DELETED,
    FAILED,
    OPEN,
    TRANSCRIPT_DONE,
    TRANSCRIPT_FAILED,
    TRANSCRIPT_NONE,
    TRANSCRIPT_OUTSTANDING,
    TRANSCRIPT_PENDING,
    Ticket,
)

COUNTER_NAME = "ticket"
DAY_SECONDS = 86_400


class LimitReached(Exception):
    """A per-user limit refused the change. Raised inside the transaction, so nothing was written."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"limit {limit} reached")
        self.limit = limit


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _answers(raw: object) -> dict[str, str]:
    try:
        data = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _row(r: sqlite3.Row) -> Ticket:
    return Ticket(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        number=int(r["number"]),
        kind=str(r["kind"]),
        category_id=str(r["category_id"]),
        user_id=int(r["user_id"]),
        channel_id=_opt_int(r["channel_id"]),
        status=str(r["status"]),
        assigned_staff_id=_opt_int(r["assigned_staff_id"]),
        form_answers=_answers(r["form_answers"]),
        control_message_id=_opt_int(r["control_message_id"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        last_activity_at=int(r["last_activity_at"]),
        closed_at=_opt_int(r["closed_at"]),
        closed_by=_opt_int(r["closed_by"]),
        close_reason=None if r["close_reason"] is None else str(r["close_reason"]),
        deleted_at=_opt_int(r["deleted_at"]),
        transcript_status=str(r["transcript_status"]),
        transcript_message_id=_opt_int(r["transcript_message_id"]),
        version=int(r["version"]),
    )


# -- reads --------------------------------------------------------------------------------------------


def get(conn: sqlite3.Connection, ticket_id: int) -> Ticket | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return None if row is None else _row(row)


def get_by_channel(conn: sqlite3.Connection, channel_id: int) -> Ticket | None:
    row = conn.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)).fetchone()
    return None if row is None else _row(row)


def count_active(conn: sqlite3.Connection, guild_id: int, user_id: int, kind: str, category_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE guild_id = ? AND user_id = ? AND kind = ? AND category_id = ? "
        "AND status IN (?, ?)",
        (guild_id, user_id, kind, category_id, *ACTIVE_STATUSES),
    ).fetchone()
    return int(row["n"])


def count_created_since(conn: sqlite3.Connection, guild_id: int, user_id: int, kind: str, since: int) -> int:
    """Tickets of this user and kind created at or after ``since`` (failed creation attempts excluded)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE guild_id = ? AND user_id = ? AND kind = ? AND created_at >= ? "
        "AND status != ?",
        (guild_id, user_id, kind, since, FAILED),
    ).fetchone()
    return int(row["n"])


def list_tickets(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    kind: str | None = None,
    statuses: Sequence[str] = (),
    user_id: int | None = None,
    limit: int = 25,
) -> list[Ticket]:
    sql = "SELECT * FROM tickets WHERE guild_id = ?"
    params: list[object] = [guild_id]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    if statuses:
        sql += " AND status IN (SELECT value FROM json_each(?))"
        params.append(json.dumps(list(statuses)))
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, limit))
    return [_row(r) for r in conn.execute(sql, params)]


def by_statuses(conn: sqlite3.Connection, guild_id: int, statuses: Sequence[str]) -> list[Ticket]:
    rows = conn.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status IN (SELECT value FROM json_each(?)) ORDER BY id",
        (guild_id, json.dumps(list(statuses))),
    )
    return [_row(r) for r in rows]


def stale_creating(conn: sqlite3.Connection, guild_id: int, created_before: int) -> list[Ticket]:
    rows = conn.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status = ? AND created_at < ? ORDER BY id",
        (guild_id, CREATING, created_before),
    )
    return [_row(r) for r in rows]


def participants(conn: sqlite3.Connection, ticket_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT user_id FROM ticket_participants WHERE ticket_id = ? ORDER BY added_at, user_id", (ticket_id,)
    )
    return [int(r["user_id"]) for r in rows]


# -- creation -----------------------------------------------------------------------------------------


def reserve(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    kind: str,
    category_id: str,
    user_id: int,
    form_answers: dict[str, str],
    limit: int,
    now: int,
    daily_limit: int = 0,
) -> Ticket:
    """Limit checks + numbered 'creating' row in one transaction. Raises ``LimitReached``.

    ``limit``: active ('creating'/'open') tickets of this user, kind and category (0 = unlimited).
    ``daily_limit``: tickets of this kind the user created during the last 24 hours, in any category and
    whatever their state, failed attempts excluded (0 = unlimited).
    """
    if limit > 0 and count_active(conn, guild_id, user_id, kind, category_id) >= limit:
        raise LimitReached(limit)
    if daily_limit > 0 and count_created_since(conn, guild_id, user_id, kind, now - DAY_SECONDS) >= daily_limit:
        raise LimitReached(daily_limit)
    number = state.next_counter(conn, guild_id, COUNTER_NAME)
    state.touch_user(conn, user_id, now)
    cursor = conn.execute(
        "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, status, form_answers, created_at, "
        "updated_at, last_activity_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            number,
            kind,
            category_id,
            user_id,
            CREATING,
            json.dumps(form_answers, ensure_ascii=False, sort_keys=True),
            now,
            now,
            now,
        ),
    )
    ticket = get(conn, int(cursor.lastrowid or 0))
    assert ticket is not None
    return ticket


def set_channel(conn: sqlite3.Connection, ticket_id: int, channel_id: int, now: int) -> bool:
    cur = conn.execute(
        "UPDATE tickets SET channel_id = ?, updated_at = ?, version = version + 1 WHERE id = ? AND status = ?",
        (channel_id, now, ticket_id, CREATING),
    )
    return cur.rowcount > 0


def activate(conn: sqlite3.Connection, ticket_id: int, control_message_id: int | None, now: int) -> Ticket | None:
    cur = conn.execute(
        "UPDATE tickets SET status = ?, control_message_id = ?, last_activity_at = ?, updated_at = ?, "
        "version = version + 1 WHERE id = ? AND status = ? AND channel_id IS NOT NULL",
        (OPEN, control_message_id, now, now, ticket_id, CREATING),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def mark_failed(conn: sqlite3.Connection, ticket_id: int, now: int) -> Ticket | None:
    cur = conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ?, version = version + 1 WHERE id = ? AND status = ?",
        (FAILED, now, ticket_id, CREATING),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def set_control_message(conn: sqlite3.Connection, ticket_id: int, message_id: int | None, now: int) -> None:
    conn.execute(
        "UPDATE tickets SET control_message_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
        (message_id, now, ticket_id),
    )


# -- lifecycle ----------------------------------------------------------------------------------------


def close(
    conn: sqlite3.Connection,
    ticket_id: int,
    *,
    closed_by: int | None,
    reason: str | None,
    transcript_status: str,
    now: int,
) -> Ticket | None:
    cur = conn.execute(
        "UPDATE tickets SET status = ?, closed_at = ?, closed_by = ?, close_reason = ?, transcript_status = ?, "
        "updated_at = ?, version = version + 1 WHERE id = ? AND status = ?",
        (CLOSED, now, closed_by, reason, transcript_status, now, ticket_id, OPEN),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def reopen(conn: sqlite3.Connection, ticket_id: int, now: int, *, limit: int = 0) -> Ticket | None:
    """closed -> open. A transcript that was still pending is dropped (the next close makes a new one).

    ``limit`` > 0 (the owner reopens): raises ``LimitReached`` when the owner already has that many active
    tickets of the ticket's kind and category. Returns None when the ticket was not closed.
    """
    if limit > 0:
        current = get(conn, ticket_id)
        if current is None or current.status != CLOSED:
            return None
        if count_active(conn, current.guild_id, current.user_id, current.kind, current.category_id) >= limit:
            raise LimitReached(limit)
    cur = conn.execute(
        "UPDATE tickets SET status = ?, closed_at = NULL, closed_by = NULL, close_reason = NULL, "
        "last_activity_at = ?, transcript_status = CASE WHEN transcript_status = ? THEN ? ELSE ? END, "
        "updated_at = ?, version = version + 1 WHERE id = ? AND status = ?",
        (OPEN, now, TRANSCRIPT_DONE, TRANSCRIPT_DONE, TRANSCRIPT_NONE, now, ticket_id, CLOSED),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def claim(conn: sqlite3.Connection, ticket_id: int, staff_id: int, now: int) -> Ticket | None:
    cur = conn.execute(
        "UPDATE tickets SET assigned_staff_id = ?, updated_at = ?, version = version + 1 "
        "WHERE id = ? AND status = ? AND assigned_staff_id IS NULL",
        (staff_id, now, ticket_id, OPEN),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def unclaim(conn: sqlite3.Connection, ticket_id: int, expected_staff_id: int, now: int) -> Ticket | None:
    cur = conn.execute(
        "UPDATE tickets SET assigned_staff_id = NULL, updated_at = ?, version = version + 1 "
        "WHERE id = ? AND status = ? AND assigned_staff_id = ?",
        (now, ticket_id, OPEN, expected_staff_id),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def mark_deleted(
    conn: sqlite3.Connection, ticket_id: int, now: int, *, external: bool, reason: str | None = None
) -> tuple[Ticket, Ticket] | None:
    """-> deleted. Returns (before, after), or None when the ticket is not in a state this path may delete.

    ``external=True``: the channel disappeared without us (its messages are gone): an open or closed ticket
    is marked deleted, and a pending transcript, which can no longer be produced, is marked failed.
    ``external=False``: the bot claims the deletion of a channel it is about to delete. Only a *closed*
    ticket without an outstanding transcript matches, so a ticket reopened (or closed again with a new
    transcript pending) meanwhile is never deleted; a reopen no longer matches once this has committed.
    ``restore_deleted`` undoes the claim when Discord refuses to delete the channel.
    """
    before = get(conn, ticket_id)
    allowed = (OPEN, CLOSED) if external else (CLOSED,)
    if before is None or before.status not in allowed:
        return None
    if not external and before.transcript_status in TRANSCRIPT_OUTSTANDING:
        return None
    cur = conn.execute(
        "UPDATE tickets SET status = ?, deleted_at = ?, closed_at = COALESCE(closed_at, ?), "
        "close_reason = CASE WHEN status = ? THEN COALESCE(?, close_reason) ELSE close_reason END, "
        "transcript_status = CASE WHEN ? AND transcript_status = ? THEN ? ELSE transcript_status END, "
        "updated_at = ?, version = version + 1 WHERE id = ? AND status = ? AND version = ?",
        (
            DELETED,
            now,
            now,
            OPEN,
            reason,
            1 if external else 0,
            TRANSCRIPT_PENDING,
            TRANSCRIPT_FAILED,
            now,
            ticket_id,
            before.status,
            before.version,
        ),
    )
    if not cur.rowcount:
        return None
    after = get(conn, ticket_id)
    assert after is not None
    return before, after


def restore_deleted(conn: sqlite3.Connection, ticket_id: int, deleted_at: int, now: int) -> Ticket | None:
    """Undo a deletion claim (``mark_deleted(external=False)``) whose channel could not be deleted:
    deleted -> closed. Only the claim made at ``deleted_at`` is undone."""
    cur = conn.execute(
        "UPDATE tickets SET status = ?, deleted_at = NULL, updated_at = ?, version = version + 1 "
        "WHERE id = ? AND status = ? AND deleted_at = ?",
        (CLOSED, now, ticket_id, DELETED, deleted_at),
    )
    return get(conn, ticket_id) if cur.rowcount else None


def set_transcript(
    conn: sqlite3.Connection,
    ticket_id: int,
    *,
    status: str,
    now: int,
    message_id: int | None = None,
    only_if: Sequence[str] = (),
) -> bool:
    sql = "UPDATE tickets SET transcript_status = ?, updated_at = ?, version = version + 1"
    params: list[object] = [status, now]
    if message_id is not None:
        sql += ", transcript_message_id = ?"
        params.append(message_id)
    sql += " WHERE id = ?"
    params.append(ticket_id)
    if only_if:
        sql += " AND transcript_status IN (SELECT value FROM json_each(?))"
        params.append(json.dumps(list(only_if)))
    return conn.execute(sql, params).rowcount > 0


def touch_activity(conn: sqlite3.Connection, channel_id: int, now: int, min_interval: int) -> Ticket | None:
    """Record activity in an open ticket channel unless it was recorded less than ``min_interval`` ago."""
    cur = conn.execute(
        "UPDATE tickets SET last_activity_at = ?, updated_at = ? WHERE channel_id = ? AND status = ? "
        "AND last_activity_at <= ?",
        (now, now, channel_id, OPEN, now - min_interval),
    )
    return get_by_channel(conn, channel_id) if cur.rowcount else None


# -- participants ---------------------------------------------------------------------------------------


def add_participant(conn: sqlite3.Connection, ticket_id: int, user_id: int, added_by: int, now: int) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO ticket_participants(ticket_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
        (ticket_id, user_id, added_by, now),
    )
    return cur.rowcount > 0


def remove_participant(conn: sqlite3.Connection, ticket_id: int, user_id: int) -> bool:
    cur = conn.execute("DELETE FROM ticket_participants WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
    return cur.rowcount > 0


# -- scheduler support -------------------------------------------------------------------------------------
