"""Audit log: every administrative or state-changing action is recorded in ``audit_events``.

If ``settings.audit.channel`` is set, a compact operator embed is also posted there (best effort). That
channel must be admin-only; the embed uses technical wording (action identifiers), not customer content.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import discord

from shopbot.db.repos import audit as audit_repo
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.spec import EmbedField, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, app: App) -> None:
        self.app = app

    async def record(
        self,
        action: str,
        *,
        actor_id: int | None,
        target_type: str | None = None,
        target_id: str | int | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        event_id = await self.app.db.transaction(
            audit_repo.insert,
            guild_id=self.app.env.guild_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            now=self.app.clock.now(),
        )
        log_event(
            log,
            logging.INFO,
            "audit",
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
        )
        await self._post(action, actor_id, target_type, target_id, details or {})
        return event_id

    async def _post(
        self,
        action: str,
        actor_id: int | None,
        target_type: str | None,
        target_id: str | int | None,
        details: dict[str, Any],
    ) -> None:
        channel_key = self.app.settings.audit.channel
        bot = self.app.bot
        if not channel_key or bot is None:
            return
        channel_id = self.app.resources.channel_id(channel_key)
        channel = bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        fields = []
        if actor_id is not None:
            fields.append(EmbedField(self.app.op("audit.actor"), f"<@{actor_id}>", inline=True))
        if target_type is not None:
            fields.append(EmbedField(self.app.op("audit.target"), f"{target_type} {target_id}", inline=True))
        if details:
            text = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            if len(text) > 1000:
                text = text[:1000] + "…"  # operator-only diagnostic data, not business text
            fields.append(EmbedField(self.app.op("audit.details"), f"```{text}```"))
        spec = MessageSpec(embeds=(self.app.ui.operator_embed(action, tone="neutral", fields=fields),))
        try:
            await channel.send(**send_kwargs(spec))
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.WARNING, "audit_post_failed", error=type(exc).__name__)
