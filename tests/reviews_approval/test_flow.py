"""Staff approval of reviews end to end through the real router: approval off (unchanged), approval on (approve,
reject with and without a reason), notices, permissions, duplicates and concurrency, and the verification marker."""

from __future__ import annotations

import asyncio
from pathlib import Path

import discord

from shopbot.features import core_keys as ck
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, service, views
from tests.fakes import click, modal_input_ids, modal_of, submit_modal
from tests.helpers import TestEnv
from tests.reviews.util import (
    audit_actions,
    make_env,
    number_text,
    only_embed,
    prompted,
    published_title,
    rendered,
    review_of,
    reviews_channel,
    set_order_status,
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
# Approval off: behaviour unchanged (published right away = approved automatically)
# --------------------------------------------------------------------------------------------------


async def test_without_approval_reviews_are_published_immediately(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        assert tenv.app.settings.reviews.require_approval is False
        customer = tenv.member(name="Ada")
        order, review, dm = await prompted(tenv, customer)
        await submit(tenv, customer, review, dm, comment="Great")
        number = number_text(tenv, order.number)
        assert only_embed(dm).description == rendered(tenv, k.THANKS, order_number=number)
        post = only_message(reviews_channel(tenv))
        card = only_embed(post)
        assert card.title == published_title(tenv, rating=4, user_name="Ada", number=order.number)
        assert card.description == "Great"
        assert field_pairs(card) == [
            (tenv.text(k.PUBLISHED_RATING_FIELD), tenv.text(k.rating_key(4, "display"))),
            (tenv.text(k.PUBLISHED_VERIFIED_FIELD), rendered(tenv, k.PUBLISHED_VERIFIED_VALUE, order_number=number)),
        ]
        final = await review_of(tenv, order)
        assert (final.status, final.approval, final.decided_by) == (repo.PUBLISHED, repo.AUTOMATIC, None)
        assert final.approval_message_id is None
        # nothing is posted for staff, no decision task, no notice
        staff_channel_ids = {tenv.app.resources.channel_id("staff_alerts")}
        assert all(not c.messages for c in tenv.guild.text_channels if c.id in staff_channel_ids)
        assert await task(tenv, repo.decision_key(review.id)) is None
        assert len(customer.dms) == 1  # only the request (edited into the thank-you text)
        assert [e.action for e in await audit_actions(tenv)] == ["reviews.prompted", "reviews.published"]
    finally:
        await tenv.close()


async def test_verification_marker_is_omitted_when_not_configured(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.PUBLISHED_VERIFIED_FIELD: "", k.PUBLISHED_VERIFIED_VALUE: ""})
    try:
        assert tenv.app.ready(k.REVIEWS)
        customer = tenv.member()
        _order, review, dm = await prompted(tenv, customer)
        await submit(tenv, customer, review, dm)
        card = only_embed(only_message(reviews_channel(tenv)))
        assert field_pairs(card) == [(tenv.text(k.PUBLISHED_RATING_FIELD), tenv.text(k.rating_key(4, "display")))]
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Approval on: the approval message
# --------------------------------------------------------------------------------------------------


async def test_submission_waits_for_approval(ap: TestEnv) -> None:
    customer = ap.member(name="Ada")
    order, review, dm = await prompted(ap, customer)
    await submit(ap, customer, review, dm, rating="5", comment="  Nice *work* <@1> @everyone ")
    number = number_text(ap, order.number)
    # the customer sees the 'pending' thank-you text; nothing is public yet
    assert only_embed(dm).description == rendered(ap, k.THANKS_PENDING, order_number=number)
    assert dm.view is None
    assert reviews_channel(ap).messages == []
    pending = await review_of(ap, order)
    assert (pending.status, pending.rating, pending.comment, pending.approval) == (
        repo.PENDING_APPROVAL,
        5,
        "Nice *work* <@1> @everyone",
        None,
    )
    message = only_message(approval_channel(ap))
    assert (pending.approval_channel_id, pending.approval_message_id) == (approval_channel(ap).id, message.id)
    embed = only_embed(message)
    assert embed.title == rendered(ap, k.APPROVAL_TITLE, rating=5, user_name="Ada", order_number=number)
    assert embed.description == "Nice \\*work\\* <@1> @everyone"
    assert field_pairs(embed) == [
        (ap.text(k.APPROVAL_RATING_FIELD), ap.text(k.rating_key(5, "display"))),
        (ap.text(k.APPROVAL_ORDER_FIELD), number),
        (ap.text(k.APPROVAL_CUSTOMER_FIELD), f"<@{customer.id}>"),
        (ap.text(k.APPROVAL_STATUS_FIELD), status_value(ap, k.STATE_PENDING)),
    ]
    assert embed.timestamp is not None
    assert embed.colour is not None and embed.colour.value == ap.app.ui.color("warning")
    assert message.custom_ids() == [views.approve_id(review.id), views.reject_id(review.id)]
    labels = [getattr(c, "item", c).label for c in message.components()]
    assert labels == [ap.text(k.APPROVAL_BUTTON_APPROVE), ap.text(k.APPROVAL_BUTTON_REJECT)]
    # embeds never ping anyone
    assert message.allowed_mentions is not None
    assert message.allowed_mentions.everyone is False and message.allowed_mentions.users is False
    assert [e.action for e in await audit_actions(ap)] == ["reviews.prompted"]


async def test_pending_text_falls_back_to_the_thank_you_text(tmp_path: Path) -> None:
    tenv = await make_approval_env(tmp_path, content={k.THANKS_PENDING: ""})
    try:
        assert tenv.app.ready(k.REVIEWS)
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer)
        await submit(tenv, customer, review, dm)
        number = number_text(tenv, order.number)
        assert only_embed(dm).description == rendered(tenv, k.THANKS, order_number=number)
    finally:
        await tenv.close()


