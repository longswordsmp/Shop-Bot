"""Announcement rows (table ``announcements``): plain synchronous units of work for ``app.db.run``/``transaction``.

Lifecycle of a row:

- ``draft``: created by ``/announce`` when the form opens (holds the image reference and the mention choice).
- ``posting``: claimed by exactly one form submission (conditional UPDATE), while the bot validates and posts.
- ``published``: the message exists in the announcements channel; the typed text is cleared.
- back to ``draft`` when posting failed (nothing was posted); the typed text is kept so staff can retry.
- ``interrupted``: the process stopped while posting, so whether the message was posted is unknown. Such a row is
  never retried automatically (that could post the announcement twice); staff are told through the audit log.

Unpublished rows are deleted once they expire (``purge_expired``).

A preset announcement (``/announce preset:<id>``) uses the same lifecycle: its row (``preset_id`` set, no typed text)
is created when the private preview opens and claimed by the Post button. When the preset has a coupon, the row keeps
the discount code the preview showed (``discount_code_id``): the claim refuses a code that is no longer that one
(``changed``: the coupon was ended and created again, so its terms may differ from the preview) and a second
announcement of the same offer (``announced``); ``offer_ended_at`` is set once the posted claim button was switched to
"offer ended". A typed form and a preset's preview never claim each other's rows.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

ClaimOutcome = Literal[
    "claimed", "missing", "not_owner", "busy", "published", "interrupted", "expired", "announced", "changed"
]


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
    preset_id: str | None = None
    discount_code_id: int | None = None
    offer_ended_at: int | None = None

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
        preset_id=row["preset_id"],
        discount_code_id=row["discount_code_id"],
        offer_ended_at=row["offer_ended_at"],
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
    preset_id: str | None = None,
    discount_code_id: int | None = None,
) -> Announcement:
    """A new draft. A preset's preview stores the code its claim button showed (``discount_code_id``)."""
    cursor = conn.execute(
        "INSERT INTO announcements (guild_id, token, actor_id, mention_role, image_url, image_filename, "
        "image_content_type, image_size, status, created_at, expires_at, updated_at, preset_id, discount_code_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)",
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
            preset_id,
            discount_code_id,
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
    now: int,
    title: str | None = None,
    message: str | None = None,
    field_values: dict[str, str] | None = None,
    discount_code_id: int | None = None,
    preset: bool = False,
) -> tuple[ClaimOutcome, Announcement | None]:
    """Claim a draft for publishing (run inside a transaction). Only one submission can ever get ``claimed``.

    The submitted text is stored with the claim so a failed attempt can be retried with the form prefilled (a preset
    has none). ``preset``: claimed by a preset's Post button (a typed form's submission never claims a preset's row,
    nor the other way round: ``missing``). ``discount_code_id``: the live code a preset's claim button would claim
    now; ``changed`` when it is not the code the preview showed, ``announced`` (with that announcement) when another
    announcement of the same preset already posts or posted it.
    """
    current = get_by_token(conn, guild_id, token)
    if current is None or (current.preset_id is not None) != preset:
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
    if current.preset_id is not None and current.discount_code_id != discount_code_id:
        return "changed", current
    if discount_code_id is not None and current.preset_id is not None:
        other = announced(conn, guild_id, current.preset_id, discount_code_id)
        if other is not None:
            return "announced", other
    values = json.dumps(field_values, ensure_ascii=False, sort_keys=True) if field_values is not None else None
    updated = conn.execute(
        "UPDATE announcements SET status = 'posting', claimed_by = ?, title = ?, message = ?, field_values = ?, "
        "discount_code_id = ?, updated_at = ? WHERE id = ? AND status = 'draft'",
        (claimed_by, title, message, values, discount_code_id, now, current.id),
    ).rowcount
    if updated != 1:
        return "busy", current
    claimed = get_by_id(conn, current.id)
    return "claimed", claimed


def release(conn: sqlite3.Connection, announcement_id: int, claimed_by: str, now: int) -> bool:
    """Return a claimed row to ``draft`` after an attempt that posted nothing (the typed text is kept; a preset's row
    keeps the code its preview showed, and the next attempt checks the live code against it again)."""
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


def cancel(conn: sqlite3.Connection, guild_id: int, token: str, actor_id: int) -> bool:
    """Delete a preset's preview that was never posted (Cancel). False when it is not an open draft of this staff
    member (already posting or posted, opened by someone else, or gone)."""
    return (
        conn.execute(
            "DELETE FROM announcements WHERE guild_id = ? AND token = ? AND actor_id = ? AND status = 'draft' "
            "AND preset_id IS NOT NULL",
            (guild_id, token, actor_id),
        ).rowcount
        == 1
    )


# -- preset offers (an announcement whose claim button claims a discount code) -----------------------------------


def announced(conn: sqlite3.Connection, guild_id: int, preset_id: str, code_id: int) -> Announcement | None:
    """The announcement of this preset that posts or posted this code (at most one; see the module docstring)."""
    row = conn.execute(
        "SELECT * FROM announcements WHERE guild_id = ? AND preset_id = ? AND discount_code_id = ? "
        "AND status IN ('posting', 'published') ORDER BY id LIMIT 1",
        (guild_id, preset_id, code_id),
    ).fetchone()
    return None if row is None else _row(row)


def forget_offer(conn: sqlite3.Connection, announcement_id: int, now: int) -> bool:
    """A posted offer whose message was deleted in Discord: it no longer counts as announced (the preset may be
    posted again) and its button is not switched any more."""
    return (
        conn.execute(
            "UPDATE announcements SET discount_code_id = NULL, updated_at = ? WHERE id = ? AND status = 'published'",
            (now, announcement_id),
        ).rowcount
        == 1
    )


def open_offers(conn: sqlite3.Connection, guild_id: int, code_id: int) -> list[Announcement]:
    """Posted announcements whose claim button claims this code and was not switched to "offer ended" yet."""
    rows = conn.execute(
        "SELECT * FROM announcements WHERE guild_id = ? AND discount_code_id = ? AND status = 'published' "
        "AND offer_ended_at IS NULL ORDER BY id",
        (guild_id, code_id),
    ).fetchall()
    return [_row(r) for r in rows]


def ended_offers(conn: sqlite3.Connection, guild_id: int, now: int) -> list[Announcement]:
    """Posted announcements whose code ended or expired while their claim button still offers it (startup sweep)."""
    rows = conn.execute(
        "SELECT a.* FROM announcements a JOIN discount_codes c ON c.id = a.discount_code_id "
        "WHERE a.guild_id = ? AND a.status = 'published' AND a.offer_ended_at IS NULL "
        "AND (c.ended_at IS NOT NULL OR (c.expires_at IS NOT NULL AND c.expires_at <= ?)) ORDER BY a.id",
        (guild_id, now),
    ).fetchall()
    return [_row(r) for r in rows]


def mark_offer_ended(conn: sqlite3.Connection, announcement_id: int, now: int) -> bool:
    """Record that the posted claim button now shows "offer ended" (or that its message is gone)."""
    return (
        conn.execute(
            "UPDATE announcements SET offer_ended_at = ?, updated_at = ? WHERE id = ? AND offer_ended_at IS NULL",
            (now, now, announcement_id),
        ).rowcount
        == 1
    )


def purge_expired(conn: sqlite3.Connection, guild_id: int, now: int) -> int:
    """Delete expired forms that were never published (and with them any text typed into them)."""
    return conn.execute(
        "DELETE FROM announcements WHERE guild_id = ? AND status IN ('draft', 'interrupted') AND expires_at <= ?",
        (guild_id, now),
    ).rowcount
