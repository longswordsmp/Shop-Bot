"""Component and form handlers (route ``rv``).

Customer routes work in direct messages (``allow_dm``) as well as in the order channel, and only for the customer
the review belongs to:

- ``sb:rv:rate:<review_id>`` (rating select): records the rating, then opens the comment form
  (``reviews.allow_comment``) or finishes the review right away.
- ``sb:rv:comment:<review_id>`` (comment form): saves the comment, replaces the request with the thank-you text
  and publishes the review (a failed publication is retried by a task; the review is never lost). With
  ``reviews.require_approval`` the review is sent to staff for approval instead (the 'pending' thank-you text).
- ``sb:rv:decline:<review_id>`` (button): the customer declines; the request shows the declined text.

Staff routes (the approval message in ``reviews.approval_channel``; staff-only in the router and re-checked here:
``permissions.staff_roles``, admins and ``checkout.staff_roles``, the same staff as ``/review``):

- ``sb:rv:approve:<review_id>`` (button): approves the pending review; the message shows the decision and the
  review is published.
- ``sb:rv:reject:<review_id>`` (button): opens the reject form (optional reason, operator text).
- ``sb:rv:rejected:<review_id>`` (form): rejects the pending review; the message shows the decision and the
  customer gets the optional rejection notice.

A decision is a conditional update, so a second or concurrent decision changes nothing (operator reply).

Each handler re-loads the review by ID, checks the server, the customer and the state, and changes state only with
the repository's conditional updates, so duplicate or concurrent clicks never apply twice. Replies are built before
any state change: if text is missing, nothing changes and the router answers with the 'unavailable' message.

Discord keeps showing the rating a customer picked until the request message is edited, and picking the same rating
again sends nothing. So when the comment form opens, and when a submitted comment is refused, the request's rating
menu is re-rendered (best effort; its text and ping stay as they are); otherwise a customer who closed the form or
had the comment refused could not pick that rating again.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NoReturn

import discord

from shopbot.config.content import MissingContent
from shopbot.features import core_keys as ck
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, service, views
from shopbot.features.reviews.commands import staff_role_keys
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, check_message, check_modal, to_view
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


async def _load(ctx: Ctx) -> repo.Review:
    review = await service.get_review(ctx.app, ctx.arg_int(0))
    if review is None:
        raise HandlerError(ck.ERROR_NOT_FOUND)
    if review.user_id != ctx.user_id:
        raise HandlerError(ck.ERROR_PERMISSION)
    return review


def _number(ctx: Ctx, review: repo.Review) -> str:
    return service.order_number_text(ctx.app, review.order_number)


async def _replace_request(ctx: Ctx, spec: MessageSpec) -> None:
    """Replace the message holding the request (clicked component or the form's origin); else answer privately."""
    if ctx.interaction.message is not None:
        await ctx.update(spec)
    else:
        await ctx.respond(spec, ephemeral=True)


async def _reset_request(ctx: Ctx, review_id: int, order_number: str) -> None:
    """Best effort: re-render the rating menu and decline button on the request message (no rating selected).

    Only the components change. After the comment form was opened the message is edited directly; for a refused
    comment the edit is the answer to the form submission (the error follows as a private followup).
    """
    message = ctx.interaction.message
    if message is None:
        return
    try:
        spec = views.prompt_view(ctx.app.ui, ctx.app.settings.reviews, review_id=review_id, order_number=order_number)
        check_message(spec)
    except (MissingContent, RenderError) as exc:
        log_event(log, logging.INFO, "reviews_request_reset_skipped", review_id=review_id, error=type(exc).__name__)
        return
    view = to_view(MessageSpec(rows=spec.rows))
    try:
        if ctx.responded:
            await message.edit(view=view)
        else:
            await ctx.interaction.response.edit_message(view=view)
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "reviews_request_reset_failed", review_id=review_id, status=exc.status)


async def _refuse(ctx: Ctx, review: repo.Review, outcome: repo.Outcome) -> None:
    number = _number(ctx, review)
    if outcome is repo.Outcome.ALREADY:
        await ctx.respond(views.already_reviewed_view(ctx.app.ui, order_number=number), ephemeral=True)
    elif outcome in (repo.Outcome.CLOSED, repo.Outcome.EXPIRED):
        await _replace_request(ctx, views.expired_view(ctx.app.ui, order_number=number))
    elif outcome is repo.Outcome.FORBIDDEN:
        raise HandlerError(ck.ERROR_PERMISSION)
    elif outcome is repo.Outcome.NOT_RATED:
        raise HandlerError(ck.ERROR_INVALID_INPUT)
    else:
        raise HandlerError(ck.ERROR_NOT_FOUND)


