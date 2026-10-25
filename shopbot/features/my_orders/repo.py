"""Read-only data access for ``/myorders``: one member's own orders, newest first.

Every query is bound to the member's user id (and the guild); there is no way to ask for another member's orders.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from shopbot.features.checkout import handling_repo
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo as checkout_repo

# The most orders one reply looks at (newest first). Far more than a member normally has; it only bounds the work.
MAX_ORDERS = 500


@dataclass(frozen=True)
class CustomerOrder:
    """One order as /myorders shows it. ``channel_id``: its ticket while the ticket is still open."""

    order: m.Order
    timeline: tuple[m.OrderEvent, ...]
    channel_id: int | None = None
    tracking: str | None = None


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
    return [
        CustomerOrder(
            order=order,
            timeline=tuple(checkout_repo.status_events(conn, order.id)),
            channel_id=_open_channel(conn, order.ticket_id),
            tracking=handling_repo.tracking_text(conn, order.id),
        )
        for order in orders
        if order.user_id == user_id and order.guild_id == guild_id  # defence in depth: only the member's own
    ]
