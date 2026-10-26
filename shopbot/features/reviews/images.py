"""The photo of a review written with ``/review``: checking the upload, downloading it, and keeping it until the
review no longer needs it.

Discord's attachment links are signed and expire, and a review can wait days for a staff decision, so the picture is
downloaded when the review is submitted and its bytes are stored in ``review_images`` (one row per review, in the same
transaction as the review). The approval message and the published review attach those bytes again
(``attachment://review-<review_id>.<ext>``). Once the review is published, rejected or removed nothing posts it again,
so the bytes are released (``data`` cleared, ``released_at`` set); what describes the picture stays. A staff decision
whose durable follow-up (``reviews.decided``) has not run yet keeps them: that follow-up shows the decision on the
approval message with the photo, and releases them afterwards. A later edit of the approval message (e.g. the
"removed" state) then shows the card without the photo. SQLite overwrites released bytes in the database file
(``secure_delete``, ``shopbot.db.database``), but a backup taken before the release keeps them.

The accepted pictures: PNG, JPEG, GIF and WebP (checked from the declared type or file name before the form opens,
and from the downloaded bytes when the review is sent), at most ``image_limit``: the server's upload limit, capped by
the per-message file limit every bot message is checked against, minus the largest banner a message with the photo
carries (the reviews banner, or one of the approval message's banners). The original file name is never used (it
could name the member).

Repository functions (``fn(conn, ...)``, use inside ``app.db.run``/``transaction``): ``store_image``, ``get_image``,
``release_image``, ``releasable_ids``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import aiohttp
import discord

from shopbot.logging_setup import log_event
from shopbot.ui.image_types import CONTENT_TYPES, sniff
from shopbot.ui.limits import ATTACHMENT_SCHEME, FILES_TOTAL_BYTES
from shopbot.ui.spec import FileSpec

if TYPE_CHECKING:
    from shopbot.config.banner_ids import BannerId
    from shopbot.context import App

log = logging.getLogger(__name__)

# The largest picture the table accepts (the per-message file limit; ``image_limit`` is usually smaller).
MAX_STORED_BYTES = 10 * 1024 * 1024
CDN_PREFIXES = ("https://cdn.discordapp.com/", "https://media.discordapp.net/")
DOWNLOAD_TIMEOUT_SECONDS = 60
# Declared file name endings accepted when an upload declares no content type.
_EXTENSIONS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
# Declared content types accepted before downloading: the four formats and the usual other names of JPEG.
_DECLARED_TYPES = frozenset({*CONTENT_TYPES, "image/jpg", "image/pjpeg"})


class Upload(Protocol):
    """What ``/review`` reads from its ``image`` option (``discord.Attachment``)."""

    @property
    def filename(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def size(self) -> int: ...

    @property
    def content_type(self) -> str | None: ...


@dataclass(frozen=True)
class UploadRef:
    """An accepted upload, before it is downloaded (kept with the open form)."""

    url: str
    size: int


@dataclass(frozen=True)
class Picture:
    """Downloaded, validated bytes of a review photo."""

    content_type: str
    data: bytes = field(repr=False)
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ReviewImage:
    """A stored review photo. ``data`` is None once released (or when only the description was loaded)."""

    review_id: int
    content_type: str
    size: int
    sha256: str
    released: bool
    data: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def filename(self) -> str:
        """The name messages attach it under (never the uploaded file's name)."""
        return attachment_name(self.review_id, self.content_type)

    def file_spec(self) -> FileSpec | None:
        """The file to attach, or None when its bytes are not loaded (released)."""
        if self.data is None:
            return None
        return FileSpec(
            filename=self.filename,
            sha256=self.sha256,
            size=self.size,
            content_type=self.content_type,
            data=self.data,
        )


class ImageRejected(Exception):
    """reason: 'type' (not a PNG/JPEG/GIF/WebP picture), 'size' (larger than ``image_limit``), 'empty', 'download'
    (Discord did not deliver the file)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def attachment_name(review_id: int, content_type: str) -> str:
    return f"review-{review_id}.{CONTENT_TYPES.get(content_type, 'png')}"


def attachment_url(filename: str) -> str:
    return f"{ATTACHMENT_SCHEME}{filename}"


# -- limits and checks ------------------------------------------------------------------------------------------------


def image_limit(app: App, banners: tuple[BannerId, ...]) -> int:
    """The largest photo a review accepts: the server's upload limit (boost level), capped by the per-message file
    limit, minus the largest of ``banners`` (the banners the messages showing the photo carry)."""
    limit = getattr(app.guild, "filesize_limit", None)
    guild_limit = limit if isinstance(limit, int) and limit > 0 else discord.utils.DEFAULT_FILE_SIZE_LIMIT_BYTES
    pictures = [app.ui.banner(banner) for banner in banners]
    largest = max((picture.size for picture in pictures if picture is not None), default=0)
    return max(0, min(guild_limit, FILES_TOTAL_BYTES, MAX_STORED_BYTES) - largest)


def megabytes(size: int) -> str:
    """The limit as customers read it: whole megabytes, rounded down (one decimal below 1 MB)."""
    if size >= 1_048_576:
        return str(size // 1_048_576)
    return f"{size / 1_048_576:.1f}"


def _declared_type(upload: Upload) -> str | None:
    """The accepted picture type the upload declares (its content type, or else its file name ending), or None."""
    if upload.content_type:
        declared = upload.content_type.split(";", 1)[0].strip().lower()
        return declared if declared in _DECLARED_TYPES else None
    suffix = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    return _EXTENSIONS.get(suffix)


def check_upload(upload: Upload, limit: int) -> UploadRef:
    """Refuse before the form opens (so nothing the member types is lost): empty, too large, not declared as a PNG,
    JPEG, GIF or WebP picture (e.g. HEIC or SVG), or not a Discord upload. The downloaded bytes are checked again when
    the review is sent (``validate_bytes``)."""
    if upload.size <= 0:
        raise ImageRejected("empty")
    if upload.size > limit:
        raise ImageRejected("size")
    if _declared_type(upload) is None or not upload.url.startswith(CDN_PREFIXES):
        raise ImageRejected("type")
    return UploadRef(url=upload.url, size=upload.size)


def validate_bytes(data: bytes, limit: int) -> Picture:
    """Downloaded bytes: the size limit and a real PNG/JPEG/GIF/WebP signature."""
    if not data:
        raise ImageRejected("empty")
    if len(data) > limit:
        raise ImageRejected("size")
    content_type = sniff(data)
    if content_type is None:
        raise ImageRejected("type")
    return Picture(content_type=content_type, data=data, sha256=hashlib.sha256(data).hexdigest())


async def download(app: App, upload: UploadRef, limit: int) -> Picture:
    """Download an accepted upload and validate its bytes (raises ``ImageRejected``)."""
    http = getattr(app.bot, "http", None)
    if http is None or not upload.url.startswith(CDN_PREFIXES):
        raise ImageRejected("download")
    try:
        data = await asyncio.wait_for(http.get_from_cdn(upload.url), DOWNLOAD_TIMEOUT_SECONDS)
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as exc:
        log_event(log, logging.WARNING, "reviews_image_download_failed", error=type(exc).__name__)
        raise ImageRejected("download") from None
    if not isinstance(data, bytes):
        raise ImageRejected("download")
    return validate_bytes(data, limit)


# -- repository ----------------------------------------------------------------------------------------------------

_META_SQL = "SELECT review_id, content_type, size, sha256, released_at FROM review_images WHERE review_id = ?"
_FULL_SQL = "SELECT review_id, content_type, size, sha256, released_at, data FROM review_images WHERE review_id = ?"


def _row(row: sqlite3.Row, *, with_data: bool) -> ReviewImage:
    data = row["data"] if with_data else None
    return ReviewImage(
        review_id=int(row["review_id"]),
        content_type=str(row["content_type"]),
        size=int(row["size"]),
        sha256=str(row["sha256"]),
        released=row["released_at"] is not None,
        data=None if data is None else bytes(data),
    )


def store_image(conn: sqlite3.Connection, review_id: int, *, guild_id: int, picture: Picture, now: int) -> None:
    """Keep the photo of a review that was just written (in the review's transaction)."""
    conn.execute(
        "INSERT INTO review_images(review_id, guild_id, content_type, size, sha256, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (review_id, guild_id, picture.content_type, picture.size, picture.sha256, sqlite3.Binary(picture.data), now),
    )


def get_image(conn: sqlite3.Connection, review_id: int, *, with_data: bool = True) -> ReviewImage | None:
    row = conn.execute(_FULL_SQL if with_data else _META_SQL, (review_id,)).fetchone()
    return None if row is None else _row(row, with_data=with_data)


def release_image(conn: sqlite3.Connection, review_id: int, *, now: int) -> bool:
    """Forget the bytes of a review's photo once nothing posts it again: the review is published, rejected or
    declined (removed), and its decision follow-up (if any) has run. False when there is nothing to release yet."""
    return (
        conn.execute(
            "UPDATE review_images SET data = NULL, released_at = ? WHERE review_id = ? AND data IS NOT NULL "
            "AND review_id IN (SELECT id FROM reviews WHERE status IN ('published', 'rejected', 'declined')) "
            # Not while the staff decision's follow-up still has to run (its dedupe key is ``repo.decision_key``).
            "AND NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.status = 'pending' "
            "AND t.dedupe_key = 'reviews.decided:' || review_images.review_id)",
            (now, review_id),
        ).rowcount
        > 0
    )


def releasable_ids(conn: sqlite3.Connection, guild_id: int) -> list[int]:
    """Reviews whose photo bytes are still stored although nothing posts them again (e.g. the process stopped right
    after the publication was recorded). A review whose decision follow-up is still to run is left to it."""
    rows = conn.execute(
        "SELECT i.review_id FROM review_images i JOIN reviews r ON r.id = i.review_id WHERE i.guild_id = ? "
        "AND i.data IS NOT NULL AND r.status IN ('published', 'rejected', 'declined') "
        "AND NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.status = 'pending' "
        "AND t.dedupe_key = 'reviews.decided:' || i.review_id) ORDER BY i.review_id",
        (guild_id,),
    )
    return [int(row["review_id"]) for row in rows]
