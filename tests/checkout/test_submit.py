"""Submission: one transaction, idempotent, race-free; price changes, stock, credits, details, staff controls."""

from __future__ import annotations

import asyncio

from shopbot.features import core_keys as core
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from tests.checkout.util import (
    add_product,
    audit_actions,
    balance,
    channel,
    component,
    error_text,
    give_credits,
    go,
    money,
    order,
    orders,
    place_order,
    press,
    product,
    reload_session,
    render,
    set_price,
    set_qty,
    staff_message,
    start,
    submit,
    task,
    to_review,
)
from tests.fakes import submit_modal
from tests.helpers import GUILD_ID, TestEnv


async def test_submit_creates_the_order_atomically(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1250, stock=5)
    p2 = await add_product(co, "[p2]", price=300)
    placed, ch, message = await place_order(co, customer, [(p1.id, 2), (p2.id, 1)])
    assert placed.number == 1 and placed.user_id == customer.id and placed.currency == "USD"
    assert placed.payment_provider == "manual" and placed.customer_message_id == message.id
    items = await co.app.db.run(repo.order_items, placed.id)
    assert [(i.name, i.unit_price_minor, i.quantity, i.line_total_minor) for i in items] == [
        ("[p1]", 1250, 2, 2500),
        ("[p2]", 300, 1, 300),
    ]
    assert await co.app.db.run(repo.reservations, placed.id) == [(p1.id, 2, "held"), (p2.id, 1, "held")]
    payment = await co.app.db.run(repo.payment_for, placed.id)
    assert payment is not None and payment.status == "pending" and payment.amount_minor == 2800
    events = await co.app.db.run(repo.events, placed.id)
    assert [(e.from_status, e.to_status, e.actor_id) for e in events] == [(None, m.PENDING, customer.id)]
    session = await reload_session(co, placed.session_id or 0)
    assert session.status == m.SUBMITTED and session.order_id == placed.id and session.step == m.DONE
    assert (await product(co, p1.id)).reserved == 2
    # customer view: submitted title, items, total, payment instructions, cancel button
    values = {"order_number": "1", "total": money(co, 2800)}
    assert message.embeds[0].title == render(co, k.SUBMITTED_TITLE, **values)
    assert message.embeds[0].description == render(co, k.SUBMITTED_DESCRIPTION, **values)
    assert message.embeds[1].title == render(co, "checkout.payment.manual.title", **values)
    assert message.custom_ids() == [views.order_cancel_id(placed.id)]
    # staff controls
    control = staff_message(co, placed)
    status = co.text(k.status_label_key(m.PENDING))
    assert control.embeds[0].title == render(co, k.STAFF_TITLE, order_number="1", total=money(co, 2800), status=status)
    assert control.embeds[0].description == render(
        co,
        k.STAFF_DESCRIPTION,
        order_number="1",
        user_mention=f"<@{customer.id}>",
        channel_mention=f"<#{ch.id}>",
        total=money(co, 2800),
        status=status,
    )
    assert control.custom_ids() == [
        views.staff_action_id(m.PAID, placed.id),
        views.staff_action_id(m.REJECTED, placed.id),
        views.staff_action_id(m.CANCELLED, placed.id),
    ]
    assert control.allowed_mentions is not None and control.content == ""
    assert "checkout.order_submitted" in await audit_actions(co)
    assert await task(co, "catalog.refresh_listing") is not None


