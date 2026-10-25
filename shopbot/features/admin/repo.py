"""Synchronous data access for the admin tools. Each function takes the connection first and runs on the
database thread (writes via ``Database.transaction``)."""

from __future__ import annotations

import sqlite3

from shopbot.db.repos import tasks as task_repo

TASK_STATUSES = ("pending", "running", "done", "failed", "cancelled")


def task_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Number of scheduled tasks per status (every status is present, zero when none)."""
    counts = dict.fromkeys(TASK_STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM scheduled_tasks GROUP BY status"):
        counts[str(row["status"])] = int(row["n"])
    return counts


def purge_audit_before(conn: sqlite3.Connection, older_than: int) -> int:
    """Delete audit events created before ``older_than`` (retention policy). Returns the number deleted."""
    return conn.execute("DELETE FROM audit_events WHERE created_at < ?", (older_than,)).rowcount


def ensure_task(
    conn: sqlite3.Connection, *, kind: str, dedupe_key: str, run_at: int, max_attempts: int, now: int
) -> bool:
    """Make sure a pending/running task exists for ``dedupe_key``; never moves an already pending one.

    A finished (done/failed/cancelled) or missing task is (re)scheduled at ``run_at``. Returns True when
    a task was scheduled. Must run inside a transaction (check-then-write).
    """
    existing = task_repo.get_by_dedupe(conn, dedupe_key)
    if existing is not None and existing.status in ("pending", "running"):
        return False
    task_id = task_repo.schedule(
        conn,
        kind=kind,
        run_at=run_at,
        payload={},
        max_attempts=max_attempts,
        dedupe_key=dedupe_key,
        replace=True,
        now=now,
    )
    return task_id is not None
