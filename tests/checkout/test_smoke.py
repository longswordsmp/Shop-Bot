"""End-to-end happy path: start -> cart -> review -> submit -> paid -> processing -> fulfilled."""

from __future__ import annotations

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views
from tests.checkout.util import (
    add_product,
    channel,
    money,
    op_text,
    order,
    place_order,
    press,
    product,
    render,
    staff_message,
    ticket_status,
)
from tests.helpers import TestEnv


async def test_full_lifecycle(co: TestEnv) -> None:
    customer = co.member()
    staff = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", price=1250, stock=5)
    p2 = await add_product(co, "[p2]", price=300)
    placed, _, message = await place_order(co, customer, [(p1.id, 2), (p2.id, 1)])
    assert placed.status == m.PENDING
    assert placed.total_minor == 2800 and placed.subtotal_minor == 2800
    assert message.embeds[0].title == render(
        co, k.SUBMITTED_TITLE, order_number=str(placed.number), total=money(co, 2800)
    )
    assert message.embeds[1].description == render(
        co, "checkout.payment.manual.instructions", order_number=str(placed.number), total=money(co, 2800)
    )
    assert (await product(co, p1.id)).reserved == 2

    control = staff_message(co, placed)
    assert views.staff_action_id(m.PAID, placed.id) in control.custom_ids()

    paid = await press(co, staff, control, views.staff_action_id(m.PAID, placed.id))
    assert op_text(paid) == co.app.op("checkout.op.status_changed", number=placed.number, status=m.PAID)
    after = await order(co, placed.id)
    assert after.status == m.PAID
    stock = await product(co, p1.id)
    assert stock.stock_on_hand == 3 and stock.reserved == 0

    await press(co, staff, staff_message(co, after), views.staff_action_id(m.PROCESSING, placed.id))
    await press(co, staff, staff_message(co, after), views.staff_action_id(m.FULFILLED, placed.id))
    final = await order(co, placed.id)
    assert final.status == m.FULFILLED
    assert await ticket_status(co, placed.ticket_id or 0) == "closed"
    assert message.embeds[0].title == render(
        co, k.status_title_key(m.FULFILLED), order_number=str(placed.number), total=money(co, 2800)
    )
    assert channel(co, "order_queue").messages
