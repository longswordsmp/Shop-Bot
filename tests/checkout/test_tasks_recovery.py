"""Scheduled tasks (session expiry, payment reminder/expiry), restart recovery, and the channel-delete listener."""

from __future__ import annotations

from pathlib import Path

from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.checkout import tasks as checkout_tasks
from shopbot.features.tickets import events as ticket_events
from tests.checkout.util import (
    add_product,
    audit_actions,
    balance,
    give_credits,
    make_env,
    money,
    order,
    place_order,
    press,
    product,
    reload_session,
    render,
    restarted_app,
    set_qty,
    staff_message,
    start,
    submit,
    task,
    ticket_status,
    to_review,
)
from tests.helpers import TestEnv

TIMERS = {"checkout": {"session_expiry_minutes": 30, "payment_reminder_minutes": 30, "payment_expiry_minutes": 60}}


async def test_idle_session_expires_and_activity_pushes_it_back(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        _, session, _, message = await start(tenv, customer)
        tenv.clock.advance(20 * 60)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)  # activity
        tenv.clock.advance(15 * 60)
        await tenv.app.scheduler.run_due()
        assert (await reload_session(tenv, session.id)).status == m.ACTIVE
        pending = await task(tenv, service.session_task_key(session.id))
        assert pending.status == "pending" and pending.run_at == tenv.clock.now() - 15 * 60 + 30 * 60
        tenv.clock.advance(16 * 60)
        await tenv.app.scheduler.run_due()
        expired = await reload_session(tenv, session.id)
        assert expired.status == m.SESSION_EXPIRED
        assert message.embeds[0].description == tenv.text(k.EXPIRED_DESCRIPTION)
        assert message.embeds[0].title == tenv.text(k.EXPIRED_TITLE)
        assert message.custom_ids() == []
        assert await ticket_status(tenv, session.ticket_id) == "closed"
        assert "checkout.session_expired" in await audit_actions(tenv)
        late = await press(tenv, customer, message, views.go_id(session.id, m.CART))
        assert late.last_sent is not None and late.last_sent.embeds[0].description == tenv.text(k.ERROR_SESSION_CLOSED)
    finally:
        await tenv.close()


