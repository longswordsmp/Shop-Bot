"""Shared building blocks of the START HERE guides (#how-to-buy, #faq, #shipping).

- ``Texts`` resolves the content keys of one panel build: at runtime a missing required key raises
  ``MissingContent`` and a ``{channel_<key>}`` link to a channel that does not exist raises ``ChannelNotCreated``
  (nothing incomplete is ever posted); the preview shows ``[content.key]`` markers instead.
- ``Card`` / ``Block`` (``shopbot.ui.card``) turn the resolved text into one card, as embeds and messages that respect
  Discord's limits without ever cutting text.
- ``guide_messages`` puts the guide's banner picture on its last message only, so it closes the guide at its bottom
  (the messages before it are the start of the same guide).
- ``publish_guide`` keeps one guide as an ordered series of messages (``<prefix><index>`` records): edited in
  place, re-posted in order when one was deleted or moved, surplus messages of an earlier, longer configuration
  removed. It is idempotent: running /setup again changes nothing when nothing changed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import weakref
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import discord

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import ContentStore, MissingContent
from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import Settings
from shopbot.core.panels import remove_message, upsert_message
from shopbot.core.permissions import channel_send_permissions, missing_permissions
from shopbot.db.repos import state
from shopbot.features.server_keys import channel_mentions, channel_placeholder, channel_placeholders, channel_url
from shopbot.logging_setup import log_event
from shopbot.ui import limits
from shopbot.ui.card import Block, block_messages
from shopbot.ui.discord_render import RenderError
from shopbot.ui.spec import Component, MessageSpec, PreviewState
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
MAX_REPORTED_VIOLATIONS = 5
# Mention value used by the preview for channels /setup has not created (renders as "#channel" in the preview).
PREVIEW_MENTION = "<#0>"


class ChannelNotCreated(Exception):
    """Text or a link button points to a configured channel that does not exist in the server (yet)."""

    def __init__(self, channel_key: str, content_key: str | None = None) -> None:
        super().__init__(channel_key)
        self.channel_key = channel_key
        self.content_key = content_key


# ---------------------------------------------------------------------------------------------------
# Text resolution
# ---------------------------------------------------------------------------------------------------


@dataclass
class Texts:
    """Resolves content keys for one build of a guide.

    ``content`` mode (runtime and preview of the real configuration) renders through the content store, so only
    placeholders a key declares are substituted. ``samples`` mode (synthetic preview examples whose keys are not
    registered) substitutes every given value. ``mentions`` maps ``channel_<key>`` placeholders to ``<#id>``.
    """

    content: ContentStore | None
    mentions: Mapping[str, str]
    channel_names: Collection[str] = ()
    preview: bool = False
    samples: Callable[[str], str | None] | None = None
    _cache: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)

    def _declared(self, key: str) -> tuple[str, ...]:
        cached = self._cache.get(key)
        if cached is None:
            definition = self.content.registry.get(key) if self.content is not None else None
            cached = definition.placeholders if definition is not None else ()
            self._cache[key] = cached
        return cached

    def optional(self, key: str, /, **values: str | int) -> str | None:
        """The configured text of ``key`` with its placeholders filled, or None when it is not configured."""
        if self.content is None:
            template = self.samples(key) if self.samples is not None else None
            if not template:
                return None
            given = {**self.mentions, **values}
            return _PLACEHOLDER_RE.sub(lambda m: str(given.get(m.group(1), m.group(0))), template)
        template = self.content.get(key)
        if template is None:
            return None
        declared = self._declared(key)
        if not self.preview:
            for name in _PLACEHOLDER_RE.findall(template):
                if name in declared and name in self.channel_names and name not in self.mentions:
                    raise ChannelNotCreated(name.removeprefix("channel_"), key)
        merged: dict[str, str | int] = dict.fromkeys(self.channel_names, PREVIEW_MENTION) if self.preview else {}
        merged.update(self.mentions)
        merged.update(values)
        return self.content.render(key, **merged)

    def required(self, key: str, /, **values: str | int) -> str:
        """Like ``optional`` but a missing value raises ``MissingContent`` (the preview shows a marker)."""
        text = self.optional(key, **values)
        if text is not None:
            return text
        if self.preview:
            return f"[{key}]"
        raise MissingContent(key)


def misplaced_link_problems(settings: Settings, content: ContentStore, keys: Iterable[ContentKey]) -> list[str]:
    """A ``{channel_<key>}`` written in text that cannot hold placeholders (titles, questions, headings, button
    labels) would reach customers verbatim: report it so the guide stays hidden until it is removed."""
    names = channel_placeholders(settings)
    problems: list[str] = []
    for definition in keys:
        if definition.placeholders:
            continue  # declared placeholders are checked by the content store itself
        value = content.raw(definition.key)
        found = [name for name in names if value and "{" + name + "}" in value]
        if found:
            problems.append(
                f"{definition.key}: {{{found[0]}}} cannot be a channel link here (Discord shows links only in "
                "descriptions and field values); remove it"
            )
    return problems


def live_mentions(app: App, guild: discord.Guild) -> dict[str, str]:
    """``channel_<key>`` -> ``<#id>`` for every configured channel that exists in the server right now."""
    mentions = channel_mentions(app)
    live: dict[str, str] = {}
    for channel in app.settings.server.channels:
        name = channel_placeholder(channel.key)
        channel_id = app.resources.channel_id(channel.key)
        if name in mentions and channel_id and guild.get_channel(channel_id) is not None:
            live[name] = mentions[name]
    return live


