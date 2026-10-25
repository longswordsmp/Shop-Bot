"""Scheduled tasks and restart recovery of the reviews feature.

- ``reviews.prompt`` (payload ``order_id``, dedupe ``reviews.prompt:<order_id>``): sends the review request of a
  fulfilled order. Idempotent: the review row is unique per order and the request is delivered at most once per
  dedupe key. While reviews are enabled but not fully configured it waits (hourly checks, up to a week).
- ``reviews.publish`` (payload ``review_id``, dedupe ``reviews.publish:<review_id>``): publishes a submitted review.
  Retries with the scheduler's backoff, then hourly, until it succeeds or the review is no longer pending.
- ``on_ready``: re-queues every submitted review that is not published yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shopbot.core.scheduler import PermanentTaskError, RescheduleTask
from shopbot.db.repos.tasks import Task
from shopbot.features.reviews import service
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


class PublishPending(Exception):
    """The review could not be published yet; the scheduler retries with backoff."""


def _payload_id(task: Task, name: str) -> int:
    try:
        value = int(task.payload[name])
    except (KeyError, TypeError, ValueError):
        raise PermanentTaskError(f"invalid payload: {name} missing") from None
    if value <= 0:
        raise PermanentTaskError(f"invalid payload: {name} must be positive")
    return value


async def run_prompt_task(app: App, task: Task) -> None:
    order_id = _payload_id(task, "order_id")
    now = app.clock.now()
    if not app.readiness.is_enabled(REVIEWS):
        log_event(log, logging.INFO, "reviews_prompt_skipped", order_id=order_id, reason="feature_disabled")
        return
    if not app.ready(REVIEWS):
        if now - task.created_at >= service.PROMPT_MAX_WAIT_SECONDS:
            log_event(log, logging.WARNING, "reviews_prompt_dropped", order_id=order_id, reason="not_configured")
            return
        log_event(log, logging.INFO, "reviews_prompt_waiting", order_id=order_id, reason="not_configured")
        raise RescheduleTask(now + service.NOT_READY_RETRY_SECONDS)
    await service.send_prompt_for_order(app, order_id)


async def run_publish_task(app: App, task: Task) -> None:
    review_id = _payload_id(task, "review_id")
    now = app.clock.now()
    if not app.ready(REVIEWS):
        raise RescheduleTask(now + service.NOT_READY_RETRY_SECONDS)
    try:
        outcome = await service.publish_review(app, review_id)
    except Exception as exc:
        log_event(log, logging.ERROR, "reviews_publish_error", review_id=review_id, error=type(exc).__name__)
        log.exception("publishing review %s failed", review_id)
        outcome = service.Publish.FAILED
    if outcome is not service.Publish.FAILED:
        return
    if task.attempts >= task.max_attempts:
        # Never give up on a submitted review: keep trying, slowly.
        raise RescheduleTask(now + service.PUBLISH_SLOW_RETRY_SECONDS)
    raise PublishPending(f"review {review_id} is not published yet")


async def on_ready(app: App) -> None:
    """Restart recovery: publish reviews that were submitted but not published before the restart."""
    if not app.readiness.is_enabled(REVIEWS):
        return
    await service.requeue_pending(app)
