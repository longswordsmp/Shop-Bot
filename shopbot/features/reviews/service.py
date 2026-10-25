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
  A publication claim is stored before each post and cleared once its message is recorded. When an attempt finds
  the claim of an earlier one (the process stopped between the post and the record, or Discord created the message
  but the answer was lost), it first looks through the bot's recent messages in the reviews channel for a post
  showing exactly this review and records that one instead of posting again.

Staff approval (``reviews.require_approval``)
---------------------------------------------
- A submitted review becomes ``pending_approval`` instead of being published. Its approval message (rating,
  comment, order, customer, status, Approve / Reject buttons; content keys ``reviews.approval.*``) is posted in
  ``reviews.approval_channel`` by ``request_approval`` with the same claim/adopt protocol as the publication, and
  retried by the task ``reviews.approval`` (dedupe ``reviews.approval:<review_id>``) until it is posted. Pending
  approval messages are re-queued at startup; /setup also edits them to the current text and re-posts deleted ones.
- A decision (``decide``: the buttons or ``/review approve|reject``) is a conditional update under the review lock,
  so it applies exactly once; the durable follow-up task ``reviews.decided`` is scheduled in the same transaction.
  The follow-ups (``run_decision_followups``) update the approval message, publish an approved review
  (``publish_or_retry``) or send the optional rejection notice, audit the decision once, then cancel the task.
- The optional approval notice is sent once the approved review is published (``publish_review``), so it can link
  the published review. Notices go to the customer by direct message with the order channel as fallback
  (notifier, dedupe ``reviews.decision:<review_id>``).
- With approval off nothing changes: a submitted review is approved automatically and published right away.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.events import FAILURE
from shopbot.core.notifier import Delivery
from shopbot.db.repos import notifications as notif_repo
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, views
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, edit_kwargs, edit_spec, send_spec, to_embed
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

PROMPT_TASK = "reviews.prompt"
PUBLISH_TASK = "reviews.publish"
APPROVAL_TASK = "reviews.approval"
DECISION_TASK = repo.DECISION_TASK
PROMPT_KIND = "reviews.prompt"  # notification kind
APPROVED_KIND = "reviews.approved_notice"  # notification kinds of the decision notices
REJECTED_KIND = "reviews.rejected_notice"

# A request whose task runs while reviews are enabled but not fully configured waits (hourly checks) up to a week.
NOT_READY_RETRY_SECONDS = 3600
PROMPT_MAX_WAIT_SECONDS = 7 * 24 * 3600
# After the scheduler's quick retries are used up, a pending publication is retried hourly until it succeeds.
PUBLISH_SLOW_RETRY_SECONDS = 3600
# How many recent messages of the reviews channel are searched for an unrecorded post of a claimed review.
PUBLISH_HISTORY_LIMIT = 50


def prompt_dedupe(order_id: int) -> str:
    return f"reviews.prompt:{order_id}"


def publish_dedupe(review_id: int) -> str:
    return f"reviews.publish:{review_id}"


def resend_dedupe(review_id: int, now: int) -> str:
    return f"reviews.resend:{review_id}:{now}"


def approval_dedupe(review_id: int) -> str:
    return f"{APPROVAL_TASK}:{review_id}"


def notice_dedupe(review_id: int) -> str:
    """At most one decision notice per review (approved or rejected)."""
    return f"reviews.decision:{review_id}"


def order_number_text(app: App, number: int) -> str:
    """{order_number} as shown to customers and staff - the same plain number checkout shows (order numbers are
    their own counter; ticket-number padding does not apply)."""
    del app
    return str(number)


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


def _approval_channel(app: App) -> discord.TextChannel | None:
    guild = app.guild
    key = app.settings.reviews.approval_channel
    if guild is None or not key:
        return None
    return app.resources.text_channel(guild, key)


def message_link(app: App, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{app.env.guild_id}/{channel_id}/{message_id}"


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
            user_id=review.user_id,
            spec=spec,
            kind=PROMPT_KIND,
            dedupe_key=dedupe_key,
            alert_on_failure=via == "dm",  # dm_then_channel: the order channel below is the fallback
        )
        if delivery != "none" or via == "dm":
            return delivery
    channel = _order_channel(app, order)
    if channel is None:
        log_event(log, logging.WARNING, "reviews_prompt_no_channel", review_id=review.id, order_id=order.id)
        await app.events.publish(FAILURE, kind="notification", source=PROMPT_KIND, user_id=review.user_id)
        return "none"
    message = await app.notifier.notify_channel(
        channel_id=channel.id,
        spec=replace(spec, mention_user_ids=(review.user_id,)),
        kind=PROMPT_KIND,
        dedupe_key=dedupe_key,
    )
    if message is None:
        await app.events.publish(FAILURE, kind="notification", source=PROMPT_KIND, user_id=review.user_id)
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


