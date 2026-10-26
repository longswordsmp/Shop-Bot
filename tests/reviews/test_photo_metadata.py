"""A review photo is stored and attached without the file's metadata (GPS position, camera, owner): crafted JPEG, PNG
and WebP files with Exif, XMP, IPTC, comment, text and Content Credentials blocks lose them and stay valid files of the
same type with the same image data; GIF and files with nothing to remove are kept byte for byte. A photo stored before
stripping existed is stripped when it is attached, and stripping never runs in the bot's event loop."""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import threading
import zlib
from pathlib import Path

import pytest

from shopbot.features.reviews import images, service
from shopbot.features.reviews.preview import sample_photo_png
from shopbot.ui.image_metadata import _exif_orientation, strip_metadata
from shopbot.ui.image_types import sniff
from tests.reviews.util import make_order, reviews_channel
from tests.reviews.write_util import (
    CDN_URL,
    Upload,
    cdn,
    channel_by_key,
    make_write_env,
    reviews_of,
    stored_image,
    write,
)
from tests.reviews_approval.util import APPROVAL_CHANNEL, approve

# Private details a phone writes into a photo; none of them may be left in the stored picture.
GPS = b"GPS 40.7128 N 74.0060 W"
OWNER = b"Camera owner Jane Doe"
SECRETS = (GPS, OWNER, b"Exif", b"http://ns.adobe.com/xap/1.0/", b"Photoshop 3.0")

# A real 8x8 grey baseline JPEG (generated, never a photo): SOI, APP0 (JFIF), DQT, SOF0, two DHT, SOS, data, EOI.
CLEAN_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300140e0f120f0d14121012171514181e32211e1c1c1e3d2c"
    "2e243249404c4b47404645505a736250556d5645466488656d777b8182814e608d978c7d96737e817cffc0000b080008"
    "000801011100ffc40014000100000000000000000000000000000000ffc4001410010000000000000000000000000000"
    "0000ffda0008010100003f003fffd9"
)
# A real 1x1 lossless WebP (simple format: one VP8L chunk).
SIMPLE_WEBP = bytes.fromhex("524946461e000000574542505650384c110000002f000000000750bc6a15b9ff8188e87f0000")


# -- building blocks of crafted files ----------------------------------------------------------------------------------


def segment(marker: int, body: bytes) -> bytes:
    """A JPEG segment: 0xFF, the marker, the length (counting itself) and the body."""
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def exif(orientation: int | None = None) -> bytes:
    """An APP1 Exif segment (little-endian TIFF) whose first directory holds the orientation (when given) and points
    to a GPS directory and the owner's name; their values sit after the directory, as cameras write them."""
    entries: list[bytes] = []
    if orientation is not None:
        entries.append(struct.pack("<HHIHH", 0x0112, 3, 1, orientation, 0))
    entries.append(struct.pack("<HHII", 0x8825, 4, 1, 0))  # the GPS directory's offset (not followed here)
    entries.append(struct.pack("<HHII", 0xA430, 2, len(OWNER), 0))  # CameraOwnerName
    directory = struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)
    tiff = b"II*\x00" + struct.pack("<I", 8) + directory + GPS + OWNER
    return segment(0xE1, b"Exif\x00\x00" + tiff)


XMP = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>" + GPS + b"</x:xmpmeta>")
IPTC = segment(0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x10" + OWNER)
# Further places phones and editors keep the same details: a comment, Content Credentials (JUMBF, which can repeat the
# Exif), a GoPro's GPS track, a multi-picture index (MPF) and an older camera block (APP12).
COMMENT = segment(0xFE, b"Shot by " + OWNER)
CONTENT_CREDENTIALS = segment(0xEB, b"JP\x00\x00\x00\x00\x00\x01jumbc2pa stds.exif " + GPS)
GPS_TRACK = segment(0xE6, b"GoPro\x00GPS5" + GPS)
MPF = segment(0xE2, b"MPF\x00II*\x00" + OWNER)
CAMERA_BLOCK = segment(0xEC, b"PictureInfo " + OWNER)
# The blocks a decoder needs, kept: the colour profile (APP2 ICC_PROFILE) and Adobe's colour transform (APP14).
ICC = segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + bytes(16))
ADOBE = segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")


def jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    """(marker, body) of every segment up to the first scan, then the scan's data and the end marker: the structure a
    decoder reads (raises when the file cannot be walked to its end-of-image marker)."""
    assert data[:2] == b"\xff\xd8"
    found: list[tuple[int, bytes]] = []
    pos = 2
    while True:
        assert data[pos] == 0xFF, f"no marker at {pos}"
        marker = data[pos + 1]
        if marker == 0xD9:
            assert pos + 2 == len(data), "bytes after the end of the image"
            found.append((marker, b""))
            return found
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        found.append((marker, data[pos + 4 : pos + 2 + length]))
        pos += 2 + length
        if marker == 0xDA:
            end = data.index(b"\xff\xd9", pos)
            found.append((-1, data[pos:end]))  # the compressed data
            pos = end


def png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png_chunks(data: bytes) -> list[bytes]:
    """The chunk types of a PNG, each CRC checked (raises when the file cannot be walked to IEND)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    kinds: list[bytes] = []
    pos = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0] == zlib.crc32(kind + body)
        kinds.append(kind)
        pos += 12 + length
    assert kinds[-1] == b"IEND" and pos == len(data)
    return kinds


def riff_chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def riff(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """(type, body) of every chunk of a WebP whose RIFF size matches the file (raises otherwise)."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
    found: list[tuple[bytes, bytes]] = []
    pos = 12
    while pos < len(data):
        kind, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        found.append((kind, data[pos + 8 : pos + 8 + size]))
        pos += 8 + size + size % 2
    assert pos == len(data)
    return found


def assert_no_secrets(data: bytes) -> None:
    for secret in SECRETS:
        assert secret not in data, secret


# -- JPEG -------------------------------------------------------------------------------------------------------------


def test_a_jpeg_loses_its_exif_xmp_iptc_and_what_follows_the_image() -> None:
    # As phones write them: Exif, XMP and IPTC (after the APP0 block, bytes 2-19) before the image, a second picture
    # with its own Exif after it.
    trailer = b"\xff\xd8" + exif() + CLEAN_JPEG[2:]
    dirty = CLEAN_JPEG[:2] + exif() + XMP + CLEAN_JPEG[2:20] + IPTC + CLEAN_JPEG[20:] + trailer
    stripped = strip_metadata(dirty, "image/jpeg")
    assert_no_secrets(stripped)
    assert stripped == CLEAN_JPEG  # the image itself, byte for byte
    assert sniff(stripped) == "image/jpeg"
    markers = [marker for marker, _ in jpeg_segments(stripped)]
    assert markers == [0xE0, 0xDB, 0xC0, 0xC4, 0xC4, 0xDA, -1, 0xD9]


def test_a_jpeg_keeps_only_its_orientation_so_it_stays_upright() -> None:
    dirty = CLEAN_JPEG[:2] + exif(orientation=6) + CLEAN_JPEG[2:]
    stripped = strip_metadata(dirty, "image/jpeg")
    assert GPS not in stripped and OWNER not in stripped  # the new Exif block holds nothing but the orientation
    segments = jpeg_segments(stripped)
    assert segments[0][0] == 0xE1 and _exif_orientation(segments[0][1]) == 6
    assert len(segments[0][1]) == 6 + 26  # "Exif", a TIFF header and one directory with one entry
    assert stripped[2 + 4 + len(segments[0][1]) :] == CLEAN_JPEG[2:]
    # Upright already (orientation 1): no Exif block at all.
    assert strip_metadata(CLEAN_JPEG[:2] + exif(orientation=1) + CLEAN_JPEG[2:], "image/jpeg") == CLEAN_JPEG


def test_a_jpeg_loses_comments_and_every_other_application_block_but_keeps_what_decoders_need() -> None:
    app0 = CLEAN_JPEG[2:20]
    dirty = (
        CLEAN_JPEG[:2]
        + app0
        + COMMENT
        + CONTENT_CREDENTIALS
        + ICC
        + GPS_TRACK
        + MPF
        + CAMERA_BLOCK
        + ADOBE
        + CLEAN_JPEG[20:]
    )
    stripped = strip_metadata(dirty, "image/jpeg")
    assert_no_secrets(stripped)
    assert b"c2pa" not in stripped and b"MPF" not in stripped
    assert stripped == CLEAN_JPEG[:2] + app0 + ICC + ADOBE + CLEAN_JPEG[20:]
    markers = [marker for marker, _ in jpeg_segments(stripped)]
    assert markers == [0xE0, 0xE2, 0xEE, 0xDB, 0xC0, 0xC4, 0xC4, 0xDA, -1, 0xD9]
    assert sniff(stripped) == "image/jpeg"


