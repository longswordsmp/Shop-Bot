"""What a picture file says about where and by whom it was taken (GPS position, camera, owner), removed in pure Python.

``strip_metadata(data, content_type)`` keeps the image data and drops the metadata blocks of each format:

- **JPEG**: the comments (COM) and every application block (APPn) except the three a decoder needs: APP0 (JFIF),
  APP2 when it is the colour profile (ICC_PROFILE) and APP14 (Adobe's colour transform). That removes APP1 (Exif,
  which holds the GPS position and the camera owner, and XMP), APP13 (IPTC), APP11 (Content Credentials, which can
  repeat the Exif), APP6 (a GoPro's GPS track) and the others. Only the Exif orientation is kept, rewritten as an Exif
  block of its own with nothing else in it, so a phone photo is still shown upright. The segments are walked one by
  one (also between the scans of a progressive JPEG); the compressed image data is copied as it is, and whatever
  follows the end-of-image marker (where phones append further pictures with their own Exif, or a short video) is
  dropped.
- **PNG**: the text chunks (tEXt, iTXt, zTXt), the Exif chunk (eXIf) and Content Credentials (caBX); nothing after
  the end chunk (IEND) is kept.
- **WebP**: the EXIF, XMP and Content Credentials (C2PA) chunks, with the Exif and XMP flags in the extended header
  (VP8X) cleared and the RIFF size written again; nothing after the RIFF container is kept.
- **GIF**: unchanged (it carries no location in practice).

A file whose structure cannot be followed to its end (damaged, or not what its first bytes claim) keeps the rest of
its bytes as they are, and a file with nothing to remove is returned unchanged.
"""

from __future__ import annotations

from typing import Literal

# -- JPEG -------------------------------------------------------------------------------------------------------------

_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})  # markers without a length: TEM, RST0-RST7
_APP0, _APP1, _APP2, _APP14, _APP15 = 0xE0, 0xE1, 0xE2, 0xEE, 0xEF
_COM = 0xFE  # a comment
_ICC_PROFILE = b"ICC_PROFILE\x00"  # an APP2 block holding the colour profile (others are e.g. MPF)
_SOS = 0xDA  # start of scan: the compressed image data follows the segment
_EOI = 0xD9  # end of image
_EXIF_HEADER = b"Exif\x00\x00"
_ORIENTATION_TAG = 0x0112
_SHORT = 3  # the TIFF type of the orientation value


def _exif_orientation(segment: bytes) -> int | None:
    """The orientation (1-8) an APP1 segment's body records in its first image directory, or None (not Exif, no
    orientation, or not readable)."""
    if not segment.startswith(_EXIF_HEADER):
        return None
    tiff = segment[len(_EXIF_HEADER) :]
    if tiff[:2] not in (b"II", b"MM") or len(tiff) < 8:
        return None
    byteorder: Literal["little", "big"] = "little" if tiff[:2] == b"II" else "big"
    directory = int.from_bytes(tiff[4:8], byteorder)
    if directory + 2 > len(tiff):
        return None
    for index in range(int.from_bytes(tiff[directory : directory + 2], byteorder)):
        entry = directory + 2 + 12 * index
        if entry + 12 > len(tiff):
            return None
        if int.from_bytes(tiff[entry : entry + 2], byteorder) != _ORIENTATION_TAG:
            continue
        if int.from_bytes(tiff[entry + 2 : entry + 4], byteorder) != _SHORT:
            return None
        value = int.from_bytes(tiff[entry + 8 : entry + 10], byteorder)
        return value if 1 <= value <= 8 else None
    return None


def _jpeg_metadata(marker: int, data: bytes, body: int, end: int) -> bool:
    """Whether the segment ``marker`` whose body is ``data[body:end]`` is metadata: a comment, or an application block
    other than APP0 (JFIF), the colour profile in APP2 and APP14 (Adobe), which decoders need."""
    if marker == _COM:
        return True
    if not _APP0 <= marker <= _APP15 or marker in (_APP0, _APP14):
        return False
    return marker != _APP2 or not data.startswith(_ICC_PROFILE, body, end)


def _orientation_segment(orientation: int) -> bytes:
    """An APP1 Exif segment holding only ``orientation`` (big-endian TIFF, one directory with one entry)."""
    entry = b"".join(
        (
            _ORIENTATION_TAG.to_bytes(2, "big"),
            _SHORT.to_bytes(2, "big"),
            (1).to_bytes(4, "big"),  # one value
            orientation.to_bytes(2, "big") + b"\x00\x00",  # the value, padded to 4 bytes
        )
    )
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big") + (1).to_bytes(2, "big") + entry + (0).to_bytes(4, "big")
    body = _EXIF_HEADER + tiff
    return bytes((0xFF, _APP1)) + (len(body) + 2).to_bytes(2, "big") + body


