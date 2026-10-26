"""Publishing submitted reviews: failures, retries (task ``reviews.publish``), restarts and the /setup hook."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import aiohttp
import discord

from shopbot.db.repos import state
from shopbot.features.reviews import MODULE, repo, service, views
from shopbot.features.reviews import content_keys as k
from tests.fakes import FakeMessage, FakeTextChannel, FakeUser, click, http_error, submit_modal
from tests.helpers import TestEnv, make_test_env, provision_channels
from tests.reviews.util import (
    audit_actions,
    make_env,
    make_order,
    number_text,
    only_embed,
    prompt_now,
    prompted,
    rendered,
    restarted_app,
    review_of,
    reviews_channel,
    task,
)


async def _submit(tenv: TestEnv, user: Any, review: repo.Review, message: Any, rating: str = "4") -> None:
    await click(tenv.app, user=user, custom_id=views.rate_id(review.id), message=message, values=[rating], dm=True)
    await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: "[kept]"},
        message=message,
        dm=True,
    )


async def test_missing_reviews_channel_is_retried_until_published(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)  # no channels created yet
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer, with_channel=False)
        await _submit(tenv, customer, review, dm)
        # the customer sees the thank-you text: the review is saved and waits for publication
        assert only_embed(dm).description == rendered(tenv, k.THANKS, order_number=number_text(tenv, order.number))
        pending = await review_of(tenv, order)
        assert pending.submitted and pending.status == repo.RATED and pending.comment == "[kept]"
        retry = await task(tenv, service.publish_dedupe(review.id))
        assert retry is not None and retry.status == "pending" and retry.payload == {"review_id": review.id}
        # still no channel: the scheduler retries with backoff
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        retry = await task(tenv, service.publish_dedupe(review.id))
        assert retry is not None and retry.status == "pending" and retry.attempts == 1
        # the channel is created (/setup): the next attempt publishes
        await provision_channels(tenv)
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        (post,) = reviews_channel(tenv).messages
        assert only_embed(post).description == "> [kept]"
        published = await review_of(tenv, order)
        assert (published.status, published.published_message_id) == (repo.PUBLISHED, post.id)
        done = await task(tenv, service.publish_dedupe(review.id))
        assert done is not None and done.status == "done"
    finally:
        await tenv.close()


async def test_forbidden_channel_then_recovery(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    await _submit(rv, customer, review, dm)
    assert channel.messages == []
    assert (await review_of(rv, order)).submitted
    channel.fail_send = None
    rv.clock.advance(3600)
    await rv.app.scheduler.run_due()
    assert len(channel.messages) == 1
    assert (await review_of(rv, order)).status == repo.PUBLISHED


async def test_publication_is_never_abandoned(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.HTTPException, 500, 0, "Internal Server Error")
    await _submit(rv, customer, review, dm)
    for _ in range(rv.app.scheduler.max_attempts):
        rv.clock.advance(3600)
        await rv.app.scheduler.run_due()
    slow = await task(rv, service.publish_dedupe(review.id))
    assert slow is not None and slow.status == "pending" and slow.attempts == 0
    assert slow.run_at == rv.clock.now() + service.PUBLISH_SLOW_RETRY_SECONDS
    channel.fail_send = None
    rv.clock.advance(service.PUBLISH_SLOW_RETRY_SECONDS)
    await rv.app.scheduler.run_due()
    assert len(channel.messages) == 1
    assert (await review_of(rv, order)).status == repo.PUBLISHED


async def test_restart_requeues_pending_publications(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.HTTPException, 503, 0, "Unavailable")
    await _submit(rv, customer, review, dm)

    # the retry task was lost/failed (e.g. exhausted before an upgrade); a restart re-queues the review
    def fail_task(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE kind = 'reviews.publish'")

    await rv.app.db.transaction(fail_task)
    channel.fail_send = None
    app = await restarted_app(rv)
    requeued = await task(rv, service.publish_dedupe(review.id), app)
    assert requeued is not None and requeued.status == "pending" and requeued.run_at <= rv.clock.now()
    await app.scheduler.run_due()
    assert len(channel.messages) == 1
    assert (await review_of(rv, order, app)).status == repo.PUBLISHED


async def test_setup_publish_hook_requeues(rv: TestEnv) -> None:
    assert MODULE.publish is not None
    channel = reviews_channel(rv)
    # /setup also keeps the intro panel at the top of the reviews channel (tests/reviews/test_intro.py)
    assert await MODULE.publish(rv.app) == [rv.app.op("reviews.intro.created", channel=channel.mention)]
    unchanged = rv.app.op("reviews.intro.unchanged", channel=channel.mention)
    customer = rv.member()
    _order, review, dm = await prompted(rv, customer)
    channel.fail_send = http_error(discord.Forbidden, 403, 50001, "Missing Access")
    await _submit(rv, customer, review, dm)
    channel.fail_send = None
    lines = await MODULE.publish(rv.app)
    assert lines == [unchanged, rv.app.op("reviews.setup.requeued", count=1)]
    await rv.app.scheduler.run_due()
    assert len(channel.messages) == 2  # the intro panel, then the review
    assert await MODULE.publish(rv.app) == [unchanged]


async def test_concurrent_publication_posts_once(rv: TestEnv) -> None:
    customer = rv.member()
    _order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
    await _submit(rv, customer, review, dm)
    channel.fail_send = None
    outcomes = await asyncio.gather(*(service.publish_review(rv.app, review.id) for _ in range(5)))
    assert sorted(outcomes) == sorted([service.Publish.PUBLISHED] + [service.Publish.SKIPPED] * 4)
    await rv.app.scheduler.run_due()  # the pending retry finds nothing left to do
    assert len(channel.messages) == 1
    assert [e.action for e in await audit_actions(rv)].count("reviews.published") == 1


async def test_publish_skips_unsubmitted_reviews(rv: TestEnv) -> None:
    customer = rv.member()
    _order, review, dm = await prompted(rv, customer)
    assert await service.publish_review(rv.app, review.id) is service.Publish.SKIPPED
    await click(rv.app, user=customer, custom_id=views.rate_id(review.id), message=dm, values=["2"], dm=True)
    assert await service.publish_review(rv.app, review.id) is service.Publish.SKIPPED  # comment form pending
    assert await service.publish_review(rv.app, 12345) is service.Publish.SKIPPED
    assert reviews_channel(rv).messages == []


async def test_published_review_never_names_the_reviewer(rv: TestEnv) -> None:
    """Neither a member nor someone who left is named, mentioned or shown on their published review."""
    member = rv.guild.add_member(name="ada_lovelace")
    gone = FakeUser(name="gone_user")
    rv.bot.extra_users[gone.id] = gone
    for user in (member, gone):
        order = await make_order(rv, user)
        await prompt_now(rv, order)
        review = await review_of(rv, order)
        await _submit(rv, user, review, user.dms[-1], rating="5")
        post = reviews_channel(rv).messages[-1]
        card = only_embed(post)
        assert card.title == rendered(rv, k.PUBLISHED_TITLE, stars="★★★★★", rating=5, scale=5)
        shown = post.text() + post.content
        for name in (user.name, user.display_name, str(user.id)):
            assert name not in shown and name.replace("_", "\\_") not in shown
        assert card.author.icon_url is None or str(user.id) not in card.author.icon_url
        assert post.allowed_mentions is not None and post.allowed_mentions.users is False


async def test_invalid_task_payloads_fail_permanently(rv: TestEnv) -> None:
    for kind, dedupe in ((service.PROMPT_TASK, "reviews.prompt:bad"), (service.PUBLISH_TASK, "reviews.publish:bad")):
        await rv.app.scheduler.schedule(kind, run_at=rv.clock.now(), payload={}, dedupe_key=dedupe)
    await rv.app.scheduler.run_due()
    for dedupe in ("reviews.prompt:bad", "reviews.publish:bad"):
        failed = await task(rv, dedupe)
        assert failed is not None and failed.status == "failed" and failed.attempts == 1


async def test_restart_with_reviews_disabled_requeues_nothing(rv: TestEnv) -> None:
    customer = rv.member()
    _order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
    await _submit(rv, customer, review, dm)

    def fail_task(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE kind = 'reviews.publish'")

    await rv.app.db.transaction(fail_task)
    settings = rv.app.settings
    rv.app.settings = replace(settings, features=replace(settings.features, reviews=False))
    rv.app.recompute_readiness()
    assert MODULE.on_ready is not None
    await MODULE.on_ready(rv.app)
    still_failed = await task(rv, service.publish_dedupe(review.id))
    assert still_failed is not None and still_failed.status == "failed"
    assert (await review_of(rv, _order)).submitted  # kept for when reviews are enabled again


async def test_comment_after_declining_is_refused(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    await click(rv.app, user=customer, custom_id=views.rate_id(review.id), message=dm, values=["3"], dm=True)
    await click(rv.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
    await submit_modal(
        rv.app,
        user=customer,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: "[too late]"},
        message=dm,
        dm=True,
    )
    assert only_embed(dm).description == rendered(rv, k.EXPIRED, order_number=number_text(rv, order.number))
    assert (await review_of(rv, order)).status == repo.DECLINED
    assert reviews_channel(rv).messages == []


# --------------------------------------------------------------------------------------------------
# A post whose message was never recorded (lost answer, or a stop between the post and the record)
# --------------------------------------------------------------------------------------------------


def _lose_answers(channel: FakeTextChannel, *, post: bool = True, times: int = 1) -> None:
    """The next ``times`` sends fail like a connection dropped mid-request: with ``post`` Discord still created the
    message (only the answer was lost); without it, nothing was created."""
    send = channel.send
    left = [times]

    async def flaky(content: str | None = None, **kwargs: Any) -> FakeMessage:
        if left[0] <= 0:
            return await send(content, **kwargs)
        left[0] -= 1
        if post:
            await send(content, **kwargs)
        raise aiohttp.ServerDisconnectedError()

    channel.send = flaky  # type: ignore[method-assign]


async def _claim(tenv: TestEnv, review_id: int) -> str | None:
    return await tenv.app.db.run(state.kv_get, repo.publish_claim_key(review_id))


async def test_lost_answer_is_adopted_instead_of_posting_twice(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    _lose_answers(channel)
    await _submit(rv, customer, review, dm)
    (post,) = channel.messages  # Discord created the message, but the bot never got its id
    pending = await review_of(rv, order)
    assert pending.submitted and pending.published_message_id is None
    assert await _claim(rv, review.id) == str(channel.id)
    rv.clock.advance(3600)
    await rv.app.scheduler.run_due()  # the retry finds the post instead of posting again
    assert channel.messages == [post]
    published = await review_of(rv, order)
    assert (published.status, published.published_message_id) == (repo.PUBLISHED, post.id)
    assert await _claim(rv, review.id) is None
    done = await task(rv, service.publish_dedupe(review.id))
    assert done is not None and done.status == "done"
    assert [e.action for e in await audit_actions(rv)].count("reviews.published") == 1


async def test_restart_after_an_unrecorded_post_adopts_it(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    _lose_answers(channel)
    await _submit(rv, customer, review, dm)

    # the process stopped before anything was recorded or retried
    def lose_retry(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE kind = 'reviews.publish'")

    await rv.app.db.transaction(lose_retry)
    (post,) = channel.messages
    app = await restarted_app(rv)
    await app.scheduler.run_due()
    assert channel.messages == [post]
    published = await review_of(rv, order, app)
    assert (published.status, published.published_message_id) == (repo.PUBLISHED, post.id)
    assert await _claim(rv, review.id) is None


async def test_retry_posts_when_the_lost_request_created_nothing(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    _lose_answers(channel, post=False)
    await _submit(rv, customer, review, dm)
    assert channel.messages == [] and await _claim(rv, review.id) is not None
    rv.clock.advance(3600)
    await rv.app.scheduler.run_due()
    (post,) = channel.messages
    published = await review_of(rv, order)
    assert (published.status, published.published_message_id) == (repo.PUBLISHED, post.id)
    assert await _claim(rv, review.id) is None


async def test_identical_post_of_another_review_is_never_adopted(tmp_path: Path) -> None:
    # a title without placeholders and no comments: two reviews with the same rating look exactly alike
    tenv = await make_env(tmp_path, {"reviews": {"allow_comment": False}}, content={k.PUBLISHED_TITLE: "[same]"})
    try:
        channel = reviews_channel(tenv)
        first_customer, second_customer = tenv.member(name="first"), tenv.member(name="second")
        first_order, first, first_dm = await prompted(tenv, first_customer)
        await click(
            tenv.app, user=first_customer, custom_id=views.rate_id(first.id), message=first_dm, values=["5"], dm=True
        )
        (first_post,) = channel.messages
        second_order, second, second_dm = await prompted(tenv, second_customer)
        _lose_answers(channel, post=False)
        await click(
            tenv.app, user=second_customer, custom_id=views.rate_id(second.id), message=second_dm, values=["5"], dm=True
        )
        assert channel.messages == [first_post]
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        assert len(channel.messages) == 2
        second_post = channel.messages[1]
        assert [e.to_dict().get("title") for e in second_post.embeds] == ["[same]"]
        assert (await review_of(tenv, first_order)).published_message_id == first_post.id
        assert (await review_of(tenv, second_order)).published_message_id == second_post.id
    finally:
        await tenv.close()


async def test_claimed_review_waits_while_the_channel_history_cannot_be_read(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    _lose_answers(channel)
    await _submit(rv, customer, review, dm)
    history = channel.history

    async def forbidden(**kwargs: Any) -> AsyncIterator[FakeMessage]:
        raise http_error(discord.Forbidden, 403, 50001, "Missing Access")
        yield  # pragma: no cover - makes this an async generator like the real history

    channel.history = forbidden  # type: ignore[method-assign]
    assert await service.publish_review(rv.app, review.id) is service.Publish.FAILED
    assert len(channel.messages) == 1 and (await review_of(rv, order)).submitted
    channel.history = history  # type: ignore[method-assign]
    assert await service.publish_review(rv.app, review.id) is service.Publish.PUBLISHED
    assert len(channel.messages) == 1
    assert (await review_of(rv, order)).published_message_id == channel.messages[0].id


async def test_removing_a_claimed_review_clears_its_claim(rv: TestEnv) -> None:
    customer = rv.member()
    order, review, dm = await prompted(rv, customer)
    _lose_answers(reviews_channel(rv), post=False)
    await _submit(rv, customer, review, dm)
    assert await _claim(rv, review.id) is not None
    await service.remove_review(rv.app, service.Target(order_number=order.number), actor_id=rv.member(staff=True).id)
    assert await _claim(rv, review.id) is None
    assert (await review_of(rv, order)).status == repo.DECLINED
