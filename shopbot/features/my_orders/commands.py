"""``/myorders``: a customer command (like ``/review``). Any member of the server can use it; it shows only their
own orders.

The command's description is customer-facing text (``my_orders.command.description``, required). Discord needs it
when the command is registered, so the command is added to the command tree only when ``features.my_orders`` is on
and the description is configured and valid; it is sent to Discord when the bot starts (``/reload`` does not
re-register commands: a restart applies a changed description or switch). The reply is always private.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, reply_content_error
from shopbot.config.content import MissingContent, validate_value
from shopbot.features import core_keys as core
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders import service
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


async def my_orders(interaction: discord.Interaction[Any]) -> None:
    """Private reply with the member's own orders (first page)."""
    app = app_of(interaction)
    if interaction.guild_id != app.env.guild_id:
        await reply_content_error(interaction, core.ERROR_PERMISSION)
        return
    if not app.ready(k.FEATURE):
        await reply_content_error(interaction, core.ERROR_UNAVAILABLE)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        spec = await service.page_spec(app, interaction.user.id, 0)
        await reply(interaction, spec, ephemeral=True)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="my_orders")
        await reply_content_error(interaction, core.ERROR_UNAVAILABLE)
    except RenderError as exc:
        error_id = uuid.uuid4().hex[:12]
        log_event(log, logging.ERROR, "my_orders_render_failed", error_id=error_id, error=str(exc)[:200])
        await reply_content_error(interaction, core.ERROR_GENERIC, error_id=error_id)


def build_command(description: str) -> app_commands.Command[Any, Any, None]:
    command: app_commands.Command[Any, Any, None] = app_commands.Command(
        name=k.COMMAND_NAME, description=description, callback=my_orders
    )
    app_commands.guild_only(command)
    return command


def command_description(app: App) -> str | None:
    """The configured, valid description of /myorders (None when it is missing or not acceptable to Discord)."""
    description = app.content.get(k.COMMAND_DESCRIPTION)
    definition = app.content.registry.get(k.COMMAND_DESCRIPTION)
    if description is None or definition is None or validate_value(definition, description):
        return None
    return description


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    if not app.settings.features.my_orders:
        return
    description = command_description(app)
    if description is None:
        # Never registered with invented wording: /status lists the missing text; a restart registers it.
        log_event(log, logging.WARNING, "my_orders_command_not_registered", reason="description_not_configured")
        return
    app.bot.tree.add_command(build_command(description), guild=app.guild_object)
