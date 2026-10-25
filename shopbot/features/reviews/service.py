"""Reviews logic: scheduling and delivering review requests, recording ratings and comments, publishing reviews,
and the staff tools behind ``/review``.

PUBLIC API (for checkout)
-------------------------
``await schedule_review_prompt(app, order_id)``
    Call after the order's change to ``fulfilled`` is committed (the task re-reads the order and ignores orders
    that are not fulfilled when it runs). When the reviews feature is ready, schedules the task ``reviews.prompt``
    at now + ``reviews.prompt_delay_minutes`` (dedupe ``reviews.prompt:<order_id>``, so calling it twice is
    harmless). A no-op when reviews are not ready; never raises.

Flow
----
- Task ``reviews.prompt``: re-reads the order (only ``fulfilled`` orders), creates the ``reviews`` row (UNIQUE
  ``order_id``) and sends the request according to ``reviews.prompt_via``: a direct message through the notifier
  (dedupe ``reviews.prompt:<order_id>``), falling back to the order channel for ``dm_then_channel``, or a post with
  a mention in the order channel for ``channel`` (its message id is stored).
- The customer picks a rating (and fills the comment form when ``reviews.allow_comment``); the review is then
  *submitted* and published in the reviews channel. A failed publication (channel missing, missing permission,
  Discord error) is retried by the task ``reviews.publish`` (dedupe ``reviews.publish:<review_id>``) until it
  succeeds: a submitted review is never lost. Pending publications are re-queued at startup and by /setup.
- Publishing is serialized per review (in-process lock + conditional UPDATE), so a review is never posted twice.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.notifier import Delivery
from shopbot.db.repos import notifications as notif_repo
from shopbot.features.reviews import repo, views
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.features.tickets.service import format_number
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, edit_kwargs, send_spec
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

PROMPT_TASK = "reviews.prompt"
PUBLISH_TASK = "reviews.publish"
PROMPT_KIND = "reviews.prompt"  # notification kind

# A request whose task runs while reviews are enabled but not fully configured waits (hourly checks) up to a week.
NOT_READY_RETRY_SECONDS = 3600
PROMPT_MAX_WAIT_SECONDS = 7 * 24 * 3600
# After the scheduler's quick retries are used up, a pending publication is retried hourly until it succeeds.
PUBLISH_SLOW_RETRY_SECONDS = 3600


def prompt_dedupe(order_id: int) -> str:
    return f"reviews.prompt:{order_id}"


def publish_dedupe(review_id: int) -> str:
    return f"reviews.publish:{review_id}"


def resend_dedupe(review_id: int, now: int) -> str:
    return f"reviews.resend:{review_id}:{now}"


def order_number_text(app: App, number: int) -> str:
    """{order_number} as shown to customers and staff (zero-padded like ticket numbers)."""
    return format_number(app, number)


def is_ready(app: App) -> bool:
    return app.ready(REVIEWS)


# -- scheduling (public) ---------------------------------------------------------------------------------


async def schedule_review_prompt(app: App, order_id: int) -> None:
    """Schedule the review request of a fulfilled order (no-op unless reviews are ready; never raises)."""
    if not is_ready(app):
        return
    delay = max(0, app.settings.reviews.prompt_delay_minutes) * 60
    try:
        task_id = await app.scheduler.schedule(
            PROMPT_TASK,
            run_at=app.clock.now() + delay,
            payload={"order_id": int(order_id)},
            dedupe_key=prompt_dedupe(order_id),
        )
    except Exception as exc:  # the caller's order flow must never fail because of reviews
        log_event(log, logging.ERROR, "reviews_schedule_failed", order_id=order_id, error=type(exc).__name__)
        log.exception("could not schedule the review request of order %s", order_id)
        return
    log_event(log, logging.INFO, "reviews_prompt_scheduled", order_id=order_id, task_id=task_id, delay=delay)


# -- loading -----------------------------------------------------------------------------------------------


async def get_review(app: App, review_id: int) -> repo.Review | None:
    review = await app.db.run(repo.get_review, review_id)
    if review is None or review.guild_id != app.env.guild_id:
        return None
    return review


async def user_name(app: App, user_id: int) -> str:
    """The reviewer's current display name (raw data; views escape it). Falls back to the user ID."""
    guild = app.guild
    member = guild.get_member(user_id) if guild is not None else None
    if member is not None:
        return member.display_name
    bot = app.bot
    if bot is not None:
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except discord.HTTPException as exc:
                log_event(log, logging.INFO, "reviews_user_lookup_failed", user_id=user_id, status=exc.status)
                user = None
        if user is not None:
            return user.display_name
    return str(user_id)


