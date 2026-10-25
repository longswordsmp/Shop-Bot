"""Approval messages and decisions across failures and restarts: Discord errors while posting or editing, the
durable tasks ``reviews.approval`` and ``reviews.decided``, adoption of an unrecorded post, the /setup hook,
``/review approve|reject|remove`` and restart persistence of the staff buttons."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.db.repos import state
from shopbot.features.reviews import MODULE, repo, service, views
from shopbot.features.reviews import content_keys as k
from tests.fakes import click, http_error
from tests.helpers import TestEnv, provision_channels
from tests.reviews.util import (
    audit_actions,
    invoke,
    number_text,
    only_embed,
    prompted,
    restarted_app,
    review_of,
    reviews_channel,
    task,
)
from tests.reviews_approval.util import (
    approval_channel,
    approve,
    field_pairs,
    make_approval_env,
    only_message,
    pending_review,
    reject,
    staff_reply,
    status_value,
    submit,
)

# --------------------------------------------------------------------------------------------------
# Posting the approval message
# --------------------------------------------------------------------------------------------------


async def test_failed_post_is_retried_until_posted_once(ap: TestEnv) -> None:
    customer = ap.member()
    order, review, dm = await prompted(ap, customer)
    channel = approval_channel(ap)
    channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    await submit(ap, customer, review, dm)
    assert channel.messages == []
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL  # the review is kept
    retry = await task(ap, service.approval_dedupe(review.id))
    assert retry is not None and retry.status == "pending" and retry.payload == {"review_id": review.id}
    # still failing: retried with backoff, then slowly but never abandoned
    for _ in range(ap.app.scheduler.max_attempts):
        ap.clock.advance(3600)
        await ap.app.scheduler.run_due()
    slow = await task(ap, service.approval_dedupe(review.id))
    assert slow is not None and slow.status == "pending"
    channel.fail_send = None
    ap.clock.advance(service.PUBLISH_SLOW_RETRY_SECONDS)
    await ap.app.scheduler.run_due()
    message = only_message(channel)
    assert (await review_of(ap, order)).approval_message_id == message.id
    ap.clock.advance(3600)
    await ap.app.scheduler.run_due()
    assert len(channel.messages) == 1


async def test_missing_approval_channel_is_retried(tmp_path: Path) -> None:
    from tests.helpers import make_settings, make_test_env
    from tests.reviews_approval.util import approval_overrides

    tenv = await make_test_env(tmp_path, settings=make_settings(approval_overrides()))  # no channels yet
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer, with_channel=False)
        await submit(tenv, customer, review, dm)
        assert (await review_of(tenv, order)).status == repo.PENDING_APPROVAL
        await provision_channels(tenv)
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        message = only_message(approval_channel(tenv))
        assert (await review_of(tenv, order)).approval_message_id == message.id
    finally:
        await tenv.close()


async def test_unrecorded_post_is_adopted_instead_of_posted_again(ap: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    customer = ap.member()
    order, review, dm = await prompted(ap, customer)
    original = repo.record_approval_message
    calls = {"n": 0}

    def crash_once(conn: sqlite3.Connection, *args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("process stopped after Discord created the message")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(repo, "record_approval_message", crash_once)
    await submit(ap, customer, review, dm)
    channel = approval_channel(ap)
    assert len(channel.messages) == 1  # posted, but not recorded
    current = await review_of(ap, order)
    assert current.approval_message_id is None
    assert await ap.app.db.run(state.kv_get, repo.approval_claim_key(review.id)) is not None
    ap.clock.advance(3600)
    await ap.app.scheduler.run_due()
    (message,) = channel.messages  # adopted, not posted twice
    adopted = await review_of(ap, order)
    assert adopted.approval_message_id == message.id
    assert await ap.app.db.run(state.kv_get, repo.approval_claim_key(review.id)) is None


async def test_restart_posts_missing_approval_messages(ap: TestEnv) -> None:
    customer = ap.member()
    order, review, dm = await prompted(ap, customer)
    channel = approval_channel(ap)
    channel.fail_send = http_error(discord.HTTPException, 503, 0, "Unavailable")
    await submit(ap, customer, review, dm)

    def fail_task(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE kind = 'reviews.approval'")

    await ap.app.db.transaction(fail_task)
    channel.fail_send = None
    app = await restarted_app(ap)
    requeued = await task(ap, service.approval_dedupe(review.id), app)
    assert requeued is not None and requeued.status == "pending" and requeued.run_at <= ap.clock.now()
    await app.scheduler.run_due()
    message = only_message(channel)
    assert (await review_of(ap, order, app)).approval_message_id == message.id


# --------------------------------------------------------------------------------------------------
# Restart persistence of the staff buttons and of decisions
# --------------------------------------------------------------------------------------------------


async def test_buttons_work_after_a_restart(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    app = await restarted_app(ap)
    interaction = await approve(ap, staff, review, message, app=app)
    assert "approved" in (staff_reply(interaction).description or "")
    assert (await review_of(ap, order, app)).status == repo.PUBLISHED
    assert len(reviews_channel(ap).messages) == 1
    # a second pending review rejected through the restarted app's form route
    other = ap.member(name="other")
    order2, review2, message2 = await pending_review(ap, other)
    app2 = await restarted_app(ap)
    opened = await click(app2, user=staff, custom_id=views.reject_id(review2.id), message=message2)
    assert opened.response.modal is not None
    await reject(ap, staff, review2, message2, "[late]", app=app2)
    assert (await review_of(ap, order2, app2)).status == repo.REJECTED


async def test_decision_followups_run_from_the_task_after_a_crash(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    # the decision committed, then the process stopped before any follow-up ran
    result = await service.decide(ap.app, review.id, approve=True, actor_id=staff.id)
    assert result.outcome is repo.Outcome.OK
    assert reviews_channel(ap).messages == []
    followup = await task(ap, repo.decision_key(review.id))
    assert followup is not None and followup.status == "pending"
    app = await restarted_app(ap)
    ap.clock.advance(repo.DECISION_FOLLOWUP_DELAY_SECONDS)
    await app.scheduler.run_due()
    assert len(reviews_channel(ap).messages) == 1
    final = await review_of(ap, order, app)
    assert final.status == repo.PUBLISHED
    assert field_pairs(only_embed(message))[-1][1] == status_value(ap, k.STATE_APPROVED, staff.id)
    assert message.custom_ids() == []
    assert [e.action for e in await audit_actions(ap, "reviews.approved")] == ["reviews.approved"]
    done = await task(ap, repo.decision_key(review.id), app)
    assert done is not None and done.status in ("done", "cancelled")
    # running the follow-ups again changes nothing
    again = await service.run_decision_followups(app, review.id)
    assert again.publication is service.Publish.SKIPPED
    assert len(reviews_channel(ap).messages) == 1
    assert [e.action for e in await audit_actions(ap, "reviews.approved")] == ["reviews.approved"]


async def test_rejection_notice_is_sent_once_even_if_followups_repeat(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    _order, review, message = await pending_review(ap, customer)
    await reject(ap, staff, review, message, "[r]")
    count = len(customer.dms)
    repeated = await service.run_decision_followups(ap.app, review.id)
    assert repeated.notice == "duplicate"
    assert len(customer.dms) == count
    assert len(await audit_actions(ap, "reviews.rejected")) == 1


# --------------------------------------------------------------------------------------------------
# Discord failures during decisions
# --------------------------------------------------------------------------------------------------


async def test_approval_message_edit_failure_does_not_block_the_decision(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    message.deleted = True  # every edit of it now fails with 404 Unknown Message
    interaction = await approve(ap, staff, review, message)
    assert (await review_of(ap, order)).status == repo.PUBLISHED
    assert len(reviews_channel(ap).messages) == 1
    assert ap.app.op("reviews.decision.message_failed") in (staff_reply(interaction).description or "")


async def test_publication_failure_after_approval_is_retried_and_notice_follows(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    channel = reviews_channel(ap)
    channel.fail_send = http_error(discord.HTTPException, 500, 0, "Internal Server Error")
    before = len(customer.dms)
    interaction = await approve(ap, staff, review, message)
    assert ap.app.op("reviews.decision.publish_pending") in (staff_reply(interaction).description or "")
    approved = await review_of(ap, order)
    assert approved.submitted and approved.approval == repo.APPROVED
    assert len(customer.dms) == before  # not published yet: no notice yet
    channel.fail_send = None
    ap.clock.advance(3600)
    await ap.app.scheduler.run_due()
    post = only_message(channel)
    assert (await review_of(ap, order)).published_message_id == post.id
    assert len(customer.dms) == before + 1
    assert service.message_link(ap.app, channel.id, post.id) in (only_embed(customer.dms[-1]).description or "")


async def test_customer_lookup_failure_still_builds_the_message(ap: TestEnv) -> None:
    customer = ap.member(name="Gone")
    staff = ap.member(name="mod", staff=True)
    _order, review, message = await pending_review(ap, customer)
    del ap.guild._members[customer.id]  # the customer left the server
    await approve(ap, staff, review, message)
    title = only_embed(message).title or ""
    assert str(customer.id) in title  # falls back to the user ID


# --------------------------------------------------------------------------------------------------
# /setup hook
# --------------------------------------------------------------------------------------------------


async def test_setup_reposts_deleted_and_edits_existing_approval_messages(ap: TestEnv) -> None:
    assert MODULE.publish is not None
    customer = ap.member()
    other = ap.member(name="other")
    order, _review, message = await pending_review(ap, customer)
    order2, review2, message2 = await pending_review(ap, other)
    await message.delete()  # someone deleted the first approval message
    ap.app.content.replace(file_values={**ap.content, k.APPROVAL_TITLE: "[new title] {order_number}"})
    lines = await MODULE.publish(ap.app)
    assert ap.app.op("reviews.setup.approvals_refreshed", count=1) in lines
    assert ap.app.op("reviews.setup.approvals_queued", count=1) in lines
    assert only_embed(message2).title == f"[new title] {number_text(ap, order2.number)}"  # edited in place
    await ap.app.scheduler.run_due()
    channel = approval_channel(ap)
    assert len(channel.messages) == 2
    reposted = await review_of(ap, order)
    assert reposted.approval_message_id not in (None, message.id)
    # idempotent: a second /setup only edits
    lines = await MODULE.publish(ap.app)
    assert lines == [ap.app.op("reviews.setup.approvals_refreshed", count=2)]
    await ap.app.scheduler.run_due()
    assert len(channel.messages) == 2
    assert (await review_of(ap, order2)).approval_message_id == review2.approval_message_id == message2.id


# --------------------------------------------------------------------------------------------------
# Staff commands
# --------------------------------------------------------------------------------------------------


async def test_review_approve_and_reject_commands(ap: TestEnv) -> None:
    customer = ap.member()
    other = ap.member(name="other")
    staff = ap.member(name="mod", staff=True)
    order, _review, message = await pending_review(ap, customer)
    order2, _review2, message2 = await pending_review(ap, other)
    approved = await invoke(ap, staff, "approve", order_number=order.number)
    number = number_text(ap, order.number)
    assert ap.app.op("reviews.decision.approved", order_number=number) in (
        approved.last_sent.embeds[0].description or ""  # type: ignore[union-attr]
    )
    assert (await review_of(ap, order)).status == repo.PUBLISHED
    assert message.custom_ids() == []  # the recorded approval message shows the decision
    rejected = await invoke(ap, staff, "reject", order_number=order2.number, reason="  [cmd reason] ")
    assert (await review_of(ap, order2)).decision_reason == "[cmd reason]"
    assert field_pairs(only_embed(message2))[-1] == (ap.text(k.APPROVAL_REASON_FIELD), "[cmd reason]")
    assert rejected.last_sent is not None and getattr(rejected.last_sent, "ephemeral", False)
    again = await invoke(ap, staff, "approve", order_number=order2.number)
    assert "no longer waits" in (again.last_sent.embeds[0].description or "")  # type: ignore[union-attr]
    unknown = await invoke(ap, staff, "reject", order_number=99_999)
    assert ap.app.op("reviews.decision.not_found", order_number="99999") in (
        unknown.last_sent.embeds[0].description or ""  # type: ignore[union-attr]
    )


async def test_customers_cannot_use_the_decision_commands(ap: TestEnv) -> None:
    from shopbot.features import core_keys as ck

    customer = ap.member()
    order, _review, _message = await pending_review(ap, customer)
    for name in ("approve", "reject"):
        interaction = await invoke(ap, customer, name, order_number=order.number)
        assert interaction.last_sent is not None
        assert interaction.last_sent.embeds[0].description == ap.text(ck.ERROR_PERMISSION)
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL


async def test_list_shows_the_approval_statuses(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    await pending_review(ap, customer)
    listing = await invoke(ap, staff, "list", status="pending_approval")
    text = listing.last_sent.embeds[0].description or ""  # type: ignore[union-attr]
    assert ap.app.op("reviews.status.pending_approval") in text


async def test_remove_of_a_pending_review_updates_its_approval_message(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    await invoke(ap, staff, "remove", order_number=order.number)
    removed = await review_of(ap, order)
    assert removed.status == repo.DECLINED
    assert message.custom_ids() == []
    assert field_pairs(only_embed(message))[-1][1] == status_value(ap, k.STATE_REMOVED)
    late = await approve(ap, staff, review, message)
    assert "no longer waits" in (staff_reply(late).description or "")
    assert reviews_channel(ap).messages == []
    leftover = await task(ap, service.approval_dedupe(review.id))
    assert leftover is None or leftover.status != "pending"


# --------------------------------------------------------------------------------------------------
# Approval switched off while reviews wait
# --------------------------------------------------------------------------------------------------


async def test_pending_reviews_stay_decidable_after_approval_is_switched_off(tmp_path: Path) -> None:
    tenv = await make_approval_env(tmp_path)
    try:
        customer = tenv.member()
        staff = tenv.member(name="mod", staff=True)
        order, review, message = await pending_review(tenv, customer)
        from dataclasses import replace

        settings = tenv.app.settings
        tenv.app.settings = replace(
            settings, reviews=replace(settings.reviews, require_approval=False, approval_channel="")
        )
        tenv.app.recompute_readiness()
        assert tenv.app.ready(k.REVIEWS)
        # nothing is published without a decision; the posted message still works
        assert await service.request_approval(tenv.app, review.id) is service.Publish.SKIPPED
        await approve(tenv, staff, review, message)
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
        # new reviews are published right away again
        other = tenv.member(name="other")
        order2, review2, dm2 = await prompted(tenv, other)
        await submit(tenv, other, review2, dm2)
        assert (await review_of(tenv, order2)).status == repo.PUBLISHED
        assert len(approval_channel(tenv).messages) == 1
    finally:
        await tenv.close()