_locks: weakref.WeakKeyDictionary[App, dict[tuple[str, int], asyncio.Lock]] = weakref.WeakKeyDictionary()


def _lock(app: App, kind: str, review_id: int) -> asyncio.Lock:
    locks = _locks.setdefault(app, {})
    lock = locks.get((kind, review_id))
    if lock is None:
        lock = locks[(kind, review_id)] = asyncio.Lock()
    return lock


def review_lock(app: App, review_id: int) -> asyncio.Lock:
    """Serializes publishing, posting the approval message, staff decisions and staff removal of one review within
    this process (one process per database)."""
    return _lock(app, "review", review_id)


def followup_lock(app: App, review_id: int) -> asyncio.Lock:
    """Serializes the decision follow-ups of one review (the in-process run and its durable task). The follow-ups
    take ``review_lock`` to publish; nothing that holds ``review_lock`` waits for this lock."""
    return _lock(app, "followup", review_id)


def _publish_failed(review_id: int, reason: str, **fields: object) -> Publish:
    log_event(log, logging.WARNING, "reviews_publish_failed", review_id=review_id, reason=reason, **fields)
    return Publish.FAILED


EmbedKey = tuple[str, str, tuple[tuple[str, str, bool], ...]]


def _embed_key(embed: discord.Embed) -> EmbedKey:
    """What identifies a published review on a message: its title, description and fields (the timestamp differs
    between attempts). Surrounding whitespace is ignored in case Discord trims it."""
    return (
        (embed.title or "").strip(),
        (embed.description or "").strip(),
        tuple(((f.name or "").strip(), (f.value or "").strip(), bool(f.inline)) for f in embed.fields),
    )


async def _unrecorded_posts(app: App, channel: discord.TextChannel, spec: MessageSpec) -> list[int]:
    """IDs of the bot's recent messages in ``channel`` that show exactly ``spec`` (newest first).

    Raises ``discord.HTTPException`` when the channel history cannot be read.
    """
    bot_user = app.bot.user if app.bot is not None else None
    if bot_user is None:  # pragma: no cover - publishing runs only while connected
        return []
    expected = [_embed_key(to_embed(embed)) for embed in spec.embeds]
    found: list[int] = []
    async for message in channel.history(limit=PUBLISH_HISTORY_LIMIT):
        if message.author.id == bot_user.id and [_embed_key(e) for e in message.embeds] == expected:
            found.append(message.id)
    return found


async def publish_review(app: App, review_id: int) -> Publish:
    """Post a submitted review in the reviews channel and mark it published (at most once).

    The publication claim is stored before posting. If an earlier attempt left its claim, a post of that attempt
    that was never recorded is adopted (recorded as this review's message) instead of posting a second copy.
    """
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
        claimed = await app.db.transaction(
            repo.claim_publication, review_id, channel_id=channel.id, now=app.clock.now()
        )
        if claimed is None:  # pragma: no cover - checked above; removal also takes the review lock
            return Publish.SKIPPED
        message_id: int | None = None
        if claimed:
            try:
                candidates = await _unrecorded_posts(app, channel, spec)
            except discord.HTTPException as exc:
                # Posting without knowing whether the earlier attempt's post exists could show the review twice.
                return _publish_failed(review_id, "history_unavailable", status=exc.status, code=exc.code)
            if candidates:
                message_id = await app.db.transaction(
                    repo.adopt_publication, review_id, message_ids=candidates, now=app.clock.now()
                )
        adopted = message_id is not None
        if message_id is None:
            try:
                message = await send_spec(channel, spec)
            except RenderError:
                return _publish_failed(review_id, "limits")
            except discord.HTTPException as exc:
                return _publish_failed(review_id, "discord_error", status=exc.status, code=exc.code)
            stored = await app.db.transaction(
                repo.record_publication, review_id, message_id=message.id, now=app.clock.now()
            )
            if not stored:  # pragma: no cover - removal also takes the review lock
                try:
                    await message.delete()
                except discord.HTTPException:
                    log.warning("could not delete the stale publication of review %s", review_id)
                return Publish.SKIPPED
            message_id = message.id
    log_event(log, logging.INFO, "reviews_published", review_id=review_id, message_id=message_id, adopted=adopted)
    await app.audit.record(
        "reviews.published",
        actor_id=None,
        target_type="review",
        target_id=review_id,
        details={"order_number": review.order_number, "rating": review.rating, "message_id": message_id},
    )
    if review.approval == repo.APPROVED:
        await _notify_approved(app, review, link=message_link(app, channel.id, message_id))
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


