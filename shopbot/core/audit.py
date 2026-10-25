"""Audit log: every administrative or state-changing action is recorded in ``audit_events``.

If ``settings.audit.channel`` is set, a compact operator embed is also posted there (best effort). That
channel must be admin-only; the embed uses technical wording (action identifiers), not customer content.
Details are sanitized before they are stored or posted (``sanitize_details``): sensitive keys are masked, error
texts are reduced to the exception type, token-shaped strings are redacted and long texts are capped.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import discord

from shopbot.db.repos import audit as audit_repo
from shopbot.features.server_keys import PRIVATE_ACCESS, effective_access
from shopbot.logging_setup import log_event, redact
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.spec import EmbedField, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


# Detail keys whose values are never recorded (defense in depth: callers must not pass secrets at all).
_SENSITIVE_KEY = re.compile(r"(token|secret|password|passwd|credential|authorization|api_?key|cookie|webhook)", re.I)
# Detail keys holding error text: only the exception type is kept (raw error text may echo request data).
_ERROR_KEY = re.compile(r"(^|_)error$", re.I)
_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
MAX_DETAIL_TEXT = 300
REDACTED = "[REDACTED]"
# The audit-channel embed shows the details JSON in up to this many fields of this many characters (within Discord's
# 1024-character field and 6000-character embed limits); anything longer is cut there (the database keeps it all).
DETAIL_FIELD_TEXT = 1000
MAX_DETAIL_FIELDS = 4


def sanitize_details(value: Any, key: str | None = None) -> Any:
    """Audit details safe to store and to post in the audit channel: sensitive keys masked, error texts reduced to
    their exception type, token-shaped strings redacted, every text capped (operator data, not business text)."""
    if isinstance(value, dict):
        return {str(k): sanitize_details(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_details(v, key) for v in value]
    if isinstance(value, str):
        if key is not None and _SENSITIVE_KEY.search(key):
            return REDACTED
        if key is not None and _ERROR_KEY.search(key):
            match = _TYPE_NAME.match(value.strip())
            return match.group(0)[:100] if match else "-"
        text = redact(value)
        return text if len(text) <= MAX_DETAIL_TEXT else text[:MAX_DETAIL_TEXT] + "…"
    return value


def detail_chunks(details: dict[str, Any]) -> list[str]:
    """The details JSON split into consecutive pieces for the audit-channel embed (operator-only diagnostic data,
    not business text): at most ``MAX_DETAIL_FIELDS`` pieces, the last one marked with "…" when cut."""
    text = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
    chunks = [text[start : start + DETAIL_FIELD_TEXT] for start in range(0, len(text), DETAIL_FIELD_TEXT)]
    if len(chunks) > MAX_DETAIL_FIELDS:
        chunks = chunks[:MAX_DETAIL_FIELDS]
        chunks[-1] = chunks[-1][: DETAIL_FIELD_TEXT - 1] + "…"
    return chunks


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
        details = sanitize_details(details) if details else details
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
        if effective_access(self.app.settings, channel_key) not in PRIVATE_ACCESS:
            log_event(log, logging.WARNING, "audit_channel_not_private", channel=channel_key)
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
            name = self.app.op("audit.details")
            fields.extend(EmbedField(name, f"```{chunk}```") for chunk in detail_chunks(details))
        spec = MessageSpec(embeds=(self.app.ui.operator_embed(action, tone="neutral", fields=fields),))
        try:
            await channel.send(**send_kwargs(spec))
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.WARNING, "audit_post_failed", error=type(exc).__name__)
