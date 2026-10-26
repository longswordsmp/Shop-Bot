"""The welcome mention: a member who joins is mentioned in the verification channel, right under the welcome panel,
and the mention is deleted ``verification.welcome_mention_seconds`` later (``0`` = off).

The mention pings the member (Discord notifies them) and points them at the panel; once it is deleted the channel
shows only the panel again. Members who join within ``GROUP_SECONDS`` of each other share one message, so a wave of
joins never floods the channel. The deletion is a durable task (``verification.welcome_cleanup``), so a restart in
between still removes the message. Needs the Server Members intent (``bot.members_intent`` and the Developer Portal);
without it Discord never reports joins and nothing is posted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from weakref import WeakKeyDictionary

import discord

from shopbot.config.content import MissingContent
from shopbot.core.scheduler import PermanentTaskError
from shopbot.features.server_keys import channel_mentions
from shopbot.features.verification import content_keys as k
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_spec
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.db.repos.tasks import Task
    from shopbot.ui.style import Ui

log = logging.getLogger(__name__)

TASK_CLEANUP: Final = "verification.welcome_cleanup"
# Joins this close together share one message.
GROUP_SECONDS: Final = 2.0
# Mentions per message (Discord's 2000-character content limit leaves room for far more; this keeps it readable).
MAX_MENTIONS: Final = 20


def cleanup_key(message_id: int) -> str:
    return f"{TASK_CLEANUP}:{message_id}"


@dataclass
class _Pending:
    members: list[int] = field(default_factory=list)
    flush: asyncio.Task[None] | None = None


_PENDING: WeakKeyDictionary[App, _Pending] = WeakKeyDictionary()


def _state(app: App) -> _Pending:
    state = _PENDING.get(app)
    if state is None:
        state = _PENDING[app] = _Pending()
    return state


def enabled(app: App) -> bool:
    return app.settings.verification.welcome_mention_seconds > 0 and app.ready(k.VERIFICATION)


def welcome_message(ui: Ui, links: dict[str, str], member_ids: list[int]) -> MessageSpec:
    """The mention(s) and the short text under them; no picture, so it stays light (it is gone 30 s later)."""
    text = ui.text(k.WELCOME_MENTION, **links)
    if text is None:
        raise MissingContent(k.WELCOME_MENTION)
    return MessageSpec(embeds=(ui.embed(description=text, tone="primary"),), mention_user_ids=tuple(member_ids))


async def on_member_join(app: App, member: discord.Member) -> None:
    """Queue the member's mention; the first join of a group posts the message ``GROUP_SECONDS`` later."""
    if member.bot or not enabled(app):
        return
    state = _state(app)
    state.members.append(member.id)
    if state.flush is None or state.flush.done():
        state.flush = asyncio.get_running_loop().create_task(_flush_later(app))


async def _flush_later(app: App) -> None:
    await asyncio.sleep(GROUP_SECONDS)
    await flush(app)


async def flush(app: App) -> None:
    """Post the queued mentions (in messages of at most ``MAX_MENTIONS``) and schedule their deletion."""
    state = _state(app)
    if state.flush is not None and state.flush is not asyncio.current_task() and not state.flush.done():
        state.flush.cancel()  # flushed now: the timed flush has nothing left to do
    queued, state.members = state.members, []
    if not queued or not enabled(app) or app.guild is None:
        return
    channel = app.resources.text_channel(app.guild, app.settings.verification.channel)
    if channel is None:
        log_event(log, logging.WARNING, "welcome_mention_skipped", reason="channel_missing")
        return
    seconds = app.settings.verification.welcome_mention_seconds
    for start in range(0, len(queued), MAX_MENTIONS):
        group = list(dict.fromkeys(queued[start : start + MAX_MENTIONS]))
        try:
            message = await send_spec(channel, welcome_message(app.ui, channel_mentions(app), group))
        except MissingContent as exc:
            log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="verification.welcome")
            return
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.WARNING, "welcome_mention_failed", error=type(exc).__name__)
            continue
        await app.scheduler.schedule(
            TASK_CLEANUP,
            run_at=app.clock.now() + seconds,
            payload={"channel_id": channel.id, "message_id": message.id},
            dedupe_key=cleanup_key(message.id),
        )


async def cleanup_task(app: App, task: Task) -> None:
    """``verification.welcome_cleanup``: delete the welcome mention (already gone counts as done)."""
    channel_id = task.payload.get("channel_id")
    message_id = task.payload.get("message_id")
    if not isinstance(channel_id, int) or not isinstance(message_id, int):
        raise PermanentTaskError("payload has no channel_id/message_id")
    guild = app.guild
    channel = guild.get_channel(channel_id) if guild is not None else None
    if not isinstance(channel, discord.TextChannel):
        return  # the channel is gone, and the message with it
    try:
        await channel.get_partial_message(message_id).delete()
    except discord.NotFound:
        return
