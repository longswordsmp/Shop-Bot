"""Staff alerts in the staff channel (new and paid orders, failures) and the Owner's private alerts."""

from __future__ import annotations

import types
from pathlib import Path

import discord

from shopbot.core.events import FAILURE
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import alerts
from shopbot.features.operations import content_keys as ok
from shopbot.ui.routing import Ctx
from tests.checkout.util import add_product, money, order, place_order
from tests.fakes import click, http_error
from tests.helpers import OWNER_ID, TestEnv
from tests.operations.util import (
    audit_events,
    make_ops,
    mark,
    owner,
    run_tasks,
    staff_channel,
    tasks_by_prefix,
)


def _render(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.app.content.render(key, **values)
    assert text is not None, key
    return text


async def _order(tenv: TestEnv) -> tuple[m.Order, int]:
    customer = tenv.member()
    product = await add_product(tenv, "[p1]", price=1000, stock=5)
    placed, ch, _ = await place_order(tenv, customer, [(product.id, 1)])
    return placed, ch.id


def _order_values(tenv: TestEnv, placed: m.Order, channel_id: int, status: str) -> dict[str, str | int]:
    from shopbot.features.checkout import content_keys as ck

    return {
        "order_number": str(placed.number),
        "customer_mention": f"<@{placed.user_id}>",
        "total": money(tenv, placed.total_minor),
        "status": tenv.text(ck.status_label_key(status)),
        "order_channel": f"<#{channel_id}>",
    }


async def test_new_order_alert_pings_staff_and_reaches_the_owner(ops: TestEnv) -> None:
    placed, channel_id = await _order(ops)
    await run_tasks(ops)
    alert = staff_channel(ops).messages[-1]
    values = _order_values(ops, placed, channel_id, m.PENDING)
    assert alert.embeds[0].description == _render(ops, ok.alert_key(ok.NEW_ORDER, "description"), **values)
    assert alert.embeds[0].title == _render(ops, ok.alert_key(ok.NEW_ORDER, "title"), **values)
    assert alert.content == f"<@&{ops.staff_role.id}>"
    assert alert.allowed_mentions is not None and [r.id for r in alert.allowed_mentions.roles] == [ops.staff_role.id]
    # The link button jumps to the live order record.
    current = await order(ops, placed.id)
    link = alert.components()[0]
    assert (
        link.url
        == f"https://discord.com/channels/{ops.app.env.guild_id}/{current.staff_channel_id}/{current.staff_message_id}"
    )
    dm = owner(ops).dms[-1]
    assert dm.embeds[0].description == _render(ops, ok.owner_key(ok.NEW_ORDER, "description"), **values)
    assert dm.content in ("", None)
    # Re-running the follow-ups (as the durable task would after a crash) never alerts twice.
    await checkout_service.run_submit_followups(ops.app, placed.id, adopt=True)
    await run_tasks(ops)
    assert (
        len([msg for msg in staff_channel(ops).messages if msg.embeds[0].description == alert.embeds[0].description])
        == 1
    )
    assert len(owner(ops).dms) == 1


async def test_owner_alert_uses_the_staff_text_when_its_own_is_empty(tmp_path: Path) -> None:
    tenv = await make_ops(
        tmp_path, content={ok.owner_key(ok.NEW_ORDER, "title"): "", ok.owner_key(ok.NEW_ORDER, "description"): ""}
    )
    try:
        placed, channel_id = await _order(tenv)
        await run_tasks(tenv)
        values = _order_values(tenv, placed, channel_id, m.PENDING)
        assert owner(tenv).dms[-1].embeds[0].description == _render(
            tenv, ok.alert_key(ok.NEW_ORDER, "description"), **values
        )
    finally:
        await tenv.close()


async def test_paid_alert_after_payment_confirmation(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, channel_id = await _order(ops)
    await run_tasks(ops)
    before = len(staff_channel(ops).messages)
    await mark(ops, staffer, placed.id, m.PAID)
    await run_tasks(ops)
    paid = staff_channel(ops).messages[before:]
    assert len(paid) == 1
    values = _order_values(ops, placed, channel_id, m.PAID)
    assert paid[0].embeds[0].description == _render(ops, ok.alert_key(ok.PAID_ORDER, "description"), **values)
    assert owner(ops).dms[-1].embeds[0].description == _render(
        ops, ok.owner_key(ok.PAID_ORDER, "description"), **values
    )


async def test_owner_dm_failure_falls_back_to_the_staff_channel(ops: TestEnv) -> None:
    owner(ops).dm_forbidden = True
    placed, channel_id = await _order(ops)
    await run_tasks(ops)
    values = _order_values(ops, placed, channel_id, m.PENDING)
    owner_text = _render(ops, ok.owner_key(ok.NEW_ORDER, "description"), **values)
    fallback = [msg for msg in staff_channel(ops).messages if msg.content == f"<@{OWNER_ID}>"]
    assert len(fallback) == 1 and fallback[0].embeds[0].description == owner_text
    mentions = fallback[0].allowed_mentions
    assert [u.id for u in mentions.users] == [OWNER_ID] and mentions.roles is False  # the Owner is pinged, no role
    failed = await audit_events(ops, "operations.owner_dm_failed")
    assert len(failed) == 1 and failed[0].details == {"kind": "operations.owner.new_order"}


async def test_no_owner_alerts_when_switched_off(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, {"operations": {"owner_dm_alerts": False}})
    try:
        await _order(tenv)
        await run_tasks(tenv)
        assert owner(tenv).dms == []
        assert len(staff_channel(tenv).messages) == 1
    finally:
        await tenv.close()


async def test_nothing_is_sent_while_operations_is_not_ready(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, content={ok.alert_key(ok.NEW_ORDER, "description"): ""})
    try:
        assert not tenv.app.ready("operations")
        await _order(tenv)
        await run_tasks(tenv)
        assert staff_channel(tenv).messages == [] and owner(tenv).dms == []
        [task] = await tasks_by_prefix(tenv, "operations.alert:new_order")
        assert task.status == "done"
    finally:
        await tenv.close()


async def test_a_failed_staff_channel_post_is_retried_once_discord_recovers(ops: TestEnv) -> None:
    staff = staff_channel(ops)
    staff.fail_send = http_error(discord.HTTPException, 503, 0, "Service Unavailable")
    await _order(ops)
    await run_tasks(ops)
    assert staff.messages == []
    [task] = await tasks_by_prefix(ops, "operations.alert:new_order")
    assert task.status == "pending" and task.attempts == 1
    staff.fail_send = None
    ops.clock.advance(3600)
    await run_tasks(ops)
    assert len(staff.messages) == 1
    assert len(owner(ops).dms) == 1  # the Owner's DM went out on the first attempt and is not repeated


# -- failures -------------------------------------------------------------------------------------------------------


async def test_a_failing_button_is_audited_and_reported_without_details(ops: TestEnv) -> None:
    async def broken(ctx: Ctx) -> None:
        raise RuntimeError("secret token abc.def.ghi in the error text")

    ops.app.router.component("zz", "boom", feature=None)(broken)
    user = ops.member()
    clicked = await click(ops.app, user=user, custom_id="sb:zz:boom")
    assert clicked.last_sent is not None  # the member got the configured error message
    [event] = await audit_events(ops, "operations.error")
    assert event.target_type == "interaction" and event.target_id == "zz:boom"
    assert event.details["error"] == "RuntimeError" and "secret" not in str(event.details)
    await run_tasks(ops)
    [alert] = staff_channel(ops).messages
    values = {"source": "zz:boom", "error_id": event.details["error_id"], "error": "RuntimeError"}
    assert alert.embeds[0].description == _render(ops, ok.failure_key(ok.FAILURE_ERROR, "description"), **values)
    assert "secret" not in alert.text() and alert.content in ("", None)  # errors do not ping
    # The same failure again within the window is audited but grouped into the same alert.
    await click(ops.app, user=user, custom_id="sb:zz:boom")
    await run_tasks(ops)
    assert len(await audit_events(ops, "operations.error")) == 2
    assert len(staff_channel(ops).messages) == 1
    ops.clock.advance(alerts.FAILURE_WINDOW_SECONDS)
    await click(ops.app, user=user, custom_id="sb:zz:boom")
    await run_tasks(ops)
    assert len(staff_channel(ops).messages) == 2


async def test_an_undeliverable_customer_notification_alerts_staff(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, channel_id = await _order(ops)
    await run_tasks(ops)
    customer = ops.guild.get_member(placed.user_id)
    assert customer is not None
    customer.dm_forbidden = True
    ops.guild.get_channel(channel_id).fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    before = len(staff_channel(ops).messages)
    await mark(ops, staffer, placed.id, m.PAID)
    await run_tasks(ops)
    [event] = await audit_events(ops, "notifications.failed")
    assert event.target_id == str(placed.user_id) and event.details == {"kind": "checkout.paid"}
    new = staff_channel(ops).messages[before:]
    values = {"user_mention": f"<@{placed.user_id}>", "notification": "checkout.paid"}
    expected = _render(ops, ok.failure_key(ok.FAILURE_NOTIFICATION, "description"), **values)
    assert [msg.embeds[0].description for msg in new if msg.embeds[0].description == expected] == [expected]


async def test_a_permanently_failed_task_alerts_staff_and_the_owner(ops: TestEnv) -> None:
    async def broken(app: object, task: object) -> None:
        raise RuntimeError("boom")

    ops.app.scheduler.register("test.broken", broken)
    task_id = await ops.app.scheduler.schedule("test.broken", run_at=ops.clock.now(), max_attempts=1)
    await run_tasks(ops)
    values = {"task": "test.broken", "task_id": task_id or 0, "attempts": 1}
    [alert] = staff_channel(ops).messages
    assert alert.embeds[0].description == _render(ops, ok.failure_key(ok.FAILURE_TASK, "description"), **values)
    assert alert.content == f"<@&{ops.staff_role.id}>"
    assert owner(ops).dms[-1].embeds[0].description == _render(
        ops, ok.owner_failure_key(ok.FAILURE_TASK, "description"), **values
    )
    [audited] = await audit_events(ops, "task.failed")
    assert audited.details["error"] == "RuntimeError"  # error text is reduced to its type in the audit log


async def test_failures_of_the_alert_tasks_never_alert_again(ops: TestEnv) -> None:
    await ops.app.events.publish(FAILURE, kind="task", source=alerts.TASK_ALERT, task_id=1, attempts=5)
    await ops.app.events.publish(FAILURE, kind="notification", source="operations.owner.new_order", user_id=1)
    await ops.app.events.publish(FAILURE, kind="unknown", source="x")
    assert await tasks_by_prefix(ops, "operations.alert:failure") == []
    # Other operations tasks (e.g. the customer role) are reported like any other task.
    await ops.app.events.publish(FAILURE, kind="task", source="operations.customer_role", task_id=2, attempts=5)
    assert len(await tasks_by_prefix(ops, "operations.alert:failure:task:2")) == 1


async def test_publish_reports_unmentionable_ping_roles_and_missing_resources(ops: TestEnv) -> None:
    lines = await alerts.publish(ops.app)
    assert ops.app.op("operations.publish.alerts", channel=staff_channel(ops).mention) in lines
    assert ops.app.op("operations.publish.ping_not_mentionable", roles=ops.staff_role.mention) in lines
    ops.staff_role.mentionable = True
    assert ops.app.op(
        "operations.publish.ping_not_mentionable", roles=ops.staff_role.mention
    ) not in await alerts.publish(ops.app)
    await ops.app.resources.forget("channel", "staff_alerts")
    lines = await alerts.publish(ops.app)
    assert ops.app.op("operations.publish.staff_channel_missing", name="staff_alerts") in lines


async def test_a_failing_command_is_audited_and_reported(ops: TestEnv) -> None:
    from discord import app_commands

    from shopbot.commands import on_tree_error
    from tests.fakes import FakeInteraction

    staffer = ops.member(name="staffer", staff=True)
    interaction = FakeInteraction(
        client=ops.bot, user=staffer, guild=ops.guild, type=discord.InteractionType.application_command
    )
    interaction.command = types.SimpleNamespace(qualified_name="order view", name="view")  # type: ignore[assignment]
    error = app_commands.CommandInvokeError(interaction.command, ValueError("private detail"))  # type: ignore[arg-type]
    await on_tree_error(interaction, error)  # type: ignore[arg-type]
    assert interaction.last_sent is not None  # the staff member got the operator error with its reference
    [event] = await audit_events(ops, "operations.error")
    assert event.target_type == "command" and event.target_id == "order view"
    assert event.details["error"] == "ValueError" and "private" not in str(event.details)
    await run_tasks(ops)
    [alert] = staff_channel(ops).messages
    assert "order view" in alert.text() and "private" not in alert.text()


async def test_payment_submitted_alerts_staff_each_time_gift_cards_cover_the_total(tmp_path: Path) -> None:
    from shopbot.features.checkout import staff_views, views
    from shopbot.features.checkout.payments import gift_card
    from tests.fakes import submit_modal

    tenv = await make_ops(tmp_path, {"payments": {"provider": "gift_card"}})
    try:
        placed, channel_id = await _order(tenv)
        customer = tenv.guild.get_member(placed.user_id)

        async def pay(code: str) -> None:
            await submit_modal(
                tenv.app,
                user=customer,
                custom_id=views.payment_form_submit_id(placed.id),
                fields={gift_card.CODE_INPUT: code, gift_card.AMOUNT_INPUT: "10.00"},
            )
            await run_tasks(tenv)

        key = ok.alert_key(ok.PAYMENT_SUBMITTED, "description")
        values = _order_values(tenv, placed, channel_id, m.PAYMENT_SUBMITTED)
        await pay("ABCD-EFGH-1234-5678")
        alerts_sent = [
            msg
            for msg in staff_channel(tenv).messages
            if msg.embeds and msg.embeds[0].description == _render(tenv, key, **values)
        ]
        assert len(alerts_sent) == 1
        # Staff refuse the card; the customer pays again: the payment staff are alerted again.
        admin = tenv.member(name="admin", admin=True)
        await submit_modal(
            tenv.app,
            user=admin,
            custom_id=views.gift_card_problem_form_id(placed.id),
            fields={staff_views.REASON_INPUT: "[reason]"},
        )
        await pay("WXYZ-9876-5432-QRST")
        assert (await order(tenv, placed.id)).status == m.PAYMENT_SUBMITTED
        again = [
            msg
            for msg in staff_channel(tenv).messages
            if msg.embeds and msg.embeds[0].description == _render(tenv, key, **values)
        ]
        assert len(again) == 2
    finally:
        await tenv.close()
