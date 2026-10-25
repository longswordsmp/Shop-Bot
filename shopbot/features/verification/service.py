"""Publishing of the welcome panel (called by /setup when the feature is ready) and Member-role diagnostics.

The panel is kept as exactly one message (``upsert_message`` under ``verification:welcome``): created once, edited
in place when its content changes, re-posted if deleted, moved if ``verification.channel`` changes. /setup withdraws
it (``panel_prefixes``) while the feature is disabled or not fully configured.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.panels import upsert_message
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, can_manage_role, missing_permissions
from shopbot.features.server_keys import channel_mentions, channel_placeholders
from shopbot.features.verification import views
from shopbot.features.verification.content_keys import link_keys
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

PANEL_PREFIX = "verification:"
PANEL_KEY = f"{PANEL_PREFIX}welcome"
MAX_REPORTED_VIOLATIONS = 5

_CHANNEL_TOKEN = re.compile(r"\{(channel_[a-z][a-z0-9_]*)\}")


def member_role(app: App, guild: discord.Guild) -> discord.Role | None:
    """The configured Member role (``permissions.member_role``) as /setup created or adopted it."""
    return app.resources.role(guild, app.settings.permissions.member_role)


def role_problems(app: App, guild: discord.Guild) -> list[str]:
    """Operator lines explaining why the button could not grant the Member role (empty when it can)."""
    key = app.settings.permissions.member_role
    role = member_role(app, guild)
    if role is None:
        return [app.op("verification.role.missing", role=key)]
    lines: list[str] = []
    me = guild.me
    if me is None or missing_permissions(me, None, ("manage_roles",)):
        lines.append(app.op("verification.role.no_manage_roles", role=role.mention))
    if me is None or not can_manage_role(me, role):
        lines.append(app.op("verification.role.not_assignable", role=role.mention))
    return lines


def unresolved_links(app: App, links: dict[str, str]) -> list[str]:
    """``{channel_<key>}`` placeholders used in this feature's text whose channel /setup has not created."""
    known = set(channel_placeholders(app.settings))
    found: set[str] = set()
    for key in link_keys(app.settings):
        text = app.content.get(key)
        if text:
            found.update(name for name in _CHANNEL_TOKEN.findall(text) if name in known and name not in links)
    return sorted(found)


async def publish(app: App) -> list[str]:
    """Create/update the welcome panel. Returns operator report lines."""
    op = app.op
    guild = app.guild
    if guild is None:
        return [op("verification.publish.no_guild")]
    settings = app.settings.verification
    channel = app.resources.text_channel(guild, settings.channel)
    if channel is None:
        return [op("verification.publish.channel_missing", channel=settings.channel)]
    links = channel_mentions(app)
    try:
        spec = views.build_panel(app.ui, settings, links)
    except MissingContent as exc:
        return [op("verification.publish.missing_content", content_key=exc.key)]
    missing = missing_permissions(guild.me, channel, CHANNEL_SEND_PERMISSIONS)
    if missing:
        return [op("verification.publish.missing_permissions", permissions=", ".join(missing), channel=channel.mention)]
    try:
        _, action = await upsert_message(app, PANEL_KEY, channel, spec)
    except RenderError as exc:
        violations = "; ".join(str(v) for v in exc.violations[:MAX_REPORTED_VIOLATIONS])
        return [op("verification.publish.limits", violations=violations)]
    except discord.HTTPException as exc:
        log_event(
            log,
            logging.WARNING,
            "verification_panel_publish_failed",
            channel_id=channel.id,
            status=exc.status,
            code=exc.code,
        )
        return [op("verification.publish.failed", http_status=exc.status, code=exc.code)]
    lines = [op(f"verification.publish.{action}", channel=channel.mention)]
    unresolved = unresolved_links(app, links)
    if unresolved:
        placeholders = ", ".join(f"`{{{name}}}`" for name in unresolved)
        lines.append(op("verification.publish.unresolved_links", placeholders=placeholders))
    lines.extend(role_problems(app, guild))
    return lines
