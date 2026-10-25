"""Product images uploaded to the bot, and how messages show product images.

Discord attachment links are signed and expire, so an uploaded image is stored in the database
(``product_images``, one row per product) and attached to every message that shows it, again on every edit:
the embed shows ``attachment://<name>`` and the message carries the file (``MessageSpec.files``). After a restart
the bytes come from the database, so images keep displaying. A product shows either its uploaded image or its
https image link (``products.image_url``); setting one clears the other.

Public API (also for other features, e.g. new-arrivals updates)
-------------------------------------------------------------------
- ``await image_for(app, product) -> (embed image URL or None, files)``: what a product card shows. Uploaded
  image: ``("attachment://product-<id>-<hash>.<ext>", [FileSpec])`` with the bytes loaded; linked image:
  ``(url, [])``; none: ``(None, [])``. Put the URL in the embed's ``image_url`` and the files in
  ``MessageSpec.files`` (use a tuple, also when empty, for a message that is edited later, so an edit keeps,
  replaces or removes the image).
- ``embed_image(product, image)``: the same decision, pure (``image`` from ``get_image``/``image_refs``).
- ``await load_files(app, spec)``: an ``upsert_message(prepare=...)`` hook that loads the bytes of file
  references (``FileSpec`` without data) only when a message is actually sent or edited.
- Repository functions (``fn(conn, ...)``, use inside ``app.db.run``/``transaction``): ``get_image``,
  ``image_refs``, ``data_by_sha``, ``store_image``, ``delete_image``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from shopbot.ui.image_types import CONTENT_TYPES, sniff
from shopbot.ui.limits import ATTACHMENT_SCHEME
from shopbot.ui.spec import FileSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.catalog.repo import Product

# Largest accepted upload (one image always fits Discord's 10 MiB per-message upload limit with room to spare).
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ORIGINAL_NAME_MAX = 255


# ---------------------------------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductImage:
    product_id: int
    guild_id: int
    filename: str  # the uploaded file's original name (shown to staff only)
    content_type: str
    size: int
    sha256: str
    uploaded_by: int | None
    created_at: int
    data: bytes | None = field(default=None, repr=False, compare=False)  # None: metadata only

    @property
    def attachment_name(self) -> str:
        """The name messages attach it under; it changes with the content, so a new image is never cached."""
        return f"product-{self.product_id}-{self.sha256[:16]}.{CONTENT_TYPES.get(self.content_type, 'img')}"

    @property
    def url(self) -> str:
        return f"{ATTACHMENT_SCHEME}{self.attachment_name}"

    def file_spec(self) -> FileSpec:
        return FileSpec(
            filename=self.attachment_name,
            sha256=self.sha256,
            size=self.size,
            content_type=self.content_type,
            data=self.data,
        )


def embed_image(product: Product, image: ProductImage | None) -> tuple[str | None, list[FileSpec]]:
    """(embed image URL, files to attach) for ``product``: the uploaded image if any, else its image link."""
    if image is not None:
        return image.url, [image.file_spec()]
    return product.image_url, []


# ---------------------------------------------------------------------------------------------------
# Validation of uploads
# ---------------------------------------------------------------------------------------------------


class ImageRejected(Exception):
    """reason: 'type' (not a PNG/JPEG/GIF/WebP image), 'size' (larger than MAX_IMAGE_BYTES), 'empty',
    'download' (Discord did not deliver the file)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Upload(Protocol):
    """What ``read_upload`` needs from an uploaded file (``discord.Attachment``)."""

    filename: str
    size: int
    content_type: str | None

    async def read(self) -> bytes: ...


@dataclass(frozen=True)
class UploadedImage:
    filename: str
    content_type: str
    data: bytes = field(repr=False)
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


def _declared_type(value: str | None) -> str | None:
    return value.split(";", 1)[0].strip().lower() if value else None


def check_declared(upload: Upload) -> None:
    """Refuse before downloading: empty, too large, or declared as something that is not an image at all. (The
    accepted formats are decided from the downloaded bytes, so an unusual image label such as ``image/jpg`` is not
    refused here.)"""
    if upload.size <= 0:
        raise ImageRejected("empty")
    if upload.size > MAX_IMAGE_BYTES:
        raise ImageRejected("size")
    declared = _declared_type(upload.content_type)
    if declared is not None and not declared.startswith("image/"):
        raise ImageRejected("type")


