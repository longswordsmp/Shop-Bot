"""Key-value state, monotonic counters, guild resources, and published messages."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def kv_set(conn: sqlite3.Connection, key: str, value: str, now: int) -> None:
    conn.execute(
        "INSERT INTO kv_state(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now),
    )


def next_counter(conn: sqlite3.Connection, guild_id: int, name: str) -> int:
    """Increment and return a per-guild counter. Must run inside a transaction."""
    conn.execute(
        "INSERT INTO counters(guild_id, name, value) VALUES (?, ?, 1) "
        "ON CONFLICT(guild_id, name) DO UPDATE SET value = value + 1",
        (guild_id, name),
    )
    row = conn.execute("SELECT value FROM counters WHERE guild_id = ? AND name = ?", (guild_id, name)).fetchone()
    return int(row["value"])


def touch_user(conn: sqlite3.Connection, user_id: int, now: int) -> None:
    conn.execute("INSERT OR IGNORE INTO users(user_id, first_seen_at) VALUES (?, ?)", (user_id, now))


def set_dm_unavailable(conn: sqlite3.Connection, user_id: int, now: int | None) -> None:
    conn.execute("INSERT OR IGNORE INTO users(user_id, first_seen_at) VALUES (?, ?)", (user_id, now or 0))
    conn.execute("UPDATE users SET dm_unavailable_at = ? WHERE user_id = ?", (now, user_id))


# --- Guild resources (roles / categories / channels managed by /setup) ---------------------------


def resource_get(conn: sqlite3.Connection, guild_id: int, kind: str, key: str) -> int | None:
    row = conn.execute(
        "SELECT discord_id FROM guild_resources WHERE guild_id = ? AND kind = ? AND key = ?",
        (guild_id, kind, key),
    ).fetchone()
    return None if row is None else int(row["discord_id"])


def resource_set(conn: sqlite3.Connection, guild_id: int, kind: str, key: str, discord_id: int, now: int) -> None:
    conn.execute(
        "INSERT INTO guild_resources(guild_id, kind, key, discord_id, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, kind, key) DO UPDATE SET discord_id = excluded.discord_id, "
        "updated_at = excluded.updated_at",
        (guild_id, kind, key, discord_id, now),
    )


def resource_delete(conn: sqlite3.Connection, guild_id: int, kind: str, key: str) -> None:
    conn.execute("DELETE FROM guild_resources WHERE guild_id = ? AND kind = ? AND key = ?", (guild_id, kind, key))


def resource_all(conn: sqlite3.Connection, guild_id: int) -> dict[tuple[str, str], int]:
    rows = conn.execute("SELECT kind, key, discord_id FROM guild_resources WHERE guild_id = ?", (guild_id,))
    return {(str(r["kind"]), str(r["key"])): int(r["discord_id"]) for r in rows}


# --- Published messages ------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedMessage:
    key: str
    channel_id: int
    message_id: int
    content_hash: str


def published_get(conn: sqlite3.Connection, guild_id: int, key: str) -> PublishedMessage | None:
    row = conn.execute(
        "SELECT key, channel_id, message_id, content_hash FROM published_messages WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    ).fetchone()
    if row is None:
        return None
    return PublishedMessage(str(row["key"]), int(row["channel_id"]), int(row["message_id"]), str(row["content_hash"]))


def published_set(
    conn: sqlite3.Connection, guild_id: int, key: str, channel_id: int, message_id: int, content_hash: str, now: int
) -> None:
    conn.execute(
        "INSERT INTO published_messages(guild_id, key, channel_id, message_id, content_hash, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, key) DO UPDATE SET channel_id = excluded.channel_id, "
        "message_id = excluded.message_id, content_hash = excluded.content_hash, updated_at = excluded.updated_at",
        (guild_id, key, channel_id, message_id, content_hash, now),
    )


def published_delete(conn: sqlite3.Connection, guild_id: int, key: str) -> None:
    conn.execute("DELETE FROM published_messages WHERE guild_id = ? AND key = ?", (guild_id, key))


def published_with_prefix(conn: sqlite3.Connection, guild_id: int, prefix: str) -> list[PublishedMessage]:
    rows = conn.execute(
        "SELECT key, channel_id, message_id, content_hash FROM published_messages "
        "WHERE guild_id = ? AND substr(key, 1, ?) = ? ORDER BY key",
        (guild_id, len(prefix), prefix),
    )
    return [
        PublishedMessage(str(r["key"]), int(r["channel_id"]), int(r["message_id"]), str(r["content_hash"]))
        for r in rows
    ]


# --- Content overrides ---------------------------------------------------------------------------


def overrides_all(conn: sqlite3.Connection) -> dict[str, str]:
    return {str(r["key"]): str(r["value"]) for r in conn.execute("SELECT key, value FROM content_overrides")}


def override_set(conn: sqlite3.Connection, key: str, value: str, actor_id: int | None, now: int) -> None:
    conn.execute(
        "INSERT INTO content_overrides(key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
        "updated_at = excluded.updated_at",
        (key, value, actor_id, now),
    )


def override_delete(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("DELETE FROM content_overrides WHERE key = ?", (key,)).rowcount > 0
