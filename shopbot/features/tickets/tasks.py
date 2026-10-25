"""Scheduled-task handlers of the ticket system. Each handler re-loads the ticket and re-checks its
current state before acting, so running a task twice (retry, restart) never applies anything twice."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.core.scheduler import PermanentTaskError, RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import repo, service, views
from shopbot.features.tickets.models import CLOSE_REASON_INACTIVE, CLOSED, TRANSCRIPT_FAILED, TRANSCRIPT_PENDING, Ticket
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def _ticket_id(task: Task) -> int:
    try:
        return int(task.payload["ticket_id"])
    except (KeyError, TypeError, ValueError):
        raise PermanentTaskError("payload has no ticket_id") from None


def _rearm(run_at: int) -> RescheduleTask:
    """The task ran early (activity happened since it was scheduled): run it again at ``run_at``."""
    return RescheduleTask(run_at)


async def transcript_task(app: App, task: Task) -> None:
    ticket_id = _ticket_id(task)
    ticket = await service.get_ticket(app, ticket_id)
    if ticket is None or ticket.transcript_status != TRANSCRIPT_PENDING:
        return
    if await service.generate_transcript(app, ticket_id):
        return
    current = await service.get_ticket(app, ticket_id)
    if current is None or current.transcript_status != TRANSCRIPT_PENDING:
        return  # resolved meanwhile, or marked failed because the channel is gone
    if task.attempts >= task.max_attempts:
        await app.db.transaction(
            repo.set_transcript,
            ticket_id,
            status=TRANSCRIPT_FAILED,
            now=app.clock.now(),
            only_if=(TRANSCRIPT_PENDING,),
        )
        await app.audit.record(
            "tickets.transcript_failed",
            actor_id=None,
            target_type="ticket",
            target_id=ticket_id,
            details={"number": current.number, "attempts": task.attempts},
        )
        raise PermanentTaskError("transcript could not be generated")
    raise RuntimeError("transcript could not be generated; retrying")


async def delete_task(app: App, task: Task) -> None:
    """Delete a closed ticket's channel. Waits (retries) while its transcript cannot be produced."""
    ticket_id = _ticket_id(task)
    ticket = await service.get_ticket(app, ticket_id)
    if ticket is None or ticket.status != CLOSED:
        return  # reopened, already deleted, or gone
    try:
        await service.delete_ticket(app, ticket_id, actor_id=None)
    except service.TicketStateError:
        return
    except service.TranscriptUnavailable:
        log_event(
            log, logging.WARNING, "tickets_delete_waiting_for_transcript", ticket_id=ticket_id, attempt=task.attempts
        )
        raise RuntimeError("transcript outstanding; deletion postponed") from None


def _open_support(ticket: Ticket | None) -> Ticket | None:
    return ticket if ticket is not None and ticket.is_support and ticket.is_open else None


async def auto_close_task(app: App, task: Task) -> None:
    ticket = _open_support(await service.get_ticket(app, _ticket_id(task)))
    if ticket is None or not app.settings.features.tickets or not k.auto_close_enabled(app.settings):
        return  # nothing to do: closed meanwhile, or automatic closing / the feature was switched off
    due = ticket.last_activity_at + app.settings.tickets.auto_close_inactive_minutes * 60
    if app.clock.now() < due:
        raise _rearm(due)
    if not app.ready(k.FEATURE):
        raise RuntimeError("tickets feature is not ready; auto-close postponed")
    await service.close_ticket(app, ticket.id, actor_id=None, reason=CLOSE_REASON_INACTIVE)


async def auto_close_warning_task(app: App, task: Task) -> None:
    ticket = _open_support(await service.get_ticket(app, _ticket_id(task)))
    if ticket is None or ticket.channel_id is None or not k.auto_close_warning_enabled(app.settings):
        return
    t = app.settings.tickets
    due = ticket.last_activity_at + (t.auto_close_inactive_minutes - t.auto_close_warning_minutes) * 60
    now = app.clock.now()
    if now < due:
        raise _rearm(due)
    if now >= ticket.last_activity_at + t.auto_close_inactive_minutes * 60 or not app.ready(k.FEATURE):
        return  # too late for a warning (the close task handles it) or the feature is not available
    try:
        spec = views.auto_close_warning(app.ui, minutes=t.auto_close_warning_minutes, user_id=ticket.user_id)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.auto_close_warning")
        return
    await app.notifier.notify_channel(
        channel_id=ticket.channel_id,
        spec=spec,
        kind="tickets.auto_close_warning",
        dedupe_key=f"tickets.autowarn:{ticket.id}:{ticket.last_activity_at}",
    )


def register(app: App) -> None:
    app.scheduler.register(service.TASK_TRANSCRIPT, transcript_task)
    app.scheduler.register(service.TASK_DELETE, delete_task)
    app.scheduler.register(service.TASK_AUTO_CLOSE, auto_close_task)
    app.scheduler.register(service.TASK_AUTO_CLOSE_WARN, auto_close_warning_task)
