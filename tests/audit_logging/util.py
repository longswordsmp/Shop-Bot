"""Helpers for the audit_logging tests: audit lookups, a recorder of ``FAILURE`` events, owner-role and voice-channel
fakes for the /setup checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.context import App
from shopbot.core.events import FAILURE
from shopbot.db.repos import audit as audit_repo
from tests.fakes import FakeGuild, _ChannelMixin, next_id
from tests.helpers import OWNER_ID, TestEnv, deep_merge, make_settings, make_test_env, server_extension


async def audit_events(app: App, action: str) -> list[audit_repo.AuditEvent]:
    """Audit events of ``action``, oldest first."""
    events = await app.db.run(audit_repo.recent, app.env.guild_id, limit=100)
    return [event for event in reversed(events) if event.action == action]


class FailureRecorder:
    """Collects every ``FAILURE`` event published on an app's event bus."""

    def __init__(self, app: App) -> None:
        self.events: list[dict[str, Any]] = []
        app.events.subscribe(FAILURE, self._on_failure)

    async def _on_failure(self, **payload: Any) -> None:
        self.events.append(payload)

    def of(self, source: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("source") == source]


def allow_everyone_to_view(guild: FakeGuild) -> None:
    """Give ``@everyone`` the server-wide View Channels permission, as in a real Discord server."""
    guild.default_role._permissions = discord.Permissions(view_channel=True).value


async def owner_env(tmp_path: Path, overrides: dict[str, Any] | None = None) -> TestEnv:
    """The base layout plus an Owner role that /setup gives to OWNER_ID (who is a member of the fake guild)."""
    settings = deep_merge(
        server_extension(roles=[{"key": "owner"}]),
        {"permissions": {"owner_role": "owner", "admin_roles": ["owner"]}},
    )
    tenv = await make_test_env(tmp_path, settings=make_settings(deep_merge(settings, overrides or {})))
    tenv.guild.add_member(id=OWNER_ID, name="owner")
    return tenv


class FakeVoiceChannel(_ChannelMixin, discord.VoiceChannel):
    """A voice channel (like Discord's default "General" voice channel) for the unmanaged-channel checks."""

    def __init__(self, guild: FakeGuild, *, name: str = "General", overwrites: Any = None) -> None:
        self.id = next_id()
        self.name = name
        self.guild = guild  # type: ignore[assignment]
        self.category_id = None
        self.position = 0
        self.nsfw = False
        self._init_common(overwrites)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"


def add_voice_channel(guild: FakeGuild, **kwargs: Any) -> FakeVoiceChannel:
    channel = FakeVoiceChannel(guild, **kwargs)
    guild._channels[channel.id] = channel
    return channel