def runtime_texts(app: App, guild: discord.Guild) -> Texts:
    return Texts(app.content, live_mentions(app, guild), channel_names=set(channel_placeholders(app.settings)))


def preview_texts(app: App) -> Texts:
    """Texts for the preview of the real configuration (markers for missing text, ``#channel`` for links)."""
    return Texts(
        app.content, channel_mentions(app), channel_names=set(channel_placeholders(app.settings)), preview=True
    )


def sample_texts(samples: Callable[[str], str | None]) -> Texts:
    """Texts for synthetic preview examples (keys that are not part of the configuration)."""
    return Texts(None, {}, preview=True, samples=samples)


def live_channel_url(app: App, guild: discord.Guild, channel_key: str) -> str:
    """Link to a configured channel for a link button; raises ``ChannelNotCreated`` if it does not exist."""
    channel_id = app.resources.channel_id(channel_key)
    url = channel_url(app, channel_key)
    if url is None or not channel_id or guild.get_channel(channel_id) is None:
        raise ChannelNotCreated(channel_key)
    return url


def preview_channel_url(app: App, channel_key: str) -> str:
    return channel_url(app, channel_key) or f"https://example.com/channel/{channel_key}"


# ---------------------------------------------------------------------------------------------------
# Layout: blocks -> embeds -> messages (``shopbot.ui.card``)
# ---------------------------------------------------------------------------------------------------


def guide_messages(
    ui: Ui, blocks: Iterable[Block], rows: tuple[tuple[Component, ...], ...] = (), *, banner: BannerId
) -> list[MessageSpec]:
    """The messages of a guide; the ``banner`` picture and ``rows`` (link buttons) are on the last message, so the
    picture closes the guide at its bottom."""
    return with_closing_banner(ui, block_messages(ui, blocks, rows, banner=None), banner)


def with_closing_banner(ui: Ui, specs: list[MessageSpec], banner: BannerId) -> list[MessageSpec]:
    """``specs`` with the ``banner`` picture on the last message only (a guide's bottom)."""
    if not specs:
        return specs
    return [*specs[:-1], replace(specs[-1], banner=ui.banner(banner))]


def preview_states(feature: str, name: str, note: str, specs: Sequence[MessageSpec]) -> list[PreviewState]:
    """One preview state per message (a multi-message guide is shown message by message)."""
    if len(specs) == 1:
        return [PreviewState(feature=feature, name=name, note=note, spec=specs[0])]
    return [
        PreviewState(
            feature=feature,
            name=f"{name}_message_{index}",
            note=f"{note} Message {index} of {len(specs)}.",
            spec=spec,
        )
        for index, spec in enumerate(specs, start=1)
    ]