def validate_bytes(filename: str, data: bytes) -> UploadedImage:
    """Validate downloaded bytes: size limit and a real PNG/JPEG/GIF/WebP signature."""
    if not data:
        raise ImageRejected("empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected("size")
    content_type = sniff(data)
    if content_type is None:
        raise ImageRejected("type")
    name = (filename or "image").strip() or "image"
    if len(name) > ORIGINAL_NAME_MAX:
        name = name[-ORIGINAL_NAME_MAX:]  # staff-only metadata; the attachment name is generated
    return UploadedImage(filename=name, content_type=content_type, data=data, sha256=hashlib.sha256(data).hexdigest())


async def read_upload(upload: Upload) -> UploadedImage:
    """Check the declared size/type, download the file, then validate the bytes themselves."""
    check_declared(upload)
    try:
        data = await upload.read()
    except Exception as exc:  # discord.HTTPException / network errors: the file could not be fetched
        raise ImageRejected("download") from exc
    return validate_bytes(upload.filename, data)


# ---------------------------------------------------------------------------------------------------
# Repository (synchronous, conn-first; writes inside a transaction)
# ---------------------------------------------------------------------------------------------------

# Static SQL (no string building): metadata with or without the bytes.
_GET_META_SQL = (
    "SELECT product_id, guild_id, filename, content_type, size, sha256, uploaded_by, created_at "
    "FROM product_images WHERE guild_id = ? AND product_id = ?"
)
_GET_FULL_SQL = (
    "SELECT product_id, guild_id, filename, content_type, size, sha256, uploaded_by, created_at, data "
    "FROM product_images WHERE guild_id = ? AND product_id = ?"
)
_REFS_SQL = (
    "SELECT product_id, guild_id, filename, content_type, size, sha256, uploaded_by, created_at "
    "FROM product_images WHERE guild_id = ? AND product_id IN (SELECT value FROM json_each(?))"
)


def _row(r: sqlite3.Row, *, with_data: bool) -> ProductImage:
    return ProductImage(
        product_id=int(r["product_id"]),
        guild_id=int(r["guild_id"]),
        filename=str(r["filename"]),
        content_type=str(r["content_type"]),
        size=int(r["size"]),
        sha256=str(r["sha256"]),
        uploaded_by=None if r["uploaded_by"] is None else int(r["uploaded_by"]),
        created_at=int(r["created_at"]),
        data=bytes(r["data"]) if with_data else None,
    )


def get_image(
    conn: sqlite3.Connection, guild_id: int, product_id: int, *, with_data: bool = True
) -> ProductImage | None:
    sql = _GET_FULL_SQL if with_data else _GET_META_SQL
    row = conn.execute(sql, (guild_id, product_id)).fetchone()
    return None if row is None else _row(row, with_data=with_data)


def image_refs(conn: sqlite3.Connection, guild_id: int, product_ids: Iterable[int]) -> dict[int, ProductImage]:
    """Metadata (no bytes) of the uploaded images of ``product_ids``, by product id."""
    ids = sorted({int(i) for i in product_ids})
    if not ids:
        return {}
    rows = conn.execute(_REFS_SQL, (guild_id, json.dumps(ids)))
    return {int(r["product_id"]): _row(r, with_data=False) for r in rows}


def data_by_sha(conn: sqlite3.Connection, guild_id: int, digests: Iterable[str]) -> dict[str, bytes]:
    """Stored bytes by sha256 (a digest that is no longer stored is missing from the result)."""
    wanted = sorted(set(digests))
    if not wanted:
        return {}
    rows = conn.execute(
        "SELECT sha256, data FROM product_images WHERE guild_id = ? AND sha256 IN (SELECT value FROM json_each(?))",
        (guild_id, json.dumps(wanted)),
    )
    return {str(r["sha256"]): bytes(r["data"]) for r in rows}


def store_image(
    conn: sqlite3.Connection,
    guild_id: int,
    product_id: int,
    image: UploadedImage,
    *,
    actor_id: int | None,
    now: int,
) -> bool:
    """Make ``image`` the product's uploaded image. False (nothing written) when it already is."""
    current = get_image(conn, guild_id, product_id, with_data=False)
    if current is not None and current.sha256 == image.sha256 and current.content_type == image.content_type:
        return False
    conn.execute(
        "INSERT INTO product_images(product_id, guild_id, filename, content_type, size, sha256, data, uploaded_by, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(product_id) DO UPDATE SET "
        "guild_id = excluded.guild_id, filename = excluded.filename, content_type = excluded.content_type, "
        "size = excluded.size, sha256 = excluded.sha256, data = excluded.data, uploaded_by = excluded.uploaded_by, "
        "created_at = excluded.created_at",
        (
            product_id,
            guild_id,
            image.filename,
            image.content_type,
            image.size,
            image.sha256,
            sqlite3.Binary(image.data),
            actor_id,
            now,
        ),
    )
    return True


def delete_image(conn: sqlite3.Connection, guild_id: int, product_id: int) -> bool:
    cursor = conn.execute("DELETE FROM product_images WHERE guild_id = ? AND product_id = ?", (guild_id, product_id))
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------------------------------


async def image_for(app: App, product: Product) -> tuple[str | None, list[FileSpec]]:
    """What a card of ``product`` shows (see the module docstring); the uploaded image's bytes are loaded."""
    image = await app.db.run(get_image, app.env.guild_id, product.id)
    return embed_image(product, image)


async def refs_for(app: App, products: Sequence[Product]) -> Mapping[int, ProductImage]:
    return await app.db.run(image_refs, app.env.guild_id, [p.id for p in products])


class StaleImage(Exception):
    """A file reference's bytes are no longer stored (the image changed after the message was built)."""


async def load_files(app: App, spec: MessageSpec) -> MessageSpec:
    """``upsert_message`` prepare hook: the spec with the bytes of every file reference loaded."""
    files = spec.files or ()
    missing = [f.sha256 for f in files if f.data is None]
    if not missing:
        return spec
    found = await app.db.run(data_by_sha, app.env.guild_id, missing)
    loaded: list[FileSpec] = []
    for item in files:
        if item.data is not None:
            loaded.append(item)
            continue
        data = found.get(item.sha256)
        if data is None:
            raise StaleImage(item.filename)
        loaded.append(replace(item, data=data))
    return replace(spec, files=tuple(loaded))
