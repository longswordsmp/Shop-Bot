"""Idempotent publishing of bot-maintained messages (panels, listings).

``upsert_message`` keeps exactly one message per key: it edits the recorded message in place when the
content changed, re-posts it if it was deleted, moves it if the target channel changed, and never
creates a duplicate. The record lives in ``published_messages`` so this works across restarts and
repeated /setup runs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

import discord

from shopbot.db.repos import state
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import edit_spec, send_spec
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

UpsertAction = Literal["created", "updated", "unchanged", "recreated", "moved"]


def spec_hash(spec: MessageSpec) -> str:
    payload = json.dumps(asdict(spec), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def upsert_message(
    app: App, key: str, channel: discord.TextChannel, spec: MessageSpec, *, force: bool = False
) -> tuple[discord.Message | discord.PartialMessage, UpsertAction]:
    """Create or update the message recorded under ``key`` in ``channel``.

    Raises ``RenderError`` (limits) or ``discord.HTTPException`` (permissions) to the caller, which
    reports them to the operator. Nothing is sent when the spec is invalid.
    """
    guild_id = app.env.guild_id
    digest = spec_hash(spec)
    record = await app.db.run(state.published_get, guild_id, key)
    action: UpsertAction = "created"
    if record is not None:
        if record.channel_id == channel.id:
            if record.content_hash == digest and not force:
                try:
                    existing = await channel.fetch_message(record.message_id)
                    return existing, "unchanged"
                except discord.NotFound:
                    action = "recreated"
            else:
                try:
                    message = channel.get_partial_message(record.message_id)
                    edited = await edit_spec(message, spec)
                    await app.db.transaction(
                        state.published_set, guild_id, key, channel.id, edited.id, digest, app.clock.now()
                    )
                    return edited, "updated"
                except discord.NotFound:
                    action = "recreated"
        else:
            action = "moved"
            old_channel = app.bot.get_channel(record.channel_id) if app.bot is not None else None
            if isinstance(old_channel, discord.TextChannel):
                with contextlib.suppress(discord.HTTPException):
                    await old_channel.get_partial_message(record.message_id).delete()
    message = await send_spec(channel, spec)
    await app.db.transaction(state.published_set, guild_id, key, channel.id, message.id, digest, app.clock.now())
    log_event(log, logging.INFO, "panel_published", key=key, channel_id=channel.id, action=action)
    return message, action


async def remove_message(app: App, key: str) -> bool:
    """Delete the recorded message (if any) and forget it. Returns True if a record existed."""
    guild_id = app.env.guild_id
    record = await app.db.run(state.published_get, guild_id, key)
    if record is None:
        return False
    channel = app.bot.get_channel(record.channel_id) if app.bot is not None else None
    if isinstance(channel, discord.TextChannel):
        with contextlib.suppress(discord.HTTPException):
            await channel.get_partial_message(record.message_id).delete()
    await app.db.transaction(state.published_delete, guild_id, key)
    log_event(log, logging.INFO, "panel_removed", key=key)
    return True
