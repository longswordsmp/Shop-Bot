"""The customer's side: rating, comment form, declining, duplicates and permissions (through the real router)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord

from shopbot.features import core_keys as ck
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, views
from shopbot.ui.routing import Ctx
from tests.fakes import FakeInteraction, FakeMessage, click, modal_input_ids, modal_of, submit_modal
from tests.helpers import TestEnv
from tests.reviews.util import (
    audit_actions,
    make_env,
    make_order,
    number_text,
    only_embed,
    prompt_now,
    prompted,
    published_title,
    rendered,
    reply_embed,
    restarted_app,
    review_of,
    reviews_channel,
    set_order_status,
)


async def _rate(
    tenv: TestEnv, user: Any, review: repo.Review, message: FakeMessage, value: str, **kw: Any
) -> FakeInteraction:
    return await click(
        tenv.app, user=user, custom_id=views.rate_id(review.id), message=message, values=[value], dm=True, **kw
    )


async def _comment(
    tenv: TestEnv, user: Any, review: repo.Review, message: FakeMessage | None, text: str, **kw: Any
) -> FakeInteraction:
    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: text},
        message=message,
        dm=True,
        **kw,
    )


# --------------------------------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------------------------------


async def test_rating_and_comment_end_to_end_in_direct_messages(rv: TestEnv) -> None:
    customer = rv.member(name="Ada")
    order, review, dm = await prompted(rv, customer)
    rated = await _rate(rv, customer, review, dm, "4")
    modal = modal_of(rated)
    assert modal is not None
    assert modal.title == rv.text(k.COMMENT_TITLE)
    assert modal.custom_id == views.comment_id(review.id)
    assert modal_input_ids(modal) == [views.COMMENT_INPUT_ID]
    text_input = next(i for i in modal.walk_children() if isinstance(i, discord.ui.TextInput))
    assert text_input.required is False and text_input.max_length == 1000
    assert text_input.style is discord.TextStyle.paragraph
    current = await review_of(rv, order)
    assert (current.status, current.rating, current.comment) == (repo.RATED, 4, None)

    submitted = await _comment(rv, customer, review, dm, "  Great *service* <@123> @everyone  ")
    number = number_text(rv, order.number)
    # the request is replaced by the thank-you text and loses its components
    assert submitted.response_kinds() == ["edit_message"]
    assert only_embed(dm).description == rendered(rv, k.THANKS, order_number=number)
    assert only_embed(dm).title is None and dm.view is None
    (post,) = reviews_channel(rv).messages
    card = only_embed(post)
    assert card.title == published_title(rv, rating=4, user_name="Ada", number=order.number)
    assert card.description == "Great \\*service\\* <@123> @everyone"
    assert [(f.name, f.value) for f in card.fields] == [
        (rv.text(k.PUBLISHED_RATING_FIELD), rv.text(k.rating_key(4, "display")))
    ]
    assert post.content == "" and post.view is None
    assert post.allowed_mentions is not None
    assert post.allowed_mentions.everyone is False and post.allowed_mentions.users is False
    final = await review_of(rv, order)
    assert (final.status, final.rating, final.comment, final.published_message_id) == (
        repo.PUBLISHED,
        4,
        "Great *service* <@123> @everyone",
        post.id,
    )
    assert [e.action for e in await audit_actions(rv)] == ["reviews.prompted", "reviews.published"]


async def test_empty_optional_comment_publishes_rating_only(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "5")
    await _comment(rv, customer, review, dm, "   ")
    (post,) = reviews_channel(rv).messages
    assert only_embed(post).description is None
    assert (await review_of(rv, order)).comment == ""


async def test_rating_publishes_immediately_without_comments(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"allow_comment": False}})
    try:
        customer = tenv.member(name="Bo")
        order, review, dm = await prompted(tenv, customer)
        rated = await _rate(tenv, customer, review, dm, "2")
        assert modal_of(rated) is None
        assert only_embed(dm).description == rendered(tenv, k.THANKS, order_number=number_text(tenv, order.number))
        assert dm.view is None
        (post,) = reviews_channel(tenv).messages
        assert only_embed(post).title == published_title(tenv, rating=2, user_name="Bo", number=order.number)
        assert only_embed(post).description is None
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
    finally:
        await tenv.close()


async def test_changing_the_rating_before_submitting_the_comment(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "1")  # form closed without submitting
    again = await _rate(rv, customer, review, dm, "5")
    assert modal_of(again) is not None
    assert (await review_of(rv, order)).rating == 5
    await _comment(rv, customer, review, dm, "[ok]")
    assert (await review_of(rv, order)).rating == 5


async def test_flow_in_the_order_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None
        (request,) = order.channel.messages
        review = await review_of(tenv, order)
        rated = await click(tenv.app, user=customer, custom_id=views.rate_id(review.id), message=request, values=["3"])
        assert modal_of(rated) is not None
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.comment_id(review.id),
            fields={views.COMMENT_INPUT_ID: "[fine]"},
            message=request,
        )
        assert only_embed(request).description == rendered(tenv, k.THANKS, order_number=number_text(tenv, order.number))
        assert request.view is None and request.content == ""  # the ping is not repeated
        assert len(reviews_channel(tenv).messages) == 1
    finally:
        await tenv.close()


async def test_form_submission_without_origin_message_answers_privately(rv: TestEnv) -> None:
    customer = rv.member()
    _, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "4")
    submitted = await _comment(rv, customer, review, None, "[no origin]")
    assert submitted.response_kinds() == ["send_message"]
    assert reply_embed(submitted).description == rendered(
        rv, k.THANKS, order_number=number_text(rv, review.order_number)
    )
    assert len(reviews_channel(rv).messages) == 1


# --------------------------------------------------------------------------------------------------
# Comment rules
# --------------------------------------------------------------------------------------------------


async def test_required_comment(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"comment_required": True, "comment_max_length": 20}})
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer)
        rated = await _rate(tenv, customer, review, dm, "3")
        modal = modal_of(rated)
        assert modal is not None
        text_input = next(i for i in modal.walk_children() if isinstance(i, discord.ui.TextInput))
        assert text_input.required is True and text_input.max_length == 20
        for bad in ("", "   ", "x" * 21):
            refused = await _comment(tenv, customer, review, dm, bad)
            assert reply_embed(refused).description == tenv.text(ck.ERROR_INVALID_INPUT)
        current = await review_of(tenv, order)
        assert (current.status, current.comment) == (repo.RATED, None)
        assert reviews_channel(tenv).messages == []
        await _comment(tenv, customer, review, dm, "x" * 20)
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
    finally:
        await tenv.close()


async def test_comment_that_cannot_be_shown_is_refused(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"comment_max_length": 4000}})
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer)
        await _rate(tenv, customer, review, dm, "3")
        refused = await _comment(tenv, customer, review, dm, "*" * 3000)  # 6000 characters once escaped
        assert reply_embed(refused).description == tenv.text(ck.ERROR_INVALID_INPUT)
        assert (await review_of(tenv, order)).comment is None
        await _comment(tenv, customer, review, dm, "a" * 4000)
        (post,) = reviews_channel(tenv).messages
        assert only_embed(post).description == "a" * 4000
    finally:
        await tenv.close()


async def test_invalid_rating_values(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    for value in ("0", "6", "x", "-1"):
        refused = await _rate(rv, customer, review, dm, value)
        assert reply_embed(refused).description == rv.text(ck.ERROR_INVALID_INPUT)
    assert (await review_of(rv, order)).status == repo.PROMPTED


# --------------------------------------------------------------------------------------------------
# Permissions and duplicates
# --------------------------------------------------------------------------------------------------


async def test_other_people_cannot_answer_someone_elses_review(rv: TestEnv) -> None:
    customer, stranger, staff = rv.member(), rv.member(name="stranger"), rv.member(name="staff", staff=True)
    order, review, dm = await prompted(rv, customer)
    for user in (stranger, staff):
        rate = await _rate(rv, user, review, dm, "1")
        assert reply_embed(rate).description == rv.text(ck.ERROR_PERMISSION)
        decline = await click(rv.app, user=user, custom_id=views.decline_id(review.id), message=dm, dm=True)
        assert reply_embed(decline).description == rv.text(ck.ERROR_PERMISSION)
        comment = await _comment(rv, user, review, dm, "[spam]")
        assert reply_embed(comment).description == rv.text(ck.ERROR_PERMISSION)
    assert (await review_of(rv, order)).status == repo.PROMPTED
    assert reviews_channel(rv).messages == []


async def test_unknown_or_forged_review_ids(rv: TestEnv) -> None:
    customer = rv.member()
    for custom_id in ("sb:rv:rate:999", "sb:rv:decline:999", "sb:rv:rate:abc"):
        interaction = await click(rv.app, user=customer, custom_id=custom_id, values=["1"], dm=True)
        assert reply_embed(interaction).description == rv.text(ck.ERROR_NOT_FOUND)


async def test_second_rating_or_comment_after_publishing(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "5")
    await _comment(rv, customer, review, dm, "[first]")
    number = number_text(rv, order.number)
    again = await _rate(rv, customer, review, dm, "1")
    assert reply_embed(again).description == rendered(rv, k.ALREADY_REVIEWED, order_number=number)
    assert again.last_sent is not None and again.last_sent.ephemeral  # type: ignore[attr-defined]
    comment = await _comment(rv, customer, review, dm, "[second]")
    assert reply_embed(comment).description == rendered(rv, k.ALREADY_REVIEWED, order_number=number)
    decline = await click(rv.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
    assert reply_embed(decline).description == rendered(rv, k.ALREADY_REVIEWED, order_number=number)
    final = await review_of(rv, order)
    assert (final.status, final.rating, final.comment) == (repo.PUBLISHED, 5, "[first]")
    assert len(reviews_channel(rv).messages) == 1


async def test_concurrent_submissions_publish_once(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "4")
    await asyncio.gather(*(_comment(rv, customer, review, dm, f"[c{i}]") for i in range(4)))
    assert len(reviews_channel(rv).messages) == 1
    assert (await review_of(rv, order)).status == repo.PUBLISHED


async def test_concurrent_handlers_never_double_apply(rv: TestEnv) -> None:
    """Bypass the router's duplicate-click guard: the database conditions alone keep one outcome."""
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "4")
    route = next(r for r in rv.app.router.routes() if r.route == views.ROUTE and r.action == "comment")

    async def submit(text: str) -> None:
        interaction = FakeInteraction(
            client=rv.app.bot, user=customer, message=dm, type=discord.InteractionType.modal_submit
        )
        ctx = Ctx(rv.app, interaction, route, (str(review.id),), fields={views.COMMENT_INPUT_ID: text})
        await route.handler(ctx)

    results = await asyncio.gather(*(submit(f"[c{i}]") for i in range(5)), return_exceptions=True)
    assert len(reviews_channel(rv).messages) == 1
    final = await review_of(rv, order)
    assert final.status == repo.PUBLISHED
    assert sum(1 for r in results if r is None) == 5  # losers answered "already reviewed"