def _rating(ctx: Ctx, scale: int) -> int:
    if len(ctx.values) != 1:
        raise HandlerError(ck.ERROR_INVALID_INPUT)
    try:
        rating = int(ctx.values[0])
    except ValueError:
        raise HandlerError(ck.ERROR_INVALID_INPUT) from None
    if not 1 <= rating <= scale:
        raise HandlerError(ck.ERROR_INVALID_INPUT)
    return rating


async def _finish(ctx: Ctx, review: repo.Review, thanks: MessageSpec) -> None:
    """The review is submitted: show the thank-you text, then publish it or ask staff for approval (retried by a
    task on failure).

    The answer goes first (Discord expects it within 3 seconds); the next step runs even if the answer failed.
    """
    try:
        await _replace_request(ctx, thanks)
    finally:
        await service.after_submission(ctx.app, review)


async def on_rate(ctx: Ctx) -> None:
    app = ctx.app
    settings = app.settings.reviews
    review = await _load(ctx)
    rating = _rating(ctx, settings.rating_scale)
    number = _number(ctx, review)
    modal = views.comment_modal(app.ui, settings, review_id=review.id) if settings.allow_comment else None
    if modal is not None:
        check_modal(modal)  # a form Discord would reject must not leave a half-done rating behind
    thanks = views.thanks_view(app.ui, order_number=number, pending_approval=settings.require_approval)
    result = await app.db.transaction(
        repo.rate,
        review.id,
        user_id=ctx.user_id,
        rating=rating,
        submit=modal is None,
        now=app.clock.now(),
        approval_required=settings.require_approval,
    )
    if result.outcome is not repo.Outcome.OK or result.review is None:
        await _refuse(ctx, review, result.outcome)
        return
    if modal is not None:
        await ctx.send_modal(modal)
        # The form can be closed or its comment refused: the menu must not keep showing this rating.
        await _reset_request(ctx, review.id, number)
        return
    await _finish(ctx, result.review, thanks)


async def _refuse_comment(ctx: Ctx, review: repo.Review, order_number: str) -> NoReturn:
    """The submitted comment is not accepted: reset the request's rating menu, then answer with the error."""
    await _reset_request(ctx, review.id, order_number)
    raise HandlerError(ck.ERROR_INVALID_INPUT)


async def on_comment(ctx: Ctx) -> None:
    app = ctx.app
    settings = app.settings.reviews
    review = await _load(ctx)
    if review.finished:
        await _refuse(ctx, review, repo.Outcome.ALREADY)
        return
    if review.status == repo.DECLINED:
        await _refuse(ctx, review, repo.Outcome.CLOSED)
        return
    number = _number(ctx, review)
    comment = ctx.fields.get(views.COMMENT_INPUT_ID, "").strip() if settings.allow_comment else ""
    if settings.comment_required and not comment:
        await _refuse_comment(ctx, review, number)
    if len(comment) > settings.comment_max_length:
        await _refuse_comment(ctx, review, number)
    thanks = views.thanks_view(app.ui, order_number=number, pending_approval=settings.require_approval)
    if review.rating is not None and 1 <= review.rating <= settings.rating_scale:
        # Refuse a comment the published review (or the staff approval message) could not show (Discord limits
        # after escaping), so it can be shortened now instead of failing later.
        name = ctx.interaction.user.display_name
        candidates = [
            views.published_view(app.ui, rating=review.rating, user_name=name, order_number=number, comment=comment)
        ]
        if settings.require_approval:
            candidates.append(
                views.approval_view(
                    app.ui,
                    review_id=review.id,
                    state=k.STATE_PENDING,
                    rating=review.rating,
                    user_name=name,
                    user_id=review.user_id,
                    order_number=number,
                    comment=comment,
                )
            )
        if not all(service.check_publishable(candidate) for candidate in candidates):
            await _refuse_comment(ctx, review, number)
    result = await app.db.transaction(
        repo.submit_comment,
        review.id,
        user_id=ctx.user_id,
        comment=comment,
        now=app.clock.now(),
        approval_required=settings.require_approval,
    )
    if result.outcome is not repo.Outcome.OK or result.review is None:
        await _refuse(ctx, review, result.outcome)
        return
    await _finish(ctx, result.review, thanks)


async def on_decline(ctx: Ctx) -> None:
    app = ctx.app
    review = await _load(ctx)
    declined = views.declined_view(app.ui, order_number=_number(ctx, review))
    result = await app.db.transaction(repo.decline, review.id, user_id=ctx.user_id, now=app.clock.now())
    if result.outcome is repo.Outcome.CLOSED:  # declined before (e.g. a second click): same answer
        await _replace_request(ctx, declined)
        return
    if result.outcome is not repo.Outcome.OK:
        await _refuse(ctx, review, result.outcome)
        return
    try:
        await _replace_request(ctx, declined)
    finally:
        await app.audit.record(
            "reviews.declined",
            actor_id=ctx.user_id,
            target_type="review",
            target_id=review.id,
            details={"order_number": review.order_number},
        )


