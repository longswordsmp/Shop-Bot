"""Publishing the ticket panel (called by /setup when the feature is ready) plus setup diagnostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.channels import CREATE_PRIVATE_REQUIRED
from shopbot.core.panels import upsert_message
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, missing_permissions
from shopbot.features.tickets import views
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

PANEL_KEY = "tickets:panel"


def _diagnostics(app: App, guild: discord.Guild) -> list[str]:
    t = app.settings.tickets
    lines: list[str] = []
    me = guild.me
    for category in t.categories:
        target = app.resources.category(guild, category.discord_category) if category.discord_category else None
        if category.discord_category and target is None:
            lines.append(
                app.op(
                    "tickets.publish.category_missing",
                    category=category.id,
                    discord_category=category.discord_category,
                )
            )
        missing = missing_permissions(me, target, CREATE_PRIVATE_REQUIRED)
        if missing:
            lines.append(
                app.op("tickets.publish.category_permissions", category=category.id, permissions=", ".join(missing))
            )
    checks = (
        ("tickets.transcript_channel", t.transcript_channel, (*CHANNEL_SEND_PERMISSIONS, "attach_files")),
        ("tickets.staff_alert_channel", t.staff_alert_channel, CHANNEL_SEND_PERMISSIONS),
    )
    for setting, key, needed in checks:
        if not key:
            continue
        channel = app.resources.text_channel(guild, key)
        if channel is None:
            lines.append(app.op("tickets.publish.channel_missing", setting=setting, name=key))
            continue
        missing = missing_permissions(me, channel, needed)
        if missing:
            lines.append(
                app.op(
                    "tickets.publish.channel_permissions",
                    setting=setting,
                    permissions=", ".join(missing),
                    channel=channel.mention,
                )
            )
    if t.transcript_channel and not app.settings.bot.message_content_intent:
        lines.append(app.op("tickets.publish.no_content_intent"))
    return lines


async def publish(app: App) -> list[str]:
    """Create or update the ticket panel (idempotent) and report problems that would break tickets."""
    guild = app.guild
    if guild is None:
        return [app.op("tickets.publish.no_guild")]
    t = app.settings.tickets
    channel = app.resources.text_channel(guild, t.panel_channel)
    if channel is None:
        return [app.op("tickets.publish.no_channel", name=t.panel_channel)]
    missing = missing_permissions(guild.me, channel, CHANNEL_SEND_PERMISSIONS)
    if missing:
        return [app.op("tickets.publish.missing_permissions", permissions=", ".join(missing), channel=channel.mention)]
    lines: list[str] = []
    try:
        spec = views.panel(app.ui, t)
        _, action = await upsert_message(app, PANEL_KEY, channel, spec)
    except MissingContent as exc:
        lines.append(app.op("tickets.publish.missing_content", name=exc.key))
    except RenderError as exc:
        lines.append(app.op("tickets.publish.invalid", error=str(exc)[:1500]))
    except discord.HTTPException as exc:
        log_event(log, logging.ERROR, "tickets_panel_publish_failed", status=exc.status, code=exc.code)
        lines.append(app.op("tickets.publish.failed", status=exc.status))
    else:
        lines.append(app.op("tickets.publish.panel", action=action, channel=channel.mention))
    lines.extend(_diagnostics(app, guild))
    return lines
