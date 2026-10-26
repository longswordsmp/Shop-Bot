"""State-change guards and durability: stale clicks, cancelled sessions whose channel must close, transient lookup
failures, durable follow-ups after a commit, restart recovery of channels, staff controls and form answers."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import discord

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.checkout import tasks as checkout_tasks
from shopbot.features.tickets import service as tickets_service
from shopbot.ui.custom_ids import cid
from shopbot.ui.routing import Ctx
from tests.checkout.util import (
    ANSWERS_IN_CHANNEL,
    FULL_SETTINGS,
    add_product,
    audit_actions,
    channel,
    control_ids,
    error_text,
    go,
    make_env,
    money,
    op_text,
    order,
    place_order,
    press,
    purchase_link,
    reload_session,
    render,
    restarted_app,
    session_of,
    set_qty,
    staff_message,
    start,
    task,
    ticket_status,
    workflow_message,
)
from tests.fakes import FakeResponse, click, http_error, submit_modal
from tests.helpers import GUILD_ID, TestEnv, deep_merge

TIMERS = {"checkout": {"session_expiry_minutes": 30, "payment_reminder_minutes": 30, "payment_expiry_minutes": 60}}


def _notified(tenv: TestEnv, status: str, placed: m.Order, channel_id: int) -> str:
    return render(
        tenv,
        k.notify_description_key(status),
        order_number=str(placed.number),
        total=money(tenv, placed.total_minor),
        channel_mention=f"<#{channel_id}>",
        **purchase_link(tenv),  # used by the texts that point to Report a problem
    )


async def _after_followup_delay(tenv: TestEnv) -> None:
    tenv.clock.advance(repo.FOLLOWUP_DELAY_SECONDS)
    await tenv.app.scheduler.run_due()


# -- [authz #0] the customer cancels only an unpaid order ------------------------------------------------------------


async def test_customer_cancel_never_cancels_an_order_paid_meanwhile(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", stock=3)
    placed, _, message = await place_order(co, customer, [(p1.id, 1)])
    original = Ctx.defer_update

    async def staff_pays_meanwhile(self: Ctx) -> None:
        await original(self)
        if self.route.action == "ocancel_yes":
            await service.change_status(co.app, placed.id, m.PAID, actor_id=staffer.id)

    monkeypatch.setattr(Ctx, "defer_update", staff_pays_meanwhile)
    result = await press(co, customer, message, views.order_cancel_yes_id(placed.id))
    assert error_text(result) == co.text(k.ERROR_ORDER_NOT_CANCELLABLE)
    current = await order(co, placed.id)
    assert current.status == m.PAID
    events = await co.app.db.run(repo.events, placed.id)
    assert [(e.from_status, e.to_status) for e in events] == [(None, m.PENDING), (m.PENDING, m.PAID)]
    assert message.embeds[0].title == render(
        co, k.status_title_key(m.PAID), order_number="1", total=money(co, current.total_minor)
    )


# -- [authz #3] staff buttons act only on the status they were rendered for ----------------------------------------


async def test_a_stale_staff_cancel_does_not_cancel_a_paid_order(co: TestEnv) -> None:
    customer = co.member()
    first, second = co.member(name="a", staff=True), co.member(name="b", staff=True)
    p1 = await add_product(co, "[p1]", stock=4)
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    control = staff_message(co, placed)
    rendered_cancel = views.staff_action_id(m.CANCELLED, placed.id, m.PENDING)
    assert rendered_cancel in control.custom_ids()
    await service.change_status(co.app, placed.id, m.PAID, actor_id=second.id)  # B's click; A still sees 'pending'
    stale = await press(co, first, control, rendered_cancel)
    assert op_text(stale) == co.app.op("checkout.op.already_handled", number=1, status=m.PAID)
    current = await order(co, placed.id)
    assert current.status == m.PAID and current.refunded_minor == 0
    assert views.staff_action_id(m.CANCELLED, placed.id, m.PAID) in staff_message(co, current).custom_ids()
    # A control rendered before buttons carried their status counts as stale, too.
    legacy = await press(co, first, control, cid("cs", "cancel", placed.id))
    assert op_text(legacy) == co.app.op("checkout.op.already_handled", number=1, status=m.PAID)
    assert (await order(co, placed.id)).status == m.PAID
    # The current controls still work.
    fresh = await press(co, first, control, views.staff_action_id(m.CANCELLED, placed.id, m.PAID))
    assert (op_text(fresh) or "").startswith(co.app.op("checkout.op.status_changed", number=1, status=m.CANCELLED))


# -- [authz #1 / concurrency #0 / discord-api #0] cancelled sessions always close their channel ----------------------


async def test_cancelling_closes_the_channel_even_when_the_answer_fails(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    _, session, _, message = await start(co, customer)
    await press(co, customer, message, views.cancel_id(session.id))

    async def expired_interaction(self: FakeResponse, **kwargs: Any) -> None:
        raise http_error(discord.NotFound, 404, 10062, "Unknown interaction")

    monkeypatch.setattr(FakeResponse, "edit_message", expired_interaction)
    await press(co, customer, message, views.cancel_yes_id(session.id))
    monkeypatch.undo()
    assert (await reload_session(co, session.id)).status == m.SESSION_CANCELLED
    assert await ticket_status(co, session.ticket_id) == "closed"
    assert "checkout.session_cancelled" in await audit_actions(co)
    co.clock.advance(service.START_COOLDOWN_SECONDS)
    again = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(again) != render(co, k.LIMIT_REACHED, limit=1)
    assert (await session_of(co, customer)).id != session.id


async def test_a_failed_close_is_repeated_by_the_durable_channel_check(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    _, session, _, message = await start(co, customer)
    original = tickets_service.close_ticket

    async def broken_close(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(tickets_service, "close_ticket", broken_close)
    await press(co, customer, message, views.cancel_yes_id(session.id))
    assert await ticket_status(co, session.ticket_id) == "open"
    monkeypatch.setattr(tickets_service, "close_ticket", original)
    await _after_followup_delay(co)
    assert await ticket_status(co, session.ticket_id) == "closed"


async def test_an_expiring_session_closes_its_channel_even_when_rendering_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        _, session, _, _ = await start(tenv, tenv.member())

        async def broken_edit(*args: Any, **kwargs: Any) -> None:
            raise ConnectionResetError(104, "Connection reset by peer")

        monkeypatch.setattr(service, "_edit_session_message", broken_edit)
        tenv.clock.advance(31 * 60)
        await tenv.app.scheduler.run_due()
        assert (await reload_session(tenv, session.id)).status == m.SESSION_EXPIRED
        assert await ticket_status(tenv, session.ticket_id) == "closed"
        assert "checkout.session_expired" in await audit_actions(tenv)
    finally:
        await tenv.close()


async def test_restart_recovery_closes_channels_that_can_no_longer_be_used(co: TestEnv) -> None:
    customer, buyer = co.member(name="a"), co.member(name="b")
    p1 = await add_product(co, "[p1]")
    # A session cancelled, and an order rejected, while the process stopped before closing their channels.
    _, session, _, _ = await start(co, customer)
    await co.app.db.transaction(repo.end_session, session.id, m.SESSION_CANCELLED, co.clock.now())
    placed, _, _ = await place_order(co, buyer, [(p1.id, 1)])
    await co.app.db.transaction(
        repo.transition,
        guild_id=GUILD_ID,
        order_id=placed.id,
        allowed_from=(m.PENDING,),
        to=m.REJECTED,
        actor_id=None,
        note=None,
        now=co.clock.now(),
    )
    await co.app.db.run(lambda conn: conn.execute("DELETE FROM scheduled_tasks"))  # the follow-ups were lost too
    assert await ticket_status(co, session.ticket_id) == "open"
    assert await ticket_status(co, placed.ticket_id or 0) == "open"
    app = restarted_app(co)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    assert await ticket_status(co, session.ticket_id) == "closed"
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    # Neither member is blocked by the open-order limit any more.
    co.clock.advance(service.START_COOLDOWN_SECONDS)
    for member in (customer, buyer):
        reply = await click(app, user=member, custom_id=views.start_id())
        assert error_text(reply) != render(co, k.LIMIT_REACHED, limit=1)


# -- [authz #2] a transient lookup failure never ends a live session -------------------------------------------------


async def test_a_transient_channel_lookup_failure_keeps_the_session(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]")
    _, session, ch, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 2)
    real_get, real_fetch = co.bot.get_channel, co.bot.fetch_channel

    def uncached(channel_id: int) -> Any:
        return None if channel_id == ch.id else real_get(channel_id)

    async def unavailable(channel_id: int) -> Any:
        if channel_id == ch.id:
            raise http_error(discord.DiscordServerError, 503, 0, "Service Unavailable")
        return await real_fetch(channel_id)

    monkeypatch.setattr(co.bot, "get_channel", uncached)
    monkeypatch.setattr(co.bot, "fetch_channel", unavailable)
    co.clock.advance(60)
    again = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(again) == co.text(core.ERROR_GENERIC)
    await checkout_tasks.on_ready(co.app)  # restart recovery with the same failure changes nothing either
    assert (await reload_session(co, session.id)).status == m.ACTIVE
    assert await ticket_status(co, session.ticket_id) == "open"
    assert await co.app.db.run(repo.cart_quantities, session.id) == {p1.id: 2}
    monkeypatch.undo()
    later = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(later) == render(co, k.SESSION_EXISTS, channel_mention=f"<#{ch.id}>")


# -- [concurrency #1] side effects after a committed status change are durable -------------------------------------


async def test_an_error_in_one_follow_up_step_skips_nothing_else(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, ch, message = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    original = service.refresh_customer_message

    async def network_error(*args: Any, **kwargs: Any) -> bool:
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(service, "refresh_customer_message", network_error)
    result = await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.FULFILLED, placed.id, m.PAID))
    assert op_text(result) == co.app.op("checkout.op.status_changed", number=1, status=m.FULFILLED)
    assert (await order(co, placed.id)).status == m.FULFILLED
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    assert customer.dms[-1].embeds[0].description == _notified(co, m.FULFILLED, placed, ch.id)
    assert await task(co, f"reviews.prompt:{placed.id}") is not None
    assert control_ids(staff_message(co, await order(co, placed.id))) == [
        views.refund_id(placed.id, views.MODE_MONEY),
        views.refund_id(placed.id, views.MODE_CREDIT),
    ]
    followup = await task(co, repo.after_transition_key(placed.id, m.FULFILLED))
    assert followup is not None and followup.status == "pending"  # the failed step is repeated later
    monkeypatch.setattr(service, "refresh_customer_message", original)
    await _after_followup_delay(co)
    assert message.embeds[0].title == render(
        co, k.status_title_key(m.FULFILLED), order_number="1", total=money(co, placed.total_minor)
    )
    assert (await task(co, repo.after_transition_key(placed.id, m.FULFILLED))).status == "done"
    fulfilled = _notified(co, m.FULFILLED, placed, ch.id)
    assert [d.embeds[0].description for d in customer.dms].count(fulfilled) == 1  # never sent twice


async def test_a_crash_after_a_committed_change_is_repaired_by_the_follow_up_task(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, ch, message = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    # paid -> fulfilled is committed, then the process stops before any side effect.
    await co.app.db.transaction(
        repo.transition,
        guild_id=GUILD_ID,
        order_id=placed.id,
        allowed_from=(m.PAID,),
        to=m.FULFILLED,
        actor_id=staffer.id,
        note=None,
        now=co.clock.now(),
    )
    assert await ticket_status(co, placed.ticket_id or 0) == "open"
    await _after_followup_delay(co)
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    assert customer.dms[-1].embeds[0].description == _notified(co, m.FULFILLED, placed, ch.id)
    assert await task(co, f"reviews.prompt:{placed.id}") is not None
    assert message.embeds[0].title == render(
        co, k.status_title_key(m.FULFILLED), order_number="1", total=money(co, placed.total_minor)
    )
    assert views.staff_action_id(m.FULFILLED, placed.id, m.PAID) not in staff_message(co, placed).custom_ids()


async def test_a_completed_follow_up_is_cancelled(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    assert (await task(co, repo.after_submit_key(placed.id))).status == "cancelled"
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    assert (await task(co, repo.after_transition_key(placed.id, m.PAID))).status == "cancelled"


# -- [concurrency #2] start(): network errors clean up; a missing workflow message is sent again ---------------------


async def test_a_network_error_while_starting_cleans_up(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()

    async def network_error(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(service, "send_spec", network_error)
    failed = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(failed) == co.text(core.ERROR_GENERIC)
    session = await session_of(co, customer)
    assert session.status == m.SESSION_CANCELLED
    assert await ticket_status(co, session.ticket_id) == "failed"
    assert [c for c in co.guild.text_channels if c.name.startswith(co.text(k.CHANNEL_PREFIX))] == []


async def test_start_sends_a_missing_workflow_message_again(co: TestEnv) -> None:
    customer = co.member()
    _, session, ch, message = await start(co, customer)
    await message.delete()
    await co.app.db.run(lambda conn: conn.execute("UPDATE checkout_sessions SET message_id = NULL"))
    co.clock.advance(60)
    again = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(again) == render(co, k.SESSION_EXISTS, channel_mention=f"<#{ch.id}>")
    resent = await reload_session(co, session.id)
    assert resent.message_id is not None and workflow_message(ch).id == resent.message_id
    assert workflow_message(ch).embeds[0].title == co.text(k.BROWSE_TITLE)


# -- [concurrency #3] submission: DB steps first, Discord steps isolated, durable follow-up ------------------------


async def test_a_network_error_posting_staff_controls_loses_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", stock=1)
        staff_channel = channel(tenv, "order_queue")
        original = service.send_spec

        async def staff_channel_unreachable(target: Any, spec: Any) -> Any:
            if target is staff_channel:
                raise ConnectionResetError(104, "Connection reset by peer")
            return await original(target, spec)

        monkeypatch.setattr(service, "send_spec", staff_channel_unreachable)
        placed, _, message = await place_order(tenv, customer, [(p1.id, 1)])
        assert message.embeds[0].title == render(
            tenv, k.SUBMITTED_TITLE, order_number="1", total=money(tenv, placed.total_minor)
        )
        assert placed.staff_message_id is None
        assert await task(tenv, service.reminder_task_key(placed.id)) is not None
        assert await task(tenv, service.expiry_task_key(placed.id)) is not None
        assert "checkout.order_submitted" in await audit_actions(tenv)
        assert (await task(tenv, repo.after_submit_key(placed.id))).status == "pending"
        monkeypatch.setattr(service, "send_spec", original)
        await _after_followup_delay(tenv)
        posted = await order(tenv, placed.id)
        assert posted.staff_message_id is not None
        assert views.staff_action_id(m.PAID, placed.id, m.PENDING) in staff_message(tenv, posted).custom_ids()
    finally:
        await tenv.close()


# -- [concurrency #4] young orphan order channels get a durable check ---------------------------------------------


async def test_young_orphan_channels_are_closed_by_a_durable_check(co: TestEnv) -> None:
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
    co.clock.advance(20)  # a quick restart
    app = restarted_app(co)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    assert await ticket_status(co, orphan.ticket.id) == "creating"
    check = await task(co, repo.ticket_check_key(orphan.ticket.id))
    assert check is not None and check.run_at == orphan.ticket.created_at + checkout_tasks.ORPHAN_GRACE_SECONDS
    co.clock.advance(checkout_tasks.ORPHAN_GRACE_SECONDS)
    await app.scheduler.run_due()
    assert await ticket_status(co, orphan.ticket.id) in ("closed", "failed")


async def test_the_channel_check_waits_for_a_young_orphan(co: TestEnv) -> None:
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
    await checkout_tasks.schedule_ticket_check(co.app, orphan.ticket.id, co.clock.now())
    await co.app.scheduler.run_due()
    assert await ticket_status(co, orphan.ticket.id) == "creating"  # rescheduled, not closed
    check = await task(co, repo.ticket_check_key(orphan.ticket.id))
    assert check.status == "pending"
    assert check.run_at == orphan.ticket.created_at + checkout_tasks.ORPHAN_GRACE_SECONDS


# -- [concurrency #5] recovery re-posts the customer's form answers for staff ----------------------------------------


async def test_restart_recovery_posts_missing_staff_details(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, deep_merge(FULL_SETTINGS, ANSWERS_IN_CHANNEL))
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=1000)
        _, session, _, message = await start(tenv, customer)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)
        await go(tenv, customer, message, session.id, m.CART)
        await go(tenv, customer, message, session.id, m.FULFILLMENT)
        await press(tenv, customer, message, views.fulfillment_id(session.id), values=["pickup"])
        await go(tenv, customer, message, session.id, m.DETAILS)
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.details_form_id(session.id, 0),
            fields={"d.name": "[sample name]"},
            message=message,
        )
        await go(tenv, customer, message, session.id, m.REVIEW)
        await press(tenv, customer, message, views.submit_id(session.id, 1250, 1250))
        staff_channel = channel(tenv, "order_queue")
        assert len(staff_channel.messages) == 2
        # The process stopped before the answers were delivered: only the controls exist.
        await staff_channel.messages[1].delete()
        await tenv.app.db.run(
            lambda conn: conn.execute("DELETE FROM notifications WHERE kind = 'checkout.staff_details'")
        )
        app = restarted_app(tenv)
        await app.resources.refresh()
        await checkout_tasks.on_ready(app)
        assert len(staff_channel.messages) == 2
        assert [(f.name, f.value) for f in staff_channel.messages[1].embeds[0].fields] == [
            (tenv.text(k.form_field_key("name", "label")), "[sample name]")
        ]
        await checkout_tasks.on_ready(app)  # deduplicated
        assert len(staff_channel.messages) == 2
    finally:
        await tenv.close()


# -- [concurrency #6] no payment reminder after the payment -----------------------------------------------------------


async def test_no_reminder_when_the_payment_lands_while_it_is_prepared(tmp_path: Path, monkeypatch: Any) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, ch, _ = await place_order(tenv, customer, [(p1.id, 1)])
        original = service.payment_instructions

        async def paid_meanwhile(app: Any, current: m.Order) -> Any:
            result = await original(app, current)
            await service.change_status(app, placed.id, m.PAID, actor_id=staffer.id)
            return result

        monkeypatch.setattr(service, "payment_instructions", paid_meanwhile)
        tenv.clock.advance(30 * 60)
        await tenv.app.scheduler.run_due()
        assert (await order(tenv, placed.id)).status == m.PAID
        assert [d.embeds[0].description for d in customer.dms] == [
            _notified(tenv, m.PENDING, placed, ch.id),  # submission received
            _notified(tenv, m.PAID, placed, ch.id),
        ]
    finally:
        await tenv.close()


# -- [concurrency #7, checkout half] posted-but-unrecorded staff controls are adopted --------------------------------


def _like_discord(message: Any) -> None:
    """Give a fake message the component structure of a real ``discord.Message`` (rows with children)."""
    message.components = [
        types.SimpleNamespace(children=[types.SimpleNamespace(custom_id=c) for c in message.custom_ids()])
    ]


async def test_restart_recovery_adopts_staff_controls_instead_of_posting_a_duplicate(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    other, _, _ = await place_order(co, co.member(name="other"), [(p1.id, 1)])
    staff_channel = channel(co, "order_queue")
    control = staff_message(co, placed)
    for message in staff_channel.messages:
        _like_discord(message)
    # The controls were posted, but the process stopped (or the response was lost) before recording them.
    await co.app.db.run(
        lambda conn: conn.execute("UPDATE orders SET staff_message_id = NULL WHERE id = ?", (placed.id,))
    )
    before = len(staff_channel.messages)
    app = restarted_app(co)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    assert len(staff_channel.messages) == before
    assert (await order(co, placed.id)).staff_message_id == control.id
    assert (await order(co, other.id)).staff_message_id == other.staff_message_id


async def test_controls_are_posted_when_none_can_be_adopted(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    staff_channel = channel(co, "order_queue")
    await staff_message(co, placed).delete()
    await co.app.db.run(lambda conn: conn.execute("UPDATE orders SET staff_message_id = NULL"))
    assert await service.refresh_staff_message(co.app, placed.id)
    assert len(staff_channel.messages) == 1
    assert (await order(co, placed.id)).staff_message_id == staff_channel.messages[0].id


def test_controls_are_recognized_by_their_order_id() -> None:
    cases = [
        (views.staff_action_id(m.PAID, 7, m.PENDING), 7),
        (views.refund_id(8, views.MODE_MONEY), 8),
        (views.credit_return_id(9), 9),
        (views.list_page_id(3, "", 0), None),
        (views.order_cancel_id(5), None),
        ("not a custom id", None),
    ]
    assert [service.controls_order_id(custom_id) for custom_id, _ in cases] == [expected for _, expected in cases]
