"""The bot's custom icons: small pictures used as emojis in its messages (status dots, step numbers, section icons).

The owner's pictures live in the icons folder (``[icons] directory``, relative to the config folder), one
``<name>.png`` per icon (128×128, at most 256 KiB; ``tools/icons/render.js`` renders the shipped set). The bot uploads
them as *application emojis*: they belong to the bot application, work in every channel and direct message, use none
of the server's emoji slots and need no permission. ``sync_application_emojis`` runs once at startup: it reuses the
emojis already uploaded (recorded in ``app_emojis`` with the picture's sha256), uploads missing ones, and replaces an
emoji whose picture changed.

Texts use an icon with the global placeholder ``{icon_<name>}`` (any content key, see ``ContentStore.render``); a
button emoji can be ``{icon_<name>}`` too. Until an icon is uploaded (or if its upload failed) the placeholder renders
as ``IconLibrary.fallback`` (nothing for section icons, a plain dot for status dots), never as raw text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from shopbot.config.settings import IconSettings
from shopbot.db.repos import app_emojis as repo
from shopbot.logging_setup import log_event
from shopbot.ui.image_types import sniff

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

MAX_ICON_BYTES = 256 * 1024  # Discord's limit for an emoji picture
NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")
PLACEHOLDER_PREFIX = "icon_"
# What an icon shows while it is not uploaded: a plain dot for the status dots, nothing for the others.
_DOT_FALLBACK = "•"


@dataclass(frozen=True)
class IconFile:
    name: str
    data: bytes = field(repr=False)
    sha256: str
    content_type: str


@dataclass
class IconLibrary:
    """The icon pictures (name -> file) and the uploaded emoji ids (name -> id)."""

    files: Mapping[str, IconFile] = field(default_factory=dict)
    ids: dict[str, int] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    enabled: bool = False

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.files)

    def text(self, name: str) -> str:
        """The icon as message text: ``<:name:id>`` once uploaded, else the fallback (never raw placeholder text)."""
        emoji_id = self.ids.get(name)
        if emoji_id is not None and name in self.files:
            return f"<:{name}:{emoji_id}>"
        return self.fallback(name)

    @staticmethod
    def fallback(name: str) -> str:
        return _DOT_FALLBACK if name.startswith("dot_") else ""


EMPTY_ICONS = IconLibrary()


def load_icons(config_dir: Path, settings: IconSettings) -> IconLibrary:
    """Read every icon picture (blocking file I/O: call it in a thread from async code)."""
    if not settings.enabled:
        return IconLibrary()
    folder = config_dir / settings.directory
    files: dict[str, IconFile] = {}
    problems: list[str] = []
    if not folder.is_dir():
        problems.append(f"icons folder '{settings.directory}' was not found in the config folder")
        return IconLibrary(problems=tuple(problems), enabled=True)
    for path in sorted(folder.glob("*.png")):
        name = path.stem
        if not NAME_RE.match(name):
            problems.append(f"icon {path.name}: names may only use a-z, 0-9 and _ (2-32 characters)")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            problems.append(f"icon {path.name} cannot be read ({type(exc).__name__})")
            continue
        content_type = sniff(data)
        if content_type is None:
            problems.append(f"icon {path.name} is not a PNG picture")
            continue
        if len(data) > MAX_ICON_BYTES:
            problems.append(f"icon {path.name} is {len(data)} bytes; Discord allows at most {MAX_ICON_BYTES}")
            continue
        files[name] = IconFile(name, data, hashlib.sha256(data).hexdigest(), content_type)
    if problems:
        log_event(log, logging.WARNING, "icons_incomplete", loaded=len(files), problems=problems[:10])
    return IconLibrary(files=files, problems=tuple(problems), enabled=True)


def keep_uploaded_ids(new: IconLibrary, old: IconLibrary) -> IconLibrary:
    """``new`` (just read from disk) with the uploaded ids of ``old`` for every picture that did not change; new or
    changed pictures are uploaded by the next ``sync_application_emojis``."""
    new.ids = {
        name: emoji_id
        for name, emoji_id in old.ids.items()
        if name in new.files and name in old.files and new.files[name].sha256 == old.files[name].sha256
    }
    return new


async def load_uploaded_ids(app: App) -> None:
    """Use the emoji ids recorded by earlier runs right away (before the sync with Discord has finished)."""
    library = app.icons
    if not library.enabled:
        return
    recorded = await app.db.run(repo.all_emojis)
    for name, row in recorded.items():
        icon = library.files.get(name)
        if icon is not None and icon.sha256 == row.sha256:
            library.ids[name] = row.emoji_id


async def sync_application_emojis(app: App) -> list[str]:
    """Make Discord's application emojis match the icon pictures. Returns the names that were uploaded or replaced.

    One failure (rate limit, invalid picture) never stops the others; the icon then shows its fallback and the next
    start tries again."""
    library = app.icons
    bot = app.bot
    if not library.enabled or bot is None:
        return []
    import discord  # local: keeps this module importable for offline tools without a client

    try:
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except discord.HTTPException as exc:
        log_event(log, logging.ERROR, "icons_fetch_failed", status=exc.status, code=exc.code)
        return []
    recorded = await app.db.run(repo.all_emojis)
    changed: list[str] = []
    for name, icon in library.files.items():
        emoji = existing.get(name)
        row = recorded.get(name)
        if emoji is not None and row is not None and row.emoji_id == emoji.id and row.sha256 == icon.sha256:
            library.ids[name] = emoji.id
            continue
        if emoji is not None and row is None:
            # Uploaded earlier but not recorded (e.g. a reset database): adopt it; its picture is assumed current.
            await app.db.transaction(repo.upsert, name, emoji.id, icon.sha256, app.clock.now())
            library.ids[name] = emoji.id
            continue
        try:
            if emoji is not None:
                await emoji.delete()  # its picture changed: Discord cannot replace a picture, so upload it again
            created = await bot.create_application_emoji(name=name, image=icon.data)
        except (discord.HTTPException, ValueError) as exc:
            log_event(log, logging.WARNING, "icon_upload_failed", icon=name, error=type(exc).__name__)
            library.ids.pop(name, None)
            continue
        await app.db.transaction(repo.upsert, name, created.id, icon.sha256, app.clock.now())
        library.ids[name] = created.id
        changed.append(name)
    if changed:
        log_event(log, logging.INFO, "icons_uploaded", count=len(changed), icons=changed[:20])
    return changed
