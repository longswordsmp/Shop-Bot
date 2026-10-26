"""Publishing the ticket panel (called by /setup when the feature is ready) plus setup diagnostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.channels import CREATE_PRIVATE_REQUIRED
from shopbot.core.panels import remove_message, upsert_message
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, missing_permissions
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import service, views
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
    if t.transcript_channel and not app.gateway_intents.message_content:
        lines.append(app.op("tickets.publish.no_content_intent"))
    lines.extend(_ping_diagnostics(app, guild))
    return lines


def _ping_diagnostics(app: App, guild: discord.Guild) -> list[str]:
    """ping_staff categories whose roles Discord would not notify (role not mentionable and the bot lacks
    "Mention @everyone, @here and All Roles" where the ping is posted)."""
    t = app.settings.tickets
    alert_channel = app.resources.text_channel(guild, t.staff_alert_channel) if t.staff_alert_channel else None
    lines: list[str] = []
    for category in t.categories:
        if not category.ping_staff:
            continue
        if t.staff_alert_channel:
            where: discord.abc.GuildChannel | None = alert_channel  # pings go with the staff alert
        else:  # pings go with the welcome message, in a channel created in the ticket category
            where = app.resources.category(guild, category.discord_category) if category.discord_category else None
        roles = service.unmentionable_ping_roles(app, category, where)
        if not roles:
            continue
        log_event(
            log,
            logging.WARNING,
            "tickets_ping_role_not_mentionable",
            category=category.id,
            path="publish",
            role_ids=[role.id for role in roles],
        )
        lines.append(
            app.op(
                "tickets.publish.ping_not_mentionable",
                category=category.id,
                roles=", ".join(role.mention for role in roles),
            )
        )
    return lines


async def publish(app: App) -> list[str]:
    """Create or update the ticket panel (idempotent) and report problems that would break tickets."""
    guild = app.guild
    if guild is None:
        return [app.op("tickets.publish.no_guild")]
    t = app.settings.tickets
    if not k.panel_enabled(app.settings):
        # No standalone panel (tickets are opened by other features): remove one published earlier, if any.
        removed: list[str] = []
        try:
            if await remove_message(app, PANEL_KEY):
                removed.append(app.op("tickets.publish.panel_removed"))
        except discord.HTTPException as exc:
            log_event(log, logging.WARNING, "tickets_panel_remove_failed", status=exc.status)
            removed.append(app.op("tickets.publish.failed", status=exc.status))
        return removed + _diagnostics(app, guild)
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
