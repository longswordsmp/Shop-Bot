"""Read-only data access for ``/myorders``: one member's own orders, newest first.

Every query is bound to the member's user id (and the guild); there is no way to ask for another member's orders.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo as checkout_repo

# The most orders one reply looks at (newest first). Far more than a member normally has; it only bounds the work.
MAX_ORDERS = 500

# Columns in which staff may record an order's tracking details (read defensively: the column is added by the
# staff-operations migration). TODO(integrator): once that migration is merged, replace this lookup with the
# order model's tracking accessor and keep ``tracking`` customer-safe text only.
TRACKING_COLUMNS = ("tracking", "tracking_text", "tracking_details")


@dataclass(frozen=True)
class CustomerOrder:
    """One order as /myorders shows it. ``channel_id``: its ticket while the ticket is still open."""

    order: m.Order
    timeline: tuple[m.OrderEvent, ...]
    channel_id: int | None = None
    tracking: str | None = None


def _tracking_column(conn: sqlite3.Connection) -> str | None:
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(orders)")}
    return next((name for name in TRACKING_COLUMNS if name in columns), None)


def _tracking(conn: sqlite3.Connection, column: str | None, order_id: int) -> str | None:
    if column is None:
        return None
    # ``column`` is one of the constant TRACKING_COLUMNS; the value is bound.
    row = conn.execute(f'SELECT "{column}" AS tracking FROM orders WHERE id = ?', (order_id,)).fetchone()  # noqa: S608
    value = row["tracking"] if row is not None else None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _open_channel(conn: sqlite3.Connection, ticket_id: int | None) -> int | None:
    if ticket_id is None:
        return None
    row = conn.execute("SELECT channel_id, status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None or row["status"] != "open" or row["channel_id"] is None:
        return None
    return int(row["channel_id"])


def customer_orders(conn: sqlite3.Connection, guild_id: int, user_id: int) -> list[CustomerOrder]:
    """The member's orders in this guild, newest first, with their timelines (status changes only: staff notes are
    never included)."""
    if user_id <= 0:  # 0 would mean "every member" to the shared order list query
        return []
    orders = checkout_repo.list_orders(conn, guild_id, user_id=user_id, limit=MAX_ORDERS, offset=0)
    column = _tracking_column(conn)
    return [
        CustomerOrder(
            order=order,
            timeline=tuple(checkout_repo.status_events(conn, order.id)),
            channel_id=_open_channel(conn, order.ticket_id),
            tracking=_tracking(conn, column, order.id),
        )
        for order in orders
        if order.user_id == user_id and order.guild_id == guild_id  # defence in depth: only the member's own
    ]
