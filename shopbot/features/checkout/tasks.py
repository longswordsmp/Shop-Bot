"""Scheduled tasks, restart recovery, and gateway listeners of the checkout feature.

Every task handler re-loads the session/order and re-checks its state and the CURRENT settings before acting, so
running a task twice (retry, restart) or after a settings change never applies anything twice or wrongly.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content import MissingContent
from shopbot.core.scheduler import PermanentTaskError, RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import KIND_ORDER
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_spec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

# An order channel younger than this may still be in the middle of being set up by a start click.
ORPHAN_GRACE_SECONDS = 300


def _payload_id(task: Task, name: str) -> int:
    try:
        return int(task.payload[name])
    except (KeyError, TypeError, ValueError):
        raise PermanentTaskError(f"payload has no {name}") from None


async def session_expire_task(app: App, task: Task) -> None:
    """``checkout.session_expire``: an active session idle for ``session_expiry_minutes`` expires."""
    session = await app.db.run(repo.get_session, _payload_id(task, "session_id"))
    if session is None or not session.active:
        return
    minutes = app.settings.checkout.session_expiry_minutes
    if minutes <= 0:
        return  # automatic expiry was switched off meanwhile
    due = session.updated_at + minutes * 60
    if app.clock.now() < due:
        raise RescheduleTask(due)  # the customer was active since the task was scheduled
    await service.expire_session(app, session.id, reason="inactive")


async def payment_reminder_task(app: App, task: Task) -> None:
    """``checkout.payment_reminder``: one reminder while an order is still waiting for payment."""
    order = await service.get_order(app, _payload_id(task, "order_id"))
    if order is None or order.status != m.PENDING:
        return
    minutes = app.settings.checkout.payment_reminder_minutes
    if minutes <= 0:
        return
    due = order.created_at + minutes * 60
    if app.clock.now() < due:
        raise RescheduleTask(due)
    if not app.ready(k.FEATURE):
        log_event(log, logging.WARNING, "checkout_reminder_skipped", order_id=order.id, reason="not_ready")
        return
    view = await service.order_view(app, order.id)
    if view is None:
        return
    instructions = await service.payment_instructions(app, order)
    await service.notify_customer(
        app, view, k.NOTIFY_PAYMENT_REMINDER, lambda: views.reminder_notification(app.ui, view, instructions)
    )


async def payment_expire_task(app: App, task: Task) -> None:
    """``checkout.payment_expire``: an order still unpaid after ``payment_expiry_minutes`` expires."""
    order = await service.get_order(app, _payload_id(task, "order_id"))
    if order is None or order.status != m.PENDING:
        return
    minutes = app.settings.checkout.payment_expiry_minutes
    if minutes <= 0:
        return
    due = order.created_at + minutes * 60
    if app.clock.now() < due:
        raise RescheduleTask(due)
    await service.change_status(app, order.id, m.EXPIRED, actor_id=None)


# -- restart recovery ------------------------------------------------------------------------------------------


async def _staff_message_exists(app: App, order: m.Order) -> bool:
    if order.staff_message_id is None or order.staff_channel_id is None or app.bot is None:
        return False
    channel = app.bot.get_channel(order.staff_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        await channel.fetch_message(order.staff_message_id)
    except discord.NotFound:
        return False
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "checkout_staff_message_check_failed", order_id=order.id, status=exc.status)
        return True  # unknown: never post a duplicate on a transient error
    return True


async def _resend_workflow(app: App, session: m.Session, channel: discord.TextChannel) -> bool:
    """The process stopped after creating the channel but before the workflow message was sent."""
    try:
        message = await send_spec(channel, await service.workflow_spec(app, session.id))
    except (MissingContent, RenderError, discord.HTTPException) as exc:
        log_event(
            log, logging.WARNING, "checkout_recovery_workflow_failed", session_id=session.id, error=type(exc).__name__
        )
        return False
    await app.db.transaction(repo.set_session_message, session.id, message.id, app.clock.now())
    return True


async def _finish_activation(app: App, session_id: int) -> None:
    """The process stopped before the order channel's ticket was marked open: open it now."""
    session = await app.db.run(repo.get_session, session_id)
    if session is None or session.message_id is None:
        return
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is not None and ticket.status == "creating":
        try:
            await tickets_service.activate_ticket(app, ticket.id, control_message_id=session.message_id)
        except tickets_service.TicketStateError:
            return