async def test_duplicate_submit_click_returns_the_same_order(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", stock=5)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(p1.id, 1)])
    total, gross = data.totals.total_minor, data.totals.gross_minor
    await press(co, customer, message, views.submit_id(session.id, total, gross))
    again = await press(co, customer, message, views.submit_id(session.id, total, gross))
    assert error_text(again) is None  # re-rendered the submitted order, no error
    assert len(await orders(co)) == 1
    assert (await product(co, p1.id)).reserved == 1
    assert len(channel(co, "order_queue").messages) == 1


async def test_concurrent_duplicate_submits_create_one_order(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", stock=5)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(p1.id, 2)])
    total, gross = data.totals.total_minor, data.totals.gross_minor
    # Bypass the router's double-click guard: call the service concurrently.
    fresh = await reload_session(co, session.id)
    results = await asyncio.gather(
        service.submit(co.app, fresh, total, gross), service.submit(co.app, fresh, total, gross), return_exceptions=True
    )
    assert all(isinstance(r, m.Order) for r in results)
    assert {r.id for r in results if isinstance(r, m.Order)} == {1}
    assert len(await orders(co)) == 1
    assert (await product(co, p1.id)).reserved == 2


async def test_last_unit_cannot_be_sold_twice(co: TestEnv) -> None:
    first, second = co.member(name="first"), co.member(name="second")
    p1 = await add_product(co, "[last]", stock=1)
    _, s1, _, m1 = await start(co, first)
    _, s2, _, m2 = await start(co, second)
    d1 = await to_review(co, first, m1, s1.id, [(p1.id, 1)])
    d2 = await to_review(co, second, m2, s2.id, [(p1.id, 1)])
    results = await asyncio.gather(
        press(co, first, m1, views.submit_id(s1.id, d1.totals.total_minor, d1.totals.gross_minor)),
        press(co, second, m2, views.submit_id(s2.id, d2.totals.total_minor, d2.totals.gross_minor)),
    )
    created = await orders(co)
    assert len(created) == 1
    stock = await product(co, p1.id)
    assert stock.reserved == 1 and stock.stock_on_hand == 1
    loser = results[1] if created[0].user_id == first.id else results[0]
    assert error_text(loser) == render(co, k.ERROR_OUT_OF_STOCK, name=views.escape("[last]"), available=0)
    losing_session = s2 if created[0].user_id == first.id else s1
    assert (await reload_session(co, losing_session.id)).status == m.ACTIVE  # nothing changed for the loser


async def test_out_of_stock_rolls_back_every_line(co: TestEnv) -> None:
    customer = co.member()
    plenty = await add_product(co, "[plenty]", stock=10)
    scarce = await add_product(co, "[scarce]", stock=2)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(plenty.id, 3), (scarce.id, 2)])
    await co.app.db.transaction(catalog_repo.reserve, scarce.id, 1, co.clock.now())  # someone else holds one
    failed = await press(
        co, customer, message, views.submit_id(session.id, data.totals.total_minor, data.totals.gross_minor)
    )
    assert error_text(failed) == render(co, k.ERROR_OUT_OF_STOCK, name=views.escape("[scarce]"), available=1)
    assert (await product(co, plenty.id)).reserved == 0  # the first line's reservation was rolled back
    assert await orders(co) == []


async def test_price_change_between_review_and_submit(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(p1.id, 2)])
    assert data.totals.total_minor == 2000
    await set_price(co, p1.id, 1100)
    changed = await press(co, customer, message, views.submit_id(session.id, 2000, 2000))
    assert await orders(co) == []
    assert error_text(changed) is None
    assert message.embeds[0].title == co.text(k.REVIEW_TITLE)
    assert message.embeds[1].description == render(co, k.REVIEW_PRICE_CHANGED, total=money(co, 2200))
    assert views.submit_id(session.id, 2200, 2200) in message.custom_ids()
    await press(co, customer, message, views.submit_id(session.id, 2200, 2200))
    placed = await orders(co)
    assert len(placed) == 1 and placed[0].total_minor == 2200
    assert [i.unit_price_minor for i in await co.app.db.run(repo.order_items, placed[0].id)] == [1100]


async def test_unlisted_product_and_caps_are_checked_at_submit(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(p1.id, 3)])
    current = await product(co, p1.id)
    await co.app.db.transaction(
        catalog_repo.update_product, p1.id, current.version, now=co.clock.now(), max_per_order=2
    )
    shown = views.submit_id(session.id, data.totals.total_minor, data.totals.gross_minor)
    capped = await press(co, customer, message, shown)
    assert error_text(capped) == render(co, k.ERROR_QUANTITY_INVALID, max=2)
    current = await product(co, p1.id)
    await co.app.db.transaction(catalog_repo.update_product, p1.id, current.version, now=co.clock.now(), active=False)
    gone = await press(co, customer, message, shown)
    assert error_text(gone) == render(co, k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape("[p1]"))
    assert await orders(co) == []


async def test_submit_requires_the_review_step_and_the_owner(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    _, session, _, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 1)
    early = await press(co, customer, message, views.submit_id(session.id, 1000, 1000))
    assert error_text(early) == co.text(core.ERROR_INVALID_INPUT)
    await go(co, customer, message, session.id, m.CART)
    await go(co, customer, message, session.id, m.REVIEW)
    other = await press(co, co.member(name="other"), message, views.submit_id(session.id, 1000, 1000))
    assert error_text(other) == co.text(core.ERROR_PERMISSION)
    assert await orders(co) == []