async def after_submission(app: App, review: repo.Review) -> Publish:
    """The customer finished their review: ask staff for approval (reviews.require_approval) or publish it."""
    if review.pending_approval:
        return await approval_or_retry(app, review.id)
    return await publish_or_retry(app, review.id)


# -- staff approval: the approval message ----------------------------------------------------------------------


def approval_state(review: repo.Review) -> str:
    """Which state the review's approval message shows."""
    if review.status == repo.DECLINED:
        return k.STATE_REMOVED
    if review.pending_approval:
        return k.STATE_PENDING
    if review.approval == repo.REJECTED:
        return k.STATE_REJECTED
    return k.STATE_APPROVED


async def approval_spec(app: App, review: repo.Review) -> MessageSpec:
    """The approval message for the review's current state (raises ``MissingContent``)."""
    if review.rating is None:  # pragma: no cover - only submitted reviews have an approval message
        raise ValueError(f"review {review.id} has no rating")
    return views.approval_view(
        app.ui,
        review_id=review.id,
        state=approval_state(review),
        rating=review.rating,
        user_name=await user_name(app, review.user_id),
        user_id=review.user_id,
        order_number=order_number_text(app, review.order_number),
        comment=review.comment,
        staff_id=review.decided_by,
        reason=review.decision_reason,
    )


def _approval_failed(review_id: int, reason: str, **fields: object) -> Publish:
    log_event(log, logging.WARNING, "reviews_approval_request_failed", review_id=review_id, reason=reason, **fields)
    return Publish.FAILED


async def request_approval(app: App, review_id: int) -> Publish:
    """Post the approval message of a pending review in reviews.approval_channel and record it (at most once).

    PUBLISHED: posted (or an unrecorded post of an earlier attempt adopted); SKIPPED: nothing to do (decided,
    removed, already posted, or no approval channel configured - /review approve|reject still work); FAILED: retry.
    """
    async with review_lock(app, review_id):
        review = await get_review(app, review_id)
        if review is None or not review.pending_approval or review.approval_message_id is not None:
            return Publish.SKIPPED
        settings = app.settings.reviews
        if not settings.approval_channel:
            log_event(log, logging.WARNING, "reviews_approval_channel_unset", review_id=review_id)
            return Publish.SKIPPED
        if review.rating is None or not 1 <= review.rating <= settings.rating_scale:
            return _approval_failed(review_id, "rating_outside_scale", rating=review.rating)
        channel = _approval_channel(app)
        if channel is None:
            return _approval_failed(review_id, "channel_missing", channel_key=settings.approval_channel)
        try:
            spec = await approval_spec(app, review)
        except MissingContent as exc:
            return _approval_failed(review_id, "missing_content", content_key=exc.key)
        claimed = await app.db.transaction(
            repo.claim_approval_post, review_id, channel_id=channel.id, now=app.clock.now()
        )
        if claimed is None:  # pragma: no cover - checked above under the review lock
            return Publish.SKIPPED
        message_id: int | None = None
        if claimed:
            try:
                candidates = await _unrecorded_posts(app, channel, spec)
            except discord.HTTPException as exc:
                return _approval_failed(review_id, "history_unavailable", status=exc.status, code=exc.code)
            if candidates:
                message_id = await app.db.transaction(
                    repo.adopt_approval_message,
                    review_id,
                    channel_id=channel.id,
                    message_ids=candidates,
                    now=app.clock.now(),
                )
        adopted = message_id is not None
        if message_id is None:
            try:
                message = await send_spec(channel, spec)
            except RenderError:
                return _approval_failed(review_id, "limits")
            except discord.HTTPException as exc:
                return _approval_failed(review_id, "discord_error", status=exc.status, code=exc.code)
            stored = await app.db.transaction(
                repo.record_approval_message,
                review_id,
                channel_id=channel.id,
                message_id=message.id,
                now=app.clock.now(),
            )
            if not stored:  # pragma: no cover - decisions and removal also take the review lock
                try:
                    await message.delete()
                except discord.HTTPException:
                    log.warning("could not delete the stale approval message of review %s", review_id)
                return Publish.SKIPPED
            message_id = message.id
    log_event(
        log, logging.INFO, "reviews_approval_requested", review_id=review_id, message_id=message_id, adopted=adopted
    )
    return Publish.PUBLISHED