def test_a_progressive_jpeg_is_walked_scan_by_scan() -> None:
    """Metadata between two scans goes too; the compressed data (with stuffed 0xFF 0x00 bytes and restart markers)
    is copied as it is."""
    head = CLEAN_JPEG[:2] + segment(0xDB, b"\x00" + bytes(64)) + segment(0xC2, bytes.fromhex("080008000801011100"))
    scan_1 = segment(0xC4, bytes(17)) + segment(0xDA, bytes.fromhex("010100003f00")) + b"\x12\xff\x00\x34\xff\xd0\x56"
    scan_2 = segment(0xC4, bytes(17)) + segment(0xDA, bytes.fromhex("010100003f00")) + b"\x78\xff\xff\x00\x9a"
    clean = head + scan_1 + scan_2 + b"\xff\xd9"
    assert strip_metadata(clean, "image/jpeg") is clean  # nothing to remove: the same bytes
    dirty = head + exif() + scan_1 + XMP + IPTC + scan_2 + b"\xff\xd9" + b"trailing data " + GPS
    stripped = strip_metadata(dirty, "image/jpeg")
    assert stripped == clean
    assert_no_secrets(stripped)


# -- PNG --------------------------------------------------------------------------------------------------------------


def test_a_png_loses_its_text_and_exif_chunks() -> None:
    clean = sample_photo_png(4, 3)
    ihdr_end = 8 + 12 + 13
    extra = (
        png_chunk(b"eXIf", b"MM\x00*" + GPS),
        png_chunk(b"tEXt", b"Author\x00" + OWNER),
        png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00http://ns.adobe.com/xap/1.0/" + GPS),
        png_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(OWNER)),
        png_chunk(b"caBX", b"jumbc2pa stds.exif " + GPS),  # Content Credentials
    )
    dirty = clean[:ihdr_end] + b"".join(extra) + clean[ihdr_end:] + b"trailing data " + GPS
    assert png_chunks(dirty[: -len(b"trailing data " + GPS)]) == [
        b"IHDR",
        b"eXIf",
        b"tEXt",
        b"iTXt",
        b"zTXt",
        b"caBX",
        b"IDAT",
        b"IEND",
    ]
    stripped = strip_metadata(dirty, "image/png")
    assert stripped == clean  # the image itself, byte for byte
    assert png_chunks(stripped) == [b"IHDR", b"IDAT", b"IEND"]
    assert sniff(stripped) == "image/png"
    assert_no_secrets(stripped)


# -- WebP -------------------------------------------------------------------------------------------------------------


def test_a_webp_loses_its_exif_xmp_and_content_credentials_chunks_and_their_flags() -> None:
    image = webp_chunks(SIMPLE_WEBP)[0]
    assert image[0] == b"VP8L"
    # The extended format: VP8X (flags: alpha, Exif and XMP; a 1x1 canvas), the image, then the metadata chunks.
    vp8x = bytes((0x10 | 0x08 | 0x04, 0, 0, 0)) + bytes(6)
    dirty = riff(
        riff_chunk(b"VP8X", vp8x),
        riff_chunk(*image),
        riff_chunk(b"EXIF", b"II*\x00" + GPS),
        riff_chunk(b"XMP ", b"<x:xmpmeta>" + OWNER + b"</x:xmpmeta>"),
        riff_chunk(b"C2PA", b"jumbc2pa stds.exif " + GPS),  # Content Credentials
    )
    stripped = strip_metadata(dirty + b"trailing data", "image/webp")
    assert_no_secrets(stripped)
    assert sniff(stripped) == "image/webp"
    chunks = webp_chunks(stripped)  # the RIFF size matches the smaller file
    assert [kind for kind, _ in chunks] == [b"VP8X", b"VP8L"]
    assert chunks[0][1] == bytes((0x10, 0, 0, 0)) + bytes(6)  # only the alpha flag is left
    assert chunks[1] == image  # the image data, byte for byte
    assert strip_metadata(SIMPLE_WEBP, "image/webp") is SIMPLE_WEBP