async def test_customer_cannot_submit_twice_while_pending(ap: TestEnv) -> None:
    customer = ap.member()
    order, review, message = await pending_review(ap, customer)
    again = await click(ap.app, user=customer, custom_id=views.rate_id(review.id), values=["1"], dm=True)
    number = number_text(ap, order.number)
    assert only_embed(again.last_sent).description == rendered(ap, k.ALREADY_REVIEWED, order_number=number)  # type: ignore[arg-type]
    comment = await submit_modal(
        ap.app, user=customer, custom_id=views.comment_id(review.id), fields={views.COMMENT_INPUT_ID: "x"}, dm=True
    )
    assert comment.last_sent is not None
    assert (await review_of(ap, order)).comment == "[kept comment]"
    assert len(approval_channel(ap).messages) == 1 and message.custom_ids()


# --------------------------------------------------------------------------------------------------
# Approve
# --------------------------------------------------------------------------------------------------


async def test_approve_publishes_and_notifies(ap: TestEnv) -> None:
    customer = ap.member(name="Ada")
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    number = number_text(ap, order.number)
    interaction = await approve(ap, staff, review, message)
    # the clicked message shows the decision (buttons removed) as the interaction's answer
    assert interaction.response_kinds() == ["defer"]
    embed = only_embed(message)
    assert message.view is None or message.custom_ids() == []
    assert field_pairs(embed)[-1] == (ap.text(k.APPROVAL_STATUS_FIELD), status_value(ap, k.STATE_APPROVED, staff.id))
    assert embed.colour is not None and embed.colour.value == ap.app.ui.color("success")
    # the review is published exactly like an unmoderated one
    post = only_message(reviews_channel(ap))
    card = only_embed(post)
    assert card.title == published_title(ap, rating=4, user_name="Ada", number=order.number)
    assert card.description == "[kept comment]"
    assert field_pairs(card)[1] == (
        ap.text(k.PUBLISHED_VERIFIED_FIELD),
        rendered(ap, k.PUBLISHED_VERIFIED_VALUE, order_number=number),
    )
    final = await review_of(ap, order)
    assert (final.status, final.approval, final.decided_by, final.published_message_id) == (
        repo.PUBLISHED,
        repo.APPROVED,
        staff.id,
        post.id,
    )
    assert final.decided_at == ap.clock.now()
    # the customer is told privately, with a link to the published review
    notice = customer.dms[-1]
    link = service.message_link(ap.app, reviews_channel(ap).id, post.id)
    notice_embed = only_embed(notice)
    assert notice_embed.title == rendered(ap, k.notice_key("approved", "title"), order_number=number)
    assert notice_embed.description == rendered(
        ap, k.notice_key("approved", "description"), order_number=number, review_link=link
    )
    # operator reply, audit, follow-up task cancelled
    reply = staff_reply(interaction)
    assert reply.title == ap.app.op("reviews.decision.title")
    assert reply.description == "\n".join(
        [ap.app.op("reviews.decision.approved", order_number=number), ap.app.op("reviews.decision.published")]
    )
    actions = [e.action for e in await audit_actions(ap)]
    assert actions == ["reviews.prompted", "reviews.published", "reviews.approved"]
    approved = next(e for e in await audit_actions(ap) if e.action == "reviews.approved")
    assert approved.actor_id == staff.id and approved.target_id == str(review.id)
    followup = await task(ap, repo.decision_key(review.id))
    assert followup is not None and followup.status == "cancelled"