async def schedule_approval_retry(app: App, review_id: int, *, delay: int | None = None) -> None:
    wait = app.scheduler.retry_base_seconds if delay is None else delay
    await app.scheduler.schedule(
        APPROVAL_TASK,
        run_at=app.clock.now() + max(0, wait),
        payload={"review_id": review_id},
        dedupe_key=approval_dedupe(review_id),
        replace=True,
    )


async def approval_or_retry(app: App, review_id: int) -> Publish:
    """Post the approval message now; on any failure the task ``reviews.approval`` retries it."""
    try:
        outcome = await request_approval(app, review_id)
    except Exception as exc:
        error_id = uuid.uuid4().hex[:12]
        log_event(
            log,
            logging.ERROR,
            "reviews_approval_request_error",
            review_id=review_id,
            error_id=error_id,
            error=type(exc).__name__,
        )
        log.exception("posting the approval message of review %s failed (%s)", review_id, error_id)
        outcome = Publish.FAILED
    if outcome is Publish.FAILED:
        await schedule_approval_retry(app, review_id)
    return outcome


async def refresh_approval_message(app: App, review: repo.Review) -> bool:
    """Best effort: edit the review's recorded approval message to its current state. False when it could not be
    updated (logged)."""
    if review.approval_message_id is None or review.approval_channel_id is None or app.bot is None:
        return False
    channel = app.bot.get_channel(review.approval_channel_id)
    if not isinstance(channel, discord.TextChannel):
        log_event(log, logging.WARNING, "reviews_approval_message_channel_missing", review_id=review.id)
        return False
    try:
        spec = await approval_spec(app, review)
        await edit_spec(channel.get_partial_message(review.approval_message_id), spec)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="reviews.approval")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(
            log,
            logging.WARNING,
            "reviews_approval_message_update_failed",
            review_id=review.id,
            error=type(exc).__name__,
        )
        return False
    return True


async def _sync_pending(app: App, review_id: int) -> str:
    """/setup: edit a pending review's approval message to the current text. 'updated', 'missing' (the message or
    its channel is gone: the record was cleared so a new one is posted), or 'skipped'."""
    async with review_lock(app, review_id):
        review = await get_review(app, review_id)
        if review is None or not review.pending_approval or review.approval_message_id is None:
            return "skipped"
        message_id = review.approval_message_id
        channel = app.bot.get_channel(review.approval_channel_id or 0) if app.bot is not None else None
        try:
            if not isinstance(channel, discord.TextChannel):
                raise LookupError("approval channel gone")
            spec = await approval_spec(app, review)
            await edit_spec(channel.get_partial_message(message_id), spec)
        except (discord.NotFound, LookupError):
            await app.db.transaction(
                repo.forget_approval_message, review_id, message_id=message_id, now=app.clock.now()
            )
            log_event(log, logging.INFO, "reviews_approval_message_missing", review_id=review_id)
            return "missing"
        except (MissingContent, RenderError, discord.HTTPException) as exc:
            log_event(
                log, logging.WARNING, "reviews_approval_sync_failed", review_id=review_id, error=type(exc).__name__
            )
            return "skipped"
    return "updated"


async def requeue_approvals(app: App, *, sync_messages: bool) -> tuple[int, int]:
    """Queue the approval message of every pending review that has none (startup recovery and /setup). With
    ``sync_messages`` (/setup) recorded approval messages are also edited to the current text, and deleted ones are
    posted again. Returns (updated, queued)."""
    pending = await app.db.run(repo.pending_approvals, app.env.guild_id)
    updated = queued = 0
    for review_id, message_id in pending:
        if message_id is not None:
            if not sync_messages:
                continue
            result = await _sync_pending(app, review_id)
            if result == "updated":
                updated += 1
            if result != "missing":
                continue
        await schedule_approval_retry(app, review_id, delay=0)
        queued += 1
    if updated or queued:
        log_event(log, logging.INFO, "reviews_approvals_requeued", updated=updated, queued=queued)
    return updated, queued


