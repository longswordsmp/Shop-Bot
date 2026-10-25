"""Staff reminders: unclaimed, unpaid and unfulfilled orders, and support tickets waiting for a staff reply.

One durable, self-rescheduling task (``operations.reminders``, dedupe key ``operations.reminders``) sweeps the open
records every ``SWEEP_SECONDS`` and posts a reminder in ``operations.staff_channel`` for each record that waited
longer than its threshold (``operations.*_minutes``; 0 = off), and again every ``operations.reminder_repeat_minutes``
while it still waits (0 = once). What was sent is recorded per waiting period (``repo.mark_reminded``) and every
message has a dedupe key, so a restart or a repeated sweep never sends a reminder twice; settings changes apply at
the next sweep. A sweep posts at most ``MAX_PER_SWEEP`` reminders (the rest follow in the next sweep).

Ticket replies are tracked from guild messages (``on_message``): a message by the ticket's customer (or any member
who is not staff) starts a waiting period, a message by staff ends it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.scheduler import RescheduleTask
from shopbot.db.repos import notifications as notif_repo
from shopbot.db.repos.tasks import Task
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import alerts, repo, views
from shopbot.features.operations import content_keys as k
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import KIND_SUPPORT, OPEN
from shopbot.logging_setup import log_event
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_REMINDERS = "operations.reminders"
SWEEP_KEY = "operations.reminders"
# How often open records are checked while a reminder is enabled / while every reminder is off (technical).
SWEEP_SECONDS = 60
IDLE_SWEEP_SECONDS = 900
MAX_PER_SWEEP = 10
CHECKOUT_FEATURE = "checkout"
TICKETS_FEATURE = "tickets"
_ORDER_STATUSES = {
    repo.UNCLAIMED: checkout_models.OPEN_STATUSES,
    repo.UNPAID: (checkout_models.PENDING,),
    repo.UNFULFILLED: (checkout_models.PAID, checkout_models.PROCESSING),
}


def thresholds(app: App) -> repo.Thresholds:
    o = app.settings.operations
    return repo.Thresholds(
        unclaimed=o.unclaimed_order_minutes * 60,
        unpaid=o.unpaid_order_minutes * 60,
        unfulfilled=o.unfulfilled_order_minutes * 60,
        unanswered=o.unanswered_ticket_minutes * 60,
        repeat=o.reminder_repeat_minutes * 60,
    )


def _minutes(app: App, kind: str) -> int:
    o = app.settings.operations
    return {
        repo.UNCLAIMED: o.unclaimed_order_minutes,
        repo.UNPAID: o.unpaid_order_minutes,
        repo.UNFULFILLED: o.unfulfilled_order_minutes,
        repo.UNANSWERED: o.unanswered_ticket_minutes,
    }[kind]


def active_kinds(app: App) -> tuple[str, ...]:
    """Reminder kinds that are switched on and whose records' feature is ready."""
    kinds: list[str] = []
    if app.ready(CHECKOUT_FEATURE):
        kinds += [kind for kind in repo.ORDER_REMINDERS if _minutes(app, kind) > 0]
    if app.ready(TICKETS_FEATURE) and _minutes(app, repo.UNANSWERED) > 0:
        kinds.append(repo.UNANSWERED)
    return tuple(kinds)


async def schedule_sweep(app: App, *, delay: int = SWEEP_SECONDS) -> None:
    await app.scheduler.schedule(
        TASK_REMINDERS, run_at=app.clock.now() + delay, payload={}, dedupe_key=SWEEP_KEY, replace=True
    )


