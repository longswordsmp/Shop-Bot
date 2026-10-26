"""Banner pictures shown at the bottom of the bot's cards.

Every kind of message names the banner that fits it (``shopbot.config.banner_ids``); the owner supplies one picture
per banner id as ``<id>.png`` (or ``.jpg``/``.gif``/``.webp``) in the banners folder (``[banners] directory``,
relative to the config folder). ``load_banners`` reads them once (at startup and on /reload) and keeps them in
memory as ``FileSpec`` objects, so builders attach them without touching the disk.

A banner is uploaded with its message as the first attachment (``MessageSpec.banner``); builders never refer to it.
``place_banner`` decides where it shows, at the moment a spec becomes a Discord message (``ui.discord_render``) and in
the offline preview: as the image of the message's last embed (``attachment://<name>``), so it closes the card at
its bottom, when that embed shows no picture of its own. When it does (a product picture, an announcement image) or
the message has no embed, the banner stays an attachment no embed uses, which Discord shows above the embeds. Its
attachment name ``banner-<id>-<sha12>.<ext>`` changes when the picture changes: edits keep the uploaded picture when
the name is unchanged (the embed still refers to it) and replace it otherwise (``ui.discord_render``).

A missing, unreadable, oversized or non-image file means that message kind shows no banner; the problem is listed in
/status and by the offline checker, and it never stops a message from being sent.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from shopbot.config.banner_ids import BANNER_IDS, BannerId
from shopbot.config.settings import BannerSettings
from shopbot.logging_setup import log_event
from shopbot.ui.image_types import CONTENT_TYPES, sniff
from shopbot.ui.limits import ATTACHMENT_SCHEME
from shopbot.ui.spec import FileSpec, MessageSpec

log = logging.getLogger(__name__)

# Largest banner file. A message may carry 10 MiB of files (``ui.limits``); a product image is at most 8 MiB, so a
# banner of at most 2 MiB always fits next to it.
MAX_BANNER_BYTES = 2 * 1024 * 1024
FILENAME_PREFIX = "banner-"
_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webp")


def is_banner_filename(filename: str) -> bool:
    """True for an attachment uploaded as a banner (its name starts with ``banner-``)."""
    return filename.startswith(FILENAME_PREFIX)


def banner_url(banner: FileSpec) -> str:
    """The ``attachment://`` address an embed shows the banner with."""
    return f"{ATTACHMENT_SCHEME}{banner.filename}"


def banner_inside(spec: MessageSpec) -> bool:
    """True when ``spec`` shows its banner as the image of its last embed (before or after ``place_banner``): the
    message has a banner and embeds, and the last embed shows no other picture."""
    if spec.banner is None or not spec.embeds or not spec.banner_in_card:
        return False
    image = spec.embeds[-1].image_url
    return not image or image == banner_url(spec.banner)


def place_banner(spec: MessageSpec) -> MessageSpec:
    """``spec`` as it is sent: the banner becomes the image of the last embed when that embed shows no picture of its
    own and the message allows it (``banner_in_card``); otherwise ``spec`` is returned unchanged (the banner then
    shows above the embeds). The banner stays the first attachment either way. Placing twice changes nothing."""
    if spec.banner is None or not spec.embeds or not spec.banner_in_card or spec.embeds[-1].image_url:
        return spec
    last = replace(spec.embeds[-1], image_url=banner_url(spec.banner))
    return replace(spec, embeds=(*spec.embeds[:-1], last))


@dataclass(frozen=True)
class BannerLibrary:
    """The loaded banners (id -> file) and the problems found while loading them."""

    files: Mapping[str, FileSpec] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    enabled: bool = False

    def get(self, banner_id: BannerId) -> FileSpec | None:
        return self.files.get(banner_id)


EMPTY_LIBRARY = BannerLibrary()


def load_banners(config_dir: Path, settings: BannerSettings) -> BannerLibrary:
    """Read every banner that is switched on. Blocking file I/O: call it in a thread from async code."""
    if not settings.enabled:
        return EMPTY_LIBRARY
    folder = config_dir / settings.directory
    files: dict[str, FileSpec] = {}
    problems: list[str] = []
    if not folder.is_dir():
        problems.append(f"banners folder '{settings.directory}' was not found in the config folder")
    else:
        for banner_id in BANNER_IDS:
            if banner_id in settings.off:
                continue
            spec, problem = _load_one(folder, banner_id)
            if spec is not None:
                files[banner_id] = spec
            elif problem is not None:
                problems.append(problem)
    if problems:
        log_event(log, logging.WARNING, "banners_incomplete", loaded=len(files), problems=problems[:10])
    return BannerLibrary(files=files, problems=tuple(problems), enabled=True)


def _load_one(folder: Path, banner_id: str) -> tuple[FileSpec | None, str | None]:
    candidates = [folder / f"{banner_id}.{ext}" for ext in _EXTENSIONS]
    found = [path for path in candidates if path.is_file()]
    if not found:
        return None, f"banner '{banner_id}': no {banner_id}.png (or .jpg/.gif/.webp) in the banners folder"
    path = found[0]
    try:
        size = path.stat().st_size
        if size > MAX_BANNER_BYTES:
            return None, f"banner '{banner_id}': {path.name} is {size} bytes; banners may be at most {MAX_BANNER_BYTES}"
        data = path.read_bytes()
    except OSError as exc:
        return None, f"banner '{banner_id}': {path.name} cannot be read ({type(exc).__name__})"
    content_type = sniff(data)
    if content_type is None:
        return None, f"banner '{banner_id}': {path.name} is not a PNG, JPEG, GIF or WebP picture"
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{FILENAME_PREFIX}{banner_id}-{digest[:12]}.{CONTENT_TYPES[content_type]}"
    return FileSpec(filename=filename, sha256=digest, size=len(data), content_type=content_type, data=data), None