async def test_session_expiry_switched_off_later_does_nothing(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        _, session, _, _ = await start(tenv, tenv.member())
        tenv.app.settings = make_settings_without_timers(tenv)
        tenv.clock.advance(31 * 60)
        await tenv.app.scheduler.run_due()
        assert (await reload_session(tenv, session.id)).status == m.ACTIVE
        assert (await task(tenv, service.session_task_key(session.id))).status == "done"
    finally:
        await tenv.close()


def make_settings_without_timers(tenv: TestEnv):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    settings = tenv.app.settings
    return replace(settings, checkout=replace(settings.checkout, session_expiry_minutes=0))


async def test_payment_reminder_once_then_expiry(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=1000, stock=2)
        await give_credits(tenv, customer.id, 100)
        _, session, ch, message = await start(tenv, customer)
        await to_review(tenv, customer, message, session.id, [(p1.id, 2)])
        await press(tenv, customer, message, views.credits_id(session.id, True))
        await submit(tenv, customer, message, session.id)
        placed = await order(tenv, 1)
        assert await balance(tenv, customer.id) == 0
        reminder = await task(tenv, service.reminder_task_key(placed.id))
        expiry = await task(tenv, service.expiry_task_key(placed.id))
        assert reminder.run_at == placed.created_at + 30 * 60 and expiry.run_at == placed.created_at + 60 * 60

        tenv.clock.advance(30 * 60)
        await tenv.app.scheduler.run_due()
        values = {"order_number": "1", "total": money(tenv, 1900), "channel_mention": f"<#{ch.id}>"}
        assert len(customer.dms) == 2  # submission received, then the reminder
        dm = customer.dms[1]
        assert dm.embeds[0].description == render(tenv, k.notify_description_key(k.NOTIFY_PAYMENT_REMINDER), **values)
        assert dm.embeds[1].description == render(
            tenv, "checkout.payment.manual.instructions", order_number="1", total=money(tenv, 1900)
        )
        # the reminder is sent once, even if its task runs again
        await tenv.app.db.transaction(
            task_repo.schedule,
            kind=service.TASK_PAYMENT_REMINDER,
            run_at=tenv.clock.now(),
            payload={"order_id": placed.id},
            max_attempts=3,
            dedupe_key=service.reminder_task_key(placed.id),
            replace=True,
            now=tenv.clock.now(),
        )
        await tenv.app.scheduler.run_due()
        assert len(customer.dms) == 2

        tenv.clock.advance(30 * 60)
        await tenv.app.scheduler.run_due()
        expired = await order(tenv, placed.id)
        assert expired.status == m.EXPIRED
        assert (await product(tenv, p1.id)).reserved == 0
        assert await balance(tenv, customer.id) == 100
        assert customer.dms[-1].embeds[0].description == render(tenv, k.notify_description_key(m.EXPIRED), **values)
        assert message.embeds[0].title == render(
            tenv, k.status_title_key(m.EXPIRED), order_number="1", total=money(tenv, 1900)
        )
        assert await ticket_status(tenv, placed.ticket_id or 0) == "closed"
    finally:
        await tenv.close()


async def test_payment_tasks_do_nothing_after_payment(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, _, _ = await place_order(tenv, customer, [(p1.id, 1)])
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        assert (await task(tenv, service.reminder_task_key(placed.id))).status == "cancelled"
        dms = len(customer.dms)
        tenv.clock.advance(120 * 60)
        await tenv.app.scheduler.run_due()
        assert (await order(tenv, placed.id)).status == m.PAID
        assert len(customer.dms) == dms
    finally:
        await tenv.close()


async def test_early_expiry_task_is_rescheduled(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        p1 = await add_product(tenv, "[p1]")
        placed, _, _ = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        await tenv.app.db.transaction(
            task_repo.schedule,
            kind=service.TASK_PAYMENT_EXPIRE,
            run_at=tenv.clock.now(),
            payload={"order_id": placed.id},
            max_attempts=3,
            dedupe_key=service.expiry_task_key(placed.id),
            replace=True,
            now=tenv.clock.now(),
        )
        await tenv.app.scheduler.run_due()
        assert (await order(tenv, placed.id)).status == m.PENDING
        assert (await task(tenv, service.expiry_task_key(placed.id))).run_at == placed.created_at + 3600
    finally:
        await tenv.close()


async def test_restart_recovery(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        p1 = await add_product(tenv, "[p1]")
        gone_customer, kept_customer, buyer = tenv.member(name="a"), tenv.member(name="b"), tenv.member(name="c")
        _, gone_session, gone_channel, _ = await start(tenv, gone_customer)
        _, kept_session, _, kept_message = await start(tenv, kept_customer)
        placed, _, _ = await place_order(tenv, buyer, [(p1.id, 1)])
        # while the bot is offline: an order channel and the staff controls are deleted, tasks are lost
        del tenv.guild._channels[gone_channel.id]
        await staff_message(tenv, placed).delete()
        await tenv.app.db.run(lambda conn: conn.execute("DELETE FROM scheduled_tasks"))

        app = restarted_app(tenv)
        await app.resources.refresh()  # as ShopBot.on_ready does before feature recovery
        await ticket_events.on_ready(app)
        await checkout_tasks.on_ready(app)
        assert (await reload_session(tenv, gone_session.id)).status == m.SESSION_EXPIRED
        assert (await reload_session(tenv, kept_session.id)).status == m.ACTIVE
        assert await ticket_status(tenv, gone_session.ticket_id) == "deleted"
        recovered = await order(tenv, placed.id)
        assert recovered.staff_message_id is not None and recovered.staff_message_id != placed.staff_message_id
        assert views.staff_action_id(m.PAID, placed.id) in staff_message(tenv, recovered).custom_ids()
        assert await task(tenv, service.reminder_task_key(placed.id)) is not None
        assert await task(tenv, service.expiry_task_key(placed.id)) is not None
        assert await task(tenv, service.session_task_key(kept_session.id)) is not None
        # a second recovery changes nothing
        messages = len(staff_message(tenv, recovered).channel.messages)  # type: ignore[union-attr]
        await checkout_tasks.on_ready(app)
        assert len(staff_message(tenv, recovered).channel.messages) == messages  # type: ignore[union-attr]
        # the restarted app keeps serving the persistent controls
        from tests.fakes import submit_modal

        await submit_modal(
            app,
            user=kept_customer,
            custom_id=views.qty_id(kept_session.id, p1.id),
            fields={views.QUANTITY_INPUT: "1"},
            message=kept_message,
        )
        assert await app.db.run(repo.cart_quantities, kept_session.id) == {p1.id: 1}
    finally:
        await tenv.close()


async def test_recovery_sends_a_missing_workflow_message(co: TestEnv) -> None:
    customer = co.member()
    _, session, ch, message = await start(co, customer)
    await message.delete()
    await co.app.db.run(lambda conn: conn.execute("UPDATE checkout_sessions SET message_id = NULL"))
    app = restarted_app(co)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    resent = await reload_session(co, session.id)
    assert resent.message_id is not None
    assert ch.messages[-1].id == resent.message_id
    assert ch.messages[-1].embeds[0].title == co.text(k.BROWSE_TITLE)


async def test_channel_delete_listener(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]")
    customer, buyer = co.member(name="a"), co.member(name="b")
    _, session, ch, _ = await start(co, customer)
    placed, order_channel, _ = await place_order(co, buyer, [(p1.id, 1)])
    await ch.delete()
    await co.bot.emit("guild_channel_delete", ch)
    assert (await reload_session(co, session.id)).status == m.SESSION_EXPIRED
    await order_channel.delete()
    await co.bot.emit("guild_channel_delete", order_channel)
    assert (await order(co, placed.id)).status == m.PENDING  # staff keep their controls
    assert views.staff_action_id(m.PAID, placed.id) in staff_message(co, placed).custom_ids()
    await co.bot.emit("guild_channel_delete", order_channel)  # repeated event: harmless


async def test_paying_after_the_channel_was_deleted(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]", stock=1)
    staffer = co.member(name="staffer", staff=True)
    placed, order_channel, _ = await place_order(co, co.member(), [(p1.id, 1)])
    await order_channel.delete()
    await co.bot.emit("guild_channel_delete", order_channel)
    result = await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    assert result.last_sent is not None
    assert (await order(co, placed.id)).status == m.PAID
    assert (await product(co, p1.id)).stock_on_hand == 0


async def test_recovery_of_an_interrupted_start(co: TestEnv) -> None:
    from shopbot.features.tickets import service as tickets_service

    customer = co.member()
    # The process stopped after creating the channel but before activating it (session + message exist).
    _, session, _, _ = await start(co, customer)
    await co.app.db.run(
        lambda conn: conn.execute("UPDATE tickets SET status = 'creating' WHERE id = ?", (session.ticket_id,))
    )
    # ...and an order channel whose session was never recorded.
    orphan = await tickets_service.create_private_channel(
        co.app,
        member=co.member(name="orphan"),
        kind="order",
        category_id="checkout",
        discord_category_key="orders",
        channel_prefix_key=k.CHANNEL_PREFIX,
        extra_staff_role_keys=(),
        topic=None,
        limit=1,
    )
    co.clock.advance(checkout_tasks.ORPHAN_GRACE_SECONDS + 1)
    app = restarted_app(co)
    await app.resources.refresh()
    await ticket_events.on_ready(app)
    await checkout_tasks.on_ready(app)
    assert await ticket_status(co, session.ticket_id) == "open"
    assert (await reload_session(co, session.id)).status == m.ACTIVE
    assert await ticket_status(co, orphan.ticket.id) in ("closed", "failed")


async def test_young_orphans_are_left_alone_and_sessions_are_activated(co: TestEnv) -> None:
    from shopbot.features.tickets import service as tickets_service

    _, session, _, message = await start(co, co.member())
    await co.app.db.run(
        lambda conn: conn.execute("UPDATE tickets SET status = 'creating' WHERE id = ?", (session.ticket_id,))
    )
    orphan = await tickets_service.create_private_channel(
        co.app,
        member=co.member(name="orphan"),
        kind="order",
        category_id="checkout",
        discord_category_key="orders",
        channel_prefix_key=k.CHANNEL_PREFIX,
        extra_staff_role_keys=(),
        topic=None,
        limit=1,
    )
    await checkout_tasks.on_ready(co.app)
    assert await ticket_status(co, orphan.ticket.id) == "creating"
    ticket = await tickets_service.get_ticket(co.app, session.ticket_id)
    assert ticket is not None and ticket.status == "open" and ticket.control_message_id == message.id
