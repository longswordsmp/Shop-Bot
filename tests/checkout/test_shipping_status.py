"""Shipped and delivered: an order with a shipping address is marked shipped with its carrier and tracking number
(the Mark shipped form or /order ship), the customer gets them with a Track package button, and Mark delivered closes
the ticket. Shipped is never set without the shipment, and shipped orders do not trigger the not-shipped reminder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff, staff_keys, staff_views, views
from shopbot.features.operations import repo as ops_repo
from tests.checkout.test_address import ADDRESS, TEXAS, _address, _at_address
from tests.checkout.util import (
    add_product,
    control_ids,
    go,
    make_env,
    op_text,
    order,
    place_order,
    press,
    reload_session,
    run_command,
    staff_message,
    submit,
    ticket_status,
)
from tests.fakes import modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv

LINK = "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400100000000000000000"


async def _paid_with_address(tenv: TestEnv) -> tuple[Any, Any, m.Order]:
    """An order shipped to Texas, marked paid by a staff member. Returns (customer, staffer, order)."""
    user, session, message = await _at_address(tenv)
    await press(tenv, user, message, views.address_country_id(session.id, 0), values=["US"])
    await _address(tenv, user, message, session.id, TEXAS)
    await go(tenv, user, message, session.id, m.FULFILLMENT)
    await press(tenv, user, message, views.fulfillment_id(session.id), values=["standard"])
    await go(tenv, user, message, session.id, m.REVIEW)
    await submit(tenv, user, message, session.id)
    order_id = (await reload_session(tenv, session.id)).order_id
    assert order_id is not None
    placed = await order(tenv, order_id)
    staffer = tenv.member(name="staffer", staff=True)
    await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    return user, staffer, await order(tenv, order_id)


async def _ship_form(
    tenv: TestEnv, staffer: Any, found: m.Order, *, rendered: str, link: str = LINK, number: str = "9400 1000"
) -> Any:
    return await submit_modal(
        tenv.app,
        user=staffer,
        custom_id=staff_views.ship_form_id(found.id, rendered),
        fields={
            staff_views.CARRIER_INPUT: " USPS ",
            staff_views.TRACKING_NUMBER_INPUT: number,
            staff_views.TRACKING_URL_INPUT: link,
        },
        message=staff_message(tenv, found),
    )


def _buttons(message: Any) -> list[Any]:
    return [getattr(item, "item", item) for item in message.components()]


async def test_mark_shipped_sends_the_tracking_number_and_mark_delivered_closes_the_ticket(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        customer, staffer, paid = await _paid_with_address(tenv)
        assert paid.status == m.PAID
        assert control_ids(staff_message(tenv, paid))[:2] == [
            views.staff_action_id(m.PROCESSING, paid.id, m.PAID),
            views.staff_action_id(m.SHIPPED, paid.id, m.PAID),  # an order with an address ships first
        ]
        # The button opens the shipment form (nothing changes yet).
        opened = await press(
            tenv, staffer, staff_message(tenv, paid), views.staff_action_id(m.SHIPPED, paid.id, m.PAID)
        )
        form = modal_of(opened)
        assert form is not None and form.custom_id == staff_views.ship_form_id(paid.id, m.PAID)
        assert (await order(tenv, paid.id)).status == m.PAID

        # A link that is not https:// is refused and nothing is written.
        refused = await _ship_form(tenv, staffer, paid, rendered=m.PAID, link="http://example.com/track")
        assert op_text(refused) == tenv.app.op("checkout.op.tracking_url_invalid")
        assert (await order(tenv, paid.id)).status == m.PAID

        dms = len(customer.dms)
        await _ship_form(tenv, staffer, paid, rendered=m.PAID)
        shipped = await order(tenv, paid.id)
        assert shipped.status == m.SHIPPED
        handling = await tenv.app.db.run(h.get_handling, paid.id)
        assert handling.shipment == h.Shipment("USPS", "9400 1000", LINK)
        assert handling.tracking == "USPS · 9400 1000"  # /myorders and the record show it as the tracking details

        # The customer gets the carrier, the tracking number and a Track package button.
        assert len(customer.dms) == dms + 1
        notice = customer.dms[-1]
        fields = {f.name: f.value for f in notice.embeds[0].fields}
        assert fields[tenv.app.ui.text(staff_keys.SHIPMENT_CARRIER_FIELD)] == "USPS"
        assert fields[tenv.app.ui.text(staff_keys.SHIPMENT_NUMBER_FIELD)] == "9400 1000"
        track = [b for b in _buttons(notice) if getattr(b, "url", None)]
        assert [(b.label, b.url) for b in track] == [(tenv.app.ui.text(staff_keys.SHIPMENT_TRACK_BUTTON), LINK)]

        # A second submission of the same (now stale) form changes nothing and sends nothing.
        await _ship_form(tenv, staffer, paid, rendered=m.PAID, number="other")
        assert (await tenv.app.db.run(h.get_handling, paid.id)).tracking_number == "9400 1000"
        assert len(customer.dms) == dms + 1

        record = staff_message(tenv, shipped)
        assert views.staff_action_id(m.FULFILLED, paid.id, m.SHIPPED) in control_ids(record)
        assert shipped.ticket_id is not None and await ticket_status(tenv, shipped.ticket_id) == "open"
        await press(tenv, staffer, record, views.staff_action_id(m.FULFILLED, paid.id, m.SHIPPED))
        delivered = await order(tenv, paid.id)
        assert delivered.status == m.FULFILLED and delivered.fulfilled_at is not None
        assert await ticket_status(tenv, shipped.ticket_id) == "closed"
    finally:
        await tenv.close()


async def test_a_tracking_link_is_optional(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        customer, staffer, paid = await _paid_with_address(tenv)
        await _ship_form(tenv, staffer, paid, rendered=m.PAID, link="   ")
        assert (await order(tenv, paid.id)).status == m.SHIPPED
        assert (await tenv.app.db.run(h.get_handling, paid.id)).tracking_url is None
        assert [b for b in _buttons(customer.dms[-1]) if getattr(b, "url", None)] == []  # no Track package button
    finally:
        await tenv.close()


async def test_shipped_is_never_set_without_the_shipment(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    # Not paid yet: Mark shipped is refused (the form never opens) and /order ship too.
    opened = await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.SHIPPED, placed.id, m.PENDING))
    assert modal_of(opened) is None
    assert (await order(co, placed.id)).status == m.PENDING
    command = await run_command(co, "ship", user=staffer, number=placed.number, carrier="UPS", tracking_number="1Z")
    assert command.last_sent is not None
    assert (await order(co, placed.id)).status == m.PENDING
    # A plain status change cannot skip the shipment details.
    reply = await staff.change_status(co.app, placed, m.SHIPPED, actor_id=staffer.id)
    assert reply.tone == "warning"
    assert (await order(co, placed.id)).status == m.PENDING


async def test_order_ship_command_marks_a_paid_order_shipped(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    # Without a shipping address the record completes the order directly; /order ship still records a shipment.
    assert views.staff_action_id(m.FULFILLED, placed.id, m.PAID) in control_ids(staff_message(co, placed))
    await run_command(co, "ship", user=staffer, number=placed.number, carrier="UPS", tracking_number="1Z999", link=LINK)
    assert (await order(co, placed.id)).status == m.SHIPPED
    assert (await co.app.db.run(h.get_handling, placed.id)).shipment == h.Shipment("UPS", "1Z999", LINK)


async def test_a_shipped_order_no_longer_waits_for_shipment(co: TestEnv) -> None:
    customer = co.member()
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    await press(co, staffer, staff_message(co, placed), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    thresholds = ops_repo.Thresholds(unfulfilled=60)

    async def waiting() -> list[int]:
        due = await co.app.db.run(
            ops_repo.due_reminders,
            GUILD_ID,
            now=co.clock.now() + 3600,
            thresholds=thresholds,
            kinds=(ops_repo.UNFULFILLED,),
            limit=10,
        )
        return [w.target_id for w in due]

    assert await waiting() == [placed.id]
    await run_command(co, "ship", user=staffer, number=placed.number, carrier="UPS", tracking_number="1Z999")
    assert (await order(co, placed.id)).status == m.SHIPPED
    assert await waiting() == []
    assert m.SHIPPED in ops_repo.qualifying_statuses(m.PAID)  # a shipped order still makes the buyer a customer
