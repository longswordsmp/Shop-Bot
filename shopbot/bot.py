"""Discord client: wiring of routes, commands, listeners, and the ready/recovery sequence."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from shopbot.commands import on_tree_error
from shopbot.logging_setup import log_event
from shopbot.ui.routing import RoutedItem, parse_modal_fields

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

Listener = Callable[..., Awaitable[None]]


def build_intents(app: App) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True  # ticket activity tracking (auto-close) and message events
    intents.message_content = app.settings.bot.message_content_intent  # privileged: transcripts need it
    intents.members = app.settings.bot.members_intent  # privileged: optional member-leave handling
    return intents


class ShopBot(discord.Client):
    def __init__(self, app: App) -> None:
        super().__init__(
            intents=build_intents(app),
            allowed_mentions=discord.AllowedMentions.none(),
            chunk_guilds_at_startup=False,
        )
        self.app = app
        app.bot = self
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = on_tree_error  # type: ignore[method-assign]
        self._event_listeners: dict[str, list[Listener]] = defaultdict(list)
        self._ready_once = False
        self._recovery_lock = asyncio.Lock()
        self.gateway_ready_seen = False

    def add_listener(self, event: str, listener: Listener) -> None:
        self._event_listeners[event].append(listener)

    async def _emit(self, event: str, *args: Any) -> None:
        for listener in self._event_listeners.get(event, []):
            try:
                await listener(*args)
            except Exception:
                log.exception("listener for %s failed", event)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(RoutedItem)
        for module in self.app.modules:
            if module.register is not None:
                module.register(self.app)
        log_event(
            log,
            logging.INFO,
            "routes_registered",
            routes=len(self.app.router.routes()),
            task_kinds=self.app.scheduler.kinds,
        )
        if self.app.settings.bot.sync_commands_on_startup:
            try:
                synced = await self.tree.sync(guild=self.app.guild_object)
                log_event(log, logging.INFO, "commands_synced", count=len(synced), guild_id=self.app.env.guild_id)
            except discord.HTTPException as exc:
                # Usually: the bot is not in GUILD_ID or lacks the applications.commands scope.
                log_event(log, logging.ERROR, "commands_sync_failed", status=exc.status, code=exc.code)

    async def on_ready(self) -> None:
        self.gateway_ready_seen = True
        if self._ready_once:
            log_event(log, logging.INFO, "gateway_ready_again")
            return
        guild = self.get_guild(self.app.env.guild_id)
        if guild is None or guild.unavailable:
            # Recovery must not run against an empty/unavailable cache (it would conclude that channels were
            # deleted). It runs from on_guild_available / on_guild_join once the guild is usable.
            log_event(
                log,
                logging.WARNING if guild is not None else logging.ERROR,
                "guild_not_available",
                guild_id=self.app.env.guild_id,
                hint="waiting for the guild; if this persists, invite the bot to the server identified by GUILD_ID",
            )
            return
        await self._startup_recovery(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        if guild.id == self.app.env.guild_id and self.gateway_ready_seen and not self._ready_once:
            await self._startup_recovery(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id == self.app.env.guild_id and self.gateway_ready_seen and not self._ready_once:
            await self._startup_recovery(guild)

    async def _startup_recovery(self, guild: discord.Guild) -> None:
        """Runs exactly once per process, after the gateway is ready and the guild is available."""
        async with self._recovery_lock:
            if self._ready_once:
                return
            self._ready_once = True
            try:
                await self.app.resources.refresh()
                await self.app.load_overrides()
                await self.app.scheduler.recover()
                for module in self.app.modules:
                    if module.on_ready is None:
                        continue
                    try:
                        await module.on_ready(self.app)
                    except Exception:
                        log.exception("recovery for feature %s failed", module.name)
            except Exception:
                log.exception("startup recovery failed; continuing with the scheduler running")
            finally:
                self.app.scheduler.start(self.app)
            statuses = self.app.readiness.statuses
            log_event(
                log,
                logging.INFO,
                "bot_ready",
                user=str(self.user),
                guild=guild.id,
                ready_features=[n for n, s in statuses.items() if s.ready],
                not_ready_features=[n for n, s in statuses.items() if s.enabled and not s.ready],
            )

    async def on_resumed(self) -> None:
        log_event(log, logging.INFO, "gateway_resumed")

    async def on_disconnect(self) -> None:
        log_event(log, logging.WARNING, "gateway_disconnected")

    async def on_interaction(self, interaction: discord.Interaction[Any]) -> None:
        if interaction.type != discord.InteractionType.modal_submit:
            return
        data: dict[str, Any] = dict(interaction.data or {})
        custom_id = str(data.get("custom_id", ""))
        if not custom_id.startswith("sb:"):
            return
        await self.app.router.dispatch(interaction, custom_id, kind="modal", fields=parse_modal_fields(data))

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.app.env.guild_id:
            return
        await self._emit("message", message)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id == self.app.env.guild_id:
            await self._emit("guild_channel_delete", channel)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id == self.app.env.guild_id:
            await self._emit("raw_message_delete", payload)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id == self.app.env.guild_id:
            await self._emit("raw_bulk_message_delete", payload)

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == self.app.env.guild_id:
            await self._emit("member_remove", member)

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        log.exception("unhandled error in %s", event_method)

    async def close(self) -> None:
        log_event(log, logging.INFO, "bot_closing")
        await self.app.scheduler.stop()
        await super().close()
