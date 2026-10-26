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

from shopbot.features.checkout import MODULE, gift_cards, handling_repo, staff_views, views
from shopbot.features.checkout import gift_card_store as store
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service as checkout_service
from shopbot.features.checkout.payments import gift_card
from shopbot.features.credits import repo as credits_repo
from shopbot.ui.spec import ButtonSpec, MessageSpec
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
THIRD = "LMNO-4455-6677-PQRS"


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
    assert amount.default is None  # the card's full value, never prefilled (a larger card would be under-declared)
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


async def test_after_a_first_card_the_instructions_ask_for_what_is_still_to_pay(gc: TestEnv) -> None:
    customer, placed, message = await _placed(gc)
    usd = views.order_money(gc.app.ui, gc.app.settings, "USD")

    def instructions(total: int) -> str:
        text = gc.app.content.render(gift_card.INSTRUCTIONS, order_number=str(placed.number), total=usd(total))
        assert text is not None
        return text

    before = message.embeds[0].description or ""
    assert instructions(1000) in before
    await _submit(gc, customer, placed, amount="4.00")
    after = message.embeds[0].description or ""
    assert instructions(600) in after and instructions(1000) not in after  # not the whole total again


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
        # Lower-case words in groups cannot be told apart from a sentence ("I paid with card 1234 then card 5678"):
        # deleting customers' ordinary messages would be worse than missing this rare form.
        ("abcd efgh 1234 5678", False),
        ("abcd1234efgh5678", True),
        ("AbCd1234EfGh5678", True),
        ("4111 1111 1111 1111", True),
        ("4111111111111111", True),
        ("X9K2P7Q4M8N3", True),
        ("my order 1042 is late", False),
        ("the order 1234 was fine", False),
        ("call 555-123-4567", False),
        ("thanks!! 12345", False),
        ("https://example.com/item?id=1234", False),
        ("my tracking 9400111899223456789012 has not moved", False),
        ("UPS 1Z999AA10123456784", False),
        ("order 2026-10-26-1530", False),
        ("see https://discord.com/channels/123456789012345678/123456789012345679/123456789012345680", False),
        ("ping <@123456789012345678> about <#123456789012345679>", False),
        ("my order 1042 from last week never came", False),
        ("I paid with card 1234 then card 5678", False),
        ("card 5678 also didnt work", False),
        ("MY ORDER 1042 FROM LAST WEEK", False),
        ("fedex 123456789012345 and my id 123456789012345678", False),
        ("ABCD1234 EFGH5678", True),
        ("ABCD-1234-EF-5678GH", True),
        ("378282246310005", True),
    ],
)
def test_looks_like_code(text: str, expected: bool) -> None:
    assert gift_cards.looks_like_code(text) is expected


async def _owner_reveal(tenv: TestEnv, placed: m.Order) -> Any:
    owner = tenv.member(name="owner", admin=True)
    current = await order(tenv, placed.id)
    await press(tenv, owner, staff_message(tenv, current), views.gift_card_reveal_id(placed.id))
    return owner


