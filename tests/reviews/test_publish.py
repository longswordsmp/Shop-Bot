"""Publishing submitted reviews: failures, retries (task ``reviews.publish``), restarts and the /setup hook."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord

from shopbot.features.reviews import MODULE, repo, service, views
from shopbot.features.reviews import content_keys as k
from tests.fakes import FakeUser, click, http_error, submit_modal
from tests.helpers import TestEnv, make_test_env, provision_channels
from tests.reviews.util import (
    audit_actions,
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
        assert only_embed(post).description == "[kept]"
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
    assert await MODULE.publish(rv.app) == []
    customer = rv.member()
    _order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.Forbidden, 403, 50001, "Missing Access")
    await _submit(rv, customer, review, dm)
    channel.fail_send = None
    lines = await MODULE.publish(rv.app)
    assert lines == [rv.app.op("reviews.setup.requeued", count=1)]
    await rv.app.scheduler.run_due()
    assert len(channel.messages) == 1
    assert await MODULE.publish(rv.app) == []


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


async def test_reviewer_who_left_is_named_by_their_account(rv: TestEnv) -> None:
    user = FakeUser(name="gone_user")
    rv.bot.extra_users[user.id] = user
    order = await make_order(rv, user)
    await prompt_now(rv, order)
    (dm,) = user.dms
    review = await review_of(rv, order)
    await _submit(rv, user, review, dm, rating="5")
    (post,) = reviews_channel(rv).messages
    assert only_embed(post).title == rendered(
        rv, k.PUBLISHED_TITLE, rating=5, user_name="gone\\_user", order_number=number_text(rv, order.number)
    )


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
