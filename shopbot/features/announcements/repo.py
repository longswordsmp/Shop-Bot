"""Announcement rows (table ``announcements``): plain synchronous units of work for ``app.db.run``/``transaction``.

Lifecycle of a row:

- ``draft``: created by ``/announce`` when the form opens (holds the image reference and the mention choice).
- ``posting``: claimed by exactly one form submission (conditional UPDATE), while the bot validates and posts.
- ``published``: the message exists in the announcements channel; the typed text is cleared.
- back to ``draft`` when posting failed (nothing was posted); the typed text is kept so staff can retry.
- ``interrupted``: the process stopped while posting, so whether the message was posted is unknown. Such a row is
  never retried automatically (that could post the announcement twice); staff are told through the audit log.

Unpublished rows are deleted once they expire (``purge_expired``).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

ClaimOutcome = Literal["claimed", "missing", "not_owner", "busy", "published", "interrupted", "expired"]


@dataclass(frozen=True)
class ImageRef:
    """The uploaded attachment of a ``/announce`` command, downloaded again when the form is submitted."""

    url: str
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True)
class Announcement:
    id: int
    guild_id: int
    token: str
    actor_id: int
    mention_role: str | None
    image: ImageRef | None
    status: str
    title: str | None
    message: str | None
    field_values: dict[str, str] = field(default_factory=dict)
    channel_id: int | None = None
    message_id: int | None = None
    created_at: int = 0
    expires_at: int = 0
    published_at: int | None = None

    def expired(self, now: int) -> bool:
        return now >= self.expires_at


def _row(row: sqlite3.Row) -> Announcement:
    image = None
    if row["image_url"] is not None:
        image = ImageRef(
            url=str(row["image_url"]),
            filename=str(row["image_filename"]),
            content_type=str(row["image_content_type"] or ""),
            size=int(row["image_size"] or 0),
        )
    values: dict[str, str] = {}
    if row["field_values"]:
        loaded = json.loads(row["field_values"])
        if isinstance(loaded, dict):
            values = {str(k): str(v) for k, v in loaded.items()}
    return Announcement(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        token=str(row["token"]),
        actor_id=int(row["actor_id"]),
        mention_role=row["mention_role"],
        image=image,
        status=str(row["status"]),
        title=row["title"],
        message=row["message"],
        field_values=values,
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        created_at=int(row["created_at"]),
        expires_at=int(row["expires_at"]),
        published_at=row["published_at"],
    )


def create_draft(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    token: str,
    actor_id: int,
    mention_role: str | None,
    image: ImageRef | None,
    now: int,
    ttl_seconds: int,
) -> Announcement:
    cursor = conn.execute(
        "INSERT INTO announcements (guild_id, token, actor_id, mention_role, image_url, image_filename, "
        "image_content_type, image_size, status, created_at, expires_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
        (
            guild_id,
            token,
            actor_id,
            mention_role,
            image.url if image else None,
            image.filename if image else None,
            image.content_type if image else None,
            image.size if image else None,
            now,
            now + ttl_seconds,
            now,
        ),
    )
    created = get_by_id(conn, int(cursor.lastrowid or 0))
    assert created is not None
    return created


def get_by_id(conn: sqlite3.Connection, announcement_id: int) -> Announcement | None:
    row = conn.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone()
    return None if row is None else _row(row)


def get_by_token(conn: sqlite3.Connection, guild_id: int, token: str) -> Announcement | None:
    row = conn.execute("SELECT * FROM announcements WHERE guild_id = ? AND token = ?", (guild_id, token)).fetchone()
    return None if row is None else _row(row)


def claim(
    conn: sqlite3.Connection,
    guild_id: int,
    token: str,
    *,
    actor_id: int,
    claimed_by: str,
    title: str,
    message: str,
    field_values: dict[str, str],
    now: int,
) -> tuple[ClaimOutcome, Announcement | None]:
    """Claim a draft for publishing (run inside a transaction). Only one submission can ever get ``claimed``.

    The submitted text is stored with the claim so a failed attempt can be retried with the form prefilled.
    """
    current = get_by_token(conn, guild_id, token)
    if current is None:
        return "missing", None
    if current.actor_id != actor_id:
        return "not_owner", current
    if current.status == "published":
        return "published", current
    if current.status == "posting":
        return "busy", current
    if current.status == "interrupted":
        return "interrupted", current
    if current.expired(now):
        return "expired", current
    updated = conn.execute(
        "UPDATE announcements SET status = 'posting', claimed_by = ?, title = ?, message = ?, field_values = ?, "
        "updated_at = ? WHERE id = ? AND status = 'draft'",
        (claimed_by, title, message, json.dumps(field_values, ensure_ascii=False, sort_keys=True), now, current.id),
    ).rowcount
    if updated != 1:
        return "busy", current
    claimed = get_by_id(conn, current.id)
    return "claimed", claimed


def release(conn: sqlite3.Connection, announcement_id: int, claimed_by: str, now: int) -> bool:
    """Return a claimed row to ``draft`` after an attempt that posted nothing (the typed text is kept)."""
    return (
        conn.execute(
            "UPDATE announcements SET status = 'draft', claimed_by = NULL, updated_at = ? "
            "WHERE id = ? AND status = 'posting' AND claimed_by = ?",
            (now, announcement_id, claimed_by),
        ).rowcount
        == 1
    )


def mark_published(
    conn: sqlite3.Connection,
    announcement_id: int,
    claimed_by: str,
    *,
    channel_id: int,
    message_id: int,
    now: int,
) -> bool:
    """Record the posted message and forget the typed text (it now lives only in Discord)."""
    return (
        conn.execute(
            "UPDATE announcements SET status = 'published', claimed_by = NULL, channel_id = ?, message_id = ?, "
            "title = NULL, message = NULL, field_values = NULL, published_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'posting' AND claimed_by = ?",
            (channel_id, message_id, now, now, announcement_id, claimed_by),
        ).rowcount
        == 1
    )


def mark_interrupted(conn: sqlite3.Connection, guild_id: int, current_process: str, now: int) -> list[Announcement]:
    """Rows a previous process was posting when it stopped (outcome unknown). Never retried automatically."""
    rows = conn.execute(
        "SELECT * FROM announcements WHERE guild_id = ? AND status = 'posting' AND claimed_by != ?",
        (guild_id, current_process),
    ).fetchall()
    affected = [_row(r) for r in rows]
    for item in affected:
        conn.execute(
            "UPDATE announcements SET status = 'interrupted', claimed_by = NULL, title = NULL, message = NULL, "
            "field_values = NULL, updated_at = ? WHERE id = ? AND status = 'posting'",
            (now, item.id),
        )
    return affected


def purge_expired(conn: sqlite3.Connection, guild_id: int, now: int) -> int:
    """Delete expired forms that were never published (and with them any text typed into them)."""
    return conn.execute(
        "DELETE FROM announcements WHERE guild_id = ? AND status IN ('draft', 'interrupted') AND expires_at <= ?",
        (guild_id, now),
    ).rowcount
