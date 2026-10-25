"""Money guards: the submit guard covers what the customer saw, applied store credit of a paid order can be given
back, and stored order amounts keep the order's currency."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.credits import repo as credits_repo
from shopbot.ui.custom_ids import cid
from tests.checkout.util import (
    add_product,
    balance,
    control_ids,
    give_credits,
    go,
    make_env,
    money,
    op_text,
    order,
    orders,
    place_order,
    press,
    reject,
    render,
    set_price,
    set_qty,
    staff_message,
    start,
    submit,
    to_review,
)
from tests.fakes import FakeMember, FakeMessage, modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv


def _submit_button(message: FakeMessage) -> str:
    """The submit custom_id exactly as the review step rendered it."""
    found = [c for c in message.custom_ids() if c.startswith("sb:co:submit:")]
    assert len(found) == 1, message.custom_ids()
    return found[0]


async def _credit_order(tenv: TestEnv, customer: FakeMember, *, price: int, credit: int) -> m.Order:
    p1 = await add_product(tenv, "[p1]", price=price, stock=5)
    await give_credits(tenv, customer.id, credit)
    _, session, _, message = await start(tenv, customer)
    await to_review(tenv, customer, message, session.id, [(p1.id, 1)])
    await press(tenv, customer, message, views.credits_id(session.id, True))
    await submit(tenv, customer, message, session.id)
    return (await orders(tenv))[0]


async def _staff_status(tenv: TestEnv, staffer: FakeMember, placed: m.Order, target: str) -> str | None:
    """Press the staff button exactly as the controls render it for the order's current status."""
    current = await order(tenv, placed.id)
    control = staff_message(tenv, current)
    result = await press(tenv, staffer, control, views.staff_action_id(target, placed.id, current.status))
    return op_text(result)


async def _return_credit(tenv: TestEnv, staffer: FakeMember, placed: m.Order, reason: str = "") -> str | None:
    form = await submit_modal(
        tenv.app,
        user=staffer,
        custom_id=views.credit_return_form_id(placed.id),
        fields={views.REFUND_REASON_INPUT: reason},
    )
    return op_text(form)


# -- [money #0] the submit guard ------------------------------------------------------------------------------------


async def test_price_change_is_caught_when_store_credit_covers_the_whole_order(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    await give_credits(co, customer.id, 5000)
    _, session, _, message = await start(co, customer)
    await to_review(co, customer, message, session.id, [(p1.id, 1)])
    await press(co, customer, message, views.credits_id(session.id, True))
    shown = _submit_button(message)  # review: credits 10.00, total 0.00
    await set_price(co, p1.id, 4000)  # total stays 0.00: the credit absorbs the change
    changed = await press(co, customer, message, shown)
    assert await orders(co) == []
    assert await balance(co, customer.id) == 5000  # nothing was debited
    assert changed.last_sent is None  # the review is shown again, with the notice
    assert message.embeds[0].title == co.text(k.REVIEW_TITLE)
    assert message.embeds[1].description == render(co, k.REVIEW_PRICE_CHANGED, total=money(co, 0))
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields[co.text(k.REVIEW_CREDITS_FIELD)] == money(co, 4000)
    # Submitting what is shown now debits exactly what is shown.
    assert _submit_button(message) == views.submit_id(session.id, 0, 4000)
    await press(co, customer, message, _submit_button(message))
    placed = (await orders(co))[0]
    assert (placed.subtotal_minor, placed.credits_applied_minor, placed.total_minor) == (4000, 4000, 0)
    assert await balance(co, customer.id) == 1000


async def test_fee_or_option_changes_hidden_by_credit_are_caught(cof: TestEnv) -> None:
    tenv = cof
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=1000)
    await give_credits(tenv, customer.id, 100_000)
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
    await press(tenv, customer, message, views.credits_id(session.id, True))
    shown = _submit_button(message)
    settings = tenv.app.settings
    fees = tuple(replace(fee, amount_minor=900) if fee.id == "service" else fee for fee in settings.checkout.fees)
    tenv.app.settings = replace(settings, checkout=replace(settings.checkout, fees=fees))  # the flat fee rose
    await press(tenv, customer, message, shown)
    assert await orders(tenv) == []
    assert await balance(tenv, customer.id) == 100_000


