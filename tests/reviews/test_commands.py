"""/review staff commands: permissions, list, remove, resend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.features import core_keys as ck
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, service, views
from tests.fakes import FakeMember, click, http_error, submit_modal
from tests.helpers import TestEnv, make_settings, make_test_env, provision_channels
from tests.reviews.util import (
    audit_actions,
    invoke,
    is_ephemeral,
    make_env,
    make_order,
    number_text,
    only_embed,
    prompt_now,
    prompted,
    rendered,
    reply_embed,
    review_command,
    review_of,
    reviews_channel,
    task,
)


async def _publish(tenv: TestEnv, user: FakeMember, rating: str = "5") -> tuple[Any, repo.Review, Any]:
    order, review, dm = await prompted(tenv, user)
    await click(tenv.app, user=user, custom_id=views.rate_id(review.id), message=dm, values=[rating], dm=True)
    await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: "[published]"},
        message=dm,
        dm=True,
    )
    return order, await review_of(tenv, order), dm


def _op(tenv: TestEnv, key: str, **values: object) -> str:
    return tenv.app.op(key, **values)


# --------------------------------------------------------------------------------------------------
# Metadata and permissions
# --------------------------------------------------------------------------------------------------


async def test_group_metadata(rv: TestEnv) -> None:
    assert rv.app.bot is not None
    group = rv.app.bot.tree.get_command("review", guild=rv.app.guild_object)
    assert isinstance(group, discord.app_commands.Group)
    assert group.default_permissions == discord.Permissions(manage_messages=True)
    assert group.guild_only is True
    assert {c.name for c in group.commands} == {"list", "remove", "resend"}
    for command in group.commands:
        assert isinstance(command, discord.app_commands.Command)
        assert command.description and all(p.description for p in command.parameters)
        assert len(command.checks) == 1
    status = next(p for p in review_command(rv.app, "list").parameters if p.name == "status")
    assert [c.value for c in status.choices] == ["prompted", "rated", "published", "declined"]
    number = next(p for p in review_command(rv.app, "remove").parameters if p.name == "order_number")
    assert number.type is discord.AppCommandOptionType.integer and number.min_value == 1


async def test_customers_are_refused(rv: TestEnv) -> None:
    customer = rv.member()
    order, _review, _ = await _publish(rv, customer)
    for name, kwargs in (("list", {}), ("remove", {"order_number": order.number}), ("resend", {"order_number": 1})):
        interaction = await invoke(rv, customer, name, **kwargs)
        assert reply_embed(interaction).description == rv.text(ck.ERROR_PERMISSION)
        assert is_ephemeral(interaction)
    assert (await review_of(rv, order)).status == repo.PUBLISHED
    assert len(reviews_channel(rv).messages) == 1


async def test_checkout_staff_role_counts_as_staff(tmp_path: Path) -> None:
    settings = make_settings(
        {"server": {"roles": [{"key": "staff"}, {"key": "cashier"}]}, "checkout": {"staff_roles": ["cashier"]}}
    )
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        await provision_channels(tenv)
        role = tenv.guild.add_role(name="cashier", position=6)
        await tenv.app.resources.remember("role", "cashier", role.id)
        cashier = tenv.guild.add_member(name="cashier", roles=[role])
        shown = await invoke(tenv, cashier, "list")
        assert reply_embed(shown).title == _op(tenv, "reviews.list.title", count=0)
    finally:
        await tenv.close()


async def test_commands_when_feature_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.THANKS: ""})
    try:
        staff = tenv.member(staff=True)
        interaction = await invoke(tenv, staff, "list")
        assert reply_embed(interaction).title == _op(tenv, "command.feature_disabled", feature="reviews")
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------------------------------


async def test_list(rv: TestEnv) -> None:
    staff = rv.member(staff=True)
    empty = await invoke(rv, staff, "list")
    assert reply_embed(empty).description == _op(rv, "reviews.list.empty")
    published_order, published, _ = await _publish(rv, rv.member(name="a"), rating="4")
    waiting_user = rv.member(name="b")
    waiting_order, waiting, _ = await prompted(rv, waiting_user)
    shown = await invoke(rv, staff, "list")
    embed = reply_embed(shown)
    assert embed.title == _op(rv, "reviews.list.title", count=2)
    channel = reviews_channel(rv)
    assert embed.description == "\n".join(
        [
            _op(
                rv,
                "reviews.list.line",
                order_number=number_text(rv, waiting_order.number),
                status=_op(rv, "reviews.status.prompted"),
                rating=_op(rv, "reviews.list.none"),
                member=f"<@{waiting_user.id}>",
                message=_op(rv, "reviews.list.none"),
            ),
            _op(
                rv,
                "reviews.list.line",
                order_number=number_text(rv, published_order.number),
                status=_op(rv, "reviews.status.published"),
                rating=4,
                member=f"<@{published.user_id}>",
                message=f"https://discord.com/channels/{rv.guild.id}/{channel.id}/{published.published_message_id}",
            ),
        ]
    )
    filtered = await invoke(rv, staff, "list", status="published")
    assert reply_embed(filtered).title == _op(rv, "reviews.list.title", count=1)
    assert waiting.status == repo.PROMPTED


# --------------------------------------------------------------------------------------------------
# remove
# --------------------------------------------------------------------------------------------------


async def test_remove_published_review(rv: TestEnv) -> None:
    staff = rv.member(staff=True)
    order, _review, _ = await _publish(rv, rv.member())
    channel = reviews_channel(rv)
    assert len(channel.messages) == 1
    removed = await invoke(rv, staff, "remove", order_number=order.number)
    number = number_text(rv, order.number)
    embed = reply_embed(removed)
    assert embed.title == _op(rv, "reviews.cmd.remove_title")
    assert embed.description == "\n".join(
        [_op(rv, "reviews.cmd.removed", order_number=number), _op(rv, "reviews.cmd.message_deleted")]
    )
    assert is_ephemeral(removed)
    assert channel.messages == []
    assert (await review_of(rv, order)).status == repo.DECLINED
    event = (await audit_actions(rv))[-1]
    assert event.action == "reviews.removed" and event.actor_id == staff.id
    assert event.details["previous_status"] == repo.PUBLISHED
    again = await invoke(rv, staff, "remove", order_number=order.number)
    assert reply_embed(again).description == _op(rv, "reviews.cmd.already_removed", order_number=number)


async def test_remove_when_message_already_deleted(rv: TestEnv) -> None:
    staff = rv.member(staff=True)
    order, _, _ = await _publish(rv, rv.member())
    await reviews_channel(rv).messages[0].delete()
    removed = await invoke(rv, staff, "remove", order_number=order.number)
    assert _op(rv, "reviews.cmd.message_missing") in (reply_embed(removed).description or "")
    assert (await review_of(rv, order)).status == repo.DECLINED


async def test_remove_failure_changes_nothing(rv: TestEnv) -> None:
    staff = rv.member(staff=True)
    order, _, _ = await _publish(rv, rv.member())
    message = reviews_channel(rv).messages[0]

    async def forbidden(*, delay: float | None = None) -> None:
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    message.delete = forbidden  # type: ignore[method-assign]
    refused = await invoke(rv, staff, "remove", order_number=order.number)
    assert reply_embed(refused).description == _op(
        rv, "reviews.cmd.remove_failed", order_number=number_text(rv, order.number), status=403
    )
    assert (await review_of(rv, order)).status == repo.PUBLISHED


async def test_remove_open_request_in_order_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        staff, customer = tenv.member(staff=True), tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None
        (request,) = order.channel.messages
        removed = await invoke(tenv, staff, "remove", order_number=order.number)
        assert reply_embed(removed).description == _op(
            tenv, "reviews.cmd.removed", order_number=number_text(tenv, order.number)
        )
        assert only_embed(request).description == rendered(
            tenv, k.EXPIRED, order_number=number_text(tenv, order.number)
        )
        assert request.view is None
        review = await review_of(tenv, order)
        assert review.status == repo.DECLINED
        late = await click(tenv.app, user=customer, custom_id=views.rate_id(review.id), values=["5"])
        assert reply_embed(late).description == rendered(tenv, k.EXPIRED, order_number=number_text(tenv, order.number))
    finally:
        await tenv.close()


async def test_remove_cancels_a_pending_request(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_delay_minutes": 60}})
    try:
        staff, customer = tenv.member(staff=True), tenv.member()
        order = await make_order(tenv, customer)
        await service.schedule_review_prompt(tenv.app, order.id)
        removed = await invoke(tenv, staff, "remove", order_number=order.number)
        number = number_text(tenv, order.number)
        assert reply_embed(removed).description == _op(tenv, "reviews.cmd.prompt_cancelled", order_number=number)
        scheduled = await task(tenv, service.prompt_dedupe(order.id))
        assert scheduled is not None and scheduled.status == "cancelled"
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        assert customer.dms == []
        nothing = await invoke(tenv, staff, "remove", order_number=order.number)
        assert reply_embed(nothing).description == _op(tenv, "reviews.cmd.no_review", order_number=number)
        unknown = await invoke(tenv, staff, "remove", order_number=4242)
        assert reply_embed(unknown).description == _op(
            tenv, "reviews.cmd.order_not_found", order_number=number_text(tenv, 4242)
        )
    finally:
        await tenv.close()


async def test_remove_during_failed_publication(rv: TestEnv) -> None:
    staff, customer = rv.member(staff=True), rv.member()
    order, review, dm = await prompted(rv, customer)
    channel = reviews_channel(rv)
    channel.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
    await click(rv.app, user=customer, custom_id=views.rate_id(review.id), message=dm, values=["1"], dm=True)
    await submit_modal(
        rv.app,
        user=customer,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: "[spam]"},
        message=dm,
        dm=True,
    )
    await invoke(rv, staff, "remove", order_number=order.number)
    channel.fail_send = None
    retry = await task(rv, service.publish_dedupe(review.id))
    assert retry is not None and retry.status == "cancelled"
    assert await service.publish_review(rv.app, review.id) is service.Publish.SKIPPED
    assert channel.messages == []


# --------------------------------------------------------------------------------------------------
# resend
# --------------------------------------------------------------------------------------------------


async def test_resend_unanswered_request(rv: TestEnv) -> None:
    staff, customer = rv.member(staff=True), rv.member()
    order, review, _ = await prompted(rv, customer)
    resent = await invoke(rv, staff, "resend", order_number=order.number)
    number = number_text(rv, order.number)
    assert reply_embed(resent).title == _op(rv, "reviews.cmd.resend_title")
    assert reply_embed(resent).description == _op(rv, "reviews.cmd.resent_dm", order_number=number)
    assert len(customer.dms) == 2
    assert only_embed(customer.dms[-1]).description == rendered(rv, k.PROMPT_DESCRIPTION, order_number=number)
    event = (await audit_actions(rv))[-1]
    assert event.action == "reviews.resent" and event.actor_id == staff.id and event.target_id == str(review.id)


async def test_resend_falls_back_to_channel_and_reports_failure(rv: TestEnv) -> None:
    staff, customer = rv.member(staff=True), rv.member()
    order, _, _ = await prompted(rv, customer)
    customer.dm_forbidden = True
    resent = await invoke(rv, staff, "resend", order_number=order.number)
    number = number_text(rv, order.number)
    assert reply_embed(resent).description == _op(rv, "reviews.cmd.resent_channel", order_number=number)
    assert order.channel is not None and len(order.channel.messages) == 1
    await order.channel.delete()
    failed = await invoke(rv, staff, "resend", order_number=order.number)
    assert reply_embed(failed).description == _op(rv, "reviews.cmd.resend_failed", order_number=number)


async def test_resend_refusals(rv: TestEnv) -> None:
    staff, customer = rv.member(staff=True), rv.member()
    order, review, _ = await _publish(rv, customer)
    number = number_text(rv, order.number)
    published = await invoke(rv, staff, "resend", order_number=order.number)
    assert reply_embed(published).description == _op(
        rv, "reviews.cmd.resend_wrong_state", order_number=number, status=_op(rv, "reviews.status.published")
    )
    pending = await make_order(rv, customer, status="paid")
    refused = await invoke(rv, staff, "resend", order_number=pending.number)
    assert reply_embed(refused).description == _op(
        rv, "reviews.cmd.not_fulfilled", order_number=number_text(rv, pending.number), status="paid"
    )
    unknown = await invoke(rv, staff, "resend", order_number=9999)
    assert reply_embed(unknown).description == _op(
        rv, "reviews.cmd.order_not_found", order_number=number_text(rv, 9999)
    )
    assert len(customer.dms) == 1 and review.status == repo.PUBLISHED


async def test_resend_before_the_scheduled_request(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"reviews": {"prompt_delay_minutes": 60}})
    try:
        staff, customer = tenv.member(staff=True), tenv.member()
        order = await make_order(tenv, customer)
        await service.schedule_review_prompt(tenv.app, order.id)
        resent = await invoke(tenv, staff, "resend", order_number=order.number)
        assert reply_embed(resent).description == _op(
            tenv, "reviews.cmd.resent_dm", order_number=number_text(tenv, order.number)
        )
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        assert len(customer.dms) == 1  # the scheduled copy was cancelled
        review = await review_of(tenv, order)
        await click(
            tenv.app, user=customer, custom_id=views.rate_id(review.id), message=customer.dms[0], values=["3"], dm=True
        )
        assert (await review_of(tenv, order)).rating == 3
    finally:
        await tenv.close()
