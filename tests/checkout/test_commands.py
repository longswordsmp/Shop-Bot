"""``/order`` staff commands (operator replies) and the staff order-list paging."""

from __future__ import annotations

from pathlib import Path

from shopbot.features import core_keys as core
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, views
from tests.checkout.util import (
    FULL_SETTINGS,
    add_product,
    balance,
    error_text,
    go,
    make_env,
    op_text,
    order,
    place_order,
    press,
    run_command,
    set_qty,
    start,
    texts,
)
from tests.fakes import submit_modal
from tests.helpers import TestEnv


async def test_commands_are_staff_only(co: TestEnv) -> None:
    p1 = await add_product(co, "[p1]")
    customer = co.member()
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    for name, kwargs in (
        ("lookup", {"number": 1}),
        ("list", {}),
        ("status", {"number": 1, "new_status": "paid"}),
        ("refund", {"number": 1, "amount": "1.00"}),
        ("cancel", {"number": 1}),
        ("note", {"number": 1, "text": "x"}),
    ):
        denied = await run_command(co, name, user=customer, **kwargs)
        assert error_text(denied) == co.text(core.ERROR_PERMISSION), name
    assert (await order(co, placed.id)).status == m.PENDING


async def test_status_cancel_and_note(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]", stock=2)
    placed, _, _ = await place_order(co, co.member(), [(p1.id, 1)])
    invalid = await run_command(co, "status", user=staffer, number=1, new_status="fulfilled")
    assert op_text(invalid) == co.app.op(
        "checkout.op.invalid_transition", number=1, status=m.PENDING, target=m.FULFILLED
    )
    paid = await run_command(co, "status", user=staffer, number=1, new_status="paid")
    assert op_text(paid) == co.app.op("checkout.op.status_changed", number=1, status=m.PAID)
    assert (await order(co, placed.id)).status == m.PAID
    missing = await run_command(co, "status", user=staffer, number=42, new_status="paid")
    assert op_text(missing) == co.app.op("checkout.op.not_found", number=42)
    noted = await run_command(co, "note", user=staffer, number=1, text="[sample note]")
    assert op_text(noted) == co.app.op("checkout.op.note_added", number=1)
    cancelled = await run_command(co, "cancel", user=staffer, number=1, reason="[sample reason]")
    # A paid order: nothing is refunded automatically, and the reply says what can still be refunded.
    assert op_text(cancelled) == "\n".join(
        [
            co.app.op("checkout.op.status_changed", number=1, status=m.CANCELLED),
            co.app.op("checkout.op.paid_cancel", number=1),
            co.app.op("checkout.op.refund_remaining", remaining="10.00 USD"),
        ]
    )
    events = await co.app.db.run(repo.events, placed.id)
    assert [(e.from_status, e.to_status, e.note) for e in events] == [
        (None, m.PENDING, None),
        (m.PENDING, m.PAID, None),
        (m.PAID, m.PAID, "[sample note]"),
        (m.PAID, m.CANCELLED, "[sample reason]"),
    ]


async def test_refund_command(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    placed, _, _ = await place_order(co, customer, [(p1.id, 1)])
    early = await run_command(co, "refund", user=staffer, number=1, amount="1.00")
    assert op_text(early) == co.app.op("checkout.op.refund_not_allowed", number=1, status=m.PENDING)
    await run_command(co, "status", user=staffer, number=1, new_status="paid")
    credited = await run_command(co, "refund", user=staffer, number=1, amount="4.00", to_credit=True)
    assert (op_text(credited) or "").startswith(co.app.op("checkout.op.refund_credit", number=1, amount="4.00 USD"))
    assert await balance(co, customer.id) == 400
    rest = await run_command(co, "refund", user=staffer, number=1, amount="6.00", reason="[r]")
    assert co.app.op("checkout.op.fully_refunded", number=1) in (op_text(rest) or "")
    final = await order(co, placed.id)
    assert final.status == m.REFUNDED and final.refunded_minor == 1000


async def test_lookup_shows_everything(cof: TestEnv) -> None:
    tenv = cof
    staffer = tenv.member(name="staffer", staff=True)
    customer = tenv.member()
    p1 = await add_product(tenv, "[p_1]", price=1000)
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
    await press(tenv, customer, message, views.submit_id(session.id, 1000 + 150 + 100, 1000 + 150 + 100))
    found = await run_command(tenv, "lookup", user=staffer, number=1)
    text = texts(found)
    assert tenv.app.op("checkout.lookup.title", number=1) in text
    assert "[p\\_1]" in text and "[sample name]" in text
    assert tenv.app.op("checkout.lookup.fee_line", fee="tax", amount="1.00 USD") in text
    assert all(len(msg.embeds) <= 10 for msg in found.sent)
    missing = await run_command(tenv, "lookup", user=staffer, number=7)
    assert op_text(missing) == tenv.app.op("checkout.op.not_found", number=7)


async def test_list_and_paging(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    buyers = [co.member(name=f"b{i}") for i in range(12)]
    for buyer in buyers:
        await place_order(co, buyer, [(p1.id, 1)])
    listed = await run_command(co, "list", user=staffer)
    message = listed.sent[-1]
    assert message.embeds[0].title == co.app.op("checkout.list.title", total=12)
    assert message.embeds[0].footer.text == co.app.op("checkout.list.page", page=1, pages=2)
    assert views.list_page_id(1, "", 0) in message.custom_ids()
    paged = await press(co, staffer, message, views.list_page_id(1, "", 0))
    assert paged.response_kinds() == ["edit_message"]
    assert message.embeds[0].footer.text == co.app.op("checkout.list.page", page=2, pages=2)
    denied = await press(co, buyers[0], message, views.list_page_id(0, "", 0))
    assert error_text(denied) == co.text(core.ERROR_PERMISSION)
    mine = await run_command(co, "list", user=staffer, member=buyers[0])
    assert mine.sent[-1].embeds[0].title == co.app.op("checkout.list.title", total=1)
    by_status = await run_command(co, "list", user=staffer, status="paid")
    assert by_status.sent[-1].embeds[0].description == co.app.op("checkout.list.empty")


async def test_commands_when_checkout_is_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, FULL_SETTINGS, content={"checkout.review.title": ""})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        reply = await run_command(tenv, "list", user=staffer)
        assert op_text(reply) == tenv.app.op("command.feature_disabled", feature="checkout")
    finally:
        await tenv.close()
