"""Scheduling and delivering review requests (task ``reviews.prompt``)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import discord

from shopbot.db.repos import notifications as notif_repo
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, schedule_review_prompt, service, views
from tests.fakes import http_error
from tests.helpers import TestEnv
from tests.reviews.util import (
    audit_actions,
    custom_ids,
    make_env,
    make_order,
    mark_ticket_deleted,
    number_text,
    only_embed,
    prompt_now,
    rendered,
    review_count,
    review_of,
    select_of,
    set_order_status,
    task,
)

# --------------------------------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------------------------------


async def test_schedule_uses_the_configured_delay_and_is_deduplicated(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_delay_minutes": 30}})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await schedule_review_prompt(tenv.app, order.id)
        await schedule_review_prompt(tenv.app, order.id)  # e.g. fulfilled twice: still one task
        scheduled = await task(tenv, service.prompt_dedupe(order.id))
        assert scheduled is not None and scheduled.kind == service.PROMPT_TASK
        assert scheduled.run_at == tenv.clock.now() + 30 * 60
        assert scheduled.payload == {"order_id": order.id}
        assert await tenv.app.scheduler.run_due() == 0  # not due yet
        assert customer.dms == []
        tenv.clock.advance(30 * 60)
        assert await tenv.app.scheduler.run_due() == 1
        assert len(customer.dms) == 1
    finally:
        await tenv.close()


async def test_schedule_is_a_noop_when_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.THANKS: ""})
    try:
        assert not tenv.app.ready(k.REVIEWS)
        order = await make_order(tenv, tenv.member())
        await schedule_review_prompt(tenv.app, order.id)
        assert await task(tenv, service.prompt_dedupe(order.id)) is None
    finally:
        await tenv.close()


async def test_schedule_never_raises(rv: TestEnv) -> None:
    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    rv.app.scheduler.schedule = broken  # type: ignore[method-assign]
    await schedule_review_prompt(rv.app, 1)  # logged, not raised


# --------------------------------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------------------------------


async def test_prompt_is_sent_by_direct_message(rv: TestEnv) -> None:
    customer = rv.member()
    order = await make_order(rv, customer)
    await prompt_now(rv, order)
    review = await review_of(rv, order)
    assert (review.status, review.user_id, review.rating, review.comment) == (repo.PROMPTED, customer.id, None, None)
    assert review.prompt_message_id is None  # direct-message ids are not known
    (dm,) = customer.dms
    number = number_text(rv, order.number)
    embed = only_embed(dm)
    assert embed.title == rendered(rv, k.PROMPT_TITLE, order_number=number)
    assert embed.description == rendered(rv, k.PROMPT_DESCRIPTION, order_number=number)
    select = select_of(dm)
    assert select.custom_id == views.rate_id(review.id)
    assert select.placeholder == rv.text(k.PROMPT_SELECT_PLACEHOLDER)
    assert [o.value for o in select.options] == ["5", "4", "3", "2", "1"]
    assert [o.label for o in select.options] == [rv.text(k.rating_key(n, "label")) for n in (5, 4, 3, 2, 1)]
    assert custom_ids(dm) == [views.rate_id(review.id), views.decline_id(review.id)]
    assert order.channel is not None and order.channel.messages == []  # not posted in the order channel
    assert await rv.app.db.run(notif_repo.already_sent, service.prompt_dedupe(order.id))
    (event,) = await audit_actions(rv)
    assert event.action == "reviews.prompted" and event.details["delivery"] == "dm"


async def test_prompt_task_is_idempotent(rv: TestEnv) -> None:
    customer = rv.member()
    order = await make_order(rv, customer)
    await prompt_now(rv, order)
    # the task runs again (e.g. the process stopped before it was marked done) and is scheduled again
    assert await service.send_prompt_for_order(rv.app, order.id) is not None
    await rv.app.scheduler.schedule(
        service.PROMPT_TASK,
        run_at=rv.clock.now(),
        payload={"order_id": order.id},
        dedupe_key=service.prompt_dedupe(order.id),
        replace=True,
    )
    await rv.app.scheduler.run_due()
    await asyncio.gather(*(service.send_prompt_for_order(rv.app, order.id) for _ in range(3)))
    assert len(customer.dms) == 1
    assert await review_count(rv) == 1
    assert [e.action for e in await audit_actions(rv)] == ["reviews.prompted"]


async def test_dm_blocked_falls_back_to_order_channel(rv: TestEnv) -> None:
    customer = rv.member()
    customer.dm_forbidden = True
    order = await make_order(rv, customer)
    await prompt_now(rv, order)
    review = await review_of(rv, order)
    assert order.channel is not None
    (message,) = order.channel.messages
    assert message.content == customer.mention  # pinged
    assert message.allowed_mentions is not None and message.allowed_mentions.users == [discord.Object(customer.id)]
    assert only_embed(message).description == rendered(
        rv, k.PROMPT_DESCRIPTION, order_number=number_text(rv, order.number)
    )
    assert custom_ids(message) == [views.rate_id(review.id), views.decline_id(review.id)]
    assert (review.prompt_channel_id, review.prompt_message_id) == (order.channel.id, message.id)
    (event,) = await audit_actions(rv)
    assert event.details["delivery"] == "channel"


async def test_dm_then_channel_without_dm_notifications_posts_in_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"dm_notifications": False}})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert customer.dms == []
        assert order.channel is not None and len(order.channel.messages) == 1
    finally:
        await tenv.close()


async def test_dm_then_channel_ignores_global_channel_fallback_switch(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"notifications": {"fallback_to_channel": False}})
    try:
        customer = tenv.member()
        customer.dm_forbidden = True
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None and len(order.channel.messages) == 1
    finally:
        await tenv.close()


async def test_channel_mode_posts_in_order_channel_with_mention(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert customer.dms == []
        assert order.channel is not None
        (message,) = order.channel.messages
        assert message.content == customer.mention
        review = await review_of(tenv, order)
        assert review.prompt_message_id == message.id
        # running again does not post a second request
        await service.send_prompt_for_order(tenv.app, order.id)
        assert len(order.channel.messages) == 1
    finally:
        await tenv.close()


async def test_dm_mode_never_posts_in_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "dm"}})
    try:
        customer = tenv.member()
        customer.dm_forbidden = True
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None and order.channel.messages == []
        assert (await review_of(tenv, order)).status == repo.PROMPTED
        (event,) = await audit_actions(tenv)
        assert event.details["delivery"] == "none"
    finally:
        await tenv.close()


async def test_fallback_skipped_when_order_channel_is_gone(rv: TestEnv) -> None:
    customer = rv.member()
    customer.dm_forbidden = True
    order = await make_order(rv, customer)
    await mark_ticket_deleted(rv, order.id)
    await prompt_now(rv, order)
    assert order.channel is not None and order.channel.messages == []
    other = rv.member(name="other")
    other.dm_forbidden = True
    vanished = await make_order(rv, other)
    assert vanished.channel is not None
    await vanished.channel.delete()
    await prompt_now(rv, vanished)
    assert (await review_of(rv, vanished)).status == repo.PROMPTED  # staff can /reviews resend later


async def test_channel_send_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        assert order.channel is not None
        order.channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        await prompt_now(tenv, order)
        scheduled = await task(tenv, service.prompt_dedupe(order.id))
        assert scheduled is not None and scheduled.status == "done"
        assert not await tenv.app.db.run(notif_repo.already_sent, service.prompt_dedupe(order.id))
    finally:
        await tenv.close()


async def test_only_fulfilled_orders_are_prompted(rv: TestEnv) -> None:
    customer = rv.member()
    for status in ("pending_payment", "paid", "processing", "cancelled", "refunded"):
        order = await make_order(rv, customer, status=status)
        await prompt_now(rv, order)
    assert await review_count(rv) == 0
    assert customer.dms == []
    # refunded between scheduling and sending
    order = await make_order(rv, customer)
    await service.schedule_review_prompt(rv.app, order.id)
    await set_order_status(rv, order.id, "refunded")
    await rv.app.scheduler.run_due()
    assert await review_count(rv) == 0 and customer.dms == []


async def test_order_of_another_server_is_ignored(rv: TestEnv) -> None:
    customer = rv.member()
    order = await make_order(rv, customer, guild_id=999)
    await prompt_now(rv, order)
    assert await review_count(rv) == 0 and customer.dms == []


async def test_task_waits_while_not_configured_then_gives_up(rv: TestEnv) -> None:
    customer = rv.member()
    order = await make_order(rv, customer)
    await service.schedule_review_prompt(rv.app, order.id)
    rv.app.content.replace(file_values={**rv.content, k.THANKS: ""})
    rv.app.recompute_readiness()
    assert not rv.app.ready(k.REVIEWS)
    await rv.app.scheduler.run_due()
    waiting = await task(rv, service.prompt_dedupe(order.id))
    assert waiting is not None and waiting.status == "pending"
    assert waiting.run_at == rv.clock.now() + service.NOT_READY_RETRY_SECONDS
    # configured again: the request goes out at the next check
    rv.app.content.replace(file_values=rv.content)
    rv.app.recompute_readiness()
    rv.clock.advance(service.NOT_READY_RETRY_SECONDS)
    await rv.app.scheduler.run_due()
    assert len(customer.dms) == 1
    # a request that waited longer than a week is dropped
    other = rv.member(name="other")
    late = await make_order(rv, other)
    await service.schedule_review_prompt(rv.app, late.id)
    rv.app.content.replace(file_values={**rv.content, k.THANKS: ""})
    rv.app.recompute_readiness()
    rv.clock.advance(service.PROMPT_MAX_WAIT_SECONDS)
    await rv.app.scheduler.run_due()
    dropped = await task(rv, service.prompt_dedupe(late.id))
    assert dropped is not None and dropped.status == "done"
    assert other.dms == []


async def test_task_does_nothing_when_feature_disabled(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await service.schedule_review_prompt(tenv.app, order.id)
        settings = tenv.app.settings
        tenv.app.settings = replace(settings, features=replace(settings.features, reviews=False))
        tenv.app.recompute_readiness()
        await tenv.app.scheduler.run_due()
        assert customer.dms == [] and await review_count(tenv) == 0
        done = await task(tenv, service.prompt_dedupe(order.id))
        assert done is not None and done.status == "done"
    finally:
        await tenv.close()
