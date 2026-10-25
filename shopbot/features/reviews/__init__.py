"""Customer reviews: a rating request after an order is fulfilled, an optional comment, publication in the reviews
channel, and ``/review`` staff tools.

Public API for other features:
- ``await schedule_review_prompt(app, order_id)`` (also ``shopbot.features.reviews.service``): call when an order
  became ``fulfilled``. Schedules the review request ``reviews.prompt_delay_minutes`` later; a no-op unless the
  reviews feature is ready; idempotent per order; never raises.

See ``service`` for the flow, ``repo`` for the review states, ``tasks`` for scheduled work and restart recovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.reviews import content_keys as review_keys
from shopbot.features.reviews import service, tasks
from shopbot.features.reviews.commands import build_review_group
from shopbot.features.reviews.handlers import register_handlers
from shopbot.features.reviews.preview import preview
from shopbot.features.reviews.service import schedule_review_prompt
from shopbot.features.reviews.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App

__all__ = ["MODULE", "schedule_review_prompt"]


def register(app: App) -> None:
    bot = app.bot
    if bot is None:
        raise RuntimeError("register() requires app.bot")
    register_handlers(app)
    app.scheduler.register(service.PROMPT_TASK, tasks.run_prompt_task)
    app.scheduler.register(service.PUBLISH_TASK, tasks.run_publish_task)
    bot.tree.add_command(build_review_group(), guild=app.guild_object)


async def publish(app: App) -> list[str]:
    """/setup hook: reviews have no panel; re-queue submitted reviews (e.g. after the reviews channel was created)."""
    count = await service.requeue_pending(app)
    return [app.op("reviews.setup.requeued", count=count)] if count else []


MODULE = FeatureModule(
    name=review_keys.REVIEWS,
    is_enabled=lambda settings: settings.features.reviews,
    content_keys=review_keys.content_keys,
    settings_problems=review_keys.settings_problems,
    depends_on=("checkout",),
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=tasks.on_ready,
    publish=publish,
    preview=preview,
)