async def test_a_submit_button_rendered_before_the_guard_counts_as_changed(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    _, session, _, message = await start(co, customer)
    data = await to_review(co, customer, message, session.id, [(p1.id, 2)])
    legacy = cid("co", "submit", session.id, data.totals.total_minor)  # no shown gross amount
    result = await press(co, customer, message, legacy)
    assert await orders(co) == []
    assert result.last_sent is None
    assert message.embeds[1].description == render(co, k.REVIEW_PRICE_CHANGED, total=money(co, 2000))
    await press(co, customer, message, _submit_button(message))
    assert len(await orders(co)) == 1
    assert (await service.submit(co.app, await _reloaded(co, session.id), 2000)).id == 1  # already submitted


async def _reloaded(tenv: TestEnv, session_id: int) -> m.Session:
    found = await tenv.app.db.run(repo.get_session, session_id)
    assert found is not None
    return found


async def test_service_submit_without_the_shown_gross_amount_is_refused(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    _, session, _, message = await start(co, customer)
    await to_review(co, customer, message, session.id, [(p1.id, 1)])
    with pytest.raises(repo.PriceChanged):
        await service.submit(co.app, await _reloaded(co, session.id), 1000)
    assert await orders(co) == []


# -- [money #1] applied store credit after payment ------------------------------------------------------------------


async def test_credit_of_an_order_paid_entirely_with_credit_is_given_back_after_cancelling(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=1000)
    assert (placed.total_minor, placed.credits_applied_minor) == (0, 1000)
    assert await balance(co, customer.id) == 0
    await _staff_status(co, staffer, placed, m.PAID)
    cancelled_reply = await _staff_status(co, staffer, placed, m.CANCELLED)
    # Cancelling a paid order returns nothing automatically, and says what is still outstanding.
    assert cancelled_reply == "\n".join(
        [
            co.app.op("checkout.op.status_changed", number=1, status=m.CANCELLED),
            co.app.op("checkout.op.paid_cancel", number=1),
            co.app.op("checkout.op.credit_returnable", amount="10.00 USD"),
        ]
    )
    assert await balance(co, customer.id) == 0
    cancelled = await order(co, placed.id)
    assert cancelled.status == m.CANCELLED and cancelled.can_return_credit and not cancelled.can_refund
    control = staff_message(co, cancelled)
    assert control_ids(control) == [views.credit_return_id(placed.id)]
    opened = await press(co, staffer, control, views.credit_return_id(placed.id))
    modal = modal_of(opened)
    assert modal is not None and modal.title == co.app.op("checkout.refund.title_return", number=1)

    reply = await _return_credit(co, staffer, placed, "[sample reason]")
    assert reply == "\n".join(
        [
            co.app.op("checkout.op.credit_returned", number=1, amount="10.00 USD"),
            co.app.op("checkout.op.fully_refunded", number=1),
        ]
    )
    done = await order(co, placed.id)
    assert done.status == m.REFUNDED and done.credit_returned_minor == 1000 and done.refunded_minor == 0
    assert await balance(co, customer.id) == 1000
    adjustments = await co.app.db.run(repo.adjustments, placed.id)
    assert [(a.kind, a.method, a.amount_minor, a.reason, a.actor_id) for a in adjustments] == [
        ("credit_return", "credits", 1000, "[sample reason]", staffer.id)
    ]
    entries = await co.app.db.run(credits_repo.order_entries, GUILD_ID, placed.id)
    assert [(e.kind, e.delta_minor) for e in entries] == [("order_debit", -1000), ("order_return", 1000)]
    assert control_ids(staff_message(co, done)) == []
    assert customer.dms[-1].embeds[0].description == render(
        co,
        k.notify_description_key(m.REFUNDED),
        order_number="1",
        total=money(co, 0),
        channel_mention=f"<#{await _channel_id(co, done)}>",
    )
    # Idempotent: a second request changes nothing.
    again = await _return_credit(co, staffer, placed)
    assert again == co.app.op("checkout.op.credit_return_not_allowed", number=1, status=m.REFUNDED)
    with pytest.raises(service.RefundError):
        await service.return_credit(co.app, placed.id, reason=None, actor_id=staffer.id)
    assert await balance(co, customer.id) == 1000
    assert len(await co.app.db.run(repo.adjustments, placed.id)) == 1


async def _channel_id(tenv: TestEnv, found: m.Order) -> int:
    view = await tenv.app.db.run(repo.order_view, found.id)
    assert view is not None and view.channel_id is not None
    return view.channel_id


async def test_partly_credit_paid_order_is_fully_refunded_only_with_money_and_credit(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=400)
    assert (placed.total_minor, placed.credits_applied_minor) == (600, 400)
    await _staff_status(co, staffer, placed, m.PAID)
    await _staff_status(co, staffer, placed, m.CANCELLED)
    control = staff_message(co, await order(co, placed.id))
    assert control_ids(control) == [
        views.refund_id(placed.id, views.MODE_MONEY),
        views.refund_id(placed.id, views.MODE_CREDIT),
        views.credit_return_id(placed.id),
    ]
    refunded = await submit_modal(
        co.app,
        user=staffer,
        custom_id=views.refund_form_id(placed.id, views.MODE_CREDIT),
        fields={views.REFUND_AMOUNT_INPUT: "6.00"},
    )
    # The money refund covers the total, but the applied credit is still outstanding: not 'refunded' yet.
    assert op_text(refunded) == "\n".join(
        [
            co.app.op("checkout.op.refund_credit", number=1, amount="6.00 USD"),
            co.app.op("checkout.op.credit_returnable", amount="4.00 USD"),
        ]
    )
    current = await order(co, placed.id)
    assert current.status == m.CANCELLED and current.refunded_minor == 600
    assert await balance(co, customer.id) == 600
    assert control_ids(staff_message(co, current)) == [views.credit_return_id(placed.id)]
    reply = await _return_credit(co, staffer, placed)
    assert co.app.op("checkout.op.fully_refunded", number=1) in (reply or "")
    final = await order(co, placed.id)
    assert final.status == m.REFUNDED
    assert await balance(co, customer.id) == 1000  # 600 refunded to credit + the 400 applied


async def test_credit_given_back_first_then_money_completes_the_refund(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=300)
    await _staff_status(co, staffer, placed, m.PAID)
    reply = await _return_credit(co, staffer, placed)
    assert reply == "\n".join(
        [
            co.app.op("checkout.op.credit_returned", number=1, amount="3.00 USD"),
            co.app.op("checkout.op.refund_remaining", remaining="7.00 USD"),
        ]
    )
    paid = await order(co, placed.id)
    assert paid.status == m.PAID and paid.credit_returned_minor == 300
    assert views.credit_return_id(placed.id) not in staff_message(co, paid).custom_ids()
    assert await balance(co, customer.id) == 300
    # The customer is told about the credit given back (the partial-refund notification).
    assert customer.dms[-1].embeds[0].description == render(
        co,
        k.notify_description_key(k.NOTIFY_REFUND),
        order_number="1",
        amount=money(co, 300),
        channel_mention=f"<#{await _channel_id(co, paid)}>",
    )
    with pytest.raises(service.RefundError) as too_much:
        await service.refund(co.app, placed.id, amount_minor=701, to_credit=False, reason=None, actor_id=staffer.id)
    assert too_much.value.reason == "amount"
    await service.refund(co.app, placed.id, amount_minor=700, to_credit=False, reason=None, actor_id=staffer.id)
    final = await order(co, placed.id)
    assert final.status == m.REFUNDED and final.refunded_minor == 700


async def test_credit_of_an_order_refunded_before_the_fix_can_still_be_given_back(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=400)
    await _staff_status(co, staffer, placed, m.PAID)
    # Earlier versions marked an order 'refunded' once its money was refunded, keeping the applied credit.
    await co.app.db.run(
        lambda conn: conn.execute(
            "UPDATE orders SET status = 'refunded', refunded_minor = total_minor WHERE id = ?", (placed.id,)
        )
    )
    legacy = await order(co, placed.id)
    assert legacy.can_return_credit and not legacy.can_refund
    assert await service.refresh_staff_message(co.app, placed.id)
    assert control_ids(staff_message(co, legacy)) == [views.credit_return_id(placed.id)]
    reply = await _return_credit(co, staffer, placed)
    assert reply == co.app.op("checkout.op.credit_returned", number=1, amount="4.00 USD")
    done = await order(co, placed.id)
    assert done.status == m.REFUNDED and done.fully_refunded
    assert await balance(co, customer.id) == 400
    assert control_ids(staff_message(co, done)) == []


async def test_credit_cannot_be_given_back_before_payment(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=300)
    reply = await _return_credit(co, staffer, placed)
    assert reply == co.app.op("checkout.op.credit_return_not_allowed", number=1, status=m.PENDING)
    opened = await press(co, staffer, None, views.credit_return_id(placed.id))
    assert modal_of(opened) is None
    assert await balance(co, customer.id) == 0
    assert views.credit_return_id(placed.id) not in staff_message(co, placed).custom_ids()
    # Rejecting the unpaid order still gives the credit back automatically, once.
    await reject(co, staffer, staff_message(co, placed), placed.id)
    assert await balance(co, customer.id) == 300
    with pytest.raises(repo.RefundNotAllowed):
        await co.app.db.transaction(
            repo.return_order_credit, guild_id=GUILD_ID, order_id=placed.id, reason=None, actor_id=1, now=0
        )
    assert await balance(co, customer.id) == 300


async def test_members_cannot_give_credit_back(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    placed = await _credit_order(co, customer, price=1000, credit=1000)
    await _staff_status(co, staffer, placed, m.PAID)
    denied = await submit_modal(
        co.app, user=customer, custom_id=views.credit_return_form_id(placed.id), fields={views.REFUND_REASON_INPUT: ""}
    )
    assert denied.last_sent is not None and denied.last_sent.embeds[0].description == co.text(core.ERROR_PERMISSION)
    assert await balance(co, customer.id) == 0


# -- [money #2] the order's own currency ----------------------------------------------------------------------------

TIMERS = {"checkout": {"payment_reminder_minutes": 30}}


async def test_order_amounts_keep_the_order_currency_after_the_shop_currency_changes(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, TIMERS)
    try:
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]", price=1250)
        placed, ch, message = await place_order(tenv, customer, [(p1.id, 1)])
        settings = tenv.app.settings
        tenv.app.settings = replace(settings, checkout=replace(settings.checkout, currency="JPY"))
        usd = render(tenv, core.MONEY_FORMAT, amount="12.50", currency="USD")
        values = {"order_number": "1", "total": usd}

        assert await service.refresh_customer_message(tenv.app, placed.id)
        assert message.embeds[0].title == render(tenv, k.SUBMITTED_TITLE, **values)
        assert message.embeds[1].description == render(tenv, "checkout.payment.manual.instructions", **values)
        assert usd in {f.value for f in message.embeds[0].fields}
        assert await service.refresh_staff_message(tenv.app, placed.id)
        control = staff_message(tenv, await order(tenv, placed.id))
        status = tenv.text(k.status_label_key(m.PENDING))
        assert control.embeds[0].title == render(tenv, k.STAFF_TITLE, order_number="1", total=usd, status=status)

        tenv.clock.advance(30 * 60)
        await tenv.app.scheduler.run_due()  # the payment reminder
        reminder = customer.dms[-1]
        assert reminder.embeds[0].description == render(
            tenv,
            k.notify_description_key(k.NOTIFY_PAYMENT_REMINDER),
            channel_mention=f"<#{ch.id}>",
            **values,
        )
        assert reminder.embeds[1].description == render(tenv, "checkout.payment.manual.instructions", **values)

        await _staff_status(tenv, staffer, placed, m.PAID)
        assert customer.dms[-1].embeds[0].description == render(
            tenv, k.notify_description_key(m.PAID), channel_mention=f"<#{ch.id}>", **values
        )
        # Store credit is kept in the shop currency: no refund to credit for a USD order in a JPY shop.
        paid = await order(tenv, placed.id)
        assert views.refund_id(placed.id, views.MODE_CREDIT) not in staff_message(tenv, paid).custom_ids()
        refused = op_text(await press(tenv, staffer, None, views.refund_id(placed.id, views.MODE_CREDIT)))
        expected = tenv.app.op("checkout.op.refund_currency", number=1, currency="USD", shop_currency="JPY")
        assert refused == expected
        form = await submit_modal(
            tenv.app,
            user=staffer,
            custom_id=views.refund_form_id(placed.id, views.MODE_CREDIT),
            fields={views.REFUND_AMOUNT_INPUT: "5.00"},
        )
        assert op_text(form) == expected
        assert await balance(tenv, customer.id) == 0
        assert (await order(tenv, placed.id)).refunded_minor == 0
        await service.refund(tenv.app, placed.id, amount_minor=500, to_credit=False, reason=None, actor_id=staffer.id)
        assert customer.dms[-1].embeds[0].description == render(
            tenv,
            k.notify_description_key(k.NOTIFY_REFUND),
            order_number="1",
            amount=render(tenv, core.MONEY_FORMAT, amount="5.00", currency="USD"),
            channel_mention=f"<#{ch.id}>",
        )
    finally:
        await tenv.close()


async def test_the_exponent_override_applies_only_to_the_shop_currency(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"currency_exponent": 3}})
    try:
        settings = tenv.app.settings
        assert views.order_money(tenv.app.ui, settings, "USD")(1250) == render(
            tenv, core.MONEY_FORMAT, amount="1.250", currency="USD"
        )
        assert views.order_money(tenv.app.ui, settings, "JPY")(1250) == render(
            tenv, core.MONEY_FORMAT, amount="1,250", currency="JPY"
        )
    finally:
        await tenv.close()
