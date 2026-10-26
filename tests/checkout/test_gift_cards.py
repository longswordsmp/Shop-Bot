"""Gift card payments (payments.provider = "gift_card"): the customer submits cards in a private form, the order is
placed once they cover the total, the payment staff reveal the codes privately and redeem or refuse them. Codes are
only stored sealed, deleted on every decision (and after keep_days), never logged or audited; unused value becomes
store credit; a code typed in the ticket chat is removed."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.features.checkout import gift_card_store as store
from shopbot.features.checkout import gift_cards, staff_views, views
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import gift_card
from shopbot.features.credits import repo as credits_repo
from tests.checkout.util import (
    add_product,
    control_ids,
    error_text,
    make_env,
    op_text,
    order,
    place_order,
    press,
    run_command,
    staff_message,
)
from tests.fakes import modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv

GIFT_CARDS: dict[str, Any] = {
    "features": {"credits": True},
    "payments": {"provider": "gift_card", "gift_card": {"buy_url": "https://example.com/buy", "max_cards": 3}},
}
CODE = "ABCD-EFGH-1234-5678"
OTHER = "WXYZ-9876-5432-QRST"


@pytest.fixture
async def gc(tmp_path: Path) -> AsyncIterator[TestEnv]:
    env = await make_env(tmp_path, GIFT_CARDS)
    try:
        yield env
    finally:
        await env.close()


async def _placed(tenv: TestEnv, price: int = 1000) -> tuple[Any, m.Order, Any]:
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=price)
    placed, _, message = await place_order(tenv, customer, [(p1.id, 1)])
    assert placed.status == m.PENDING and placed.payment_provider == gift_card.PROVIDER_ID
    return customer, placed, message


async def _submit(tenv: TestEnv, user: Any, placed: m.Order, *, code: str = CODE, amount: str = "10.00") -> Any:
    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.payment_form_submit_id(placed.id),
        fields={gift_card.CODE_INPUT: code, gift_card.PIN_INPUT: "4321", gift_card.AMOUNT_INPUT: amount},
    )


async def _raw_cards(tenv: TestEnv, order_id: int) -> list[dict[str, Any]]:
    def read(conn: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute("SELECT * FROM gift_cards WHERE order_id = ? ORDER BY id", (order_id,))]

    return await tenv.app.db.run(read)


def _reply(interaction: Any) -> str:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].description or ""


async def test_the_order_view_offers_the_private_form_and_the_buy_link(gc: TestEnv) -> None:
    customer, placed, message = await _placed(gc)
    buttons = [getattr(item, "item", item) for item in message.components()]
    assert views.payment_form_id(placed.id) in message.custom_ids()
    assert [b.url for b in buttons if getattr(b, "url", None)] == ["https://example.com/buy"]
    opened = await press(gc, customer, message, views.payment_form_id(placed.id))
    form = modal_of(opened)
    assert form is not None and form.custom_id == views.payment_form_submit_id(placed.id)
    amount = next(i for i in form.walk_children() if getattr(i, "custom_id", "") == gift_card.AMOUNT_INPUT)
    assert amount.default == "10.00"  # prefilled with what is still to pay
    # Someone else cannot open it.
    stranger = gc.member(name="stranger")
    refused = await press(gc, stranger, message, views.payment_form_id(placed.id))
    assert modal_of(refused) is None


async def test_a_card_for_the_total_places_the_order_and_is_stored_only_sealed(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    bad = await _submit(gc, customer, placed, code="no!")
    assert error_text(bad) == gc.app.content.render(gift_card.ERROR_CODE, min=store.CODE_MIN, max=store.CODE_MAX)
    bad = await _submit(gc, customer, placed, amount="ten")
    assert error_text(bad) is not None and (await order(gc, placed.id)).status == m.PENDING
    assert await _raw_cards(gc, placed.id) == []

    dms = len(customer.dms)
    done = await _submit(gc, customer, placed)
    total = views.order_money(gc.app.ui, gc.app.settings, "USD")(1000)
    assert _reply(done) == gc.app.content.render(
        gift_card.REPLY_COMPLETE, amount=total, received=total, order_number=placed.number
    )
    current = await order(gc, placed.id)
    assert current.status == m.PAYMENT_SUBMITTED
    assert len(customer.dms) == dms + 1  # "Order placed"
    (row,) = await _raw_cards(gc, placed.id)
    assert row["status"] == store.SUBMITTED and row["amount_minor"] == 1000 and row["last4"] == "5678"
    sealed = bytes(row["sealed"])
    assert b"ABCD" not in sealed and b"4321" not in sealed  # never stored readable
    assert gift_cards.vault(gc.app).open(sealed).code == CODE
    # The record shows the payment field and the gift card controls (never the code).
    record = staff_message(gc, current)
    assert "ABCD" not in record.text() and "5678" in record.text()
    ids = control_ids(record)
    assert views.gift_card_reveal_id(placed.id) in ids
    assert views.staff_action_id(m.PAID, placed.id, m.PAYMENT_SUBMITTED) in ids
    assert views.gift_card_problem_id(placed.id) in ids
    # The same card cannot be submitted again (here: the order no longer waits for a payment anyway).
    again = await _submit(gc, customer, placed)
    assert error_text(again) == gc.app.content.render(gift_card.ERROR_STATE)


async def test_only_the_payment_staff_reveal_and_every_reveal_is_audited_without_the_code(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    current = await order(gc, placed.id)
    staffer = gc.member(name="staffer", staff=True)
    refused = await press(gc, staffer, staff_message(gc, current), views.gift_card_reveal_id(placed.id))
    assert op_text(refused) == gc.app.op("checkout.gift.no_permission")
    owner = gc.member(name="owner", admin=True)
    revealed = await press(gc, owner, staff_message(gc, current), views.gift_card_reveal_id(placed.id))
    text = revealed.last_sent.text()  # type: ignore[union-attr]
    assert "||`ABCD-EFGH-1234-5678`||" in text and "||`4321`||" in text
    (row,) = await _raw_cards(gc, placed.id)
    assert row["reveal_count"] == 1 and row["revealed_by"] == owner.id

    def audit_rows(conn: Any) -> list[str]:
        return [json.dumps(dict(r)) for r in conn.execute("SELECT * FROM audit_events")]

    audited = "\n".join(await gc.app.db.run(audit_rows))
    assert "checkout.gift_card_revealed" in audited and "ABCD" not in audited and "4321" not in audited
    # A plain staff member cannot confirm the payment either.
    confirm = views.staff_action_id(m.PAID, placed.id, m.PAYMENT_SUBMITTED)
    blocked = await press(gc, staffer, staff_message(gc, current), confirm)
    assert op_text(blocked) == gc.app.op("checkout.gift.no_permission")
    command = await run_command(gc, "status", user=staffer, number=placed.number, new_status="paid")
    assert command.last_sent is not None and (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED


async def test_payment_confirmed_redeems_the_card_and_deletes_its_code(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    await press(gc, owner, staff_message(gc, current), views.staff_action_id(m.PAID, placed.id, m.PAYMENT_SUBMITTED))
    assert (await order(gc, placed.id)).status == m.PAID
    (row,) = await _raw_cards(gc, placed.id)
    assert row["status"] == store.REDEEMED and row["sealed"] is None and row["erased_at"] is not None


async def test_several_cards_add_up_and_value_beyond_the_total_becomes_store_credit(gc: TestEnv) -> None:
    customer, placed, message = await _placed(gc)
    partial = await _submit(gc, customer, placed, amount="4.00")
    assert (await order(gc, placed.id)).status == m.PENDING
    assert _reply(partial) == gc.app.content.render(
        gift_card.REPLY_PARTIAL,
        amount=views.order_money(gc.app.ui, gc.app.settings, "USD")(400),
        received=views.order_money(gc.app.ui, gc.app.settings, "USD")(400),
        remaining=views.order_money(gc.app.ui, gc.app.settings, "USD")(600),
        total=views.order_money(gc.app.ui, gc.app.settings, "USD")(1000),
    )
    assert "5678" in message.text()  # the order view lists the card (and what is still to pay)
    duplicate = await _submit(gc, customer, placed, amount="6.00")
    assert error_text(duplicate) == gc.app.content.render(gift_card.ERROR_DUPLICATE)
    await _submit(gc, customer, placed, code=OTHER, amount="8.00")
    assert (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    reply = await press(gc, owner, staff_message(gc, current), views.staff_action_id(m.PAID, placed.id, current.status))
    paid = await order(gc, placed.id)
    assert paid.status == m.PAID
    entry = await gc.app.db.run(credits_repo.order_entry, GUILD_ID, placed.id, credits_repo.KIND_GIFT_CARD)
    assert entry is not None and entry.delta_minor == 200 and entry.user_id == customer.id
    assert reply.last_sent is not None


async def test_a_refused_card_puts_the_order_back_and_the_customer_can_pay_again(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    opened = await press(gc, owner, staff_message(gc, current), views.gift_card_problem_id(placed.id))
    assert modal_of(opened) is not None
    dms = len(customer.dms)
    reply = await submit_modal(
        gc.app,
        user=owner,
        custom_id=views.gift_card_problem_form_id(placed.id),
        fields={staff_views.REASON_INPUT: "The card was already redeemed."},
    )
    assert gc.app.op("checkout.gift.outcome.pending") in (op_text(reply) or "")
    back = await order(gc, placed.id)
    assert back.status == m.PENDING
    (row,) = await _raw_cards(gc, placed.id)
    assert (
        row["status"] == store.REFUSED and row["sealed"] is None and row["reason"] == "The card was already redeemed."
    )
    assert len(customer.dms) == dms + 1
    notice = customer.dms[-1].text()
    assert "The card was already redeemed." in notice and "5678" in notice
    # The customer submits a card again (even the same one after fixing the amount) and the order is placed again.
    await _submit(gc, customer, placed)
    assert (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED
    assert len(customer.dms) == dms + 2  # "Order placed" again


async def test_one_card_redeemed_one_refused_then_cancelled_credits_the_redeemed_value(gc: TestEnv) -> None:
    customer, placed, message = await _placed(gc)
    await _submit(gc, customer, placed, amount="4.00")
    await _submit(gc, customer, placed, code=OTHER, amount="6.00")
    cards = await gc.app.db.run(store.cards, placed.id)
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    revealed = await press(gc, owner, staff_message(gc, current), views.gift_card_reveal_id(placed.id))
    assert revealed.last_sent is not None
    assert views.gift_card_redeemed_id(placed.id, cards[0].id) in revealed.last_sent.custom_ids()
    await press(gc, owner, None, views.gift_card_redeemed_id(placed.id, cards[0].id))
    assert (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED  # card 2 still waits
    await submit_modal(
        gc.app,
        user=owner,
        custom_id=views.gift_card_problem_form_id(placed.id, cards[1].id),
        fields={staff_views.REASON_INPUT: "Not a DoorDash eGift card."},
    )
    assert (await order(gc, placed.id)).status == m.PENDING
    # The customer cancels instead of paying the rest: the redeemed $4.00 becomes their store credit.
    await press(gc, customer, message, views.order_cancel_id(placed.id))
    await press(gc, customer, message, views.order_cancel_yes_id(placed.id))
    assert (await order(gc, placed.id)).status == m.CANCELLED
    entry = await gc.app.db.run(credits_repo.order_entry, GUILD_ID, placed.id, credits_repo.KIND_GIFT_CARD)
    assert entry is not None and entry.delta_minor == 400


async def test_codes_nobody_decided_on_are_deleted_after_keep_days(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    gc.clock.advance(7 * 86_400 - 60)
    await gc.app.scheduler.run_due()
    assert (await _raw_cards(gc, placed.id))[0]["sealed"] is not None
    gc.clock.advance(120)
    await gc.app.scheduler.run_due()
    (row,) = await _raw_cards(gc, placed.id)
    assert row["sealed"] is None and row["status"] == store.SUBMITTED
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    revealed = await press(gc, owner, staff_message(gc, current), views.gift_card_reveal_id(placed.id))
    assert gc.app.op("checkout.gift.reveal.erased", days=7) in revealed.last_sent.text()  # type: ignore[union-attr]


async def test_a_code_typed_in_the_ticket_is_removed_and_the_customer_told(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    ch = gc.guild.get_channel(int(await gc.app.db.run(_ticket_channel, placed.ticket_id or 0)))
    staffer = gc.member(name="staffer", staff=True)
    dms = len(customer.dms)
    leaked = ch.add_user_message(customer, f"here is my card {CODE}")  # type: ignore[union-attr]
    await gift_cards.on_message(gc.app, leaked)
    assert leaked.deleted and len(customer.dms) == dms + 1
    fine = ch.add_user_message(customer, "my order 1001 has 2 items")  # type: ignore[union-attr]
    await gift_cards.on_message(gc.app, fine)
    assert not fine.deleted
    staff_text = ch.add_user_message(staffer, f"tracking {CODE}")  # type: ignore[union-attr]
    await gift_cards.on_message(gc.app, staff_text)
    assert not staff_text.deleted


def _ticket_channel(conn: Any, ticket_id: int) -> int:
    return int(conn.execute("SELECT channel_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ABCD-EFGH-1234-5678", True),
        ("abcd-efgh-1234-5678", True),
        ("ABCD EFGH 1234 5678", True),
        ("4111 1111 1111 1111", True),
        ("X9K2P7Q4M8N3", True),
        ("my order 1042 is late", False),
        ("the order 1234 was fine", False),
        ("call 555-123-4567", False),
        ("thanks!! 12345", False),
        ("https://example.com/item?id=1234", False),
    ],
)
def test_looks_like_code(text: str, expected: bool) -> None:
    assert gift_cards.looks_like_code(text) is expected