def _scan_end(data: bytes, pos: int) -> int:
    """Where the compressed data of a scan that starts at ``pos`` ends: at the next marker (0xFF followed by neither a
    stuffed 0x00 nor a restart marker; fill bytes 0xFF before it belong to it), or at the end of the file."""
    while True:
        at = data.find(b"\xff", pos)
        if at < 0 or at + 1 >= len(data):
            return len(data)
        following = data[at + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:
            pos = at + 2
        elif following == 0xFF:
            pos = at + 1
        else:
            return at


def _strip_jpeg(data: bytes) -> bytes:
    out = bytearray(data[:2])  # start of image
    pos = 2
    orientation_kept = False
    while pos < len(data):
        if data[pos] != 0xFF:
            out += data[pos:]  # not a marker where one belongs: kept as it is
            break
        at = pos
        while at < len(data) and data[at] == 0xFF:  # fill bytes before the marker
            at += 1
        if at >= len(data):
            out += data[pos:]
            break
        marker = data[at]
        if marker == _EOI:
            out += data[pos : at + 1]
            break  # anything after the end of the image is dropped
        if marker in _JPEG_STANDALONE:
            out += data[pos : at + 1]
            pos = at + 1
            continue
        length = int.from_bytes(data[at + 1 : at + 3], "big") if at + 3 <= len(data) else 0
        end = at + 1 + length
        if length < 2 or end > len(data):
            out += data[pos:]
            break
        if not _jpeg_metadata(marker, data, at + 3, end):
            out += data[pos:end]
        elif marker == _APP1 and not orientation_kept:
            orientation = _exif_orientation(data[at + 3 : end])
            if orientation is not None and orientation != 1:
                out += _orientation_segment(orientation)  # still shown upright
                orientation_kept = True
        pos = end
        if marker == _SOS:
            scan_end = _scan_end(data, pos)
            out += data[pos:scan_end]
            pos = scan_end
    return bytes(out)


# -- PNG --------------------------------------------------------------------------------------------------------------

_PNG_SIGNATURE_LENGTH = 8
_PNG_DROPPED = frozenset({b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"caBX"})
_PNG_END = b"IEND"


def _strip_png(data: bytes) -> bytes:
    out = bytearray(data[:_PNG_SIGNATURE_LENGTH])
    pos = _PNG_SIGNATURE_LENGTH
    while pos < len(data):
        if pos + 8 > len(data):
            out += data[pos:]
            break
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length  # length, type, data, CRC
        if end > len(data):
            out += data[pos:]
            break
        if kind not in _PNG_DROPPED:
            out += data[pos:end]
        if kind == _PNG_END:
            break  # anything after the end chunk is dropped
        pos = end
    return bytes(out)


# -- WebP -------------------------------------------------------------------------------------------------------------

_WEBP_HEADER_LENGTH = 12  # "RIFF", the size, "WEBP"
_WEBP_DROPPED = frozenset({b"EXIF", b"XMP ", b"C2PA"})
_VP8X = b"VP8X"
_VP8X_METADATA_FLAGS = 0x08 | 0x04  # the Exif and XMP flags of the extended header


def _strip_webp(data: bytes) -> bytes:
    riff_end = min(8 + int.from_bytes(data[4:8], "little"), len(data))
    out = bytearray(data[:_WEBP_HEADER_LENGTH])
    pos = _WEBP_HEADER_LENGTH
    changed = riff_end < len(data)  # anything after the container is dropped
    while pos < riff_end:
        if pos + 8 > riff_end:
            out += data[pos:riff_end]
            break
        kind = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        if pos + 8 + size > riff_end:
            out += data[pos:riff_end]
            break
        end = min(pos + 8 + size + (size & 1), riff_end)  # a chunk of odd size is padded to an even one
        if kind in _WEBP_DROPPED:
            changed = True
        else:
            chunk = bytearray(data[pos:end])
            if kind == _VP8X and size >= 1 and chunk[8] & _VP8X_METADATA_FLAGS:
                chunk[8] &= ~_VP8X_METADATA_FLAGS & 0xFF
                changed = True
            out += chunk
        pos = end
    if not changed:
        return data
    out[4:8] = (len(out) - 8).to_bytes(4, "little")
    return bytes(out)


# -- all formats ------------------------------------------------------------------------------------------------------

_STRIPPERS = {"image/jpeg": _strip_jpeg, "image/png": _strip_png, "image/webp": _strip_webp}


def strip_metadata(data: bytes, content_type: str) -> bytes:
    """``data`` (a picture of ``content_type``, as ``image_types.sniff`` recognised it) without its location, camera and
    owner metadata (see the module docstring). Unchanged when there is nothing to remove (and for GIF)."""
    strip = _STRIPPERS.get(content_type)
    if strip is None:
        return data
    stripped = strip(data)
    return data if stripped == data else stripped
