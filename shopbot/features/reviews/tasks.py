"""Scheduled tasks and restart recovery of the reviews feature.

- ``reviews.prompt`` (payload ``order_id``, dedupe ``reviews.prompt:<order_id>``): sends the review request of a
  fulfilled order. Idempotent: the review row is unique per order and the request is delivered at most once per
  dedupe key. While reviews are enabled but not fully configured it waits (hourly checks, up to a week).
- ``reviews.publish`` (payload ``review_id``, dedupe ``reviews.publish:<review_id>``): publishes a submitted review.
  Retries with the scheduler's backoff, then hourly, until it succeeds or the review is no longer pending.
- ``reviews.approval`` (payload ``review_id``, dedupe ``reviews.approval:<review_id>``): posts the staff approval
  message of a review waiting for approval. Retries like ``reviews.publish`` until it is posted or the review no
  longer waits (or no approval channel is configured).
- ``reviews.decided`` (payload ``review_id``, dedupe ``reviews.decided:<review_id>``): the durable follow-up of a
  staff decision, scheduled in the decision's transaction; normally cancelled by the in-process follow-up, it runs
  the (idempotent) follow-ups again when the process stopped in between.
- ``on_ready``: re-queues every submitted review that is not published yet and every pending review whose
  approval message was not posted yet, and releases the stored photos of reviews nothing posts any more.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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


async def _run_until_done(
    app: App,
    task: Task,
    run: Callable[[App, int], Awaitable[service.Publish]],
    *,
    what: str,
) -> None:
    """Shared body of the publication and approval-request tasks: never give up while the review waits."""
    review_id = _payload_id(task, "review_id")
    now = app.clock.now()
    if not app.ready(REVIEWS):
        raise RescheduleTask(now + service.NOT_READY_RETRY_SECONDS)
    try:
        outcome = await run(app, review_id)
    except Exception as exc:
        log_event(log, logging.ERROR, f"reviews_{what}_error", review_id=review_id, error=type(exc).__name__)
        log.exception("%s of review %s failed", what, review_id)
        outcome = service.Publish.FAILED
    if outcome is not service.Publish.FAILED:
        return
    if task.attempts >= task.max_attempts:
        # Never give up on a submitted review: keep trying, slowly.
        raise RescheduleTask(now + service.PUBLISH_SLOW_RETRY_SECONDS)
    raise PublishPending(f"{what} of review {review_id} is not done yet")


async def run_publish_task(app: App, task: Task) -> None:
    await _run_until_done(app, task, service.publish_review, what="publish")


async def run_approval_task(app: App, task: Task) -> None:
    await _run_until_done(app, task, service.request_approval, what="approval_request")


async def run_decision_task(app: App, task: Task) -> None:
    """The durable follow-up of a staff decision (the decision itself is already committed)."""
    review_id = _payload_id(task, "review_id")
    now = app.clock.now()
    if not app.ready(REVIEWS):
        raise RescheduleTask(now + service.NOT_READY_RETRY_SECONDS)
    followups = await service.run_decision_followups(app, review_id)
    if not followups.complete:
        raise PublishPending(f"the decision follow-up of review {review_id} is not complete yet")


async def on_ready(app: App) -> None:
    """Restart recovery: publish reviews that were submitted but not published before the restart, post the approval
    messages that were not posted yet, and release the photos of reviews that were published, rejected or removed."""
    if not app.readiness.is_enabled(REVIEWS):
        return
    await service.requeue_pending(app)
    await service.requeue_approvals(app, sync_messages=False)
    await service.release_images(app)
