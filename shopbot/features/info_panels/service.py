"""Publishing of info panels (called by /setup when the feature is ready).

Each configured panel is kept as exactly one message (``upsert_message`` under ``info_panel:<key>``):
created once, edited in place when its content changes, re-posted if deleted, moved if its channel
changes. Panels removed from settings have their message deleted and their record forgotten.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import InfoPanelDef
from shopbot.core.panels import remove_message, upsert_message
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, missing_permissions
from shopbot.db.repos import state
from shopbot.features.info_panels.views import build_panel
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

RECORD_PREFIX = "info_panel:"
MAX_REPORTED_VIOLATIONS = 5


def record_key(panel_key: str) -> str:
    return f"{RECORD_PREFIX}{panel_key}"


async def publish_panel(app: App, guild: discord.Guild, panel: InfoPanelDef) -> str:
    op = app.op
    channel = app.resources.text_channel(guild, panel.channel)
    if channel is None:
        return op("info_panels.publish.channel_missing", panel=panel.key, channel=panel.channel)
    try:
        spec = build_panel(app.ui, panel)
    except MissingContent as exc:
        return op("info_panels.publish.missing_content", panel=panel.key, content_key=exc.key)
    missing = missing_permissions(guild.me, channel, CHANNEL_SEND_PERMISSIONS)
    if missing:
        return op(
            "info_panels.publish.missing_permissions",
            panel=panel.key,
            permissions=", ".join(missing),
            channel=channel.mention,
        )
    try:
        _, action = await upsert_message(app, record_key(panel.key), channel, spec)
    except RenderError as exc:
        violations = "; ".join(str(v) for v in exc.violations[:MAX_REPORTED_VIOLATIONS])
        return op("info_panels.publish.limits", panel=panel.key, violations=violations)
    except discord.HTTPException as exc:
        log_event(
            log,
            logging.WARNING,
            "info_panel_publish_failed",
            panel=panel.key,
            channel_id=channel.id,
            status=exc.status,
            code=exc.code,
        )
        return op("info_panels.publish.failed", panel=panel.key, http_status=exc.status, code=exc.code)
    return op(f"info_panels.publish.{action}", panel=panel.key, channel=channel.mention)


async def remove_unconfigured(app: App, configured: set[str]) -> list[str]:
    lines: list[str] = []
    records = await app.db.run(state.published_with_prefix, app.env.guild_id, RECORD_PREFIX)
    for record in records:
        if record.key in configured:
            continue
        await remove_message(app, record.key)
        panel = record.key[len(RECORD_PREFIX) :]
        log_event(log, logging.INFO, "info_panel_removed", panel=panel)
        lines.append(app.op("info_panels.publish.removed", panel=panel))
    return lines


async def publish(app: App) -> list[str]:
    """Create/update every configured panel and remove unconfigured ones. Returns operator report lines."""
    guild = app.guild
    if guild is None:
        return [app.op("info_panels.publish.no_guild")]
    panels = app.settings.info_panels
    lines = [await publish_panel(app, guild, panel) for panel in panels]
    lines += await remove_unconfigured(app, {record_key(panel.key) for panel in panels})
    if not panels and not lines:
        lines.append(app.op("info_panels.publish.none_configured"))
    return lines