# -- staff approval: decisions ----------------------------------------------------------------------------------


async def decide(app: App, review_id: int, *, approve: bool, actor_id: int, reason: str | None = None) -> repo.Result:
    """Approve or reject a pending review: a conditional update, so only the first decision applies. Serialized with
    posting the approval message and removal (review lock). The follow-ups are run separately."""
    async with review_lock(app, review_id):
        if approve:
            return await app.db.transaction(
                repo.approve,
                review_id,
                actor_id=actor_id,
                now=app.clock.now(),
                task_attempts=app.scheduler.max_attempts,
            )
        return await app.db.transaction(
            repo.reject,
            review_id,
            actor_id=actor_id,
            reason=reason or None,
            now=app.clock.now(),
            task_attempts=app.scheduler.max_attempts,
        )


@dataclass(frozen=True)
class Followups:
    # Approved reviews: the outcome of publishing it (None when there was nothing to publish).
    publication: Publish | None = None
    # Rejected reviews: how the customer was told (dm, channel, none, duplicate; "off" = no notice configured).
    notice: str = ""
    # Whether the approval message shows the decision (False also when the review has none).
    message_updated: bool = False
    # False when a step failed unexpectedly (the durable follow-up task is kept and runs again).
    complete: bool = True


async def _order_channel_id(app: App, review: repo.Review) -> int | None:
    order = await app.db.run(repo.get_order, review.order_id)
    return order.channel_id if order is not None else None


async def notify_rejected(app: App, review: repo.Review) -> str:
    """The optional rejection notice (once per review): DM, or the order channel as fallback."""
    spec = views.rejected_notice(
        app.ui, order_number=order_number_text(app, review.order_number), reason=review.decision_reason
    )
    if spec is None:
        return "off"
    return await app.notifier.notify_user(
        user_id=review.user_id,
        spec=spec,
        kind=REJECTED_KIND,
        dedupe_key=notice_dedupe(review.id),
        fallback_channel_id=await _order_channel_id(app, review),
    )


async def _notify_approved(app: App, review: repo.Review, *, link: str) -> None:
    """After an approved review was published: the optional approval notice (once per review; never raises)."""
    try:
        spec = views.approved_notice(app.ui, order_number=order_number_text(app, review.order_number), review_link=link)
        if spec is None:
            return
        delivery = await app.notifier.notify_user(
            user_id=review.user_id,
            spec=spec,
            kind=APPROVED_KIND,
            dedupe_key=notice_dedupe(review.id),
            fallback_channel_id=await _order_channel_id(app, review),
        )
    except Exception as exc:  # the publication itself succeeded and must not be reported as failed
        log_event(log, logging.ERROR, "reviews_approved_notice_failed", review_id=review.id, error=type(exc).__name__)
        log.exception("the approval notice of review %s failed", review.id)
        return
    log_event(log, logging.INFO, "reviews_approved_notice", review_id=review.id, delivery=delivery)


async def run_decision_followups(app: App, review_id: int, *, message_done: int | None = None) -> Followups:
    """After a decision: update the approval message (unless ``message_done`` is that message, already showing the
    decision), publish an approved review / send the rejection notice, audit the decision once, then cancel the
    durable follow-up task. Each step is idempotent, so the task may run them again after a crash."""
    async with followup_lock(app, review_id):
        review = await get_review(app, review_id)
        if review is None or review.approval not in (repo.APPROVED, repo.REJECTED) or review.decided_by is None:
            return Followups()
        complete = True
        message_updated = review.approval_message_id is not None and review.approval_message_id == message_done
        if review.approval_message_id is not None and not message_updated:
            message_updated = await refresh_approval_message(app, review)
        publication: Publish | None = None
        notice = ""
        try:
            if review.approval == repo.APPROVED:
                publication = await publish_or_retry(app, review.id)
            else:
                notice = await notify_rejected(app, review)
        except Exception as exc:  # e.g. a network error: the durable follow-up task tries again
            complete = False
            log_event(
                log, logging.ERROR, "reviews_decision_followup_failed", review_id=review.id, error=type(exc).__name__
            )
            log.exception("decision follow-up of review %s failed", review.id)
        action = "reviews.approved" if review.approval == repo.APPROVED else "reviews.rejected"
        try:
            if not await app.db.run(repo.audited, action, review.id):
                await app.audit.record(
                    action,
                    actor_id=review.decided_by,
                    target_type=repo.AUDIT_TARGET,
                    target_id=review.id,
                    details={
                        "order_number": review.order_number,
                        "rating": review.rating,
                        "with_reason": bool(review.decision_reason),
                        "approval_message_updated": message_updated,
                        "notice": notice or None,
                    },
                )
            if complete:
                await app.db.transaction(repo.cancel_followup, review.id, now=app.clock.now())
        except Exception as exc:
            complete = False
            log_event(
                log, logging.ERROR, "reviews_decision_audit_failed", review_id=review.id, error=type(exc).__name__
            )
            log.exception("auditing the decision on review %s failed", review.id)
    return Followups(publication=publication, notice=notice, message_updated=message_updated, complete=complete)


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
    await app.scheduler.cancel(approval_dedupe(previous.id))
    if previous.approval_message_id is not None:
        removed = await get_review(app, previous.id)
        if removed is not None:
            await refresh_approval_message(app, removed)  # its buttons are removed; best effort
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