# -- unchanged files --------------------------------------------------------------------------------------------------


def test_gif_and_files_with_nothing_to_remove_are_kept_as_they_are() -> None:
    gif = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
    png = sample_photo_png(4, 3)
    damaged = (b"\x89PNG\r\n\x1a\n" + bytes(64), b"\xff\xd8\xff\xe0" + bytes(64), b"RIFF\xff\x00\x00\x00WEBPVP8L\x01")
    assert strip_metadata(gif, "image/gif") is gif
    assert strip_metadata(png, "image/png") is png
    assert strip_metadata(CLEAN_JPEG, "image/jpeg") is CLEAN_JPEG
    for data in damaged:  # a structure that cannot be followed: the bytes stay as they are
        assert strip_metadata(data, sniff(data) or "") is data


# -- the review photo -------------------------------------------------------------------------------------------------


def test_validated_bytes_are_the_picture_without_its_metadata() -> None:
    dirty = CLEAN_JPEG[:2] + exif() + CLEAN_JPEG[2:]
    picture = images.validate_bytes(dirty, len(dirty))
    assert picture.data == CLEAN_JPEG and picture.content_type == "image/jpeg"
    assert picture.sha256 == hashlib.sha256(CLEAN_JPEG).hexdigest() and picture.size == len(CLEAN_JPEG)


async def test_the_stored_review_photo_has_no_metadata(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await make_order(tenv, customer)
        clean = sample_photo_png(40, 30)
        dirty = (
            clean[:33] + png_chunk(b"eXIf", b"MM\x00*" + GPS) + png_chunk(b"tEXt", b"Author\x00" + OWNER) + clean[33:]
        )
        cdn(tenv).files[CDN_URL] = dirty
        await write(tenv, customer, image=Upload(size=len(dirty)))
        (review,) = await reviews_of(tenv, customer.id)
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.data == clean
        assert (stored.size, stored.sha256) == (len(clean), hashlib.sha256(clean).hexdigest())
    finally:
        await tenv.close()


async def test_a_photo_stored_before_stripping_is_published_without_its_metadata(tmp_path: Path) -> None:
    """A review that still waits for our team when stripping ships keeps the bytes it was stored with; the published
    file is stripped when it is attached."""
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await make_order(tenv, customer)
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        dirty = CLEAN_JPEG[:2] + exif() + COMMENT + CLEAN_JPEG[2:]

        def stored_as_before(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE review_images SET content_type = 'image/jpeg', size = ?, sha256 = ?, data = ? "
                "WHERE review_id = ?",
                (len(dirty), hashlib.sha256(dirty).hexdigest(), sqlite3.Binary(dirty), review.id),
            )

        await tenv.app.db.transaction(stored_as_before)
        attached = await service.stored_image(tenv.app, review)
        assert attached is not None and attached.data == CLEAN_JPEG
        assert (attached.size, attached.sha256) == (len(CLEAN_JPEG), hashlib.sha256(CLEAN_JPEG).hexdigest())

        await approve(tenv, staff, review, channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1])
        (post,) = reviews_channel(tenv).messages
        (published,) = [f for f in post.files if f.filename == images.attachment_name(review.id, "image/jpeg")]
        data = published.fp.getvalue()  # type: ignore[attr-defined]
        assert data == CLEAN_JPEG and 0xE1 not in [marker for marker, _ in jpeg_segments(data)]
        assert_no_secrets(data)
    finally:
        await tenv.close()


async def test_stripping_runs_in_a_worker_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crafted file can take a second or two to walk: the download's check and the attached file never block the
    bot's event loop."""
    threads: list[int] = []

    def recording(data: bytes, content_type: str) -> bytes:
        threads.append(threading.get_ident())
        return strip_metadata(data, content_type)

    monkeypatch.setattr(images, "strip_metadata", recording)
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await make_order(tenv, customer)
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        assert await service.stored_image(tenv.app, review) is not None
        assert len(threads) >= 2 and threading.get_ident() not in threads
    finally:
        await tenv.close()
