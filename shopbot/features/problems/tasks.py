"""Scheduled tasks of the Report a Problem workflow. Both are scheduled in the same transaction as the change they
follow up on, re-load the report and re-check its state, so running them twice (retry, restart) changes nothing.

- ``problems.finish`` (scheduled when a report is reserved, due after the ticket system's own recovery of interrupted
  creations): a creation that was interrupted is completed (report panel adopted or posted, staff alert, audit) or
  cleaned up (the ticket never opened). Normally cancelled by the creation itself.
- ``problems.after_decision`` (scheduled with the decision): the decision's follow-ups (report panel, customer
  notification, the order's staff message, audit). Normally cancelled right after they ran in-process.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shopbot.core.scheduler import PermanentTaskError, RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.problems import repo, service
from shopbot.features.tickets import service as tickets_service
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def _report_id(task: Task) -> int:
    try:
        return int(task.payload["report_id"])
    except (KeyError, TypeError, ValueError):
        raise PermanentTaskError("payload has no report_id") from None


async def finish_task(app: App, task: Task) -> None:
    report_id = _report_id(task)
    result = await service.finish_creation(app, report_id)
    if result == "wait":  # the ticket system has not finished or failed the ticket yet
        raise RescheduleTask(app.clock.now() + tickets_service.CREATING_STALE_SECONDS)
    if result == "discarded":
        log_event(log, logging.WARNING, "problems_creation_discarded", report_id=report_id)


async def decision_task(app: App, task: Task) -> None:
    followups = await service.run_decision_followups(app, _report_id(task))
    if not followups.complete:
        raise RuntimeError("decision follow-ups incomplete; retrying")


def register(app: App) -> None:
    app.scheduler.register(repo.TASK_FINISH, finish_task)
    app.scheduler.register(repo.TASK_AFTER_DECISION, decision_task)
