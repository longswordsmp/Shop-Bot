"""Privileged gateway intents: what the settings ask for, and what the Discord Developer Portal allows.

Discord refuses to connect a bot that asks for a privileged intent (Message Content, Server Members) its Developer
Portal page does not switch on. Before connecting, the bot reads its application's flags and leaves out a requested
intent the portal does not allow, so a missing switch turns off only what needs it (message text in transcripts and
the gift card chat guard; the welcome mention) instead of stopping the bot. The log and /status name it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp
import discord
from discord.http import HTTPClient

from shopbot.logging_setup import log_event

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivilegedIntents:
    message_content: bool = False
    members: bool = False


def portal_allows(flags: discord.ApplicationFlags) -> PrivilegedIntents:
    """The intents the portal switches on (the "limited" flags are what an unverified bot gets)."""
    return PrivilegedIntents(
        message_content=flags.gateway_message_content or flags.gateway_message_content_limited,
        members=flags.gateway_guild_members or flags.gateway_guild_members_limited,
    )


def effective(requested: PrivilegedIntents, allowed: PrivilegedIntents | None) -> PrivilegedIntents:
    """What the bot asks for when it connects: requested and allowed (``allowed`` None: unknown, ask as configured)."""
    if allowed is None:
        return requested
    return PrivilegedIntents(
        message_content=requested.message_content and allowed.message_content,
        members=requested.members and allowed.members,
    )


async def read_portal_intents(token: str) -> PrivilegedIntents | None:
    """The intents the portal allows for this bot, or None when they cannot be read (the bot then connects as
    configured and Discord's own check applies). The token is never logged."""
    http = HTTPClient(asyncio.get_running_loop())
    try:
        await http.static_login(token)
        data = await http.application_info()
    except (discord.LoginFailure, discord.HTTPException, aiohttp.ClientError, OSError) as exc:
        log_event(log, logging.WARNING, "portal_intents_unknown", error=type(exc).__name__)
        return None
    finally:
        await http.close()
    return portal_allows(discord.ApplicationFlags._from_value(int(data.get("flags") or 0)))


def report(requested: PrivilegedIntents, allowed: PrivilegedIntents | None) -> list[str]:
    """Names of the requested intents the portal does not allow (logged at startup, shown by /status)."""
    if allowed is None:
        return []
    missing = []
    if requested.message_content and not allowed.message_content:
        missing.append("message_content")
    if requested.members and not allowed.members:
        missing.append("members")
    return missing
