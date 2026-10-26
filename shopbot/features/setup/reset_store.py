"""Synchronous data changes for /reset (``reset``): forgetting the Discord objects it deleted, and the optional wipe
of the shop data.

Forgetting removes the records of deleted roles, categories and channels (``guild_resources``) and of the messages
that were in deleted channels (``published_messages``), so the next /setup creates them anew and posts every panel
again. Records of objects that still exist (a channel Discord does not let anyone delete, a role above the bot) are
kept, so /setup keeps using them.

The wipe (``/reset wipe_shop_data:True``) deletes every row of ``WIPED_TABLES`` in one transaction: orders and
everything that belongs to them, products, reviews, tickets, store credit and customer records. ``KEPT_TABLES`` stay:
texts, settings-related records, the audit log, announcements, counters (order and ticket numbers keep counting, so
a number in an old message never points to a new order) and the id sequences (every id table uses AUTOINCREMENT, so
ids are never reused either). Pending tasks of the wiped features are cancelled; ``KEPT_TASK_KINDS`` are system tasks
that stay scheduled. The database belongs to the one server ``GUILD_ID`` names, so the wipe is not filtered by guild.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Sequence
from typing import Final

# Children before parents (foreign keys are checked at commit; the order keeps the intent readable).
WIPED_TABLES: Final = (
    "cart_items",
    "inventory_reservations",
    "order_items",
    "order_fees",
    "order_events",
    "order_adjustments",
    "order_handling",
    "order_activity",
    "payments",
    "credit_ledger",
    "gift_cards",
    "reviews",
    "problem_reports",
    "checkout_sessions",
    "orders",
    "new_arrival_announcements",
    "product_images",
    "products",
    "ticket_participants",
    "ticket_replies",
    "tickets",
    "operations_reminders",
    "notifications",
    "verification_acceptances",
    "users",
)
KEPT_TABLES: Final = (
    "announcements",
    "app_emojis",
    "audit_events",
    "content_overrides",
    "counters",
    "guild_resources",  # forgotten per deleted object (``forget``), not wiped
    "kv_state",
    "published_messages",  # likewise
    "scheduled_tasks",  # tasks of the wiped features are cancelled
    "schema_migrations",
)
# System tasks that stay scheduled through a wipe (daily maintenance, the reminder sweep, deleting welcome mentions).
KEPT_TASK_KINDS: Final = ("admin.maintenance", "operations.reminders", "verification.welcome_cleanup")


def forget(conn: sqlite3.Connection, guild_id: int, gone_ids: Collection[int]) -> tuple[int, int]:
    """Forget the recorded roles/categories/channels with these ids and the published messages in those channels.
    Returns (resources, messages) forgotten."""
    ids = json.dumps(sorted(gone_ids))
    resources = conn.execute(
        "DELETE FROM guild_resources WHERE guild_id = ? AND discord_id IN (SELECT value FROM json_each(?))",
        (guild_id, ids),
    ).rowcount
    messages = conn.execute(
        "DELETE FROM published_messages WHERE guild_id = ? AND channel_id IN (SELECT value FROM json_each(?))",
        (guild_id, ids),
    ).rowcount
    return resources, messages


def recorded_ids(conn: sqlite3.Connection, guild_id: int) -> set[int]:
    """Every Discord id the bot recorded: roles, categories and channels, and the channels of published messages."""
    rows = conn.execute(
        "SELECT discord_id AS id FROM guild_resources WHERE guild_id = ? "
        "UNION SELECT channel_id FROM published_messages WHERE guild_id = ?",
        (guild_id, guild_id),
    )
    return {int(row["id"]) for row in rows}


def wipe_shop_data(conn: sqlite3.Connection, now: int) -> dict[str, int]:
    """Delete every row of ``WIPED_TABLES`` and cancel the pending tasks of the wiped features. Returns the number of
    rows deleted per table (and ``tasks_cancelled``)."""
    # Orders and checkout sessions refer to each other; checking foreign keys at commit lets both go in one pass.
    conn.execute("PRAGMA defer_foreign_keys = ON")
    counts = {table: conn.execute(f"DELETE FROM {table}").rowcount for table in WIPED_TABLES}  # noqa: S608
    counts["tasks_cancelled"] = conn.execute(
        "UPDATE scheduled_tasks SET status = 'cancelled', updated_at = ? WHERE status = 'pending' "
        "AND kind NOT IN (SELECT value FROM json_each(?))",
        (now, json.dumps(list(KEPT_TASK_KINDS))),
    ).rowcount
    return counts


def open_counts(conn: sqlite3.Connection, guild_id: int, open_statuses: Sequence[str]) -> tuple[int, int]:
    """(orders in progress, tickets open or being created): the preview names what loses its channel."""
    orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE guild_id = ? AND status IN (SELECT value FROM json_each(?))",
        (guild_id, json.dumps(list(open_statuses))),
    ).fetchone()[0]
    tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status IN ('creating', 'open')", (guild_id,)
    ).fetchone()[0]
    return int(orders), int(tickets)