# -- staff approval -------------------------------------------------------------------------------------------


async def _load_for_staff(ctx: Ctx) -> repo.Review:
    """The review of a staff control, after the server-side staff check (the router checked too)."""
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(ck.ERROR_PERMISSION)
    review = await service.get_review(ctx.app, ctx.arg_int(0))
    if review is None or review.rating is None or not (review.finished or review.status == repo.DECLINED):
        raise HandlerError(ck.ERROR_NOT_FOUND)
    return review


async def _staff_reply(ctx: Ctx, result: service.StaffResult) -> None:
    app = ctx.app
    spec = views.operator_reply(app.ui, app.op("reviews.decision.title"), result.lines, tone=result.tone)
    await ctx.respond(spec, ephemeral=True)


async def _show_current(ctx: Ctx, review: repo.Review) -> None:
    """Best effort: the clicked approval message shows the review's current state (e.g. decided by someone else)."""
    if ctx.interaction.message is None:
        return
    try:
        spec = await service.approval_spec(ctx.app, review)
        check_message(spec)
        await ctx.update(spec)
    except (MissingContent, RenderError, discord.HTTPException) as exc:
        log_event(log, logging.INFO, "reviews_approval_show_failed", review_id=review.id, error=type(exc).__name__)


async def _not_pending(ctx: Ctx, review: repo.Review) -> None:
    app = ctx.app
    status = service.status_label(app, review)
    text = app.op("reviews.decision.not_pending", order_number=_number(ctx, review), status=status)
    await _show_current(ctx, review)
    await _staff_reply(ctx, service.StaffResult([text], "warning"))


async def _decide(ctx: Ctx, review: repo.Review, *, approve: bool, reason: str | None) -> None:
    """Apply the decision; the clicked message shows it at once, the operator reply follows privately."""
    await ctx.defer_update()
    applied = False

    async def show(decided: repo.Review) -> int | None:
        nonlocal applied
        applied = True
        if ctx.interaction.message is None:
            return None
        spec = await service.approval_spec(ctx.app, decided)
        check_message(spec)
        await ctx.update(spec)
        return ctx.message_id

    result = await service.staff_decide(
        ctx.app, review, approve=approve, actor_id=ctx.user_id, reason=reason, on_decided=show
    )
    if not applied:  # e.g. decided by someone else meanwhile: show the current state on the clicked message
        current = await service.get_review(ctx.app, review.id)
        if current is not None:
            await _show_current(ctx, current)
    await _staff_reply(ctx, result)


async def on_approve(ctx: Ctx) -> None:
    review = await _load_for_staff(ctx)
    if not review.pending_approval:
        await _not_pending(ctx, review)
        return
    await _decide(ctx, review, approve=True, reason=None)


async def on_reject(ctx: Ctx) -> None:
    review = await _load_for_staff(ctx)
    if not review.pending_approval:
        await _not_pending(ctx, review)
        return
    modal = views.reject_modal(
        ctx.app.op, review_id=review.id, order_number=_number(ctx, review), max_length=repo.REASON_MAX
    )
    await ctx.send_modal(modal)


async def on_rejected(ctx: Ctx) -> None:
    app = ctx.app
    review = await _load_for_staff(ctx)
    reason = ctx.fields.get(views.REASON_INPUT_ID, "").strip()
    if len(reason) > repo.REASON_MAX:
        text = app.op("reviews.decision.reason_too_long", length=len(reason), max=repo.REASON_MAX)
        await _staff_reply(ctx, service.StaffResult([text], "warning"))
        return
    if not review.pending_approval:
        await _not_pending(ctx, review)
        return
    await _decide(ctx, review, approve=False, reason=reason or None)


def register_handlers(app: App) -> None:
    router = app.router
    router.component(views.ROUTE, "rate", feature=REVIEWS, allow_dm=True)(on_rate)
    router.component(views.ROUTE, "decline", feature=REVIEWS, allow_dm=True)(on_decline)
    router.modal(views.ROUTE, "comment", feature=REVIEWS, allow_dm=True)(on_comment)
    staff = staff_role_keys
    router.component(views.ROUTE, "approve", feature=REVIEWS, access="staff", staff_roles=staff)(on_approve)
    router.component(views.ROUTE, "reject", feature=REVIEWS, access="staff", staff_roles=staff)(on_reject)
    router.modal(views.ROUTE, "rejected", feature=REVIEWS, access="staff", staff_roles=staff)(on_rejected)
