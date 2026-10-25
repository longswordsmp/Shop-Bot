"""Order state machine: staff controls, permissions, stock, credits, notifications, refunds, reviews hook."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import discord

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.credits import repo as credits_repo
from tests.checkout.util import (
    add_product,
    audit_actions,
    balance,
    channel,
    error_text,
    give_credits,
    make_env,
    money,
    op_text,
    order,
    place_order,
    press,
    product,
    render,
    staff_message,
    start,
    submit,
    ticket_status,
    to_review,
)
from tests.fakes import http_error, modal_input_ids, modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv


def _notification(tenv: TestEnv, status: str, placed: m.Order, channel_id: int) -> str:
    return render(
        tenv,
        k.notify_description_key(status),
        order_number=str(placed.number),
        total=money(tenv, placed.total_minor),
        channel_mention=f"<#{channel_id}>",
    )


async def test_customers_and_members_cannot_use_staff_controls(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", stock=2)
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    control = staff_message(co, placed)
    for user in (customer, co.member(name="other")):
        denied = await press(co, user, control, views.staff_action_id(m.PAID, placed.id))
        assert error_text(denied) == co.text(core.ERROR_PERMISSION)
        refund = await press(co, user, control, views.refund_id(placed.id, views.MODE_MONEY))
        assert error_text(refund) == co.text(core.ERROR_PERMISSION) and modal_of(refund) is None
        form = await submit_modal(
            co.app,
            user=user,
            custom_id=views.refund_form_id(placed.id, views.MODE_MONEY),
            fields={views.REFUND_AMOUNT_INPUT: "1"},
        )
        assert error_text(form) == co.text(core.ERROR_PERMISSION)
    assert (await order(co, placed.id)).status == m.PENDING
    assert (await product(co, p1.id)).stock_on_hand == 2


async def test_checkout_staff_roles_count_as_staff(tmp_path: Path) -> None:
    tenv = await make_env(
        tmp_path,
        {"server": {"roles": [{"key": "staff"}, {"key": "cashier"}]}, "checkout": {"staff_roles": ["cashier"]}},
    )
    try:
        role = tenv.guild.add_role(name="cashier", position=4)
        await tenv.app.resources.remember("role", "cashier", role.id)
        cashier = tenv.guild.add_member(name="cashier", roles=[role])
        p1 = await add_product(tenv, "[p1]")
        placed, ch, _ = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        assert ch.permissions_for(cashier).view_channel  # order channels include checkout.staff_roles
        result = await press(tenv, cashier, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        assert op_text(result) == tenv.app.op("checkout.op.status_changed", number=1, status=m.PAID)
    finally:
        await tenv.close()


async def test_concurrent_staff_clicks_apply_once(co: TestEnv) -> None:
    customer = co.member()
    s1, s2 = co.member(name="s1", staff=True), co.member(name="s2", staff=True)
    p1 = await add_product(co, "[p1]", stock=3)
    placed, _, _ = await place_order(co, customer, [(p1.id, 2)])
    control = staff_message(co, placed)
    results = await asyncio.gather(
        press(co, s1, control, views.staff_action_id(m.PAID, placed.id)),
        press(co, s2, control, views.staff_action_id(m.REJECTED, placed.id)),
    )
    texts = sorted(op_text(r) or "" for r in results)
    current = await order(co, placed.id)
    assert current.status in (m.PAID, m.REJECTED)
    assert co.app.op("checkout.op.already_handled", number=1, status=current.status) in texts
    events = await co.app.db.run(repo.events, placed.id)
    assert len(events) == 2  # submitted + exactly one transition
    stock = await product(co, p1.id)
    if current.status == m.PAID:
        assert (stock.stock_on_hand, stock.reserved) == (1, 0)
    else:
        assert (stock.stock_on_hand, stock.reserved) == (3, 0)


async def test_each_status_change_updates_views_and_notifies_once(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", price=1000, stock=5)
    placed, ch, message = await place_order(co, customer, [(p1.id, 1)])
    for target in (m.PAID, m.PROCESSING, m.FULFILLED):
        before = len(customer.dms)
        await press(co, staffer, staff_message(co, placed), views.staff_action_id(target, placed.id))
        current = await order(co, placed.id)
        assert current.status == target
        assert message.embeds[0].title == render(
            co, k.status_title_key(target), order_number="1", total=money(co, 1000)
        )
        assert message.custom_ids() == []
        assert len(customer.dms) == before + 1
        assert customer.dms[-1].embeds[0].description == _notification(co, target, placed, ch.id)
        assert customer.dms[-1].embeds[0].title == render(
            co, k.notify_title_key(target), order_number="1", total=money(co, 1000), channel_mention=f"<#{ch.id}>"
        )
        control = staff_message(co, current)
        status = co.text(k.status_label_key(target))
        assert control.embeds[0].title == render(
            co, k.STAFF_TITLE, order_number="1", total=money(co, 1000), status=status
        )
    control = staff_message(co, await order(co, placed.id))
    assert control.custom_ids() == [
        views.refund_id(placed.id, views.MODE_MONEY),
        views.refund_id(placed.id, views.MODE_CREDIT),
    ]
    # Repeating a transition does nothing (and notifies nobody).
    dms = len(customer.dms)
    again = await press(co, staffer, control, views.staff_action_id(m.FULFILLED, placed.id))
    assert op_text(again) == co.app.op(
        "checkout.op.invalid_transition", number=1, status=m.FULFILLED, target=m.FULFILLED
    )
    assert len(customer.dms) == dms
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    actions = await audit_actions(co)
    assert [a for a in actions if a.startswith("checkout.order_") and a != "checkout.order_submitted"] == [
        "checkout.order_paid",
        "checkout.order_processing",
        "checkout.order_fulfilled",
    ]


async def test_paid_buttons_and_stock_commit(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", stock=4)
    placed, _, _ = await place_order(co, customer, [(p1.id, 3)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    paid = await order(co, placed.id)
    assert paid.paid_at == co.clock.now()
    assert staff_message(co, paid).custom_ids() == [
        views.staff_action_id(m.PROCESSING, placed.id),
        views.staff_action_id(m.FULFILLED, placed.id),
        views.refund_id(placed.id, views.MODE_MONEY),
        views.refund_id(placed.id, views.MODE_CREDIT),
        views.staff_action_id(m.CANCELLED, placed.id),
    ]
    assert await co.app.db.run(repo.reservations, placed.id) == [(p1.id, 3, "committed")]
    payment = await co.app.db.run(repo.payment_for, placed.id)
    assert payment is not None and payment.status == "succeeded"
    stock = await product(co, p1.id)
    assert (stock.stock_on_hand, stock.reserved) == (1, 0)
    # paid -> cancelled: nothing is restocked or refunded automatically
    await press(co, staffer, staff_message(co, paid), views.staff_action_id(m.CANCELLED, placed.id))
    cancelled = await order(co, placed.id)
    assert cancelled.status == m.CANCELLED and cancelled.refunded_minor == 0
    stock = await product(co, p1.id)
    assert (stock.stock_on_hand, stock.reserved) == (1, 0)
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    # a paid-then-cancelled order can still be refunded
    assert staff_message(co, cancelled).custom_ids() == [
        views.refund_id(placed.id, views.MODE_MONEY),
        views.refund_id(placed.id, views.MODE_CREDIT),
    ]


async def test_rejection_releases_stock_and_returns_credits(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", price=1000, stock=2)
    await give_credits(co, customer.id, 250)
    _, session, ch, message = await start(co, customer)
    await to_review(co, customer, message, session.id, [(p1.id, 2)])
    await press(co, customer, message, views.credits_id(session.id, True))
    await submit(co, customer, message, session.id)
    placed = await order(co, 1)
    assert await balance(co, customer.id) == 0 and (await product(co, p1.id)).reserved == 2
    rejected = await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.REJECTED, placed.id))
    assert op_text(rejected) == co.app.op("checkout.op.status_changed", number=1, status=m.REJECTED)
    current = await order(co, placed.id)
    assert current.status == m.REJECTED and current.closed_at is not None
    assert await co.app.db.run(repo.reservations, placed.id) == [(p1.id, 2, "released")]
    assert (await product(co, p1.id)).reserved == 0
    assert await balance(co, customer.id) == 250
    payment = await co.app.db.run(repo.payment_for, placed.id)
    assert payment is not None and payment.status == "cancelled"
    assert customer.dms[-1].embeds[0].description == _notification(co, m.REJECTED, current, ch.id)
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    assert staff_message(co, current).custom_ids() == []
    entries = await co.app.db.run(credits_repo.order_entries, GUILD_ID, placed.id)
    assert [e.kind for e in entries] == ["order_debit", "order_return"]


async def test_notifications_fall_back_to_the_order_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"dm_notifications": False}})
    try:
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, ch, _ = await place_order(tenv, customer, [(p1.id, 1)])
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        assert customer.dms == []
        posted = ch.messages[-1]
        assert posted.embeds[0].description == _notification(tenv, m.PAID, placed, ch.id)
        assert posted.content == f"<@{customer.id}>"  # the fallback mentions the customer
    finally:
        await tenv.close()


async def test_closed_dms_fall_back_to_the_order_channel(co: TestEnv) -> None:
    customer = co.member()
    customer.dm_forbidden = True
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, ch, _ = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    assert ch.messages[-1].embeds[0].description == _notification(co, m.PAID, placed, ch.id)
    rows = await co.app.db.run(lambda conn: conn.execute("SELECT delivery FROM notifications").fetchall())
    assert [r["delivery"] for r in rows] == ["channel"]


async def test_customer_cancel_does_not_notify_the_customer(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]")
    placed, _, message = await place_order(co, customer, [(p1.id, 1)])
    await press(co, customer, message, views.order_cancel_yes_id(placed.id))
    assert (await order(co, placed.id)).status == m.CANCELLED
    assert customer.dms == []
    others = await press(co, co.member(name="x"), message, views.order_cancel_yes_id(placed.id))
    assert error_text(others) == co.text(core.ERROR_PERMISSION)


async def test_paid_orders_cannot_be_cancelled_by_the_customer(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, message = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    denied = await press(co, customer, message, views.order_cancel_yes_id(placed.id))
    assert error_text(denied) == co.text(k.ERROR_ORDER_NOT_CANCELLABLE)
    assert (await order(co, placed.id)).status == m.PAID


async def test_partial_and_full_refunds(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", price=1000)
    placed, ch, _ = await place_order(co, customer, [(p1.id, 2)])
    pending_refund = await press(co, staffer, staff_message(co, placed), views.refund_id(placed.id, views.MODE_MONEY))
    assert op_text(pending_refund) == co.app.op("checkout.op.refund_not_allowed", number=1, status=m.PENDING)
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))

    opened = await press(co, staffer, staff_message(co, placed), views.refund_id(placed.id, views.MODE_MONEY))
    modal = modal_of(opened)
    assert modal is not None and modal.title == co.app.op("checkout.refund.title", number=1)
    assert modal_input_ids(modal) == [views.REFUND_AMOUNT_INPUT, views.REFUND_REASON_INPUT]

    async def refund(amount: str, mode: str = views.MODE_MONEY, reason: str = "") -> Any:
        return await submit_modal(
            co.app,
            user=staffer,
            custom_id=views.refund_form_id(placed.id, mode),
            fields={views.REFUND_AMOUNT_INPUT: amount, views.REFUND_REASON_INPUT: reason},
        )

    invalid = await refund("ten")
    assert (op_text(invalid) or "").startswith(
        co.app.op("checkout.op.refund_invalid_amount", reason="x", example="y")[:15]
    )
    first = await refund("5.00", reason="[sample reason]")
    assert op_text(first) == "\n".join(
        [
            co.app.op("checkout.op.refund_manual", number=1, amount="5.00 USD"),
            co.app.op("checkout.op.refund_remaining", remaining="15.00 USD"),
        ]
    )
    current = await order(co, placed.id)
    assert current.refunded_minor == 500 and current.status == m.PAID
    adjustments = await co.app.db.run(repo.adjustments, placed.id)
    assert [(a.kind, a.method, a.amount_minor, a.reason, a.actor_id) for a in adjustments] == [
        ("refund", "manual", 500, "[sample reason]", staffer.id)
    ]
    assert customer.dms[-1].embeds[0].description == render(
        co,
        k.notify_description_key(k.NOTIFY_REFUND),
        order_number="1",
        amount=money(co, 500),
        channel_mention=f"<#{ch.id}>",
    )
    fields = {f.name: f.value for f in staff_message(co, current).embeds[0].fields}
    assert fields[co.text(k.STAFF_REFUNDED_FIELD)] == money(co, 500)
    too_much = await refund("15.01")
    assert op_text(too_much) == co.app.op("checkout.op.refund_too_much", number=1, max="15.00 USD")
    zero = await refund("0")
    assert op_text(zero) == co.app.op("checkout.op.refund_too_much", number=1, max="15.00 USD")
    last = await refund("15.00")
    assert co.app.op("checkout.op.fully_refunded", number=1) in (op_text(last) or "")
    done = await order(co, placed.id)
    assert done.status == m.REFUNDED and done.refunded_minor == done.total_minor == 2000
    assert staff_message(co, done).custom_ids() == []
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    refunds = [
        d
        for d in customer.dms
        if d.embeds[0].description.startswith(co.text(k.notify_description_key(k.NOTIFY_REFUND))[:5])
    ]
    assert len(refunds) >= 2
    after = await refund("1.00")
    assert op_text(after) == co.app.op("checkout.op.refund_not_allowed", number=1, status=m.REFUNDED)
    assert "checkout.order_refund" in await audit_actions(co)


async def test_refund_to_credit_and_concurrent_refunds(co: TestEnv) -> None:
    customer = co.member()
    s1, s2 = co.member(name="s1", staff=True), co.member(name="s2", staff=True)
    p1 = await add_product(co, "[p1]", price=1000)
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    await press(co, s1, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    opened = await press(co, s1, staff_message(co, placed), views.refund_id(placed.id, views.MODE_CREDIT))
    modal = modal_of(opened)
    assert modal is not None and modal.title == co.app.op("checkout.refund.title_credit", number=1)
    results = await asyncio.gather(
        submit_modal(
            co.app,
            user=s1,
            custom_id=views.refund_form_id(placed.id, views.MODE_CREDIT),
            fields={views.REFUND_AMOUNT_INPUT: "7.00"},
        ),
        submit_modal(
            co.app,
            user=s2,
            custom_id=views.refund_form_id(placed.id, views.MODE_CREDIT),
            fields={views.REFUND_AMOUNT_INPUT: "7.00"},
        ),
    )
    texts = [op_text(r) or "" for r in results]
    assert sum(t.startswith(co.app.op("checkout.op.refund_credit", number=1, amount="7.00 USD")) for t in texts) == 1
    assert co.app.op("checkout.op.refund_too_much", number=1, max="3.00 USD") in texts
    current = await order(co, placed.id)
    assert current.refunded_minor == 700
    assert await balance(co, customer.id) == 700
    entries = await co.app.db.run(credits_repo.order_entries, GUILD_ID, placed.id)
    assert [(e.kind, e.delta_minor) for e in entries] == [("refund_to_credit", 700)]


async def test_refund_to_credit_needs_credits(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"credits": False}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, _, _ = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        assert views.refund_id(placed.id, views.MODE_CREDIT) not in staff_message(tenv, placed).custom_ids()
        forged = await press(tenv, staffer, None, views.refund_id(placed.id, views.MODE_CREDIT))
        assert op_text(forged) == tenv.app.op("checkout.op.credits_unavailable")
        result = await service.refund(tenv.app, placed.id, amount_minor=100, to_credit=False, reason=None, actor_id=1)
        assert result.record.adjustment.method == "manual"
    finally:
        await tenv.close()


async def test_reviews_hook_is_called_when_available(co: TestEnv, monkeypatch: Any) -> None:
    calls: list[int] = []

    async def schedule_review_prompt(app: Any, order_id: int) -> None:
        calls.append(order_id)

    fake = types.ModuleType("shopbot.features.reviews.service")
    fake.schedule_review_prompt = schedule_review_prompt  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shopbot.features.reviews.service", fake)
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    assert calls == []
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.FULFILLED, placed.id))
    assert calls == [placed.id]


async def test_reviews_hook_is_skipped_without_the_function(co: TestEnv, monkeypatch: Any) -> None:
    monkeypatch.setitem(sys.modules, "shopbot.features.reviews.service", types.ModuleType("x"))
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.FULFILLED, placed.id))
    assert (await order(co, placed.id)).status == m.FULFILLED


async def test_deleted_staff_message_is_reposted_on_the_next_change(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    old = staff_message(co, placed)
    await old.delete()
    outcome = await service.change_status(co.app, placed.id, m.PAID, actor_id=staffer.id)
    assert outcome.applied
    current = await order(co, placed.id)
    assert current.staff_message_id != old.id
    assert views.staff_action_id(m.PROCESSING, placed.id) in staff_message(co, current).custom_ids()


async def test_discord_failures_never_undo_a_committed_change(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, ch, _ = await place_order(co, customer, [(p1.id, 1)])
    ch.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
    customer.dm_forbidden = True
    result = await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id))
    assert op_text(result) == co.app.op("checkout.op.status_changed", number=1, status=m.PAID)
    assert (await order(co, placed.id)).status == m.PAID
    rows = await co.app.db.run(lambda conn: conn.execute("SELECT status FROM notifications").fetchall())
    assert [r["status"] for r in rows] == ["failed"]


async def test_order_not_found_for_staff(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    missing = await press(co, staffer, None, views.staff_action_id(m.PAID, 999))
    assert op_text(missing) == co.app.op("checkout.op.order_missing")
    assert channel(co, "order_queue").messages == []
