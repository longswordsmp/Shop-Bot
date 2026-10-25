"""Scheduled maintenance: one self-rescheduling daily task (dedupe key ``admin.maintenance``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.db.repos.tasks import Task
from shopbot.features.admin import repo as admin_repo
from shopbot.features.admin import service

if TYPE_CHECKING:
    from shopbot.context import App


async def maintenance_task(app: App, task: Task) -> None:
    """Purge old finished tasks and expired audit events, then move this same task row to tomorrow.

    Idempotent: purging twice deletes nothing more. If the row could not be rescheduled (it is no longer
    running, e.g. it was started by hand), a fresh one is ensured instead so the cycle never stops.
    """
    await service.run_maintenance(app)
    now = app.clock.now()
    rescheduled = await app.db.transaction(
        admin_repo.reschedule_running, task.id, run_at=now + service.MAINTENANCE_INTERVAL_SECONDS, now=now
    )
    if not rescheduled:
        await service.ensure_maintenance_scheduled(app)


def register_tasks(app: App) -> None:
    app.scheduler.register(service.MAINTENANCE_KIND, maintenance_task)
