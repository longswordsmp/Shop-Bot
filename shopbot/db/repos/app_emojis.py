"""Uploaded application emojis (the bot's custom icons): name -> Discord emoji id and the picture's sha256."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class AppEmoji:
    name: str
    emoji_id: int
    sha256: str


def all_emojis(conn: sqlite3.Connection) -> dict[str, AppEmoji]:
    rows = conn.execute("SELECT name, emoji_id, sha256 FROM app_emojis ORDER BY name")
    return {str(r["name"]): AppEmoji(str(r["name"]), int(r["emoji_id"]), str(r["sha256"])) for r in rows}


def upsert(conn: sqlite3.Connection, name: str, emoji_id: int, sha256: str, now: int) -> None:
    conn.execute(
        "INSERT INTO app_emojis(name, emoji_id, sha256, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE "
        "SET emoji_id = excluded.emoji_id, sha256 = excluded.sha256, updated_at = excluded.updated_at",
        (name, emoji_id, sha256, now),
    )


def delete(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM app_emojis WHERE name = ?", (name,))
