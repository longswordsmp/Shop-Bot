"""Notifications to users and channels, at most once per ``dedupe_key``.

User notifications try a direct message first (when ``features.dm_notifications`` is on). If the user
has DMs closed, or DMs are disabled, the notification falls back to a configured channel (usually the
user's ticket/order channel) with a mention so the user is pinged. Every attempt is recorded.
The caller supplies the fully rendered ``MessageSpec``; the notifier never adds wording.

Failure reporting (each failure is reported once, by the notifier; callers must not report it again):
- a direct message failed but the notification reached the user another way (the fallback channel, or the caller's
  own way when ``alert_on_failure=False``): audit entry ``notifications.dm_failed`` (kind, user, reason; no content
  and no staff alert - closed DMs are common);
- nothing was delivered (``notify_user`` with ``alert_on_failure``, or any ``notify_channel`` failure: channel
  missing or a Discord error): the ``FAILURE`` event (kind "notification", source = the notification kind), which
  the operations feature audits (``notifications.failed``) and alerts staff about.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import discord

from shopbot.core.events import FAILURE
from shopbot.db.repos import notifications as notif_repo
from shopbot.db.repos import state
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

Delivery = Literal["dm", "channel", "none", "duplicate"]
Attachment = tuple[str, bytes]  # (filename, data)


def _files(attachments: Sequence[Attachment]) -> list[discord.File]:
    """Fresh File objects per send attempt (a File's stream cannot be reused after a failed send)."""
    return [discord.File(io.BytesIO(data), filename=name) for name, data in attachments]


class Notifier:
    def __init__(self, app: App) -> None:
        self.app = app

    async def _already(self, dedupe_key: str | None) -> bool:
        return bool(dedupe_key) and await self.app.db.run(notif_repo.already_sent, dedupe_key or "")

    async def _record(
        self,
        dedupe_key: str | None,
        *,
        user_id: int | None,
        channel_id: int | None,
        kind: str,
        delivery: str,
        status: str,
        error: str | None,
    ) -> None:
        if not dedupe_key:
            return
        await self.app.db.transaction(
            notif_repo.record,
            dedupe_key=dedupe_key,
            user_id=user_id,
            channel_id=channel_id,
            kind=kind,
            delivery=delivery,
            status=status,
            error=error,
            now=self.app.clock.now(),
        )

    async def _audit_dm_failed(self, *, kind: str, user_id: int, reason: str) -> None:
        """Audit a direct message that failed although the notification reaches the user another way."""
        try:
            await self.app.audit.record(
                "notifications.dm_failed",
                actor_id=None,
                target_type="user",
                target_id=user_id,
                details={"kind": kind, "reason": reason},
            )
        except Exception as exc:  # the audit is best effort; it never breaks the notification
            log_event(log, logging.WARNING, "notification_audit_failed", kind=kind, error=type(exc).__name__)

    async def _report_undelivered(self, *, kind: str, user_id: int | None, channel_id: int | None) -> None:
        """Publish ``FAILURE`` for a notification nothing delivered (operations audits it and alerts staff)."""
        await self.app.events.publish(FAILURE, kind="notification", source=kind, user_id=user_id, channel_id=channel_id)

    async def notify_user(
        self,
        *,
        user_id: int,
        spec: MessageSpec,
        kind: str,
        dedupe_key: str | None,
        fallback_channel_id: int | None = None,
        prefer_channel: bool = False,
        attachments: Sequence[Attachment] = (),
        alert_on_failure: bool = True,
        always_dm: bool = False,
    ) -> Delivery:
        """Deliver ``spec`` by DM, else in ``fallback_channel_id``. ``alert_on_failure=False``: the caller has another
        way to reach the user (e.g. it posts in a channel itself), so "none" is not reported as a failed notification
        (a failed DM is audited as ``notifications.dm_failed``); the caller reports the failure if that way fails too.
        ``always_dm``: try the direct message even when ``features.dm_notifications`` is off (the caller's own
        setting asked for a direct message, e.g. ``tickets.transcript_dm_user``)."""
        if await self._already(dedupe_key):
            return "duplicate"
        bot = self.app.bot
        if bot is None:
            raise RuntimeError("bot is not running")
        dm_error: str | None = None
        if (self.app.settings.features.dm_notifications or always_dm) and not prefer_channel:
            try:
                user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                kwargs = send_kwargs(spec)
                if attachments:
                    kwargs["files"] = [*kwargs.get("files", ()), *_files(attachments)]
                await user.send(**kwargs)
                await self._record(
                    dedupe_key, user_id=user_id, channel_id=None, kind=kind, delivery="dm", status="sent", error=None
                )
                return "dm"
            except discord.Forbidden:
                dm_error = "dm_forbidden"
                await self.app.db.transaction(state.set_dm_unavailable, user_id, self.app.clock.now())
            except (discord.HTTPException, RenderError) as exc:
                dm_error = f"dm_failed: {type(exc).__name__}"
            log_event(log, logging.INFO, "notification_dm_unavailable", kind=kind, user_id=user_id, reason=dm_error)
        dm_reason = dm_error  # why the direct message failed (None: no DM was attempted)

        if fallback_channel_id and (self.app.settings.notifications.fallback_to_channel or prefer_channel):
            channel = bot.get_channel(fallback_channel_id)
            if isinstance(channel, discord.abc.Messageable):
                try:
                    kwargs = send_kwargs(replace(spec, mention_user_ids=(user_id,)))
                    if attachments:
                        kwargs["files"] = [*kwargs.get("files", ()), *_files(attachments)]
                    await channel.send(**kwargs)
                except (discord.HTTPException, RenderError) as exc:
                    dm_error = f"{dm_error or ''} channel_failed: {type(exc).__name__}".strip()
                else:
                    await self._record(
                        dedupe_key,
                        user_id=user_id,
                        channel_id=fallback_channel_id,
                        kind=kind,
                        delivery="channel",
                        status="sent",
                        error=dm_error,
                    )
                    if dm_reason is not None:  # the DM failed and the fallback channel delivered the notification
                        await self._audit_dm_failed(kind=kind, user_id=user_id, reason=dm_reason)
                    return "channel"
            else:
                dm_error = f"{dm_error or ''} channel_missing".strip()

        log_event(log, logging.WARNING, "notification_undelivered", kind=kind, user_id=user_id, reason=dm_error)
        await self._record(
            dedupe_key,
            user_id=user_id,
            channel_id=fallback_channel_id,
            kind=kind,
            delivery="none",
            status="failed",
            error=dm_error,
        )
        # Operations records it in the audit log and alerts staff (who the notification was for and its kind only).
        if alert_on_failure:
            await self._report_undelivered(kind=kind, user_id=user_id, channel_id=fallback_channel_id)
        elif dm_reason is not None:
            # The caller reaches the user another way (and reports it if that fails): only the DM failure is audited.
            await self._audit_dm_failed(kind=kind, user_id=user_id, reason=dm_reason)
        return "none"

    async def notify_channel(
        self,
        *,
        channel_id: int,
        spec: MessageSpec,
        kind: str,
        dedupe_key: str | None,
        attachments: Sequence[Attachment] = (),
        user_id: int | None = None,
    ) -> discord.Message | None:
        """Post to a channel (e.g. staff alerts). Returns the message, or None if undeliverable/duplicate.

        A failure (channel missing, Discord error) is recorded and reported with ``FAILURE``; callers must not
        report it again. ``user_id`` is the member the post is addressed to (e.g. the customer in their order
        channel), named in that report; None for posts addressed to staff."""
        if await self._already(dedupe_key):
            return None
        bot = self.app.bot
        channel = bot.get_channel(channel_id) if bot is not None else None
        if not isinstance(channel, discord.abc.Messageable):
            log_event(log, logging.WARNING, "notification_channel_missing", kind=kind, channel_id=channel_id)
            await self._record(
                dedupe_key,
                user_id=user_id,
                channel_id=channel_id,
                kind=kind,
                delivery="none",
                status="failed",
                error="channel_missing",
            )
            await self._report_undelivered(kind=kind, user_id=user_id, channel_id=channel_id)
            return None
        try:
            kwargs = send_kwargs(spec)
            if attachments:
                kwargs["files"] = [*kwargs.get("files", ()), *_files(attachments)]
            message = await channel.send(**kwargs)
        except (discord.HTTPException, RenderError) as exc:
            log_event(
                log,
                logging.WARNING,
                "notification_channel_failed",
                kind=kind,
                channel_id=channel_id,
                error=type(exc).__name__,
            )
            await self._record(
                dedupe_key,
                user_id=user_id,
                channel_id=channel_id,
                kind=kind,
                delivery="none",
                status="failed",
                error=type(exc).__name__,
            )
            await self._report_undelivered(kind=kind, user_id=user_id, channel_id=channel_id)
            return None
        await self._record(
            dedupe_key, user_id=user_id, channel_id=channel_id, kind=kind, delivery="channel", status="sent", error=None
        )
        return message