def long_sample(label: str, length: int) -> str:
    """Obviously synthetic text of about ``length`` characters for preview examples."""
    unit = f"[{label}] "
    return (unit * max(1, length // len(unit))).rstrip()


# ---------------------------------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------------------------------

_locks: weakref.WeakKeyDictionary[App, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


def _lock(app: App, feature: str) -> asyncio.Lock:
    per_app = _locks.setdefault(app, {})
    lock = per_app.get(feature)
    if lock is None:
        lock = per_app[feature] = asyncio.Lock()
    return lock


def record_key(prefix: str, index: int) -> str:
    return f"{prefix}{index}"


def _record_index(prefix: str, key: str) -> int | None:
    suffix = key[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


Build = Callable[[discord.Guild], list[MessageSpec]]


async def publish_guide(app: App, *, feature: str, prefix: str, channel_key: str, build: Build) -> list[str]:
    """Create/update the messages of one guide in its channel. Returns operator report lines."""
    async with _lock(app, feature):
        return await _publish(app, feature=feature, prefix=prefix, channel_key=channel_key, build=build)


async def _publish(app: App, *, feature: str, prefix: str, channel_key: str, build: Build) -> list[str]:
    op = app.op
    guild = app.guild
    if guild is None:
        return [op("guides.publish.no_guild", feature=feature)]
    channel = app.resources.text_channel(guild, channel_key) if channel_key else None
    if channel is None:
        return [op("guides.publish.channel_missing", feature=feature, channel=channel_key)]
    try:
        specs = build(guild)
    except MissingContent as exc:
        return [op("guides.publish.missing_content", feature=feature, content_key=exc.key)]
    except ChannelNotCreated as exc:
        if exc.content_key is None:
            return [op("guides.publish.link_channel_missing", feature=feature, channel=exc.channel_key)]
        return [
            op(
                "guides.publish.text_channel_missing",
                feature=feature,
                channel=exc.channel_key,
                content_key=exc.content_key,
            )
        ]
    if not specs:
        return [op("guides.publish.empty", feature=feature)]
    if not app.ready(feature):
        return [op("guides.publish.not_ready", feature=feature)]
    for index, spec in enumerate(specs, start=1):  # validate everything before changing anything
        violations = limits.validate_message(spec)
        if violations:
            text = "; ".join(str(v) for v in violations[:MAX_REPORTED_VIOLATIONS])
            return [op("guides.publish.limits", feature=feature, index=index, violations=text)]
    me = guild.me
    missing = (
        missing_permissions(me, channel, channel_send_permissions(uploads=app.banners.enabled))
        if me is not None
        else []
    )
    if missing:
        return [
            op(
                "guides.publish.missing_permissions",
                feature=feature,
                permissions=", ".join(missing),
                channel=channel.mention,
            )
        ]

    guild_id = app.env.guild_id
    records = await app.db.run(state.published_with_prefix, guild_id, prefix)
    existing = {record.key for record in records}
    counts: Counter[str] = Counter()
    removed = 0
    try:
        repost = False
        for index, spec in enumerate(specs):
            key = record_key(prefix, index)
            if repost and key in existing:
                # An earlier message was posted again at the bottom of the channel: post the following ones again
                # too, so the guide keeps its order.
                await remove_message(app, key)
            _, action = await upsert_message(app, key, channel, spec)
            counts[action] += 1
            repost = repost or action in ("created", "recreated", "moved")
        for record in records:
            index_of = _record_index(prefix, record.key)
            if index_of is None or index_of >= len(specs):
                removed += int(await remove_message(app, record.key))
    except RenderError as exc:
        text = "; ".join(str(v) for v in exc.violations[:MAX_REPORTED_VIOLATIONS])
        return [op("guides.publish.limits", feature=feature, index="?", violations=text)]
    except discord.HTTPException as exc:
        log_event(
            log,
            logging.WARNING,
            "guide_publish_failed",
            feature=feature,
            channel_id=channel.id,
            status=exc.status,
            code=exc.code,
        )
        return [op("guides.publish.failed", feature=feature, http_status=exc.status, code=exc.code)]

    if not app.ready(feature):
        # Readiness changed while posting (/reload, /content clear): the automatic withdrawal may already have run.
        for record in await app.db.run(state.published_with_prefix, guild_id, prefix):
            await remove_message(app, record.key)
        return [op("guides.publish.not_ready", feature=feature)]
    log_event(log, logging.INFO, "guide_published", feature=feature, messages=len(specs), changed=dict(counts))
    return [
        op(
            "guides.publish.done",
            feature=feature,
            channel=channel.mention,
            messages=len(specs),
            created=counts["created"] + counts["recreated"] + counts["moved"],
            updated=counts["updated"],
            unchanged=counts["unchanged"],
            removed=removed,
        )
    ]
