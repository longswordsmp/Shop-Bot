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
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Literal

import discord

from shopbot.db.repos import state
from shopbot.logging_setup import log_event
from shopbot.ui.banners import place_banner
from shopbot.ui.discord_render import edit_spec, send_spec
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

UpsertAction = Literal["created", "updated", "unchanged", "recreated", "moved"]
Prepare = Callable[[MessageSpec], Awaitable[MessageSpec]]


def spec_hash(spec: MessageSpec) -> str:
    """Digest of everything a message shows, as it is sent (the banner placed in the card, ``place_banner``). Files
    and the banner count by name, type and sha256 (never by their bytes), so a new image re-renders the message."""
    spec = place_banner(spec)
    data = asdict(replace(spec, files=None, banner=None))
    del data["files"], data["banner"]
    if spec.files is not None:
        data["files"] = [[f.filename, f.content_type, f.sha256] for f in spec.files]
    if spec.banner is not None:
        data["banner"] = [spec.banner.filename, spec.banner.content_type, spec.banner.sha256]
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def upsert_message(
    app: App,
    key: str,
    channel: discord.TextChannel,
    spec: MessageSpec,
    *,
    force: bool = False,
    prepare: Prepare | None = None,
) -> tuple[discord.Message | discord.PartialMessage, UpsertAction]:
    """Create or update the message recorded under ``key`` in ``channel``.

    ``prepare`` (optional) completes the spec right before it is sent or edited, e.g. loads the bytes of file
    references (``FileSpec`` without data) from the database; it is not called when the message is unchanged, so
    unchanged messages never load their files. The content hash comes from ``spec`` (files count by sha256).

    Raises ``RenderError`` (limits) or ``discord.HTTPException`` (permissions) to the caller, which
    reports them to the operator. Nothing is sent when the spec is invalid.
    """
    guild_id = app.env.guild_id
    digest = spec_hash(spec)
    prepared: list[MessageSpec] = []

    async def outgoing() -> MessageSpec:
        if not prepared:
            prepared.append(await prepare(spec) if prepare is not None else spec)
        return prepared[0]

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
                    edited = await edit_spec(message, await outgoing())
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
                try:
                    await old_channel.get_partial_message(record.message_id).delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as exc:
                    # The new message is still published; the old one must be removed by hand. Make it visible.
                    log_event(
                        log,
                        logging.WARNING,
                        "panel_old_copy_not_deleted",
                        key=key,
                        channel_id=record.channel_id,
                        message_id=record.message_id,
                        error=type(exc).__name__,
                    )
    message = await send_spec(channel, await outgoing())
    await app.db.transaction(state.published_set, guild_id, key, channel.id, message.id, digest, app.clock.now())
    log_event(log, logging.INFO, "panel_published", key=key, channel_id=channel.id, action=action)
    return message, action


async def remove_message(app: App, key: str) -> bool:
    """Delete the recorded message (if any) and forget it. Returns True if a record existed."""
    guild_id = app.env.guild_id
    record = await app.db.run(state.published_get, guild_id, key)
    if record is None:
        return False
    if app.bot is None:
        raise RuntimeError("cannot remove a published message without a Discord connection")
    channel = app.bot.get_channel(record.channel_id)
    if channel is None:
        try:
            channel = await app.bot.fetch_channel(record.channel_id)
        except discord.NotFound:
            channel = None  # the channel is gone, so is the message
    if isinstance(channel, discord.TextChannel):
        # NotFound = already deleted. Any other HTTPException propagates: the record is kept so a later run can
        # retry, and the caller reports it.
        with contextlib.suppress(discord.NotFound):
            await channel.get_partial_message(record.message_id).delete()
    await app.db.transaction(state.published_delete, guild_id, key)
    log_event(log, logging.INFO, "panel_removed", key=key)
    return True


@dataclass(frozen=True)
class Withdrawal:
    feature: str
    key: str
    ok: bool


async def withdraw_unready_panels(app: App, *, dry_run: bool = False) -> list[Withdrawal]:
    """Remove the published panels of every feature that is disabled or not fully configured.

    Customers must never see a panel whose feature is not ready (e.g. after required text was cleared, a
    reload, or a redeploy). Called by /setup, after configuration changes, and at startup. With ``dry_run``
    nothing is changed and the panels that would be removed are returned.
    """
    results: list[Withdrawal] = []
    for module in app.modules:
        if not module.panel_prefixes or app.ready(module.name):
            continue
        for prefix in module.panel_prefixes:
            records = await app.db.run(state.published_with_prefix, app.env.guild_id, prefix)
            for record in records:
                if dry_run:
                    results.append(Withdrawal(module.name, record.key, True))
                    continue
                try:
                    await remove_message(app, record.key)
                except Exception as exc:  # report and continue with the other panels
                    log_event(log, logging.WARNING, "panel_withdraw_failed", key=record.key, error=type(exc).__name__)
                    results.append(Withdrawal(module.name, record.key, False))
                    continue
                log_event(log, logging.INFO, "panel_withdrawn", feature=module.name, key=record.key)
                results.append(Withdrawal(module.name, record.key, True))
    return results


async def withdraw_and_audit(app: App, *, actor_id: int | None, reason: str) -> list[Withdrawal]:
    """``withdraw_unready_panels`` for automatic triggers; records an audit event when anything was removed."""
    if app.bot is None or app.guild is None:
        return []
    results = await withdraw_unready_panels(app)
    if results:
        await app.audit.record(
            "panels.withdrawn",
            actor_id=actor_id,
            target_type="panels",
            target_id=reason,
            details={"withdrawn": [r.key for r in results if r.ok], "failed": [r.key for r in results if not r.ok]},
        )
    return results