async def test_credits_are_debited_and_returned_on_cancel(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000, stock=3)
    await give_credits(co, customer.id, 400)
    _, session, _, message = await start(co, customer)
    await to_review(co, customer, message, session.id, [(p1.id, 1)])
    await press(co, customer, message, views.credits_id(session.id, True))
    await submit(co, customer, message, session.id)
    placed = (await orders(co))[0]
    assert placed.credits_applied_minor == 400 and placed.total_minor == 600
    assert await balance(co, customer.id) == 0
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields[co.text(k.REVIEW_CREDITS_FIELD)] == money(co, 400)

    await press(co, customer, message, views.order_cancel_id(placed.id))
    assert message.embeds[0].description == render(co, k.ORDER_CANCEL_CONFIRM_DESCRIPTION, order_number="1")
    await press(co, customer, message, views.order_cancel_no_id(placed.id))
    assert message.embeds[0].title == render(co, k.SUBMITTED_TITLE, order_number="1", total=money(co, 600))
    await press(co, customer, message, views.order_cancel_id(placed.id))
    await press(co, customer, message, views.order_cancel_yes_id(placed.id))
    cancelled = await order(co, placed.id)
    assert cancelled.status == m.CANCELLED
    assert await balance(co, customer.id) == 400
    assert (await product(co, p1.id)).reserved == 0
    assert message.embeds[0].title == render(
        co, k.status_title_key(m.CANCELLED), order_number="1", total=money(co, 600)
    )
    again = await press(co, customer, message, views.order_cancel_yes_id(placed.id))
    assert error_text(again) == co.text(k.ERROR_ORDER_NOT_CANCELLABLE)
    assert await balance(co, customer.id) == 400


async def test_credits_changed_since_review_is_a_price_change(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    await give_credits(co, customer.id, 400)
    _, session, _, message = await start(co, customer)
    await to_review(co, customer, message, session.id, [(p1.id, 1)])
    await press(co, customer, message, views.credits_id(session.id, True))
    await give_credits(co, customer.id, -300)  # spent elsewhere meanwhile
    await press(co, customer, message, views.submit_id(session.id, 600, 1000))
    assert await orders(co) == []
    assert message.embeds[1].description == render(co, k.REVIEW_PRICE_CHANGED, total=money(co, 900))


async def test_full_workflow_order_keeps_details_fees_and_posts_staff_details(cof: TestEnv) -> None:
    tenv = cof
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=1000)
    _, session, _, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, p1.id, 2)
    await go(tenv, customer, message, session.id, m.CART)
    await go(tenv, customer, message, session.id, m.FULFILLMENT)
    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["delivery"])
    await go(tenv, customer, message, session.id, m.DETAILS)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": "[sample name]", "o.delivery.address": "[sample address]", "o.delivery.phone": ""},
        message=message,
    )
    await go(tenv, customer, message, session.id, m.REVIEW)
    await press(tenv, customer, message, views.submit_id(session.id, 2850, 2850))
    placed = (await orders(tenv))[0]
    assert (placed.subtotal_minor, placed.fulfillment_fee_minor, placed.fees_minor, placed.total_minor) == (
        2000,
        500,
        350,
        2850,
    )
    assert placed.fulfillment_option == "delivery"
    assert placed.details == {"d.name": "[sample name]", "o.delivery.address": "[sample address]"}
    fees = await tenv.app.db.run(repo.order_fees, placed.id)
    assert [(f.fee_id, f.amount_minor) for f in fees] == [("service", 150), ("tax", 200)]
    staff_channel = channel(tenv, "order_queue")
    assert len(staff_channel.messages) == 2
    details = staff_channel.messages[1]
    assert details.embeds[0].title == render(tenv, k.STAFF_DETAILS_TITLE, order_number="1")
    assert [(f.name, f.value) for f in details.embeds[0].fields] == [
        (tenv.text(k.form_field_key("name", "label")), "[sample name]"),
        (tenv.text(k.option_form_key("delivery", "address", "label")), "[sample address]"),
    ]
    control = staff_message(tenv, placed)
    names = [f.name for f in control.embeds[0].fields]
    assert tenv.text(k.fee_label_key("tax")) in names and tenv.text(k.FULFILLMENT_CHOSEN_FIELD) in names


async def test_order_numbers_are_sequential_and_unique(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]")
    for index in range(3):
        await place_order(co, co.member(name=f"c{index}"), [(p1.id, 1)])
    numbers = sorted(o.number for o in await orders(co))
    assert numbers == [1, 2, 3]
    row = await co.app.db.run(lambda conn: conn.execute("SELECT value FROM counters WHERE name = 'order'").fetchone())
    assert row["value"] == 3
    assert GUILD_ID


async def test_staff_channel_missing_does_not_lose_the_order(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]")
    staff_channel = channel(co, "order_queue")
    del co.guild._channels[staff_channel.id]
    placed, _, message = await place_order(co, customer, [(p1.id, 1)])
    assert placed.status == m.PENDING and placed.staff_message_id is None
    assert message.embeds[0].title == render(co, k.SUBMITTED_TITLE, order_number="1", total=money(co, 1000))
    assert component(message, views.order_cancel_id(placed.id)).label == co.text(k.BUTTON_CANCEL_ORDER)