def _order_channel(app: App, order: repo.Order) -> discord.TextChannel | None:
    if order.channel_id is None or app.bot is None:
        return None
    channel = app.bot.get_channel(order.channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _reviews_channel(app: App) -> discord.TextChannel | None:
    guild = app.guild
    if guild is None:
        return None
    return app.resources.text_channel(guild, app.settings.reviews.channel)


# -- review requests -------------------------------------------------------------------------------------


async def deliver_prompt(app: App, review: repo.Review, order: repo.Order, *, dedupe_key: str) -> Delivery:
    """Send the review request per ``reviews.prompt_via``. Raises MissingContent when it cannot be built."""
    settings = app.settings.reviews
    spec = views.prompt_view(app.ui, settings, review_id=review.id, order_number=order_number_text(app, order.number))
    if await app.db.run(notif_repo.already_sent, dedupe_key):
        return "duplicate"
    via = settings.prompt_via
    if via == "dm" or (via == "dm_then_channel" and app.settings.features.dm_notifications):
        delivery = await app.notifier.notify_user(
            user_id=review.user_id, spec=spec, kind=PROMPT_KIND, dedupe_key=dedupe_key
        )
        if delivery != "none" or via == "dm":
            return delivery
    channel = _order_channel(app, order)
    if channel is None:
        log_event(log, logging.WARNING, "reviews_prompt_no_channel", review_id=review.id, order_id=order.id)
        return "none"
    message = await app.notifier.notify_channel(
        channel_id=channel.id,
        spec=replace(spec, mention_user_ids=(review.user_id,)),
        kind=PROMPT_KIND,
        dedupe_key=dedupe_key,
    )
    if message is None:
        return "none"
    await app.db.transaction(
        repo.set_prompt_message, review.id, channel_id=channel.id, message_id=message.id, now=app.clock.now()
    )
    return "channel"


async def send_prompt_for_order(app: App, order_id: int) -> Delivery | None:
    """Task body: create the review of a fulfilled order and send its request once. None when nothing was due."""
    started = await app.db.transaction(
        repo.start_review, order_id=order_id, guild_id=app.env.guild_id, now=app.clock.now()
    )
    if started is None:
        log_event(log, logging.INFO, "reviews_prompt_skipped", order_id=order_id, reason="order_not_eligible")
        return None
    review = started.review
    if review.status != repo.PROMPTED:
        log_event(log, logging.INFO, "reviews_prompt_skipped", order_id=order_id, reason=review.status)
        return None
    delivery = await deliver_prompt(app, review, started.order, dedupe_key=prompt_dedupe(order_id))
    log_event(log, logging.INFO, "reviews_prompt_sent", review_id=review.id, order_id=order_id, delivery=delivery)
    if delivery != "duplicate":
        await app.audit.record(
            "reviews.prompted",
            actor_id=None,
            target_type="order",
            target_id=order_id,
            details={"review_id": review.id, "order_number": started.order.number, "delivery": delivery},
        )
    return delivery


# -- publishing --------------------------------------------------------------------------------------------


class Publish(StrEnum):
    PUBLISHED = "published"
    SKIPPED = "skipped"  # nothing to publish (already published, removed, or not submitted)
    FAILED = "failed"  # will be retried


_locks: weakref.WeakKeyDictionary[App, dict[int, asyncio.Lock]] = weakref.WeakKeyDictionary()


def review_lock(app: App, review_id: int) -> asyncio.Lock:
    """Serializes publishing and staff removal of one review within this process (one process per database)."""
    locks = _locks.setdefault(app, {})
    lock = locks.get(review_id)
    if lock is None:
        lock = locks[review_id] = asyncio.Lock()
    return lock


def _publish_failed(review_id: int, reason: str, **fields: object) -> Publish:
    log_event(log, logging.WARNING, "reviews_publish_failed", review_id=review_id, reason=reason, **fields)
    return Publish.FAILED


async def publish_review(app: App, review_id: int) -> Publish:
    """Post a submitted review in the reviews channel and mark it published (at most once)."""
    async with review_lock(app, review_id):
        review = await get_review(app, review_id)
        if review is None or not review.submitted or review.rating is None:
            return Publish.SKIPPED
        if not 1 <= review.rating <= app.settings.reviews.rating_scale:
            return _publish_failed(review_id, "rating_outside_scale", rating=review.rating)
        channel = _reviews_channel(app)
        if channel is None:
            return _publish_failed(review_id, "channel_missing", channel_key=app.settings.reviews.channel)
        name = await user_name(app, review.user_id)
        try:
            spec = views.published_view(
                app.ui,
                rating=review.rating,
                user_name=name,
                order_number=order_number_text(app, review.order_number),
                comment=review.comment,
            )
        except MissingContent as exc:
            return _publish_failed(review_id, "missing_content", content_key=exc.key)
        try:
            message = await send_spec(channel, spec)
        except RenderError:
            return _publish_failed(review_id, "limits")
        except discord.HTTPException as exc:
            return _publish_failed(review_id, "discord_error", status=exc.status, code=exc.code)
        stored = await app.db.transaction(repo.mark_published, review_id, message_id=message.id, now=app.clock.now())
        if not stored:  # pragma: no cover - removal also takes the review lock
            try:
                await message.delete()
            except discord.HTTPException:
                log.warning("could not delete the stale publication of review %s", review_id)
            return Publish.SKIPPED
    log_event(log, logging.INFO, "reviews_published", review_id=review_id, message_id=message.id)
    await app.audit.record(
        "reviews.published",
        actor_id=None,
        target_type="review",
        target_id=review_id,
        details={"order_number": review.order_number, "rating": review.rating, "message_id": message.id},
    )
    return Publish.PUBLISHED


async def schedule_publish_retry(app: App, review_id: int, *, delay: int | None = None) -> None:
    wait = app.scheduler.retry_base_seconds if delay is None else delay
    await app.scheduler.schedule(
        PUBLISH_TASK,
        run_at=app.clock.now() + max(0, wait),
        payload={"review_id": review_id},
        dedupe_key=publish_dedupe(review_id),
        replace=True,
    )


async def publish_or_retry(app: App, review_id: int) -> Publish:
    """Publish now; on any failure the review stays submitted and the task ``reviews.publish`` retries it."""
    try:
        outcome = await publish_review(app, review_id)
    except Exception as exc:
        error_id = uuid.uuid4().hex[:12]
        log_event(
            log,
            logging.ERROR,
            "reviews_publish_error",
            review_id=review_id,
            error_id=error_id,
            error=type(exc).__name__,
        )
        log.exception("publishing review %s failed (%s)", review_id, error_id)
        outcome = Publish.FAILED
    if outcome is Publish.FAILED:
        await schedule_publish_retry(app, review_id)
    return outcome


async def requeue_pending(app: App) -> int:
    """Queue every submitted-but-unpublished review for publication now (startup recovery and /setup)."""
    review_ids = await app.db.run(repo.submitted_review_ids, app.env.guild_id)
    for review_id in review_ids:
        await schedule_publish_retry(app, review_id, delay=0)
    if review_ids:
        log_event(log, logging.INFO, "reviews_publications_requeued", count=len(review_ids))
    return len(review_ids)


# -- staff tools -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffResult:
    """Operator reply of a staff action."""

    lines: list[str]
    tone: str = "info"


def _member(user_id: int) -> str:
    return f"<@{user_id}>"


def status_label(app: App, review: repo.Review) -> str:
    if review.submitted:
        return app.op("reviews.status.submitted")
    return app.op(f"reviews.status.{review.status}")


def _message_link(app: App, channel_id: int | None, message_id: int | None) -> str:
    if not channel_id or not message_id:
        return app.op("reviews.list.none")
    return f"https://discord.com/channels/{app.env.guild_id}/{channel_id}/{message_id}"


async def list_view(app: App, status: str | None) -> MessageSpec:
    reviews = await app.db.run(repo.list_reviews, app.env.guild_id, status=status, limit=25)
    channel_id = app.resources.channel_id(app.settings.reviews.channel)
    none = app.op("reviews.list.none")
    lines = [
        app.op(
            "reviews.list.line",
            order_number=order_number_text(app, r.order_number),
            status=status_label(app, r),
            rating=r.rating if r.rating is not None else none,
            member=_member(r.user_id),
            message=_message_link(app, channel_id, r.published_message_id) if r.status == repo.PUBLISHED else none,
        )
        for r in reviews
    ]
    title = app.op("reviews.list.title", count=len(reviews))
    return views.operator_list(app.ui, title, lines, app.op("reviews.list.empty"))


async def _edit_prompt(app: App, review: repo.Review, spec: MessageSpec) -> None:
    """Best effort: replace a request posted in an order channel (direct-message requests cannot be edited)."""
    if not review.prompt_channel_id or not review.prompt_message_id or app.bot is None:
        return
    channel = app.bot.get_channel(review.prompt_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(review.prompt_message_id)
        await message.edit(**edit_kwargs(spec))
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.INFO, "reviews_prompt_edit_failed", review_id=review.id, error=type(exc).__name__)


async def remove_review(app: App, order_number: int, *, actor_id: int) -> StaffResult:
    """Staff: delete the published message (if any) and close the review (status declined)."""
    number = order_number_text(app, order_number)
    order = await app.db.run(repo.get_order_by_number, app.env.guild_id, order_number)
    if order is None:
        return StaffResult([app.op("reviews.cmd.order_not_found", order_number=number)], "warning")
    review = await app.db.run(repo.get_review_by_order, order.id)
    if review is None:
        cancelled = await app.scheduler.cancel(prompt_dedupe(order.id))
        if not cancelled:
            return StaffResult([app.op("reviews.cmd.no_review", order_number=number)], "warning")
        await app.audit.record(
            "reviews.prompt_cancelled",
            actor_id=actor_id,
            target_type="order",
            target_id=order.id,
            details={"order_number": order.number},
        )
        return StaffResult([app.op("reviews.cmd.prompt_cancelled", order_number=number)], "success")
    async with review_lock(app, review.id):
        current = await get_review(app, review.id)
        if current is None or current.status == repo.DECLINED:
            return StaffResult([app.op("reviews.cmd.already_removed", order_number=number)], "warning")
        message_note = None
        if current.status == repo.PUBLISHED and current.published_message_id:
            channel = _reviews_channel(app)
            if channel is None:
                message_note = app.op("reviews.cmd.message_missing")
            else:
                try:
                    message = await channel.fetch_message(current.published_message_id)
                    await message.delete()
                    message_note = app.op("reviews.cmd.message_deleted")
                except discord.NotFound:
                    message_note = app.op("reviews.cmd.message_missing")
                except discord.HTTPException as exc:
                    log_event(log, logging.WARNING, "reviews_remove_failed", review_id=current.id, status=exc.status)
                    return StaffResult(
                        [app.op("reviews.cmd.remove_failed", order_number=number, status=exc.status)], "danger"
                    )
        previous = await app.db.transaction(repo.remove, current.id, now=app.clock.now())
    if previous is None:  # pragma: no cover - serialized by the review lock
        return StaffResult([app.op("reviews.cmd.already_removed", order_number=number)], "warning")
    await app.scheduler.cancel(publish_dedupe(previous.id))
    await app.scheduler.cancel(prompt_dedupe(order.id))
    if previous.open:
        try:
            await _edit_prompt(app, previous, views.expired_view(app.ui, order_number=number))
        except MissingContent:
            log_event(log, logging.WARNING, "reviews_prompt_edit_skipped", review_id=previous.id)
    await app.audit.record(
        "reviews.removed",
        actor_id=actor_id,
        target_type="review",
        target_id=previous.id,
        details={"order_number": order.number, "previous_status": previous.status},
    )
    lines = [app.op("reviews.cmd.removed", order_number=number)]
    if message_note:
        lines.append(message_note)
    return StaffResult(lines, "success")


async def resend_prompt(app: App, order_number: int, *, actor_id: int) -> StaffResult:
    """Staff: send the review request again while it is unanswered (creates it for a fulfilled order)."""
    number = order_number_text(app, order_number)
    order = await app.db.run(repo.get_order_by_number, app.env.guild_id, order_number)
    if order is None:
        return StaffResult([app.op("reviews.cmd.order_not_found", order_number=number)], "warning")
    if not order.fulfilled:
        return StaffResult([app.op("reviews.cmd.not_fulfilled", order_number=number, status=order.status)], "warning")
    started = await app.db.transaction(
        repo.start_review, order_id=order.id, guild_id=app.env.guild_id, now=app.clock.now()
    )
    if started is None:  # pragma: no cover - checked above
        return StaffResult([app.op("reviews.cmd.order_not_found", order_number=number)], "warning")
    review = started.review
    if not review.open:
        return StaffResult(
            [app.op("reviews.cmd.resend_wrong_state", order_number=number, status=status_label(app, review))],
            "warning",
        )
    # The scheduled request (if still pending) would arrive as a second copy.
    await app.scheduler.cancel(prompt_dedupe(order.id))
    try:
        delivery = await deliver_prompt(app, review, order, dedupe_key=resend_dedupe(review.id, app.clock.now()))
    except MissingContent as exc:
        return StaffResult([app.op("reviews.cmd.missing_content", content_key=exc.key)], "danger")
    await app.audit.record(
        "reviews.resent",
        actor_id=actor_id,
        target_type="review",
        target_id=review.id,
        details={"order_number": order.number, "delivery": delivery},
    )
    if delivery == "dm":
        return StaffResult([app.op("reviews.cmd.resent_dm", order_number=number)], "success")
    if delivery == "channel":
        return StaffResult([app.op("reviews.cmd.resent_channel", order_number=number)], "success")
    return StaffResult([app.op("reviews.cmd.resend_failed", order_number=number)], "danger")


def check_publishable(spec: MessageSpec) -> bool:
    """Whether a published review built from customer data fits Discord's limits."""
    return not validate_message(spec)
