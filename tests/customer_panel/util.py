"""Helpers for the customer-panel tests: the live ticket panel, the submission form, notifications and /myorders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from shopbot.context import App
from shopbot.features.checkout import content_keys as k
from shopbot.features.my_orders import content_keys as mk
from shopbot.ui.spec import EmbedField
from tests.checkout.util import make_env
from tests.fakes import FakeInteraction, FakeMember, FakeMessage
from tests.helpers import TestEnv, deep_merge

MY_ORDERS: dict[str, Any] = {"features": {"my_orders": True}}

# A submission form with a required short field and an optional paragraph (synthetic ids).
SUBMISSION_FORM: dict[str, Any] = {
    "checkout": {
        "submission_form": [
            {"id": "reference", "style": "short", "required": True, "min_length": 3, "max_length": 40},
            {"id": "note", "style": "paragraph", "required": False, "max_length": 500},
        ]
    }
}

# Idle carts expire after 30 minutes; the customer is warned 10 minutes before.
WARNING: dict[str, Any] = {"checkout": {"session_expiry_minutes": 30, "session_expiry_warning_minutes": 10}}


async def env(tmp_path: Path, *overrides: dict[str, Any], **kwargs: Any) -> TestEnv:
    merged: dict[str, Any] = dict(MY_ORDERS)
    for override in overrides:
        merged = deep_merge(merged, override)
    return await make_env(tmp_path, merged, **kwargs)


def fields_of(message: FakeMessage, index: int = 0) -> list[EmbedField]:
    return [EmbedField(f.name or "", f.value or "", bool(f.inline)) for f in message.embeds[index].fields]


def field_map(message: FakeMessage) -> dict[str, str]:
    """Field name -> value over every card of the message (later cards win on duplicate names)."""
    return {f.name or "": f.value or "" for embed in message.embeds for f in embed.fields}


def stage(tenv: TestEnv, status: str) -> str:
    return tenv.text(k.stage_key(status))


def timeline_entry(tenv: TestEnv, status: str, at: int) -> str:
    text = tenv.app.content.render(
        k.TICKET_TIMELINE_ENTRY, stage=stage(tenv, status), time=f"<t:{at}:f>", relative=f"<t:{at}:R>"
    )
    assert text is not None
    return text


def bot_messages(channel: Any) -> list[FakeMessage]:
    return [msg for msg in channel.messages if msg.author is channel.guild.me]


async def run_my_orders(tenv: TestEnv, user: FakeMember, *, app: App | None = None) -> FakeInteraction:
    """Invoke /myorders like discord.py does (the command has no checks)."""
    app = app or tenv.app
    assert app.bot is not None
    command = app.bot.tree.get_command(mk.COMMAND_NAME, guild=app.guild_object)
    assert isinstance(command, app_commands.Command), "the /myorders command is not registered"
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    await command.callback(interaction)  # type: ignore[call-arg]
    return interaction
