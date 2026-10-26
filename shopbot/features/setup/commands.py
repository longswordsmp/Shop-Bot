"""The admin-only /setup slash command and the owner-only /reset command."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_admin
from shopbot.features.setup.report import notice_message, report_messages
from shopbot.features.setup.reset_handlers import handle_reset
from shopbot.features.setup.service import run_setup, setup_lock
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError

log = logging.getLogger(__name__)


async def handle_setup(interaction: discord.Interaction[Any], *, dry_run: bool, publish: bool) -> None:
    app = app_of(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = app.guild
    if guild is None or guild.me is None:
        await reply(interaction, notice_message(app.ui, app.op("server.setup.no_guild"), tone="danger", banner="error"))
        return
    lock = setup_lock(app)
    if lock.locked():
        busy = notice_message(app.ui, app.op("server.setup.busy"), tone="warning", banner="submission-and-processing")
        await reply(interaction, busy)
        return
    async with lock:
        report = await run_setup(app, guild, actor_id=interaction.user.id, dry_run=dry_run, publish=publish)
    try:
        for spec in report_messages(app.ui, app.op, report):
            await reply(interaction, spec, ephemeral=True)
    except (discord.HTTPException, RenderError) as exc:
        # The work is done and audited; only the report delivery failed (e.g. the interaction expired).
        log_event(log, logging.ERROR, "setup_report_failed", error=type(exc).__name__)


def build_setup_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="setup",
        description="Create or repair the server's roles, categories and channels, then publish panels.",
    )
    @app_commands.describe(
        dry_run="Only report what would change; change nothing.",
        publish="Also post or update the panels of every ready feature (default: true).",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def setup_command(interaction: discord.Interaction[Any], dry_run: bool = False, publish: bool = True) -> None:
        await handle_setup(interaction, dry_run=dry_run, publish=publish)

    return setup_command


def build_reset_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="reset",
        description="Owner only: delete the server's channels, categories and roles so /setup can build them anew.",
    )
    @app_commands.describe(
        wipe_shop_data="Also delete every order, product, review, ticket and customer record (default: keep them).",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def reset_command(interaction: discord.Interaction[Any], wipe_shop_data: bool = False) -> None:
        await handle_reset(interaction, wipe=wipe_shop_data)

    return reset_command
