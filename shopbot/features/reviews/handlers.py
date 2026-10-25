"""Component and form handlers (route ``rv``). Every route works in direct messages (``allow_dm``) as well as in
the order channel, and only for the customer the review belongs to.

- ``sb:rv:rate:<review_id>`` (rating select): records the rating, then opens the comment form
  (``reviews.allow_comment``) or finishes and publishes the review right away.
- ``sb:rv:comment:<review_id>`` (comment form): saves the comment, replaces the request with the thank-you text
  and publishes the review (a failed publication is retried by a task; the review is never lost).
- ``sb:rv:decline:<review_id>`` (button): the customer declines; the request shows the declined text.

Each handler re-loads the review by ID, checks the server, the customer and the state, and changes state only with
the repository's conditional updates, so duplicate or concurrent clicks never apply twice. Replies are built before
any state change: if text is missing, nothing changes and the router answers with the 'unavailable' message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features import core_keys as ck
from shopbot.features.reviews import repo, service, views
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.ui.discord_render import check_modal
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App


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
    """The review is submitted: show the thank-you text, then publish (retried by a task on failure).

    The answer goes first (Discord expects it within 3 seconds); publication runs even if the answer failed.
    """
    try:
        await _replace_request(ctx, thanks)
    finally:
        await service.publish_or_retry(ctx.app, review.id)


async def on_rate(ctx: Ctx) -> None:
    app = ctx.app
    settings = app.settings.reviews
    review = await _load(ctx)
    rating = _rating(ctx, settings.rating_scale)
    number = _number(ctx, review)
    modal = views.comment_modal(app.ui, settings, review_id=review.id) if settings.allow_comment else None
    if modal is not None:
        check_modal(modal)  # a form Discord would reject must not leave a half-done rating behind
    thanks = views.thanks_view(app.ui, order_number=number)
    result = await app.db.transaction(
        repo.rate, review.id, user_id=ctx.user_id, rating=rating, submit=modal is None, now=app.clock.now()
    )
    if result.outcome is not repo.Outcome.OK or result.review is None:
        await _refuse(ctx, review, result.outcome)
        return
    if modal is not None:
        await ctx.send_modal(modal)
        return
    await _finish(ctx, result.review, thanks)


async def on_comment(ctx: Ctx) -> None:
    app = ctx.app
    settings = app.settings.reviews
    review = await _load(ctx)
    if review.status == repo.PUBLISHED or review.submitted:
        await _refuse(ctx, review, repo.Outcome.ALREADY)
        return
    if review.status == repo.DECLINED:
        await _refuse(ctx, review, repo.Outcome.CLOSED)
        return
    comment = ctx.fields.get(views.COMMENT_INPUT_ID, "").strip() if settings.allow_comment else ""
    if settings.comment_required and not comment:
        raise HandlerError(ck.ERROR_INVALID_INPUT)
    if len(comment) > settings.comment_max_length:
        raise HandlerError(ck.ERROR_INVALID_INPUT)
    number = _number(ctx, review)
    thanks = views.thanks_view(app.ui, order_number=number)
    if review.rating is not None and 1 <= review.rating <= settings.rating_scale:
        # Refuse a comment the published review could not show (Discord limits after escaping), so it can be
        # shortened now instead of failing later.
        candidate = views.published_view(
            app.ui,
            rating=review.rating,
            user_name=ctx.interaction.user.display_name,
            order_number=number,
            comment=comment,
        )
        if not service.check_publishable(candidate):
            raise HandlerError(ck.ERROR_INVALID_INPUT)
    result = await app.db.transaction(
        repo.submit_comment, review.id, user_id=ctx.user_id, comment=comment, now=app.clock.now()
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


def register_handlers(app: App) -> None:
    router = app.router
    router.component(views.ROUTE, "rate", feature=REVIEWS, allow_dm=True)(on_rate)
    router.component(views.ROUTE, "decline", feature=REVIEWS, allow_dm=True)(on_decline)
    router.modal(views.ROUTE, "comment", feature=REVIEWS, allow_dm=True)(on_comment)
