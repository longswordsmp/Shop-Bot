"""Synchronous data access of the operations feature: support-ticket reply tracking, staff reminders and the
customer-role rule (tables ``ticket_replies`` and ``operations_reminders``, migration 0003; orders and claims are read
from the checkout tables).

Reminders: a record (order or ticket) is *waiting* since a moment (``since``): an open unclaimed order since it was
submitted or its claim was released, an unpaid order since it was submitted, a paid order since its payment was
confirmed, a support ticket since the customer's first message after the last staff reply (or since it was opened
when no staff member replied yet). One reminder is sent once it waited the configured minutes; with a repeat
interval, another one each interval while it still waits. ``operations_reminders`` remembers what was sent per
waiting period, so a restart or a repeated sweep never sends a reminder twice.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from shopbot.features.checkout import models as checkout_models

UNCLAIMED: Final = "unclaimed"
UNPAID: Final = "unpaid"
UNFULFILLED: Final = "unfulfilled"
UNANSWERED: Final = "unanswered"
ORDER_REMINDERS: Final = (UNCLAIMED, UNPAID, UNFULFILLED)
REMINDER_KINDS: Final = (*ORDER_REMINDERS, UNANSWERED)


# -- support-ticket replies -----------------------------------------------------------------------------------------


def record_customer_message(conn: sqlite3.Connection, ticket_id: int, *, opened_at: int, now: int) -> bool:
    """The customer wrote in their ticket: they wait for staff from now on (or since the ticket was opened, when no
    staff member replied yet). Writes only when the waiting state changes. True when it changed."""
    cur = conn.execute(
        "INSERT INTO ticket_replies(ticket_id, awaiting_since, last_customer_message_at, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET awaiting_since = ?, "
        "last_customer_message_at = excluded.last_customer_message_at, updated_at = excluded.updated_at "
        "WHERE ticket_replies.awaiting_since IS NULL",
        (ticket_id, opened_at, now, now, now),
    )
    return cur.rowcount == 1


def record_staff_reply(conn: sqlite3.Connection, ticket_id: int, *, now: int) -> bool:
    """A staff member replied: the customer no longer waits. True when the waiting state changed."""
    cur = conn.execute(
        "INSERT INTO ticket_replies(ticket_id, awaiting_since, last_staff_reply_at, updated_at) "
        "VALUES (?, NULL, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET awaiting_since = NULL, "
        "last_staff_reply_at = excluded.last_staff_reply_at, updated_at = excluded.updated_at "
        "WHERE ticket_replies.awaiting_since IS NOT NULL",
        (ticket_id, now, now),
    )
    return cur.rowcount == 1


def awaiting_since(conn: sqlite3.Connection, ticket_id: int, opened_at: int) -> int | None:
    """Since when the ticket's customer waits for a staff reply; None when staff answered."""
    row = conn.execute("SELECT awaiting_since FROM ticket_replies WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        return opened_at
    return None if row["awaiting_since"] is None else int(row["awaiting_since"])


# -- reminders -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Waiting:
    """A record that waits for staff, and the reminder round that is due for it."""

    kind: str
    target_id: int
    since: int
    round: int


@dataclass(frozen=True)
class Thresholds:
    """Reminder thresholds in seconds (0 = off) and the repeat interval in seconds (0 = once)."""

    unclaimed: int = 0
    unpaid: int = 0
    unfulfilled: int = 0
    unanswered: int = 0
    repeat: int = 0

    def of(self, kind: str) -> int:
        return int(getattr(self, kind))


def _order_waits(conn: sqlite3.Connection, guild_id: int) -> list[tuple[str, int, int]]:
    """(kind, order id, since) of every open order, per reminder kind it can be waiting for."""
    rows = conn.execute(
        "SELECT o.id, o.status, o.created_at, o.paid_at, h.claimed_by, h.unclaimed_at FROM orders o "
        "LEFT JOIN order_handling h ON h.order_id = o.id "
        "WHERE o.guild_id = ? AND o.status IN (SELECT value FROM json_each(?)) ORDER BY o.id",
        (guild_id, json.dumps(list(checkout_models.OPEN_STATUSES))),
    )
    waits: list[tuple[str, int, int]] = []
    for r in rows:
        order_id, status, created_at = int(r["id"]), str(r["status"]), int(r["created_at"])
        if r["claimed_by"] is None:
            unclaimed_at = r["unclaimed_at"]
            waits.append((UNCLAIMED, order_id, int(unclaimed_at) if unclaimed_at is not None else created_at))
        if status in checkout_models.UNPAID_STATUSES:
            waits.append((UNPAID, order_id, created_at))
        elif status in checkout_models.AWAITING_SHIPMENT and r["paid_at"] is not None:
            waits.append((UNFULFILLED, order_id, int(r["paid_at"])))
    return waits


def _ticket_waits(conn: sqlite3.Connection, guild_id: int) -> list[tuple[str, int, int]]:
    rows = conn.execute(
        "SELECT t.id, t.created_at, r.ticket_id AS tracked, r.awaiting_since FROM tickets t "
        "LEFT JOIN ticket_replies r ON r.ticket_id = t.id "
        "WHERE t.guild_id = ? AND t.kind = 'support' AND t.status = 'open' ORDER BY t.id",
        (guild_id,),
    )
    waits: list[tuple[str, int, int]] = []
    for r in rows:
        if r["tracked"] is None:
            waits.append((UNANSWERED, int(r["id"]), int(r["created_at"])))
        elif r["awaiting_since"] is not None:
            waits.append((UNANSWERED, int(r["id"]), int(r["awaiting_since"])))
    return waits


def _sent(conn: sqlite3.Connection) -> dict[tuple[str, int], tuple[int, int, int | None]]:
    rows = conn.execute("SELECT kind, target_id, since, sent_count, last_sent_at FROM operations_reminders")
    return {
        (str(r["kind"]), int(r["target_id"])): (
            int(r["since"]),
            int(r["sent_count"]),
            None if r["last_sent_at"] is None else int(r["last_sent_at"]),
        )
        for r in rows
    }


def due_reminders(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    now: int,
    thresholds: Thresholds,
    kinds: Sequence[str],
    limit: int,
) -> list[Waiting]:
    """Reminders due now, oldest waiting first, at most ``limit`` (the rest follow in the next sweep)."""
    waits: list[tuple[str, int, int]] = []
    if any(kind in ORDER_REMINDERS for kind in kinds):
        waits += _order_waits(conn, guild_id)
    if UNANSWERED in kinds:
        waits += _ticket_waits(conn, guild_id)
    sent = _sent(conn)
    due: list[Waiting] = []
    for kind, target_id, since in waits:
        threshold = thresholds.of(kind)
        if kind not in kinds or threshold <= 0 or since + threshold > now:
            continue
        previous = sent.get((kind, target_id))
        if previous is None or previous[0] != since or previous[1] == 0:
            due.append(Waiting(kind, target_id, since, 1))
            continue
        _, count, last_sent_at = previous
        if thresholds.repeat > 0 and last_sent_at is not None and last_sent_at + thresholds.repeat <= now:
            due.append(Waiting(kind, target_id, since, count + 1))
    due.sort(key=lambda w: (w.since, w.kind, w.target_id))
    return due[: max(0, limit)]


def mark_reminded(conn: sqlite3.Connection, waiting: Waiting, now: int) -> bool:
    """Record that ``waiting.round`` was sent. A round that is not the next one (another sweep recorded it) changes
    nothing (False)."""
    if waiting.round == 1:
        cur = conn.execute(
            "INSERT INTO operations_reminders(kind, target_id, since, sent_count, last_sent_at) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(kind, target_id) DO UPDATE SET since = excluded.since, sent_count = 1, "
            "last_sent_at = excluded.last_sent_at "
            "WHERE operations_reminders.since != excluded.since OR operations_reminders.sent_count = 0",
            (waiting.kind, waiting.target_id, waiting.since, now),
        )
    else:
        cur = conn.execute(
            "UPDATE operations_reminders SET sent_count = ?, last_sent_at = ? "
            "WHERE kind = ? AND target_id = ? AND since = ? AND sent_count = ?",
            (waiting.round, now, waiting.kind, waiting.target_id, waiting.since, waiting.round - 1),
        )
    return cur.rowcount == 1


# -- customer role ---------------------------------------------------------------------------------------------------


def qualifying_statuses(trigger: str) -> tuple[str, ...]:
    """Order statuses that make a member a customer for ``checkout.customer_role_on``."""
    if trigger == checkout_models.PAID:
        return (*checkout_models.AWAITING_SHIPMENT, checkout_models.SHIPPED, checkout_models.FULFILLED)
    return (checkout_models.FULFILLED,)


def latest_qualifying_orders(conn: sqlite3.Connection, guild_id: int, statuses: Sequence[str]) -> list[tuple[int, int]]:
    """``(user_id, order_id)`` of each member's newest order that makes them a customer."""
    rows = conn.execute(
        "SELECT user_id, MAX(id) AS order_id FROM orders WHERE guild_id = ? "
        "AND status IN (SELECT value FROM json_each(?)) GROUP BY user_id ORDER BY user_id",
        (guild_id, json.dumps(list(statuses))),
    )
    return [(int(r["user_id"]), int(r["order_id"])) for r in rows]


def has_qualifying_order(conn: sqlite3.Connection, guild_id: int, user_id: int, statuses: Sequence[str]) -> bool:
    placeholders = ", ".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT 1 FROM orders WHERE guild_id = ? AND user_id = ? AND status IN ({placeholders}) LIMIT 1",  # noqa: S608
        (guild_id, user_id, *statuses),
    ).fetchone()
    return row is not None
