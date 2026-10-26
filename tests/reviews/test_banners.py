"""Banners on review messages: the request is one message whose banner follows its state (request, thanks,
declined, expired), the published review and the staff replies show theirs, and an edit with an unchanged banner
never uploads it again."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import views
from tests.fakes import FakeInteraction, FakeMessage, click, submit_modal
from tests.helpers import TestEnv, banner_in_card, banner_names, make_banner_env, provision_channels, uploaded
from tests.reviews.util import invoke, make_order, prompt_now, prompted, review_of, reviews_channel, set_order_status


async def banner_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    """The reviews test environment (every channel provisioned) with banners on."""
    tenv = await make_banner_env(tmp_path, overrides, **kwargs)
    await provision_channels(tenv)
    return tenv


@pytest.fixture
async def bn(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None
    return banner.filename


def uploaded_banners(edit: dict[str, Any]) -> list[str]:
    """Banner files an edit uploads again (a kept attachment is not an upload)."""
    return [
        a.filename
        for a in edit.get("attachments") or []
        if isinstance(a, discord.File) and a.filename.startswith("banner-")
    ]


def reply(interaction: FakeInteraction) -> FakeMessage:
    assert interaction.last_sent is not None
    return interaction.last_sent


async def rate(tenv: TestEnv, user: Any, review_id: int, message: FakeMessage | None, value: str) -> FakeInteraction:
    return await click(
        tenv.app, user=user, custom_id=views.rate_id(review_id), message=message, values=[value], dm=True
    )


async def comment(tenv: TestEnv, user: Any, review_id: int, message: FakeMessage, text: str) -> FakeInteraction:
    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.comment_id(review_id),
        fields={views.COMMENT_INPUT_ID: text},
        message=message,
        dm=True,
    )


async def test_the_request_swaps_its_banner_to_thanks_and_the_review_is_published_with_one(bn: TestEnv) -> None:
    customer = bn.member(name="Ada")
    _, review, dm = await prompted(bn, customer)
    assert banner_names(dm) == [name(bn, "reviews")]

    await rate(bn, customer, review.id, dm, "4")  # opens the comment form; the menu is re-rendered (view only)
    assert dm.edits[-1].keys() == {"view"} and banner_names(dm) == [name(bn, "reviews")]

    await comment(bn, customer, review.id, dm, "[great]")
    assert banner_names(dm) == [name(bn, "successful-confirmation")] and len(dm.attachments) == 1
    assert banner_in_card(dm) == name(bn, "successful-confirmation")
    assert dm.embeds[0].description == bn.text(k.THANKS).replace("{order_number}", str(review.order_number))
    (post,) = reviews_channel(bn).messages
    assert banner_names(post) == [name(bn, "reviews")] and banner_in_card(post) == name(bn, "reviews")

    again = await rate(bn, customer, review.id, None, "5")  # an old copy of the request
    assert banner_names(reply(again)) == [name(bn, "general-response")]  # already reviewed


async def test_declining_shows_the_general_response_banner_and_a_second_click_keeps_it(bn: TestEnv) -> None:
    customer = bn.member()
    _, review, dm = await prompted(bn, customer)
    await click(bn.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
    assert banner_names(dm) == [name(bn, "general-response")] and len(dm.attachments) == 1
    await click(bn.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
    assert uploaded(dm.edits[-1]) == []  # the same banner: not uploaded again
    assert banner_names(dm) == [name(bn, "general-response")]


async def test_a_request_that_can_no_longer_be_answered_shows_the_cancellation_banner(bn: TestEnv) -> None:
    customer = bn.member()
    order, review, dm = await prompted(bn, customer)
    await set_order_status(bn, order.id, "refunded")
    await rate(bn, customer, review.id, dm, "5")
    assert banner_names(dm) == [name(bn, "cancellation")] and len(dm.attachments) == 1


async def test_removing_an_open_channel_request_edits_its_banner(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        staff, customer = tenv.member(staff=True), tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None
        (request,) = order.channel.messages
        assert banner_names(request) == [name(tenv, "reviews")]
        removed = await invoke(tenv, staff, "remove", order_number=order.number)
        assert banner_names(reply(removed)) == [name(tenv, "general-response")]
        assert banner_names(request) == [name(tenv, "cancellation")] and len(request.attachments) == 1
        assert (await review_of(tenv, order)).status == "declined"
    finally:
        await tenv.close()


async def test_staff_commands_show_the_banner_of_their_outcome(bn: TestEnv) -> None:
    staff = bn.member(staff=True)
    listed = await invoke(bn, staff, "list")
    assert banner_names(reply(listed)) == [name(bn, "staff-dashboard")]
    unknown = await invoke(bn, staff, "remove", order_number=4242)
    assert banner_names(reply(unknown)) == [name(bn, "error")]
    order, _, _ = await prompted(bn, bn.member())
    resent = await invoke(bn, staff, "resend", order_number=order.number)
    assert banner_names(reply(resent)) == [name(bn, "general-response")]


async def test_commands_of_unready_reviews_answer_with_the_error_banner(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, content={k.THANKS: ""})
    try:
        answered = await invoke(tenv, tenv.member(staff=True), "list")
        assert reply(answered).embeds[0].title == tenv.app.op("command.feature_disabled", feature="reviews")
        assert banner_names(reply(answered)) == [name(tenv, "error")]
    finally:
        await tenv.close()
