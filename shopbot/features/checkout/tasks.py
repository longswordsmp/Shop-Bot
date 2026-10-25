"""Scheduled tasks, restart recovery, and gateway listeners of the checkout feature.

Every task handler re-loads the session/order and re-checks its state and the CURRENT settings before acting, so
running a task twice (retry, restart) or after a settings change never applies anything twice or wrongly.

``checkout.session_warning`` warns the customer in the order ticket before an idle unfinished order expires (once
per idle period; scheduled with the session, re-armed by restart recovery).

Follow-up tasks (scheduled in the same transaction as the change they follow, see ``repo``):
- ``checkout.after_submit``: the staff controls (adopting controls that were posted but never recorded), the
  customer's form answers for staff (only with ``operations.answers_in_orders_channel``), the payment
  provider's start;
- ``checkout.after_transition``: the order views, the status notification, closing the channel of a final order,
  the review request of a fulfilled order;
- ``checkout.ticket_check``: closes an order channel that can no longer be used (its session ended without an
  order, its order is final, or it never got a session - after ``ORPHAN_GRACE_SECONDS``);
- ``checkout.activity_followup``: the live staff record and the customer notification after a staff activity.
A follow-up that could not complete every step raises, so the scheduler retries it with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from functools import partial
from typing import TYPE_CHECKING, Any

import discord

from shopbot.core.scheduler import PermanentTaskError, RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import handling_repo, repo, service, views
from shopbot.features.checkout import models as m
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import CREATING, KIND_ORDER, OPEN
from shopbot.logging_setup import log_event

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


async def session_warning_task(app: App, task: Task) -> None:
    """``checkout.session_warning``: warn the customer in the order ticket ``session_expiry_warning_minutes`` before
    an idle unfinished order expires. The task follows the session: it moves with every activity and, after a
    warning, waits until the expiry time to warn again for a later idle period (one warning per idle period). It
    ends with the session."""
    session = await app.db.run(repo.get_session, _payload_id(task, "session_id"))
    if session is None or not session.active:
        return
    c = app.settings.checkout
    if not k.session_warning_enabled(app.settings):
        return  # switched off meanwhile (restart recovery re-arms it when it is switched on again)
    expires = session.updated_at + c.session_expiry_minutes * 60
    due = expires - c.session_expiry_warning_minutes * 60
    now = app.clock.now()
    if now < due:
        raise RescheduleTask(due)  # the customer was active since the task was scheduled
    if now >= expires:
        return  # the expiry task ends the session now: there is nothing left to warn about
    if app.ready(k.FEATURE):
        delivery = await service.warn_session_expiry(app, session)
        log_event(log, logging.INFO, "checkout_session_warning", session_id=session.id, delivery=delivery)
    else:
        log_event(log, logging.WARNING, "checkout_session_warning_skipped", session_id=session.id, reason="not_ready")
    raise RescheduleTask(expires)  # look again at the expiry time: activity meanwhile starts a new idle period


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
    if view is None or view.order.status != m.PENDING:
        return
    instructions = await service.payment_instructions(app, view.order)
    # Re-read right before sending: a payment confirmed meanwhile is never followed by a reminder to pay.
    current = await service.get_order(app, order.id)
    if current is None or current.status != m.PENDING:
        return
    await service.notify_customer(
        app,
        view,
        k.NOTIFY_PAYMENT_REMINDER,
        lambda: views.reminder_notification(app.ui, app.settings, view, instructions),
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


async def after_submit_task(app: App, task: Task) -> None:
    """``checkout.after_submit``: the Discord side of a new order that did not complete right after the commit."""
    order_id = _payload_id(task, "order_id")
    if await service.get_order(app, order_id) is None:
        return
    if not await service.run_submit_followups(app, order_id, adopt=True):
        raise service.FollowupIncomplete(f"order {order_id}: submission follow-up incomplete")


async def after_transition_task(app: App, task: Task) -> None:
    """``checkout.after_transition``: the follow-up of a status change that did not complete right after the
    commit (every step re-reads the order and is safe to repeat)."""
    order_id = _payload_id(task, "order_id")
    status = task.payload.get("status")
    if status not in m.ORDER_STATUSES:
        raise PermanentTaskError("payload has no valid status")
    if await service.get_order(app, order_id) is None:
        return
    raw_actor = task.payload.get("actor_id")
    actor_id = int(raw_actor) if isinstance(raw_actor, int) else None
    released = bool(task.payload.get("released"))
    if not await service.run_transition_followups(app, order_id, status, actor_id=actor_id, released=released):
        raise service.FollowupIncomplete(f"order {order_id}: follow-up of {status} incomplete")


async def activity_followup_task(app: App, task: Task) -> None:
    """``checkout.activity_followup``: the follow-up of a staff activity (claim, note, correction request, tracking
    details) that did not complete right after the commit: refresh the live record, send the customer notification
    (deduplicated)."""
    order_id = _payload_id(task, "order_id")
    activity_id = _payload_id(task, "activity_id")
    if await service.get_order(app, order_id) is None:
        return
    if not await service.run_activity_followup(app, order_id, activity_id):
        raise service.FollowupIncomplete(f"order {order_id}: follow-up of activity {activity_id} incomplete")


async def ticket_check_task(app: App, task: Task) -> None:
    """``checkout.ticket_check``: close the order channel when it can no longer be used."""
    ticket_id = _payload_id(task, "ticket_id")
    ticket = await tickets_service.get_ticket(app, ticket_id)
    if ticket is None or ticket.kind != KIND_ORDER or ticket.status not in (CREATING, OPEN):
        return
    reason = await app.db.run(repo.ticket_close_reason, ticket_id)
    if reason == repo.ORPHAN and app.clock.now() < ticket.created_at + ORPHAN_GRACE_SECONDS:
        raise RescheduleTask(ticket.created_at + ORPHAN_GRACE_SECONDS)
    await close_unused_ticket(app, ticket_id)


async def schedule_ticket_check(app: App, ticket_id: int, run_at: int) -> None:
    await app.scheduler.schedule(
        service.TASK_TICKET_CHECK,
        run_at=run_at,
        payload={"ticket_id": ticket_id},
        dedupe_key=repo.ticket_check_key(ticket_id),
        replace=True,
    )


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


async def close_unused_ticket(app: App, ticket_id: int) -> bool:
    """An order channel that can no longer be used (no active session and no open order, or no session at all):
    remove it while it is still being created, close it when it is open. True when something was done."""
    ticket = await tickets_service.get_ticket(app, ticket_id)
    if ticket is None or ticket.kind != KIND_ORDER or ticket.status not in (CREATING, OPEN):
        return False
    reason = await app.db.run(repo.ticket_close_reason, ticket_id)
    if reason is None:
        return False
    if ticket.status == CREATING:
        guild = app.guild
        channel = guild.get_channel(ticket.channel_id) if guild is not None and ticket.channel_id else None
        await tickets_service.abort_ticket(
            app,
            ticket_id,
            reason=reason if reason == repo.ORPHAN else f"checkout_{reason}",
            channel=channel if isinstance(channel, discord.TextChannel) else None,
        )
        return True
    return await tickets_service.close_ticket(app, ticket_id, actor_id=None, reason=reason) is not None


async def _recover_session(app: App, session: m.Session, counts: dict[str, int]) -> None:
    channel = await service.session_channel(app, session)
    if channel is None:
        # Only a channel that is certainly gone ends the session (a lookup failure keeps it for the next start).
        if await service.retire_vanished_session(app, session):
            counts["sessions_expired"] += 1
        return
    if session.message_id is None and await service.resend_workflow(app, session, channel):
        counts["workflows_sent"] += 1
    await service.finish_activation(app, session.id)
    await service.schedule_session_expiry(app, session)
    await service.schedule_session_warning(app, session)


async def _recover_order(app: App, order: m.Order, counts: dict[str, int]) -> None:
    if not await _staff_message_exists(app, order) and await service.post_staff_message(app, order.id, adopt=True):
        counts["staff_reposted"] += 1
    await service.post_staff_details(app, order.id)  # deduplicated: only parts that were never delivered
    await service.schedule_payment_tasks(app, order)


async def _isolated(what: str, step: Awaitable[object]) -> None:
    try:
        await step
    except Exception as exc:  # one broken record never stops the recovery of the others
        log_event(log, logging.ERROR, "checkout_recovery_step_failed", step=what, error=type(exc).__name__)


async def on_ready(app: App) -> None:
    """Restart recovery: expire sessions whose order channel is certainly gone, finish interrupted session starts,
    close order channels that can no longer be used (an ended session without an order, a final order, or no
    session at all; younger ones get a ``checkout.ticket_check`` task when their grace time ends), re-post missing
    staff controls (adopting controls posted but never recorded) and form answers, and make sure the reminder and
    expiry tasks of unpaid orders exist. Every step is deduplicated or conditional."""
    guild = app.guild
    if guild is None:
        log_event(log, logging.WARNING, "checkout_recovery_skipped", reason="guild_unavailable")
        return
    guild_id = app.env.guild_id
    counts = {"sessions_expired": 0, "workflows_sent": 0, "staff_reposted": 0, "channels_closed": 0, "checks": 0}
    for session in await app.db.run(repo.active_sessions, guild_id):
        await _isolated("session", _recover_session(app, session, counts))
    now = app.clock.now()
    for ticket_id, created_at in await app.db.run(repo.orphan_order_tickets, guild_id):
        if created_at + ORPHAN_GRACE_SECONDS <= now:
            if await close_unused_ticket(app, ticket_id):
                counts["channels_closed"] += 1
        else:
            await schedule_ticket_check(app, ticket_id, created_at + ORPHAN_GRACE_SECONDS)
            counts["checks"] += 1
    for ticket_id in await app.db.run(repo.unused_order_tickets, guild_id):
        if await close_unused_ticket(app, ticket_id):
            counts["channels_closed"] += 1
    for order in await app.db.run(repo.orders_with_status, guild_id, m.OPEN_STATUSES):
        await _isolated("order", _recover_order(app, order, counts))
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
    app.scheduler.register(service.TASK_SESSION_WARNING, session_warning_task)
    app.scheduler.register(service.TASK_PAYMENT_REMINDER, payment_reminder_task)
    app.scheduler.register(service.TASK_PAYMENT_EXPIRE, payment_expire_task)
    app.scheduler.register(service.TASK_AFTER_SUBMIT, after_submit_task)
    app.scheduler.register(service.TASK_AFTER_TRANSITION, after_transition_task)
    app.scheduler.register(service.TASK_TICKET_CHECK, ticket_check_task)
    app.scheduler.register(handling_repo.TASK_ACTIVITY_FOLLOWUP, activity_followup_task)
    app.bot.add_listener("guild_channel_delete", partial(on_channel_delete, app))
