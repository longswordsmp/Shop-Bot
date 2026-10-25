"""Synchronous data access for problem reports. Every function takes the connection first and runs on the database
thread (``app.db.run`` for reads, ``app.db.transaction`` for writes).

Guarantees (each check runs in the same transaction as the write it guards, so concurrent requests cannot both pass):
- ``reserve``: one report per run of the private report flow (``nonce``, also UNIQUE in the schema); at most
  ``max_open`` undecided reports with an active ticket per customer; the chosen order must belong to the customer and
  be in an eligible status. The report's ticket row (numbered, 'creating') and the durable finishing task are
  created in the same transaction.
- ``activate``: records the report panel and opens the ticket atomically (both or neither).
- ``decide``: ``open -> resolved | rejected`` exactly once (conditional UPDATE), with the order's history note and
  the durable follow-up task in the same transaction.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import handling_repo
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import repo as checkout_repo
from shopbot.features.operations import repo as operations_repo
from shopbot.features.problems.content_keys import OPEN, TICKET_CATEGORY
from shopbot.features.problems.models import Report
from shopbot.features.tickets import repo as tickets_repo
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import ACTIVE_STATUSES, FAILED, KIND_SUPPORT, Ticket

TASK_FINISH = "problems.finish"
TASK_AFTER_DECISION = "problems.after_decision"
AUDIT_TARGET = "problem_report"

# The finishing task runs after the ticket system's own recovery of an interrupted creation (which decides whether
# the ticket survives), so it only has to complete or clean up the report.
FINISH_DELAY_SECONDS = tickets_service.CREATING_STALE_SECONDS + 60
# The decision follow-up task normally gets cancelled right after the in-process follow-up completed.
FOLLOWUP_DELAY_SECONDS = 120


class LimitReached(Exception):
    """The customer already has ``limit`` undecided reports with an active ticket (nothing was written)."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"limit {limit} reached")
        self.limit = limit


class NotEligible(Exception):
    """The chosen order does not belong to the customer or is not in an eligible status (nothing was written)."""


class Superseded(Exception):
    """The report or its ticket changed state while it was being created (nothing was written)."""


@dataclass(frozen=True)
class Reservation:
    report: Report
    ticket: Ticket
    # False: this flow was submitted before; ``report`` is the earlier report (nothing new was written).
    created: bool


def finish_key(report_id: int) -> str:
    return f"problems.finish:{report_id}"


def decision_key(report_id: int) -> str:
    return f"problems.after_decision:{report_id}"


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


