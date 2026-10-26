"""Customer reviews: a rating request after an order is fulfilled, an optional comment, ``/review`` for every member
(stars, a description and an optional photo; a review of their delivered order, or an unverified review that always
waits for staff), optional staff approval (``reviews.require_approval``: Approve / Reject in a staff channel),
anonymous publication in the reviews channel with an optional verification marker, the #reviews intro panel with its
Write a review button (the same review as ``/review``, for members who cannot type in the read-only channel), and
``/reviews`` staff tools.

Public API for other features:
- ``await schedule_review_prompt(app, order_id)`` (also ``shopbot.features.reviews.service``): call when an order
  became ``fulfilled``. Schedules the review request ``reviews.prompt_delay_minutes`` later; a no-op unless the
  reviews feature is ready; idempotent per order; never raises.

See ``service`` for the flow, ``repo`` for the review states, ``images`` for the photo, ``tasks`` for scheduled work
and restart recovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.reviews import content_keys as review_keys
from shopbot.features.reviews import service, tasks
from shopbot.features.reviews.commands import register_commands
from shopbot.features.reviews.handlers import register_handlers
from shopbot.features.reviews.preview import preview
from shopbot.features.reviews.service import schedule_review_prompt
from shopbot.features.reviews.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App

__all__ = ["MODULE", "schedule_review_prompt"]


def register(app: App) -> None:
    register_handlers(app)
    app.scheduler.register(service.PROMPT_TASK, tasks.run_prompt_task)
    app.scheduler.register(service.PUBLISH_TASK, tasks.run_publish_task)
    app.scheduler.register(service.APPROVAL_TASK, tasks.run_approval_task)
    app.scheduler.register(service.DECISION_TASK, tasks.run_decision_task)
    register_commands(app)


async def publish(app: App) -> list[str]:
    """/setup hook: post or update the #reviews intro panel (first, so reviews waiting to be published come after it),
    re-queue submitted reviews (e.g. after the reviews channel was created), edit the approval messages of pending
    reviews to the current text and (re-)post missing ones, and release the photos nothing posts any more."""
    lines = await service.publish_intro(app)
    count = await service.requeue_pending(app)
    if count:
        lines.append(app.op("reviews.setup.requeued", count=count))
    updated, queued = await service.requeue_approvals(app, sync_messages=True)
    if updated:
        lines.append(app.op("reviews.setup.approvals_refreshed", count=updated))
    if queued:
        lines.append(app.op("reviews.setup.approvals_queued", count=queued))
    await service.release_images(app)
    return lines


MODULE = FeatureModule(
    name=review_keys.REVIEWS,
    is_enabled=lambda settings: settings.features.reviews,
    content_keys=review_keys.content_keys,
    settings_problems=review_keys.settings_problems,
    content_problems=review_keys.content_problems,
    depends_on=("checkout",),
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=tasks.on_ready,
    publish=publish,
    panel_prefixes=(service.INTRO_PREFIX,),
    preview=preview,
)