async def test_concurrent_rate_and_decline_keep_one_outcome(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"allow_comment": False}})
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer)
        await asyncio.gather(
            _rate(tenv, customer, review, dm, "5"),
            click(tenv.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True),
        )
        final = await review_of(tenv, order)
        posts = reviews_channel(tenv).messages
        assert (final.status, len(posts)) in ((repo.PUBLISHED, 1), (repo.DECLINED, 0))
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Declining and expiry
# --------------------------------------------------------------------------------------------------


async def test_decline(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    number = number_text(rv, order.number)
    declined = await click(rv.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
    assert declined.response_kinds() == ["edit_message"]
    assert only_embed(dm).description == rendered(rv, k.DECLINED, order_number=number)
    assert dm.view is None
    assert (await review_of(rv, order)).status == repo.DECLINED
    # a second decline (stale copy) gives the same answer; a rating is no longer possible
    stale = FakeMessage(None)
    again = await click(rv.app, user=customer, custom_id=views.decline_id(review.id), message=stale, dm=True)
    assert again.response_kinds() == ["edit_message"]
    assert only_embed(stale).description == rendered(rv, k.DECLINED, order_number=number)
    late = FakeMessage(None)
    await _rate(rv, customer, review, late, "5")
    assert only_embed(late).description == rendered(rv, k.EXPIRED, order_number=number)
    assert reviews_channel(rv).messages == []
    assert [e.action for e in await audit_actions(rv)] == ["reviews.prompted", "reviews.declined"]


async def test_order_no_longer_fulfilled_expires_the_request(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await set_order_status(rv, order.id, "refunded")
    await _rate(rv, customer, review, dm, "5")
    assert only_embed(dm).description == rendered(rv, k.EXPIRED, order_number=number_text(rv, order.number))
    assert dm.view is None
    assert (await review_of(rv, order)).status == repo.PROMPTED
    # refunded after the rating, before the comment
    other = rv.member(name="other")
    order2, review2, dm2 = await prompted(rv, other)
    await _rate(rv, other, review2, dm2, "4")
    await set_order_status(rv, order2.id, "refunded")
    await _comment(rv, other, review2, dm2, "[late]")
    assert only_embed(dm2).description == rendered(rv, k.EXPIRED, order_number=number_text(rv, order2.number))
    assert reviews_channel(rv).messages == []


# --------------------------------------------------------------------------------------------------
# Readiness, missing content and restarts
# --------------------------------------------------------------------------------------------------


async def test_not_ready_answers_unavailable_without_changes(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    rv.app.content.replace(file_values={**rv.content, k.THANKS: ""})
    rv.app.recompute_readiness()
    refused = await _rate(rv, customer, review, dm, "5")
    assert reply_embed(refused).description == rv.text(ck.ERROR_UNAVAILABLE)
    assert (await review_of(rv, order)).status == repo.PROMPTED
    assert only_embed(dm).description == rendered(rv, k.PROMPT_DESCRIPTION, order_number=number_text(rv, order.number))
    # without even the unavailable text, the click is acknowledged silently: no invented wording
    rv.app.content.replace(file_values={**rv.content, k.THANKS: "", ck.ERROR_UNAVAILABLE: ""})
    rv.app.recompute_readiness()
    silent = await _rate(rv, customer, review, dm, "5")
    assert silent.sent == [] and silent.response_kinds() == ["defer"]


async def test_choice_survives_a_restart(rv: TestEnv) -> None:
    customer = rv.member(name="Cy")
    order, review, dm = await prompted(rv, customer)
    await _rate(rv, customer, review, dm, "3")
    app = await restarted_app(rv)
    submitted = await submit_modal(
        app,
        user=customer,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: "[after restart]"},
        message=dm,
        dm=True,
    )
    assert submitted.response_kinds() == ["edit_message"]
    (post,) = reviews_channel(rv).messages
    assert only_embed(post).title == published_title(rv, rating=3, user_name="Cy", number=order.number)
    assert (await review_of(rv, order, app)).status == repo.PUBLISHED
    # the request components still route after the restart
    again = await click(app, user=customer, custom_id=views.rate_id(review.id), message=dm, values=["1"], dm=True)
    assert reply_embed(again).description == rendered(
        rv, k.ALREADY_REVIEWED, order_number=number_text(rv, order.number)
    )