async def test_a_placed_order_leaves_payment_submitted_only_through_a_decision_on_its_cards(gc: TestEnv) -> None:
    """The owner revealed (and may have redeemed) the card: nobody can reject or cancel the order and drop the card
    unpaid. Card problem first; redeemed cards of an order cancelled afterwards become store credit."""
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    owner = await _owner_reveal(gc, placed)
    current = await order(gc, placed.id)
    record = staff_message(gc, current)
    assert views.staff_action_id(m.REJECTED, placed.id, m.PAYMENT_SUBMITTED) not in control_ids(record)
    assert views.staff_action_id(m.CANCELLED, placed.id, m.PAYMENT_SUBMITTED) not in control_ids(record)
    staffer = gc.member(name="staffer", staff=True)
    await run_command(gc, "cancel", user=staffer, number=placed.number)
    await run_command(gc, "status", user=owner, number=placed.number, new_status="rejected")
    assert (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED
    assert (await _raw_cards(gc, placed.id))[0]["status"] == store.SUBMITTED


async def test_payment_confirmed_never_marks_an_underpaid_order_paid(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed, amount="6.00")
    await _submit(gc, customer, placed, code=OTHER, amount="6.00")
    cards = await gc.app.db.run(store.cards, placed.id)
    owner = await _owner_reveal(gc, placed)
    await submit_modal(
        gc.app,
        user=owner,
        custom_id=views.gift_card_problem_form_id(placed.id, cards[0].id),
        fields={staff_views.REASON_INPUT: "Already used."},
    )
    current = await order(gc, placed.id)
    assert current.status == m.PAYMENT_SUBMITTED  # card 2 still waits
    await press(gc, owner, staff_message(gc, current), views.staff_action_id(m.PAID, placed.id, m.PAYMENT_SUBMITTED))
    back = await order(gc, placed.id)
    assert back.status == m.PENDING  # $6.00 redeemed of $10.00: the customer pays the rest
    assert [c.status for c in await gc.app.db.run(store.cards, placed.id)] == [store.REFUSED, store.REDEEMED]
    assert await gc.app.db.run(credits_repo.order_entry, GUILD_ID, placed.id, credits_repo.KIND_GIFT_CARD) is None


async def test_mark_paid_on_a_pending_order_gives_up_its_unseen_cards(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed, amount="4.00")
    owner = gc.member(name="owner", admin=True)
    current = await order(gc, placed.id)
    await press(gc, owner, staff_message(gc, current), views.staff_action_id(m.PAID, placed.id, m.PENDING))
    assert (await order(gc, placed.id)).status == m.PAID
    (row,) = await _raw_cards(gc, placed.id)
    assert row["status"] == store.WITHDRAWN and row["sealed"] is None  # never redeemed by anyone
    assert await gc.app.db.run(credits_repo.order_entry, GUILD_ID, placed.id, credits_repo.KIND_GIFT_CARD) is None


async def test_the_reveal_reply_and_the_record_stay_within_discord_limits(tmp_path: Path) -> None:
    from shopbot.config.settings import SettingsError, parse_settings
    from shopbot.ui.limits import validate_message
    from tests.helpers import base_settings_dict, deep_merge

    with pytest.raises(SettingsError):  # five waiting cards fit the reveal reply's five rows of buttons
        parse_settings(deep_merge(base_settings_dict(), {"payments": {"gift_card": {"max_cards": 6}}}))
    tenv = await make_env(
        tmp_path, {**GIFT_CARDS, "payments": {**GIFT_CARDS["payments"], "gift_card": {"max_cards": 5}}}
    )
    try:
        customer, placed, _ = await _placed(tenv, price=5000)
        codes = [CODE, OTHER, THIRD, "TUVW-1122-3344-XYZA", "BCDE-5566-7788-FGHI"]
        for code in codes:
            await _submit(tenv, customer, placed, code=code, amount="10.00")
        assert (await order(tenv, placed.id)).status == m.PAYMENT_SUBMITTED
        owner = tenv.member(name="owner", admin=True)
        current = await order(tenv, placed.id)
        revealed = await press(tenv, owner, staff_message(tenv, current), views.gift_card_reveal_id(placed.id))
        assert revealed.last_sent is not None and len(revealed.last_sent.custom_ids()) == 10
        # Long refusal reasons on every card: the record's payment field is split, never over Discord's limits.
        cards = await tenv.app.db.run(store.cards, placed.id)
        for card in cards[:4]:
            await submit_modal(
                tenv.app,
                user=owner,
                custom_id=views.gift_card_problem_form_id(placed.id, card.id),
                fields={staff_views.REASON_INPUT: "x" * store.REASON_MAX},
            )
        view = await checkout_service.order_view(tenv.app, placed.id)
        data = await tenv.app.db.run(handling_repo.staff_data, placed.id)
        assert view is not None
        for spec in checkout_service.staff_specs(tenv.app, view, data):
            assert validate_message(spec) == []
        record = staff_message(tenv, await order(tenv, placed.id))
        assert views.gift_card_reveal_id(placed.id) in control_ids(record)  # the record was refreshed
    finally:
        await tenv.close()


async def test_an_edited_message_with_a_code_is_removed_too(gc: TestEnv) -> None:
    customer, placed, _ = await _placed(gc)
    ch = gc.guild.get_channel(int(await gc.app.db.run(_ticket_channel, placed.ticket_id or 0)))
    message = ch.add_user_message(customer, "hello")  # type: ignore[union-attr]
    message.content = f"oops {CODE}"
    await gift_cards.on_message_edit(gc.app, message, message)
    assert message.deleted


async def test_a_stale_mark_paid_never_errors_when_the_last_card_placed_the_order(gc: TestEnv) -> None:
    from shopbot.features.checkout import staff

    customer, placed, _ = await _placed(gc)
    owner = gc.member(name="owner", admin=True)
    stale = await order(gc, placed.id)  # read before the customer's card placed the order
    await _submit(gc, customer, placed)
    reply = await staff.change_status(gc.app, stale, m.PAID, actor_id=owner.id)
    assert reply.tone == "warning"  # nothing changed, no error
    assert (await order(gc, placed.id)).status == m.PAYMENT_SUBMITTED


async def test_a_markdown_heavy_refusal_reason_keeps_every_message_within_limits(gc: TestEnv) -> None:
    from shopbot.ui.limits import validate_message

    customer, placed, _ = await _placed(gc)
    await _submit(gc, customer, placed)
    owner = await _owner_reveal(gc, placed)
    await submit_modal(
        gc.app,
        user=owner,
        custom_id=views.gift_card_problem_form_id(placed.id),
        fields={staff_views.REASON_INPUT: "_" * store.REASON_MAX},
    )
    assert (await order(gc, placed.id)).status == m.PENDING
    view = await checkout_service.order_view(gc.app, placed.id)
    assert view is not None
    data = await gc.app.db.run(handling_repo.staff_data, placed.id)
    for spec in checkout_service.staff_specs(gc.app, view, data):
        assert validate_message(spec) == []
    assert validate_message((await checkout_service.customer_order_messages(gc.app, placed.id)).main) == []
    assert "_" * 10 not in customer.dms[-1].text()  # the reason is escaped
    assert validate_message(_spec_of(customer.dms[-1])) == []


def _spec_of(message: Any) -> Any:
    from shopbot.ui.spec import EmbedField, EmbedSpec, MessageSpec

    embeds = tuple(
        EmbedSpec(
            title=e.title,
            description=e.description,
            fields=tuple(EmbedField(f.name or "", f.value or "") for f in e.fields),
        )
        for e in message.embeds
    )
    return MessageSpec(embeds=embeds)


async def test_a_card_whose_code_was_deleted_offers_only_its_problem_button(gc: TestEnv) -> None:
    # Two waiting cards, the second one's code deleted unread (the preview's sample): no redeemed button for it, and
    # the record says only that its code was deleted (not also that it waits for a decision).
    assert MODULE.preview is not None
    states = {state.name: state.spec for state in MODULE.preview(gc.app)}
    reveal = states["staff_gift_card_reveal"]
    assert isinstance(reveal, MessageSpec)
    rows = [[button.custom_id or "" for button in row if isinstance(button, ButtonSpec)] for row in reveal.rows]
    assert [len(row) for row in rows] == [2, 1]  # one row per card
    assert ":gcproblem:" in rows[1][0]
    record = states["staff_payment_submitted_gift_cards"]
    assert isinstance(record, MessageSpec)
    name = gc.text(gift_card.RECORD_FIELD)
    [payment] = [f.value for f in record.embeds[0].fields if f.name == name]
    erased_line = payment.split("\n")[1]
    assert gc.text(gift_card.RECORD_ERASED) in erased_line and gc.text(gift_card.RECORD_WAITING) not in erased_line
