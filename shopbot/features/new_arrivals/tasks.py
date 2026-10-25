"""Scheduled task of the new-arrivals feature.

``new_arrivals.announce`` (payload ``product_id``, ``event``, ``version``; dedupe
``new_arrivals.announce:<product_id>:<event>:<version>``): posts one automatic product update. Idempotent - the
announcement row is unique per product, event and version, so running the task again never posts twice. Skipped
(and finished) when the feature is not ready, the event type was switched off, or the product no longer qualifies;
retried with the scheduler's backoff while the channel is missing or Discord refuses the post.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.repos.tasks import Task
from shopbot.features.new_arrivals import service, views

if TYPE_CHECKING:
    from shopbot.context import App


def _positive(task: Task, name: str) -> int:
    value = task.payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PermanentTaskError(f"invalid payload: {name} must be a positive integer")
    return value


def _event(task: Task) -> views.Event:
    value = task.payload.get("event")
    for event in views.EVENTS:
        if value == event:
            return event
    raise PermanentTaskError(f"invalid payload: event must be one of {list(views.EVENTS)}")


async def run_announce_task(app: App, task: Task) -> None:
    await service.announce(
        app,
        product_id=_positive(task, "product_id"),
        event=_event(task),
        version=_positive(task, "version"),
    )
