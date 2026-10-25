"""Gateway listeners (ticket activity, manually deleted channels) and restart recovery."""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content import MissingContent
from shopbot.features.tickets import repo, service, views
from shopbot.features.tickets.models import CLOSED, OPEN, TRANSCRIPT_PENDING, Ticket
from shopbot.features.tickets.runtime import ACTIVITY_THROTTLE_SECONDS, runtime
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_spec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

CREATING_STALE_SECONDS = 300


async def on_message(app: App, message: discord.Message) -> None:
    """Customer/staff activity in an open ticket pushes its automatic close back (throttled)."""
    if getattr(message.author, "bot", False):
        return
    channel_id = getattr(message.channel, "id", None)
    if channel_id is None:
        return
    if not runtime(app).should_touch(channel_id, app.clock.now()):
        return
    await service.record_activity(app, channel_id, min_interval=ACTIVITY_THROTTLE_SECONDS)


async def on_channel_delete(app: App, channel: Any) -> None:
    channel_id = getattr(channel, "id", None)
    if channel_id is not None:
        await service.channel_deleted(app, int(channel_id))


async def _adopt_or_send_welcome(app: App, channel: discord.TextChannel, ticket: Ticket) -> int | None:
    """For a support ticket interrupted during creation: reuse the bot's welcome message if it was sent,
    otherwise send it now. Returns the control message id (None if it cannot be sent)."""
    bot_id = app.bot.user.id if app.bot is not None and app.bot.user is not None else None
    try:
        async for message in channel.history(limit=20, oldest_first=True):
            if bot_id is not None and message.author.id == bot_id:
                return int(message.id)
    except discord.HTTPException:
        return None
    category = app.settings.tickets.category(ticket.category_id)
    member = channel.guild.get_member(ticket.user_id)
    if member is None:
        try:
            member = await channel.guild.fetch_member(ticket.user_id)
        except discord.HTTPException:
            member = None
    if category is None or member is None:
        return None
    try:
        specs = views.welcome_messages(
            app.ui,
            app.settings.tickets,
            category,
            ticket_id=ticket.id,
            number=ticket.number,
            user_id=ticket.user_id,
            user_name=member.display_name,
            answers=ticket.form_answers,
        )
        control = await send_spec(channel, specs[0])
        for spec in specs[1:]:
            await send_spec(channel, spec)
    except (MissingContent, RenderError, discord.HTTPException) as exc:
        log_event(
            log, logging.WARNING, "tickets_recovery_welcome_failed", ticket_id=ticket.id, error=type(exc).__name__
        )
        return None
    return int(control.id)


async def on_ready(app: App) -> None:
    """Restart recovery: finish or fail interrupted creations, mark tickets whose channel vanished while
    the bot was offline as deleted, and make sure the scheduled tasks of open/closed tickets exist."""
    guild = app.guild
    if guild is None:
        log_event(log, logging.WARNING, "tickets_recovery_skipped", reason="guild_unavailable")
        return
    now = app.clock.now()
    guild_id = app.env.guild_id
    recovered = {"opened": 0, "failed": 0, "deleted": 0}

    for ticket in await app.db.run(repo.stale_creating, guild_id, now - CREATING_STALE_SECONDS):
        channel = guild.get_channel(ticket.channel_id) if ticket.channel_id else None
        if isinstance(channel, discord.TextChannel):
            control_id = await _adopt_or_send_welcome(app, channel, ticket) if ticket.is_support else None
            opened = await app.db.transaction(repo.activate, ticket.id, control_id, app.clock.now())
            if opened is not None:
                recovered["opened"] += 1
                await app.audit.record(
                    "tickets.recovered",
                    actor_id=None,
                    target_type="ticket",
                    target_id=ticket.id,
                    details={"number": ticket.number, "result": "open"},
                )
                await service.schedule_inactivity(app, opened)
        else:
            failed = await app.db.transaction(repo.mark_failed, ticket.id, app.clock.now())
            if failed is not None:
                recovered["failed"] += 1
                await app.audit.record(
                    "tickets.recovered",
                    actor_id=None,
                    target_type="ticket",
                    target_id=ticket.id,
                    details={"number": ticket.number, "result": "failed"},
                )

    t = app.settings.tickets
    tickets_enabled = app.settings.features.tickets
    for ticket in await app.db.run(repo.by_statuses, guild_id, (OPEN, CLOSED)):
        if ticket.channel_id is None:
            continue
        if guild.get_channel(ticket.channel_id) is None:
            if await service.channel_deleted(app, ticket.channel_id) is not None:
                recovered["deleted"] += 1
            continue
        if ticket.status == OPEN and ticket.is_support and tickets_enabled:
            await service.schedule_inactivity(app, ticket)
        if ticket.status == CLOSED:
            if ticket.transcript_status == TRANSCRIPT_PENDING:
                await app.scheduler.schedule(
                    service.TASK_TRANSCRIPT,
                    run_at=now,
                    payload={"ticket_id": ticket.id},
                    dedupe_key=service.dedupe_transcript(ticket.id),
                )
            if ticket.is_support and t.delete_after_close_minutes > 0:
                await app.scheduler.schedule(
                    service.TASK_DELETE,
                    run_at=(ticket.closed_at or now) + t.delete_after_close_minutes * 60,
                    payload={"ticket_id": ticket.id},
                    dedupe_key=service.dedupe_delete(ticket.id),
                )
    if any(recovered.values()):
        log_event(log, logging.WARNING, "tickets_recovered", **recovered)


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.bot.add_listener("message", partial(on_message, app))
    app.bot.add_listener("guild_channel_delete", partial(on_channel_delete, app))