async def _close_orphan(app: App, ticket_id: int) -> bool:
    """An order channel without a session can never be used: remove it (creating) or close it (open)."""
    ticket = await tickets_service.get_ticket(app, ticket_id)
    if ticket is None:
        return False
    if ticket.status == "creating":
        guild = app.guild
        channel = guild.get_channel(ticket.channel_id) if guild is not None and ticket.channel_id else None
        await tickets_service.abort_ticket(
            app,
            ticket_id,
            reason="checkout_orphan",
            channel=channel if isinstance(channel, discord.TextChannel) else None,
        )
        return True
    return await tickets_service.close_ticket(app, ticket_id, actor_id=None, reason="checkout_orphan") is not None


async def on_ready(app: App) -> None:
    """Restart recovery: expire sessions whose order channel is gone, finish interrupted session starts, re-post
    missing staff order controls, and make sure reminder/expiry tasks exist for unpaid orders (deduplicated).
    Nothing that exists is re-rendered or removed."""
    guild = app.guild
    if guild is None:
        log_event(log, logging.WARNING, "checkout_recovery_skipped", reason="guild_unavailable")
        return
    counts = {"sessions_expired": 0, "workflows_sent": 0, "staff_reposted": 0, "orphans_closed": 0}
    for session in await app.db.run(repo.active_sessions, app.env.guild_id):
        channel = await service.session_channel(app, session)
        if channel is None:
            if await service.expire_session(app, session.id, reason="channel_missing", render=False, close=False):
                counts["sessions_expired"] += 1
            continue
        if session.message_id is None and await _resend_workflow(app, session, channel):
            counts["workflows_sent"] += 1
        await _finish_activation(app, session.id)
        await service.schedule_session_expiry(app, session)
    stale = app.clock.now() - ORPHAN_GRACE_SECONDS
    for ticket_id in await app.db.run(repo.orphan_order_tickets, app.env.guild_id, stale):
        if await _close_orphan(app, ticket_id):
            counts["orphans_closed"] += 1
    for order in await app.db.run(repo.orders_with_status, app.env.guild_id, m.OPEN_STATUSES):
        if not await _staff_message_exists(app, order) and await service.post_staff_message(app, order.id):
            counts["staff_reposted"] += 1
        await service.schedule_payment_tasks(app, order)
    if any(counts.values()):
        log_event(log, logging.WARNING, "checkout_recovered", **counts)


# -- listeners -----------------------------------------------------------------------------------------------------


async def on_channel_delete(app: App, channel: Any) -> None:
    """An order channel was deleted: an unfinished session in it expires (no stock is held before submission).
    Submitted orders keep their staff controls; staff handle them from the staff channel."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return
    ticket = await tickets_service.get_ticket_by_channel(app, int(channel_id))
    if ticket is None or ticket.kind != KIND_ORDER:
        return
    session = await app.db.run(repo.active_session_for_ticket, ticket.id)
    if session is not None:
        await service.expire_session(app, session.id, reason="channel_deleted", render=False, close=False)


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.scheduler.register(service.TASK_SESSION_EXPIRE, session_expire_task)
    app.scheduler.register(service.TASK_PAYMENT_REMINDER, payment_reminder_task)
    app.scheduler.register(service.TASK_PAYMENT_EXPIRE, payment_expire_task)
    app.bot.add_listener("guild_channel_delete", partial(on_channel_delete, app))