def _row(r: sqlite3.Row) -> Report:
    return Report(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        user_id=int(r["user_id"]),
        nonce=str(r["nonce"]),
        order_id=_opt_int(r["order_id"]),
        issue_type=str(r["issue_type"]),
        answers=_answers(r["answers"]),
        ticket_id=int(r["ticket_id"]),
        panel_message_id=_opt_int(r["panel_message_id"]),
        status=str(r["status"]),
        reason=None if r["reason"] is None else str(r["reason"]),
        decided_by=_opt_int(r["decided_by"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        decided_at=_opt_int(r["decided_at"]),
    )


# -- reads ----------------------------------------------------------------------------------------------------


def get(conn: sqlite3.Connection, report_id: int) -> Report | None:
    row = conn.execute("SELECT * FROM problem_reports WHERE id = ?", (report_id,)).fetchone()
    return None if row is None else _row(row)


def by_ticket(conn: sqlite3.Connection, ticket_id: int) -> Report | None:
    row = conn.execute("SELECT * FROM problem_reports WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return None if row is None else _row(row)


def by_nonce(conn: sqlite3.Connection, guild_id: int, user_id: int, nonce: str) -> Report | None:
    row = conn.execute(
        "SELECT * FROM problem_reports WHERE guild_id = ? AND user_id = ? AND nonce = ?", (guild_id, user_id, nonce)
    ).fetchone()
    return None if row is None else _row(row)


_OPEN_WITH_ACTIVE_TICKET = (
    "FROM problem_reports r JOIN tickets t ON t.id = r.ticket_id "
    "WHERE r.guild_id = ? AND r.user_id = ? AND r.status = ? AND t.status IN (?, ?)"
)


def count_open(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    """Undecided reports of the customer whose ticket is still being created or open (a report whose ticket was
    closed or deleted without a decision no longer blocks a new one)."""
    row = conn.execute(
        f"SELECT COUNT(*) AS n {_OPEN_WITH_ACTIVE_TICKET}",
        (guild_id, user_id, OPEN, *ACTIVE_STATUSES),
    ).fetchone()
    return int(row["n"])


def newest_open_channel(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int | None:
    """Ticket channel of the customer's newest undecided report with an active ticket."""
    row = conn.execute(
        f"SELECT t.channel_id AS channel_id {_OPEN_WITH_ACTIVE_TICKET} "
        "AND t.channel_id IS NOT NULL ORDER BY r.id DESC LIMIT 1",
        (guild_id, user_id, OPEN, *ACTIVE_STATUSES),
    ).fetchone()
    return None if row is None else int(row["channel_id"])


def eligible_orders(
    conn: sqlite3.Connection, guild_id: int, user_id: int, statuses: Sequence[str], limit: int
) -> list[checkout_models.Order]:
    """The customer's orders in one of ``statuses``, newest first, at most ``limit``."""
    if not statuses or limit <= 0:
        return []
    rows = conn.execute(
        "SELECT id FROM orders WHERE guild_id = ? AND user_id = ? AND status IN (SELECT value FROM json_each(?)) "
        "ORDER BY id DESC LIMIT ?",
        (guild_id, user_id, json.dumps(list(statuses)), limit),
    )
    orders = [checkout_repo.get_order(conn, int(r["id"])) for r in rows]
    return [order for order in orders if order is not None]


def eligible_order(
    conn: sqlite3.Connection, guild_id: int, user_id: int, order_id: int, statuses: Sequence[str]
) -> checkout_models.Order | None:
    order = checkout_repo.get_order(conn, order_id)
    if order is None or order.guild_id != guild_id or order.user_id != user_id or order.status not in statuses:
        return None
    return order


def unfinished(conn: sqlite3.Connection, guild_id: int) -> list[Report]:
    """Reports whose creation has not completed (no report panel yet)."""
    rows = conn.execute(
        "SELECT * FROM problem_reports WHERE guild_id = ? AND panel_message_id IS NULL ORDER BY id", (guild_id,)
    )
    return [_row(r) for r in rows]


def audited(conn: sqlite3.Connection, action: str, report_id: int) -> bool:
    """Whether an audit event ``action`` was recorded for the report (keeps a re-run follow-up from repeating it)."""
    row = conn.execute(
        "SELECT 1 FROM audit_events WHERE action = ? AND target_type = ? AND target_id = ? LIMIT 1",
        (action, AUDIT_TARGET, str(report_id)),
    ).fetchone()
    return row is not None


# -- writes ---------------------------------------------------------------------------------------------------


def _delete_unfinished(conn: sqlite3.Connection, report_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM problem_reports WHERE id = ? AND panel_message_id IS NULL AND status = ?", (report_id, OPEN)
    )
    return cur.rowcount > 0


def reserve(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    nonce: str,
    order_id: int | None,
    issue_type: str,
    answers: dict[str, str],
    eligible_statuses: Sequence[str],
    max_open: int,
    daily_limit: int,
    now: int,
    task_attempts: int,
) -> Reservation:
    """Limit and eligibility checks + the report row + its ticket row ('creating') + the finishing task.

    Raises ``LimitReached``, ``NotEligible``, or the ticket system's ``LimitReached`` (daily ticket cap).
    """
    existing = by_nonce(conn, guild_id, user_id, nonce)
    if existing is not None:
        ticket = tickets_repo.get(conn, existing.ticket_id)
        if ticket is not None and ticket.status != FAILED:
            return Reservation(report=existing, ticket=ticket, created=False)
        discard(conn, existing.id, now)  # an earlier attempt of this flow failed: try again
    if count_open(conn, guild_id, user_id) >= max_open:
        raise LimitReached(max_open)
    if order_id is not None and eligible_order(conn, guild_id, user_id, order_id, eligible_statuses) is None:
        raise NotEligible(str(order_id))
    ticket = tickets_service.reserve_txn(
        conn,
        guild_id=guild_id,
        kind=KIND_SUPPORT,
        category_id=TICKET_CATEGORY,
        user_id=user_id,
        form_answers={},
        limit=0,  # the report limit above governs; the daily ticket cap still applies
        daily_limit=daily_limit,
        now=now,
        recovery_max_attempts=task_attempts,
    )
    cursor = conn.execute(
        "INSERT INTO problem_reports(guild_id, user_id, nonce, order_id, issue_type, answers, ticket_id, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            user_id,
            nonce,
            order_id,
            issue_type,
            json.dumps(answers, ensure_ascii=False, sort_keys=True),
            ticket.id,
            OPEN,
            now,
            now,
        ),
    )
    report = get(conn, int(cursor.lastrowid or 0))
    assert report is not None
    task_repo.schedule(
        conn,
        kind=TASK_FINISH,
        run_at=now + FINISH_DELAY_SECONDS,
        payload={"report_id": report.id},
        max_attempts=task_attempts,
        dedupe_key=finish_key(report.id),
        replace=True,
        now=now,
    )
    return Reservation(report=report, ticket=ticket, created=True)


def _order_note(conn: sqlite3.Connection, report: Report, *, actor_id: int, note: str | None, now: int) -> None:
    """An internal note on the related order: shown on its live #orders record (staff activity), never to customers."""
    if report.order_id is None or not note:
        return
    # Internal staff note: shortened to the note limit if needed (the full reason stays on the report and its
    # panel). The order may have vanished: the report itself must still be recorded.
    text = note.strip()
    if len(text) > handling_repo.NOTE_MAX:
        text = text[: handling_repo.NOTE_MAX - 1].rstrip() + "…"
    with contextlib.suppress(handling_repo.HandlingError):
        handling_repo.add_note(
            conn, guild_id=report.guild_id, order_id=report.order_id, actor_id=actor_id, text=text, now=now
        )


def activate(
    conn: sqlite3.Connection, report_id: int, panel_message_id: int, *, order_note: str | None, now: int
) -> tuple[Report, Ticket]:
    """The report panel was posted: record it, open the ticket ('creating' -> 'open'), add the order's history note.
    Raises ``Superseded`` (nothing written) when the report already has a panel or its ticket left 'creating'."""
    report = get(conn, report_id)
    if report is None or report.panel_message_id is not None:
        raise Superseded("report")
    conn.execute(
        "UPDATE problem_reports SET panel_message_id = ?, updated_at = ? WHERE id = ? AND panel_message_id IS NULL",
        (panel_message_id, now, report_id),
    )
    opened = tickets_service.activate_txn(conn, report.ticket_id, None, now)
    if opened is None:
        raise Superseded("ticket")
    _order_note(conn, report, actor_id=report.user_id, note=order_note, now=now)
    updated = get(conn, report_id)
    assert updated is not None
    return updated, opened


def adopt_panel(
    conn: sqlite3.Connection, report_id: int, panel_message_id: int, *, order_note: str | None, now: int
) -> Report | None:
    """Recovery: the ticket system opened the ticket with the report panel as its control message (the process
    stopped between posting the panel and recording it). Record the panel here and detach it from the ticket's
    controls (they would otherwise replace the panel's buttons on close/reopen). None when already recorded."""
    report = get(conn, report_id)
    if report is None or report.panel_message_id is not None:
        return None
    conn.execute(
        "UPDATE problem_reports SET panel_message_id = ?, updated_at = ? WHERE id = ? AND panel_message_id IS NULL",
        (panel_message_id, now, report_id),
    )
    tickets_repo.set_control_message(conn, report.ticket_id, None, now)
    _order_note(conn, report, actor_id=report.user_id, note=order_note, now=now)
    return get(conn, report_id)


def discard(conn: sqlite3.Connection, report_id: int, now: int) -> bool:
    """Remove a report whose creation failed (no panel was recorded) and its finishing task."""
    removed = _delete_unfinished(conn, report_id)
    if removed:
        task_repo.cancel(conn, dedupe_key=finish_key(report_id), now=now)
    return removed


def decide(
    conn: sqlite3.Connection,
    report_id: int,
    *,
    status: str,
    reason: str | None,
    actor_id: int,
    order_note: str | None,
    now: int,
    task_attempts: int,
) -> Report | None:
    """``open -> status`` exactly once. Returns the decided report, or None when it was not open (nothing written)."""
    cur = conn.execute(
        "UPDATE problem_reports SET status = ?, reason = ?, decided_by = ?, decided_at = ?, updated_at = ? "
        "WHERE id = ? AND status = ? AND panel_message_id IS NOT NULL",
        (status, reason or None, actor_id, now, now, report_id, OPEN),
    )
    if not cur.rowcount:
        return None
    decided = get(conn, report_id)
    assert decided is not None
    _order_note(conn, decided, actor_id=actor_id, note=order_note, now=now)
    # A decision is the staff's answer: the ticket no longer waits for a staff reply (no more reminders).
    operations_repo.record_staff_reply(conn, decided.ticket_id, now=now)
    task_repo.schedule(
        conn,
        kind=TASK_AFTER_DECISION,
        run_at=now + FOLLOWUP_DELAY_SECONDS,
        payload={"report_id": report_id},
        max_attempts=task_attempts,
        dedupe_key=decision_key(report_id),
        replace=True,
        now=now,
    )
    return decided


def cancel_task(conn: sqlite3.Connection, dedupe_key: str, now: int) -> int:
    return task_repo.cancel(conn, dedupe_key=dedupe_key, now=now)
