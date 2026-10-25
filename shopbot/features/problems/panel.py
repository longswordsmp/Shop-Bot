"""/setup hook of the Report a Problem workflow. It has no panel of its own: its button lives on the #purchase panel,
which checkout publishes (with the button whenever this feature is ready). This hook reports setup problems that
would break report tickets or staff alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from shopbot.core.channels import CREATE_PRIVATE_REQUIRED
from shopbot.core.permissions import missing_permissions
from shopbot.features.problems import service

if TYPE_CHECKING:
    from shopbot.context import App


def _unmentionable(app: App, guild: discord.Guild, where: discord.abc.GuildChannel | None) -> list[discord.Role]:
    """Staff roles a ping would not notify: not mentionable, and the bot lacks "Mention @everyone, @here and All
    Roles" where the ping is posted."""
    me = guild.me
    perms = where.permissions_for(me) if where is not None else me.guild_permissions
    if perms.mention_everyone:
        return []
    roles = (guild.get_role(role_id) for role_id in service.ping_role_ids(app))
    return [role for role in roles if role is not None and not role.mentionable]


async def publish(app: App) -> list[str]:
    guild = app.guild
    if guild is None:
        return [app.op("problems.publish.no_guild")]
    p = app.settings.problems
    lines = [app.op("problems.publish.ready")]
    category = app.resources.category(guild, p.discord_category)
    if category is None:
        lines.append(app.op("problems.publish.category_missing", name=p.discord_category))
    missing = missing_permissions(guild.me, category, CREATE_PRIVATE_REQUIRED)
    if missing:
        lines.append(app.op("problems.publish.category_permissions", permissions=", ".join(missing)))
    alert_key = app.settings.tickets.staff_alert_channel
    alert_channel = app.resources.text_channel(guild, alert_key) if alert_key else None
    if not alert_key:
        lines.append(app.op("problems.publish.no_alert_channel"))
    elif alert_channel is None:
        lines.append(app.op("problems.publish.alert_channel_missing", name=alert_key))
    if p.ping_staff:
        where: discord.abc.GuildChannel | None = alert_channel if alert_key else category
        roles = _unmentionable(app, guild, where)
        if roles:
            lines.append(
                app.op("problems.publish.ping_not_mentionable", roles=", ".join(role.mention for role in roles))
            )
    return lines
