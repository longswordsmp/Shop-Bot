"""Publishing the shop panel (called by /setup when checkout is ready) plus setup diagnostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.channels import CREATE_PRIVATE_REQUIRED
from shopbot.core.panels import upsert_message
from shopbot.core.permissions import channel_send_permissions, missing_permissions
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import views
from shopbot.features.credits import service as credits_service
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError
from shopbot.ui.spec import ButtonSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

CATALOG_FEATURE = "catalog"


def browse_button(app: App) -> ButtonSpec | None:
    """The catalog's browse button, when private browsing is on, the catalog is ready and its label is set."""
    if not app.settings.catalog.browse_enabled or not app.ready(CATALOG_FEATURE):
        return None
    return views.catalog_browse_button(app.ui)


def panel_spec(app: App) -> MessageSpec:
    # Imported here: the problems feature depends on checkout (no import cycle at module load).
    from shopbot.features.problems import button as problems_button

    return views.panel(
        app.ui,
        browse_button=browse_button(app),
        balance_button=credits_service.balance_button(app),
        report_button=problems_button.report_button(app),
    )


def _diagnostics(app: App, guild: discord.Guild) -> list[str]:
    c = app.settings.checkout
    lines: list[str] = []
    me = guild.me
    staff = app.resources.text_channel(guild, c.staff_channel)
    if staff is None:
        lines.append(app.op("checkout.publish.staff_channel_missing", name=c.staff_channel))
    else:
        missing = missing_permissions(me, staff, channel_send_permissions(uploads=app.banners.enabled))
        if missing:
            lines.append(
                app.op(
                    "checkout.publish.staff_channel_permissions", permissions=", ".join(missing), channel=staff.mention
                )
            )
    category = app.resources.category(guild, c.order_category)
    if category is None:
        lines.append(app.op("checkout.publish.category_missing", name=c.order_category))
    missing = missing_permissions(me, category, CREATE_PRIVATE_REQUIRED)
    if missing:
        lines.append(app.op("checkout.publish.category_permissions", permissions=", ".join(missing)))
    if c.transcript_on_close and (not app.settings.tickets.transcript_channel or not app.ready("tickets")):
        lines.append(app.op("checkout.publish.transcripts_unavailable"))
    return lines


async def publish(app: App) -> list[str]:
    """Create or update the shop panel (idempotent) and report problems that would break checkout."""
    guild = app.guild
    if guild is None:
        return [app.op("checkout.publish.no_guild")]
    c = app.settings.checkout
    channel = app.resources.text_channel(guild, c.panel_channel)
    if channel is None:
        return [app.op("checkout.publish.no_channel", name=c.panel_channel)]
    missing = missing_permissions(guild.me, channel, channel_send_permissions(uploads=app.banners.enabled))
    if missing:
        return [app.op("checkout.publish.missing_permissions", permissions=", ".join(missing), channel=channel.mention)]
    lines: list[str] = []
    try:
        _, action = await upsert_message(app, k.PANEL_KEY, channel, panel_spec(app))
    except MissingContent as exc:
        lines.append(app.op("checkout.publish.missing_content", name=exc.key))
    except RenderError as exc:
        lines.append(app.op("checkout.publish.invalid", error=str(exc)[:1500]))
    except discord.HTTPException as exc:
        log_event(log, logging.ERROR, "checkout_panel_publish_failed", status=exc.status, code=exc.code)
        lines.append(app.op("checkout.publish.failed", status=exc.status))
    else:
        lines.append(app.op("checkout.publish.panel", action=action, channel=channel.mention))
    lines.extend(_diagnostics(app, guild))
    return lines
