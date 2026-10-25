"""Image formats the bot uploads (product images, banners), recognised from the file's first bytes."""

from __future__ import annotations

# Accepted image types and the file extension used in attachment names.
CONTENT_TYPES: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def sniff(data: bytes) -> str | None:
    """The image type from the file's first bytes (the declared type is not trusted), or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
