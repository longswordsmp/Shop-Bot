"""Resolve internal resource keys (from settings.server) to Discord IDs and objects.

An explicit ``id`` in settings wins; otherwise the ID recorded by /setup in ``guild_resources`` is used.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import discord

from shopbot.config.settings import Settings
from shopbot.core.clock import Clock
from shopbot.db.database import Database
from shopbot.db.repos import state


class Resources:
    def __init__(self, db: Database, settings: Callable[[], Settings], guild_id: int, clock: Clock) -> None:
        self._db = db
        self._settings = settings
        self.guild_id = guild_id
        self._clock = clock
        self._ids: dict[tuple[str, str], int] = {}

    async def refresh(self) -> None:
        self._ids = await self._db.run(state.resource_all, self.guild_id)

    def _explicit(self, kind: str, key: str) -> int:
        server = self._settings().server
        definition = {"role": server.role, "category": server.category, "channel": server.channel}[kind](key)
        return definition.id if definition is not None else 0

    def id_for(self, kind: str, key: str) -> int | None:
        if not key:
            return None
        explicit = self._explicit(kind, key)
        if explicit:
            return explicit
        return self._ids.get((kind, key))

    def role_id(self, key: str) -> int | None:
        return self.id_for("role", key)

    def channel_id(self, key: str) -> int | None:
        return self.id_for("channel", key)

    def category_id(self, key: str) -> int | None:
        return self.id_for("category", key)

    def role_ids(self, keys: Iterable[str]) -> set[int]:
        return {rid for rid in (self.role_id(k) for k in keys) if rid}

    async def remember(self, kind: str, key: str, discord_id: int) -> None:
        await self._db.transaction(state.resource_set, self.guild_id, kind, key, discord_id, self._clock.now())
        self._ids[(kind, key)] = discord_id

    async def forget(self, kind: str, key: str) -> None:
        await self._db.transaction(state.resource_delete, self.guild_id, kind, key)
        self._ids.pop((kind, key), None)

    # -- object lookups (cache only; callers handle None) -----------------------------------------

    def role(self, guild: discord.Guild, key: str) -> discord.Role | None:
        rid = self.role_id(key)
        return guild.get_role(rid) if rid else None

    def text_channel(self, guild: discord.Guild, key: str) -> discord.TextChannel | None:
        cid = self.channel_id(key)
        channel = guild.get_channel(cid) if cid else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def category(self, guild: discord.Guild, key: str) -> discord.CategoryChannel | None:
        cid = self.category_id(key)
        channel = guild.get_channel(cid) if cid else None
        return channel if isinstance(channel, discord.CategoryChannel) else None
