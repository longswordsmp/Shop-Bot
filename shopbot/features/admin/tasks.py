"""Scheduled maintenance: one self-rescheduling daily task (dedupe key ``admin.maintenance``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.core.scheduler import RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.admin import service

if TYPE_CHECKING:
    from shopbot.context import App


async def maintenance_task(app: App, task: Task) -> None:
    """Purge old finished tasks and expired audit events, then move this same task row to tomorrow.

    Idempotent: purging twice deletes nothing more. ``on_ready`` re-ensures the task should the row ever
    be cancelled, so the cycle never stops.
    """
    await service.run_maintenance(app)
    raise RescheduleTask(app.clock.now() + service.MAINTENANCE_INTERVAL_SECONDS)


def register_tasks(app: App) -> None:
    app.scheduler.register(service.MAINTENANCE_KIND, maintenance_task)
