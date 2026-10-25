"""Audit events: an append-only record of administrative and state-changing actions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    id: int
    guild_id: int
    actor_id: int | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    created_at: int


def insert(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    actor_id: int | None,
    action: str,
    target_type: str | None,
    target_id: str | int | None,
    details: dict[str, Any] | None,
    now: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO audit_events(guild_id, actor_id, action, target_type, target_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            actor_id,
            action,
            target_type,
            None if target_id is None else str(target_id),
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )
    return int(cursor.lastrowid or 0)


def _row(r: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        actor_id=None if r["actor_id"] is None else int(r["actor_id"]),
        action=str(r["action"]),
        target_type=r["target_type"],
        target_id=r["target_id"],
        details=json.loads(r["details"]),
        created_at=int(r["created_at"]),
    )


def recent(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    limit: int = 20,
    target_type: str | None = None,
    target_id: str | None = None,
) -> list[AuditEvent]:
    sql = "SELECT * FROM audit_events WHERE guild_id = ?"
    params: list[Any] = [guild_id]
    if target_type is not None:
        sql += " AND target_type = ?"
        params.append(target_type)
    if target_id is not None:
        sql += " AND target_id = ?"
        params.append(target_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    return [_row(r) for r in conn.execute(sql, params)]
