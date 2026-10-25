"""Startup check and channel-deletion warnings for resources managed by /setup."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

import discord

from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def managed_resources(app: App) -> Iterator[tuple[str, str, int]]:
    """``(kind, key, discord_id)`` for every configured resource with a known id (explicit or recorded)."""
    server = app.settings.server
    for kind, keys in (
        ("role", [r.key for r in server.roles]),
        ("category", [c.key for c in server.categories]),
        ("channel", [c.key for c in server.channels]),
    ):
        for key in keys:
            discord_id = app.resources.id_for(kind, key)
            if discord_id:
                yield kind, key, discord_id


async def on_ready(app: App) -> None:
    """Warn about managed roles/channels deleted while the bot was offline (/setup re-creates them)."""
    guild = app.guild
    if guild is None:
        return
    missing: list[str] = []
    for kind, key, discord_id in managed_resources(app):
        exists = guild.get_role(discord_id) if kind == "role" else guild.get_channel(discord_id)
        if exists is None:
            missing.append(f"{kind}:{key}")
    if missing:
        log_event(
            log,
            logging.WARNING,
            "managed_resources_missing",
            resources=missing,
            hint="run /setup to re-create them",
        )


async def on_channel_delete(app: App, channel: discord.abc.GuildChannel) -> None:
    for kind, key, discord_id in managed_resources(app):
        if kind != "role" and discord_id == channel.id:
            log_event(
                log,
                logging.WARNING,
                "managed_channel_deleted",
                kind=kind,
                key=key,
                channel_id=channel.id,
                hint="run /setup to re-create it",
            )