async def _reminder_spec(app: App, waiting: repo.Waiting) -> MessageSpec | None:
    """The reminder message, or None when the record is gone (or no longer waiting)."""
    values: dict[str, str | int] = {
        "waiting_since": f"<t:{waiting.since}:R>",
        "minutes": _minutes(app, waiting.kind),
    }
    if waiting.kind == repo.UNANSWERED:
        ticket = await tickets_service.get_ticket(app, waiting.target_id)
        if ticket is None or ticket.status != OPEN or ticket.kind != KIND_SUPPORT:
            return None
        values |= {
            "ticket_number": tickets_service.format_number(app, ticket.number),
            "customer_mention": f"<@{ticket.user_id}>",
            "ticket_channel": f"<#{ticket.channel_id}>" if ticket.channel_id else "",
        }
        link = f"https://discord.com/channels/{app.env.guild_id}/{ticket.channel_id}" if ticket.channel_id else None
        return views.reminder(app.ui, waiting.kind, values, ping_role_ids=alerts.ping_role_ids(app), link_url=link)
    view = await checkout_service.order_view(app, waiting.target_id)
    if view is None or view.order.status not in _ORDER_STATUSES[waiting.kind]:
        return None  # handled since the sweep read it
    values |= alerts.order_values(app, view)
    link = alerts.message_url(app, view.order.staff_channel_id, view.order.staff_message_id)
    return views.reminder(app.ui, waiting.kind, values, ping_role_ids=alerts.ping_role_ids(app), link_url=link)


async def sweep(app: App) -> int:
    """Post the reminders that are due now. Returns how many were handled."""
    kinds = active_kinds(app)
    channel_id = alerts.staff_channel_id(app)
    if not kinds or not channel_id:
        return 0
    now = app.clock.now()
    due = await app.db.run(
        repo.due_reminders, app.env.guild_id, now=now, thresholds=thresholds(app), kinds=kinds, limit=MAX_PER_SWEEP
    )
    handled = 0
    for waiting in due:
        try:
            spec = await _reminder_spec(app, waiting)
        except MissingContent as exc:
            log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="operations.reminder")
            continue
        dedupe = f"operations.remind:{waiting.kind}:{waiting.target_id}:{waiting.since}:{waiting.round}"
        if spec is not None and not await app.db.run(notif_repo.already_sent, dedupe):
            posted = await app.notifier.notify_channel(
                channel_id=channel_id, spec=spec, kind=f"operations.reminder.{waiting.kind}", dedupe_key=dedupe
            )
            if posted is None:
                log_event(log, logging.WARNING, "operations_reminder_undelivered", kind=waiting.kind)
        # Recorded even when undelivered: the next attempt follows with the repeat interval (no message every sweep).
        await app.db.transaction(repo.mark_reminded, waiting, now)
        handled += 1
    if handled:
        log_event(log, logging.INFO, "operations_reminders_sent", count=handled)
    return handled


async def reminders_task(app: App, task: Task) -> None:
    """``operations.reminders``: one sweep, then the same task runs again (it never ends)."""
    del task
    enabled = app.settings.operations.reminders_enabled()
    try:
        if enabled and app.ready(k.FEATURE):
            await sweep(app)
    except Exception as exc:  # a failed sweep is retried at the next one, never lost
        log_event(log, logging.ERROR, "operations_reminder_sweep_failed", error=type(exc).__name__)
    raise RescheduleTask(app.clock.now() + (SWEEP_SECONDS if enabled else IDLE_SWEEP_SECONDS))


# -- ticket replies -----------------------------------------------------------------------------------------------


async def on_message(app: App, message: discord.Message) -> None:
    """Track whether a support ticket's customer waits for a staff reply (bots are ignored)."""
    author = message.author
    if getattr(author, "bot", False):
        return
    channel_id = getattr(message.channel, "id", None)
    if channel_id is None:
        return
    ticket = await tickets_service.get_ticket_by_channel(app, int(channel_id))
    if ticket is None or ticket.kind != KIND_SUPPORT or ticket.status != OPEN:
        return
    member = author if isinstance(author, discord.Member) else None
    now = app.clock.now()
    if tickets_service.is_ticket_staff(app, member, author.id, ticket):
        await app.db.transaction(repo.record_staff_reply, ticket.id, now=now)
    else:
        await app.db.transaction(repo.record_customer_message, ticket.id, opened_at=ticket.created_at, now=now)
