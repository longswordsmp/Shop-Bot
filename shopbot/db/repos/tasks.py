"""Persistent scheduled tasks (see ``shopbot.core.scheduler``)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Task:
    id: int
    kind: str
    run_at: int
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    dedupe_key: str | None
    last_error: str | None


def _row(r: sqlite3.Row) -> Task:
    return Task(
        id=int(r["id"]),
        kind=str(r["kind"]),
        run_at=int(r["run_at"]),
        payload=json.loads(r["payload"]),
        status=str(r["status"]),
        attempts=int(r["attempts"]),
        max_attempts=int(r["max_attempts"]),
        dedupe_key=r["dedupe_key"],
        last_error=r["last_error"],
    )


def schedule(
    conn: sqlite3.Connection,
    *,
    kind: str,
    run_at: int,
    payload: dict[str, Any],
    max_attempts: int,
    dedupe_key: str | None,
    replace: bool,
    now: int,
) -> int | None:
    """Insert a pending task.

    With a ``dedupe_key``: if a task with that key exists and ``replace`` is true, a *pending* task is
    rescheduled with the new time/payload (a running/done/failed one is left alone and None is
    returned); if ``replace`` is false the existing task wins and None is returned.
    """
    payload_json = json.dumps(payload, sort_keys=True)
    if dedupe_key is not None:
        existing = conn.execute("SELECT id, status FROM scheduled_tasks WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        if existing is not None:
            if replace and existing["status"] == "pending":
                conn.execute(
                    "UPDATE scheduled_tasks SET run_at = ?, payload = ?, updated_at = ? WHERE id = ?",
                    (run_at, payload_json, now, existing["id"]),
                )
                return int(existing["id"])
            if replace and existing["status"] in ("done", "cancelled", "failed"):
                conn.execute(
                    "UPDATE scheduled_tasks SET run_at = ?, payload = ?, status = 'pending', attempts = 0, "
                    "last_error = NULL, locked_at = NULL, updated_at = ? WHERE id = ?",
                    (run_at, payload_json, now, existing["id"]),
                )
                return int(existing["id"])
            return None
    cursor = conn.execute(
        "INSERT INTO scheduled_tasks(kind, run_at, payload, status, attempts, max_attempts, dedupe_key, "
        "created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)",
        (kind, run_at, payload_json, max_attempts, dedupe_key, now, now),
    )
    return int(cursor.lastrowid or 0)


def cancel(conn: sqlite3.Connection, *, dedupe_key: str | None = None, task_id: int | None = None, now: int) -> int:
    if dedupe_key is not None:
        cur = conn.execute(
            "UPDATE scheduled_tasks SET status = 'cancelled', updated_at = ? "
            "WHERE dedupe_key = ? AND status = 'pending'",
            (now, dedupe_key),
        )
    elif task_id is not None:
        cur = conn.execute(
            "UPDATE scheduled_tasks SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, task_id),
        )
    else:
        raise ValueError("dedupe_key or task_id is required")
    return cur.rowcount


def cancel_prefix(conn: sqlite3.Connection, prefix: str, now: int) -> int:
    cur = conn.execute(
        "UPDATE scheduled_tasks SET status = 'cancelled', updated_at = ? "
        "WHERE status = 'pending' AND substr(dedupe_key, 1, ?) = ?",
        (now, len(prefix), prefix),
    )
    return cur.rowcount


def claim_due(conn: sqlite3.Connection, now: int, limit: int) -> list[Task]:
    """Atomically mark up to ``limit`` due tasks as running. Must run inside a transaction."""
    rows = conn.execute(
        "SELECT * FROM scheduled_tasks WHERE status = 'pending' AND run_at <= ? ORDER BY run_at, id LIMIT ?",
        (now, limit),
    ).fetchall()
    claimed: list[Task] = []
    for r in rows:
        updated = conn.execute(
            "UPDATE scheduled_tasks SET status = 'running', attempts = attempts + 1, locked_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, now, r["id"]),
        ).rowcount
        if updated:
            row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (r["id"],)).fetchone()
            claimed.append(_row(row))
    return claimed


def mark_done(conn: sqlite3.Connection, task_id: int, now: int) -> None:
    conn.execute(
        "UPDATE scheduled_tasks SET status = 'done', last_error = NULL, locked_at = NULL, updated_at = ? "
        "WHERE id = ? AND status = 'running'",
        (now, task_id),
    )


def mark_failed_attempt(conn: sqlite3.Connection, task_id: int, error: str, retry_at: int | None, now: int) -> None:
    """Record a failure. ``retry_at=None`` marks the task permanently failed."""
    if retry_at is None:
        conn.execute(
            "UPDATE scheduled_tasks SET status = 'failed', last_error = ?, locked_at = NULL, updated_at = ? "
            "WHERE id = ?",
            (error[:1000], now, task_id),
        )
    else:
        conn.execute(
            "UPDATE scheduled_tasks SET status = 'pending', run_at = ?, last_error = ?, locked_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (retry_at, error[:1000], now, task_id),
        )


def recover_interrupted(conn: sqlite3.Connection, now: int) -> int:
    """Tasks left 'running' by a crash or restart are returned to 'pending' (handlers are idempotent)."""
    return conn.execute(
        "UPDATE scheduled_tasks SET status = 'pending', locked_at = NULL, updated_at = ? WHERE status = 'running'",
        (now,),
    ).rowcount


def get_by_dedupe(conn: sqlite3.Connection, dedupe_key: str) -> Task | None:
    row = conn.execute("SELECT * FROM scheduled_tasks WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    return None if row is None else _row(row)


def list_tasks(conn: sqlite3.Connection, *, status: str | None = None, limit: int = 50) -> list[Task]:
    if status is None:
        rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY run_at, id LIMIT ?", (limit,))
    else:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE status = ? ORDER BY run_at, id LIMIT ?", (status, limit)
        )
    return [_row(r) for r in rows]


def purge_finished(conn: sqlite3.Connection, older_than: int) -> int:
    return conn.execute(
        "DELETE FROM scheduled_tasks WHERE status IN ('done', 'cancelled') AND updated_at < ?", (older_than,)
    ).rowcount
