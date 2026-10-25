"""Gateway listeners (ticket activity, manually deleted channels) and restart recovery.

A channel is only treated as deleted when Discord confirms it (404 from ``fetch_channel``): a channel
missing from the cache, or a guild that is still unavailable after a restart, never deletes a ticket.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content import MissingContent
from shopbot.features.tickets import repo, service, views
from shopbot.features.tickets.models import CLOSED, CREATING, OPEN, TRANSCRIPT_PENDING, Ticket
from shopbot.features.tickets.runtime import ACTIVITY_THROTTLE_SECONDS, runtime
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_spec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

CREATING_STALE_SECONDS = service.CREATING_STALE_SECONDS

RESULT_OPENED = "opened"
RESULT_FAILED = "failed"


class RecoveryDeferred(Exception):
    """Discord could not answer (or the row changed meanwhile): decide about this creation later."""


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


def guild_ready(guild: discord.Guild | None) -> bool:
    """The guild is known and its channels are loaded (an unavailable guild has an empty cache)."""
    return guild is not None and not getattr(guild, "unavailable", False)


async def lookup_channel(app: App, guild: discord.Guild, channel_id: int) -> tuple[Any, bool]:
    """``(channel, confirmed_missing)``. A channel missing from the cache is looked up with the API; only a
    404 confirms it is gone. Any other error leaves it undecided: ``(None, False)``."""
    channel = guild.get_channel(channel_id)
    if channel is not None:
        return channel, False
    if app.bot is None:
        return None, False
    try:
        return await app.bot.fetch_channel(channel_id), False
    except discord.NotFound:
        return None, True
    except Exception as exc:
        log_event(log, logging.WARNING, "tickets_channel_check_failed", channel_id=channel_id, error=type(exc).__name__)
        return None, False


async def _adopt_or_send_welcome(app: App, channel: discord.TextChannel, ticket: Ticket) -> int | None:
    """For a support ticket interrupted during creation: reuse the bot's welcome message if it was sent,
    otherwise send it now. Returns the control message id, or None when the welcome cannot be sent (the
    category no longer exists, the customer left, or its text cannot be built). Raises
    ``RecoveryDeferred`` when Discord could not answer."""
    bot_id = app.bot.user.id if app.bot is not None and app.bot.user is not None else None
    try:
        async for message in channel.history(limit=20, oldest_first=True):
            if bot_id is not None and message.author.id == bot_id:
                return int(message.id)
    except discord.HTTPException as exc:
        raise RecoveryDeferred("history") from exc
    category = app.settings.tickets.category(ticket.category_id)
    member = channel.guild.get_member(ticket.user_id)
    if member is None:
        try:
            member = await channel.guild.fetch_member(ticket.user_id)
        except discord.NotFound:
            member = None
        except discord.HTTPException as exc:
            raise RecoveryDeferred("member") from exc
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
    except (MissingContent, RenderError) as exc:
        log_event(
            log, logging.WARNING, "tickets_recovery_welcome_failed", ticket_id=ticket.id, error=type(exc).__name__
        )
        return None
    except discord.HTTPException as exc:
        log_event(
            log, logging.WARNING, "tickets_recovery_welcome_failed", ticket_id=ticket.id, error=type(exc).__name__
        )
        raise RecoveryDeferred("welcome") from exc
    return int(control.id)


async def recover_creating(app: App, guild: discord.Guild, ticket: Ticket) -> str | None:
    """Finish or fail one interrupted creation ('creating' row): when its channel exists and the welcome was
    sent (or can be sent now) the ticket is opened, otherwise it is failed (a channel left behind is
    deleted). Returns ``RESULT_OPENED`` or ``RESULT_FAILED``, None when the ticket is no longer 'creating';
    raises ``RecoveryDeferred`` when nothing could be decided. Restart recovery and the durable task never
    work on the same ticket at the same time (no duplicate welcome)."""
    async with runtime(app).lifecycle_lock(ticket.id):
        current = await service.get_ticket(app, ticket.id)
        if current is None or current.status != CREATING:
            return None
        return await _recover_creating(app, guild, current)


async def _recover_creating(app: App, guild: discord.Guild, ticket: Ticket) -> str:
    channel: Any = None
    if ticket.channel_id is not None:
        channel, missing = await lookup_channel(app, guild, ticket.channel_id)
        if channel is None and not missing:
            raise RecoveryDeferred("channel")
    if isinstance(channel, discord.TextChannel):
        control_id = await _adopt_or_send_welcome(app, channel, ticket) if ticket.is_support else None
        if control_id is not None or not ticket.is_support:
            opened = await app.db.transaction(service.activate_txn, ticket.id, control_id, app.clock.now())
            if opened is None:
                raise RecoveryDeferred("changed")
            await app.audit.record(
                "tickets.recovered",
                actor_id=None,
                target_type="ticket",
                target_id=ticket.id,
                details={"number": ticket.number, "result": "open"},
            )
            await service.schedule_inactivity(app, opened)
            return RESULT_OPENED
    if isinstance(channel, discord.abc.GuildChannel):
        # A channel without a usable welcome (no controls): remove it rather than leave an orphan.
        runtime(app).deleting.add(ticket.id)
        try:
            await channel.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            log_event(log, logging.ERROR, "tickets_recovery_delete_failed", ticket_id=ticket.id, status=exc.status)
        finally:
            runtime(app).deleting.discard(ticket.id)
    failed = await app.db.transaction(service.fail_txn, ticket.id, app.clock.now())
    if failed is None:
        raise RecoveryDeferred("changed")
    await app.audit.record(
        "tickets.recovered",
        actor_id=None,
        target_type="ticket",
        target_id=ticket.id,
        details={"number": ticket.number, "result": "failed"},
    )
    return RESULT_FAILED


async def on_ready(app: App) -> None:
    """Restart recovery: finish or fail interrupted creations (and make sure every creation still in
    progress has its recovery task), mark tickets whose channel was deleted while the bot was offline as
    deleted, and make sure the scheduled tasks of open/closed tickets exist. Skipped while the guild is
    unavailable (its channel cache is empty then)."""
    guild = app.guild
    if guild is None or not guild_ready(guild):  # unavailable: its (empty) cache would look like deletions
        log_event(log, logging.WARNING, "tickets_recovery_skipped", reason="guild_unavailable")
        return
    now = app.clock.now()
    guild_id = app.env.guild_id
    recovered = {RESULT_OPENED: 0, RESULT_FAILED: 0, "deleted": 0, "deferred": 0}

    for ticket in await app.db.run(repo.by_statuses, guild_id, (CREATING,)):
        run_at = ticket.created_at + CREATING_STALE_SECONDS
        if run_at <= now:
            try:
                result = await recover_creating(app, guild, ticket)
                if result is not None:
                    recovered[result] += 1
                continue
            except RecoveryDeferred:
                recovered["deferred"] += 1
                run_at = now + CREATING_STALE_SECONDS
        await app.scheduler.schedule(
            service.TASK_RECOVER_CREATING,
            run_at=run_at,
            payload={"ticket_id": ticket.id},
            dedupe_key=service.dedupe_recover_creating(ticket.id),
            replace=True,
        )

    t = app.settings.tickets
    tickets_enabled = app.settings.features.tickets
    for ticket in await app.db.run(repo.by_statuses, guild_id, (OPEN, CLOSED)):
        if ticket.channel_id is None:
            continue
        channel, missing = await lookup_channel(app, guild, ticket.channel_id)
        if missing:
            if await service.channel_deleted(app, ticket.channel_id) is not None:
                recovered["deleted"] += 1
            continue
        if channel is None:
            continue  # Discord could not tell: change nothing now
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