OnDecided = Callable[[repo.Review], Awaitable[int | None]]


async def staff_decide(
    app: App,
    review: repo.Review,
    *,
    approve: bool,
    actor_id: int,
    reason: str | None = None,
    on_decided: OnDecided | None = None,
) -> StaffResult:
    """A staff decision with its follow-ups and the operator reply (buttons and ``/review approve|reject``).

    ``on_decided`` runs right after the decision applied (before the follow-ups); it may show the decision on the
    message the staff member clicked and returns that message's id (or None).
    """
    number = order_number_text(app, review.order_number)
    result = await decide(app, review.id, approve=approve, actor_id=actor_id, reason=reason)
    current = result.review
    if result.outcome is repo.Outcome.NOT_PENDING and current is not None:
        status = status_label(app, current)
        return StaffResult([app.op("reviews.decision.not_pending", order_number=number, status=status)], "warning")
    if result.outcome is repo.Outcome.EXPIRED:
        order = await app.db.run(repo.get_order, review.order_id)
        status = order.status if order is not None else "-"
        return StaffResult([app.op("reviews.decision.expired", order_number=number, status=status)], "warning")
    if result.outcome is not repo.Outcome.OK or current is None:
        return StaffResult([app.op("reviews.decision.not_found", order_number=number)], "warning")
    message_done = None
    if on_decided is not None:
        try:
            message_done = await on_decided(current)
        except Exception as exc:  # the decision is applied; the follow-ups update the recorded message
            log_event(
                log, logging.WARNING, "reviews_decision_message_failed", review_id=review.id, error=type(exc).__name__
            )
    followups = await run_decision_followups(app, review.id, message_done=message_done)
    if approve:
        lines = [app.op("reviews.decision.approved", order_number=number)]
        if followups.publication is Publish.PUBLISHED:
            lines.append(app.op("reviews.decision.published"))
        elif followups.publication is Publish.FAILED:
            lines.append(app.op("reviews.decision.publish_pending"))
    else:
        lines = [app.op("reviews.decision.rejected", order_number=number)]
        if followups.notice:
            lines.append(app.op(f"reviews.decision.notice.{followups.notice}"))
    if current.approval_message_id is not None and not followups.message_updated:
        lines.append(app.op("reviews.decision.message_failed"))
    return StaffResult(lines, "success")


async def decide_by_order(
    app: App, order_number: int, *, approve: bool, actor_id: int, reason: str | None = None
) -> StaffResult:
    """Staff command: approve or reject the pending review of an order (when its approval message is not at hand)."""
    number = order_number_text(app, order_number)
    order = await app.db.run(repo.get_order_by_number, app.env.guild_id, order_number)
    review = await app.db.run(repo.get_review_by_order, order.id) if order is not None else None
    if review is None:
        return StaffResult([app.op("reviews.decision.not_found", order_number=number)], "warning")
    if reason is not None and len(reason) > repo.REASON_MAX:
        return StaffResult(
            [app.op("reviews.decision.reason_too_long", length=len(reason), max=repo.REASON_MAX)], "warning"
        )
    return await staff_decide(app, review, approve=approve, actor_id=actor_id, reason=reason)


def check_publishable(spec: MessageSpec) -> bool:
    """Whether a published review built from customer data fits Discord's limits."""
    return not validate_message(spec)