async def test_double_approve_applies_once(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    await approve(ap, staff, review, message)
    second = await approve(ap, staff, review, message)
    number = number_text(ap, order.number)
    status = ap.app.op("reviews.status.published")
    assert staff_reply(second).description == ap.app.op(
        "reviews.decision.not_pending", order_number=number, status=status
    )
    assert len(reviews_channel(ap).messages) == 1
    assert [e.action for e in await audit_actions(ap, "reviews.approved")] == ["reviews.approved"]
    assert len([m for m in customer.dms if m.embeds and m.embeds[0].title]) == 1  # one approval notice


async def test_concurrent_approve_and_reject_apply_exactly_once(ap: TestEnv) -> None:
    customer = ap.member()
    first = ap.member(name="mod1", staff=True)
    second = ap.member(name="mod2", staff=True)
    order, review, message = await pending_review(ap, customer)
    approved, rejected = await asyncio.gather(
        approve(ap, first, review, message), reject(ap, second, review, message, "[no]")
    )
    final = await review_of(ap, order)
    assert final.status in (repo.PUBLISHED, repo.REJECTED)
    decisions = [e.action for e in await audit_actions(ap) if e.action in ("reviews.approved", "reviews.rejected")]
    assert len(decisions) == 1
    replies = [staff_reply(approved).description or "", staff_reply(rejected).description or ""]
    assert sum("no longer waits" in text for text in replies) == 1
    assert len(reviews_channel(ap).messages) == (1 if final.status == repo.PUBLISHED else 0)


async def test_approve_refused_when_the_order_is_no_longer_fulfilled(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    await set_order_status(ap, order.id, "refunded")
    interaction = await approve(ap, staff, review, message)
    number = number_text(ap, order.number)
    assert staff_reply(interaction).description == ap.app.op(
        "reviews.decision.expired", order_number=number, status="refunded"
    )
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL
    assert reviews_channel(ap).messages == []
    assert message.custom_ids() == [views.approve_id(review.id), views.reject_id(review.id)]  # still decidable
    await reject(ap, staff, review, message)
    assert (await review_of(ap, order)).status == repo.REJECTED


# --------------------------------------------------------------------------------------------------
# Reject
# --------------------------------------------------------------------------------------------------


async def test_reject_button_opens_the_staff_form(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    interaction = await click(ap.app, user=staff, custom_id=views.reject_id(review.id), message=message)
    modal = modal_of(interaction)
    assert modal is not None
    assert modal.custom_id == views.reject_form_id(review.id)
    assert modal.title == ap.app.op("reviews.form.reject_title", order_number=number_text(ap, order.number))
    assert modal_input_ids(modal) == [views.REASON_INPUT_ID]
    text_input = next(i for i in modal.walk_children() if isinstance(i, discord.ui.TextInput))
    assert text_input.required is False and text_input.max_length == repo.REASON_MAX
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL  # opening the form changes nothing


async def test_reject_with_reason(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    interaction = await reject(ap, staff, review, message, "  Contains *a link*  ")
    number = number_text(ap, order.number)
    final = await review_of(ap, order)
    assert (final.status, final.approval, final.decided_by, final.decision_reason) == (
        repo.REJECTED,
        repo.REJECTED,
        staff.id,
        "Contains *a link*",
    )
    assert reviews_channel(ap).messages == []
    embed = only_embed(message)
    assert message.custom_ids() == []
    assert field_pairs(embed)[-2:] == [
        (ap.text(k.APPROVAL_STATUS_FIELD), status_value(ap, k.STATE_REJECTED, staff.id)),
        (ap.text(k.APPROVAL_REASON_FIELD), "Contains *a link*"),
    ]
    assert embed.colour is not None and embed.colour.value == ap.app.ui.color("danger")
    notice = only_embed(customer.dms[-1])
    assert notice.title == rendered(ap, k.notice_key("rejected", "title"), order_number=number)
    assert notice.description == rendered(ap, k.notice_key("rejected", "description"), order_number=number)
    assert field_pairs(notice) == [(ap.text(k.NOTICE_REJECTED_REASON_FIELD), "Contains *a link*")]
    assert staff_reply(interaction).description == "\n".join(
        [ap.app.op("reviews.decision.rejected", order_number=number), ap.app.op("reviews.decision.notice.dm")]
    )
    (event,) = await audit_actions(ap, "reviews.rejected")
    assert event.actor_id == staff.id and event.details["with_reason"] is True and event.details["notice"] == "dm"
    assert "Contains" not in str(event.details)  # the audit log records that a reason exists, not the text
    # a later approve changes nothing
    later = await approve(ap, staff, review, message)
    assert "no longer waits" in (staff_reply(later).description or "")
    assert reviews_channel(ap).messages == []


async def test_reject_without_reason_and_without_reason_headings(tmp_path: Path) -> None:
    tenv = await make_approval_env(tmp_path, content={k.APPROVAL_REASON_FIELD: "", k.NOTICE_REJECTED_REASON_FIELD: ""})
    try:
        customer = tenv.member()
        staff = tenv.member(name="mod", staff=True)
        order, review, message = await pending_review(tenv, customer)
        await reject(tenv, staff, review, message, "[reason]")
        assert (await review_of(tenv, order)).decision_reason == "[reason]"
        assert tenv.text(k.APPROVAL_STATUS_FIELD) == field_pairs(only_embed(message))[-1][0]  # no reason field
        assert field_pairs(only_embed(customer.dms[-1])) == []
    finally:
        await tenv.close()


async def test_reject_with_empty_reason(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    await reject(ap, staff, review, message, "   ")
    final = await review_of(ap, order)
    assert (final.status, final.decision_reason) == (repo.REJECTED, None)
    assert field_pairs(only_embed(message))[-1][0] == ap.text(k.APPROVAL_STATUS_FIELD)
    assert field_pairs(only_embed(customer.dms[-1])) == []


async def test_too_long_reason_is_refused(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    interaction = await reject(ap, staff, review, message, "x" * (repo.REASON_MAX + 1))
    assert staff_reply(interaction).description == ap.app.op(
        "reviews.decision.reason_too_long", length=repo.REASON_MAX + 1, max=repo.REASON_MAX
    )
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL


async def test_notices_are_optional(tmp_path: Path) -> None:
    off = {k.notice_key(decision, part): "" for decision in k.NOTICE_DECISIONS for part in ("title", "description")}
    tenv = await make_approval_env(tmp_path, content=off)
    try:
        assert tenv.app.ready(k.REVIEWS)
        customer = tenv.member()
        staff = tenv.member(name="mod", staff=True)
        _order, review, message = await pending_review(tenv, customer)
        before = len(customer.dms)
        interaction = await reject(tenv, staff, review, message)
        assert len(customer.dms) == before  # nothing incomplete is sent
        assert tenv.app.op("reviews.decision.notice.off") in (staff_reply(interaction).description or "")
        other = tenv.member(name="other")
        _order2, review2, message2 = await pending_review(tenv, other)
        before = len(other.dms)
        await approve(tenv, staff, review2, message2)
        assert len(other.dms) == before
        assert len(reviews_channel(tenv).messages) == 1
    finally:
        await tenv.close()


async def test_notice_falls_back_to_the_order_channel(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, message = await pending_review(ap, customer)
    customer.dm_forbidden = True
    interaction = await reject(ap, staff, review, message, "[why]")
    assert order.channel is not None
    fallback = order.channel.messages[-1]
    assert fallback.content == f"<@{customer.id}>"
    assert only_embed(fallback).description == rendered(
        ap, k.notice_key("rejected", "description"), order_number=number_text(ap, order.number)
    )
    assert ap.app.op("reviews.decision.notice.channel") in (staff_reply(interaction).description or "")


async def test_undeliverable_notice_is_reported(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    _order, review, message = await pending_review(ap, customer, with_channel=False)
    customer.dm_forbidden = True
    interaction = await reject(ap, staff, review, message)
    assert ap.app.op("reviews.decision.notice.none") in (staff_reply(interaction).description or "")
    assert (await review_of(ap, _order)).status == repo.REJECTED


# --------------------------------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------------------------------


async def test_only_staff_can_decide(ap: TestEnv) -> None:
    customer = ap.member()
    order, review, message = await pending_review(ap, customer)
    for user in (customer, ap.member(name="someone")):
        for interaction in (
            await approve(ap, user, review, message),
            await click(ap.app, user=user, custom_id=views.reject_id(review.id), message=message),
            await reject(ap, user, review, message, "[x]"),
        ):
            assert only_embed(interaction.last_sent).description == ap.text(ck.ERROR_PERMISSION)  # type: ignore[arg-type]
            assert modal_of(interaction) is None
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL
    assert message.custom_ids() == [views.approve_id(review.id), views.reject_id(review.id)]
    assert reviews_channel(ap).messages == []


async def test_staff_controls_are_refused_in_direct_messages(ap: TestEnv) -> None:
    customer = ap.member()
    staff = ap.member(name="mod", staff=True)
    order, review, _message = await pending_review(ap, customer)
    interaction = await click(ap.app, user=staff, custom_id=views.approve_id(review.id), dm=True)
    assert interaction.last_sent is None
    assert (await review_of(ap, order)).status == repo.PENDING_APPROVAL


async def test_checkout_staff_roles_count_as_staff(tmp_path: Path) -> None:
    role_settings = {"key": "order_staff", "id": 0, "permissions": [], "color": "", "hoist": False}
    tenv = await make_approval_env(
        tmp_path, {"checkout": {"staff_roles": ["order_staff"]}}, roles=[{**role_settings, "mentionable": False}]
    )
    try:
        role = tenv.guild.add_role(name="order staff", position=5)
        await tenv.app.resources.remember("role", "order_staff", role.id)
        customer = tenv.member()
        order, review, message = await pending_review(tenv, customer)
        helper = tenv.guild.add_member(name="helper", roles=[role])
        await approve(tenv, helper, review, message)
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
    finally:
        await tenv.close()


async def test_unknown_review_and_forged_ids(ap: TestEnv) -> None:
    staff = ap.member(name="mod", staff=True)
    interaction = await click(ap.app, user=staff, custom_id="sb:rv:approve:999999")
    assert only_embed(interaction.last_sent).description == ap.text(ck.ERROR_NOT_FOUND)  # type: ignore[arg-type]
    # a review whose customer has not finished it has no approval message
    customer = ap.member()
    order, review, _dm = await prompted(ap, customer)
    interaction = await click(ap.app, user=staff, custom_id=views.approve_id(review.id))
    assert only_embed(interaction.last_sent).description == ap.text(ck.ERROR_NOT_FOUND)  # type: ignore[arg-type]
    assert (await review_of(ap, order)).status == repo.PROMPTED


# --------------------------------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------------------------------


async def test_comment_the_approval_message_cannot_show_is_refused(tmp_path: Path) -> None:
    long_fields = {
        k.APPROVAL_RATING_FIELD: "r" * 256,
        k.APPROVAL_ORDER_FIELD: "o" * 256,
        k.APPROVAL_CUSTOMER_FIELD: "c" * 256,
        k.APPROVAL_STATUS_FIELD: "s" * 256,
        k.approval_status_key(k.STATE_PENDING): "p" * 1024,
        k.rating_key(4, "display"): "d" * 1024,
    }
    tenv = await make_approval_env(tmp_path, {"reviews": {"comment_max_length": 4000}}, content=long_fields)
    try:
        customer = tenv.member()
        order, review, dm = await prompted(tenv, customer)
        refused = await submit(tenv, customer, review, dm, comment="a" * 3900)
        assert only_embed(refused.last_sent).description == tenv.text(ck.ERROR_INVALID_INPUT)  # type: ignore[arg-type]
        current = await review_of(tenv, order)
        assert (current.status, current.comment) == (repo.RATED, None)  # can still be shortened
        assert approval_channel(tenv).messages == []
        await submit(tenv, customer, review, dm, comment="a" * 1000)
        assert (await review_of(tenv, order)).status == repo.PENDING_APPROVAL
        assert len(approval_channel(tenv).messages) == 1
    finally:
        await tenv.close()


async def test_rating_only_review_waits_for_approval(tmp_path: Path) -> None:
    tenv = await make_approval_env(tmp_path, {"reviews": {"allow_comment": False}})
    try:
        customer = tenv.member()
        staff = tenv.member(name="mod", staff=True)
        order, review, dm = await prompted(tenv, customer)
        await click(tenv.app, user=customer, custom_id=views.rate_id(review.id), message=dm, values=["3"], dm=True)
        pending = await review_of(tenv, order)
        assert (pending.status, pending.comment) == (repo.PENDING_APPROVAL, "")
        message = only_message(approval_channel(tenv))
        assert only_embed(message).description is None
        await approve(tenv, staff, pending, message)
        assert only_embed(only_message(reviews_channel(tenv))).description is None
    finally:
        await tenv.close()
