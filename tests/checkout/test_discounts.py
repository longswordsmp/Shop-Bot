"""Discount codes on the review step: adding a typed code, using a claimed coupon, removing it, the reasons a code does
not apply, the limit on guessing codes, a code that stops applying, submission (re-checked in the order's
transaction, also under concurrent submits), how uses are counted, and every place an order's amounts are shown."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from shopbot.core.events import DISCOUNT_CODE_ENDED
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_codes, discount_repo, discount_views, repo, service, staff, views
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys as sk
from tests.checkout.util import (
    add_product,
    amount_line,
    error_text,
    give_credits,
    money,
    order,
    orders,
    press,
    reload_session,
    render,
    section_lines,
    set_qty,
    staff_message,
    start,
    submit,
    to_review,
    total_line,
)
from tests.fakes import FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv

DAY = 86_400


async def make_code(
    tenv: TestEnv,
    code: str = "SAVE10",
    kind: str = d.PERCENT,
    value: int = 1000,
    *,
    minimum: int = 0,
    max_uses: int | None = None,
    once: bool = False,
    days: int | None = None,
    products: tuple[int, ...] = (),
) -> d.Code:
    now = tenv.clock.now()
    return await tenv.app.db.transaction(
        discount_repo.create_code,
        guild_id=GUILD_ID,
        code=code,
        kind=kind,
        value=value,
        min_subtotal_minor=minimum,
        max_uses=max_uses,
        once_per_customer=once,
        expires_at=now + days * DAY if days else None,
        product_ids=list(products),
        actor_id=1,
        now=now,
    )


async def end_code(tenv: TestEnv, code: str) -> None:
    await tenv.app.db.transaction(
        discount_repo.end_code, guild_id=GUILD_ID, code=code, actor_id=1, now=tenv.clock.now()
    )


async def enter_code(tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, text: str) -> Any:
    return await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.discount_form_id(session_id),
        fields={views.DISCOUNT_INPUT: text},
        message=message,
    )


async def at_review(
    tenv: TestEnv, user: FakeMember, items: list[tuple[int, int]]
) -> tuple[m.Session, FakeTextChannel, FakeMessage]:
    _, session, ch, message = await start(tenv, user)
    await to_review(tenv, user, message, session.id, items)
    return session, ch, message


async def workflow(tenv: TestEnv, session_id: int) -> m.WorkflowData:
    data = await service.workflow_data(tenv.app, session_id)
    assert data is not None
    return data


def summary(tenv: TestEnv, message: FakeMessage) -> list[str]:
    lines = section_lines(tenv, message, k.SUMMARY_HEADING)
    assert lines is not None
    return lines


def discount_text(tenv: TestEnv, code: str, minor: int, key: str = k.SUMMARY_DISCOUNT_LINE) -> str:
    return render(tenv, key, code=code, amount=money(tenv, minor))


def reason_text(tenv: TestEnv, reason: str, code: str, **values: str | int) -> str:
    return render(tenv, k.discount_error_key(reason), code=code, **values)


# -- applying a code --------------------------------------------------------------------------------------------------


async def test_a_typed_code_is_shown_under_the_subtotal_and_replaces_the_buttons(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "SAVE10", d.PERCENT, 1000)
    session, _, message = await at_review(co, customer, [(shirt.id, 2)])
    assert views.discount_open_id(session.id) in message.custom_ids()
    assert views.discount_remove_id(session.id) not in message.custom_ids()
    opened = await press(co, customer, message, views.discount_open_id(session.id))
    assert opened.response.modal is not None  # the private code form
    await enter_code(co, customer, message, session.id, "  save10 ")  # normalized: spaces, lower case
    assert (await reload_session(co, session.id)).discount_code_id is not None
    subtotal = co.text(k.CART_SUBTOTAL_FIELD)
    assert summary(co, message) == [
        amount_line(co, subtotal, 2000),
        discount_text(co, "SAVE10", 200),
        total_line(co, 1800),
    ]
    assert views.discount_remove_id(session.id) in message.custom_ids()
    assert views.discount_open_id(session.id) not in message.custom_ids()
    data = await workflow(co, session.id)
    assert (data.totals.discount_minor, data.totals.total_minor) == (200, 1800)
    # Remove discount: the totals and the add button come back.
    await press(co, customer, message, views.discount_remove_id(session.id))
    assert (await reload_session(co, session.id)).discount_code_id is None
    assert summary(co, message) == [total_line(co, 2000)]
    assert views.discount_open_id(session.id) in message.custom_ids()


async def test_buy_one_get_one_free_is_written_as_such(co: TestEnv) -> None:
    customer = co.member()
    a = await add_product(co, "[a]", price=1000)
    b = await add_product(co, "[b]", price=599)
    await make_code(co, "BOGO", d.BOGO, 0)
    session, _, message = await at_review(co, customer, [(a.id, 1), (b.id, 1)])
    await enter_code(co, customer, message, session.id, "BOGO")
    assert discount_text(co, "BOGO", 599, k.SUMMARY_BOGO_LINE) in summary(co, message)
    assert total_line(co, 1000) in summary(co, message)


async def test_the_add_button_shows_only_while_a_code_can_be_used(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    assert views.discount_open_id(session.id) not in message.custom_ids()  # the shop has no code
    await make_code(co, "LATER", days=1)
    data = await workflow(co, session.id)
    assert data.offer.codes
    co.clock.advance(DAY)  # it expired
    assert not (await workflow(co, session.id)).offer.codes
    await make_code(co, "OVER")
    await end_code(co, "OVER")
    assert not (await workflow(co, session.id)).offer.codes


@pytest.mark.parametrize(
    "case", ["unknown", "badly_written", "ended", "expired", "minimum", "no_eligible_items", "needs_two_items"]
)
async def test_each_reason_gets_its_own_reply(co: TestEnv, case: str) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    other = await add_product(co, "[other]", price=500)
    typed = "SAVE10"
    if case == "ended":
        await make_code(co)
        await end_code(co, "SAVE10")
    elif case == "expired":
        await make_code(co, days=1)
    elif case == "minimum":
        await make_code(co, minimum=5000)
    elif case == "no_eligible_items":
        await make_code(co, products=(other.id,))
    elif case == "needs_two_items":
        await make_code(co, kind=d.BOGO, value=0)
    elif case == "badly_written":
        await make_code(co)
        typed = "SAVE 10!"
    else:
        await make_code(co, "OTHER")
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    if case == "expired":
        co.clock.advance(2 * DAY)
    reply = await enter_code(co, customer, message, session.id, typed)
    reason = {"badly_written": d.UNKNOWN}.get(case, case)
    values = {"minimum": money(co, 5000)} if case == "minimum" else {}
    assert error_text(reply) == reason_text(co, reason, views.escape(d.shown(typed)), **values)
    assert (await reload_session(co, session.id)).discount_code_id is None
    assert summary(co, message) == [total_line(co, 1000)]  # the review is unchanged


async def test_already_used_and_used_up(co: TestEnv) -> None:
    first, second = co.member(name="first"), co.member(name="second")
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "ONCE", once=True)
    await make_code(co, "SINGLE", max_uses=1)
    placed = await place_with_code(co, first, [(shirt.id, 1)], "ONCE")
    await service.change_status(co.app, placed.id, m.PAID, actor_id=None)
    await service.change_status(co.app, placed.id, m.FULFILLED, actor_id=None)  # closed: a new order can start
    co.clock.advance(60)  # past the anti-spam pause between two order tickets
    session, _, message = await at_review(co, first, [(shirt.id, 1)])
    reply = await enter_code(co, first, message, session.id, "ONCE")
    assert error_text(reply) == reason_text(co, d.ALREADY_USED, "ONCE")
    await place_with_code(co, second, [(shirt.id, 1)], "SINGLE")
    reply = await enter_code(co, first, message, session.id, "SINGLE")
    assert error_text(reply) == reason_text(co, d.USED_UP, "SINGLE")


async def place_with_code(tenv: TestEnv, user: FakeMember, items: list[tuple[int, int]], code: str) -> m.Order:
    session, _, message = await at_review(tenv, user, items)
    await enter_code(tenv, user, message, session.id, code)
    assert (await reload_session(tenv, session.id)).discount_code_id is not None
    await submit(tenv, user, message, session.id)
    session = await reload_session(tenv, session.id)
    assert session.order_id is not None
    return await order(tenv, session.order_id)


async def test_guessing_codes_is_limited(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "REAL")
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    for attempt in range(service.DISCOUNT_TRIES):
        reply = await enter_code(co, customer, message, session.id, f"GUESS{attempt}")
        assert error_text(reply) == reason_text(co, d.UNKNOWN, f"GUESS{attempt}")
    too_many = render(co, k.DISCOUNT_TOO_MANY_TRIES)
    # Even the right code is not checked now, and the form does not open.
    assert error_text(await enter_code(co, customer, message, session.id, "REAL")) == too_many
    assert (await reload_session(co, session.id)).discount_code_id is None
    opened = await press(co, customer, message, views.discount_open_id(session.id))
    assert opened.response.modal is None and error_text(opened) == too_many
    # Other customers are not affected.
    other = co.member(name="other")
    other_session, _, other_message = await at_review(co, other, [(shirt.id, 1)])
    await enter_code(co, other, other_message, other_session.id, "REAL")
    assert (await reload_session(co, other_session.id)).discount_code_id is not None
    co.clock.advance(service.DISCOUNT_TRIES_SECONDS)
    await enter_code(co, customer, message, session.id, "REAL")
    assert (await reload_session(co, session.id)).discount_code_id is not None


# -- claimed coupons --------------------------------------------------------------------------------------------------


async def claim(tenv: TestEnv, user: FakeMember, code_id: int) -> Any:
    return await click(tenv.app, user=user, custom_id=discount_views.claim_id(code_id))


async def test_a_claimed_coupon_is_offered_and_applied_with_one_click(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    coupon = await make_code(co, "WAREHOUSE", d.BOGO, 0, once=True, days=30, products=(shirt.id,))
    reply = await claim(co, customer, coupon.id)
    sent = reply.last_sent
    assert sent is not None and sent.embeds[0].title == render(co, k.CLAIM_TITLE)
    text = sent.text()
    assert render(co, k.CLAIM_DESCRIPTION, code="WAREHOUSE") in text
    assert render(co, k.TERMS_BOGO) in text and render(co, k.TERMS_ONCE) in text
    assert render(co, k.TERMS_PRODUCTS, products=views.escape("[shirt]")) in text
    assert render(co, k.TERMS_EXPIRES, date=f"<t:{coupon.expires_at}:f>") in text
    # Claiming again changes nothing (one claim per member) and shows the same reply.
    again = await claim(co, customer, coupon.id)
    assert again.last_sent is not None and again.last_sent.text() == text
    claims = await co.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM discount_claims").fetchone()[0])
    assert claims == 1
    session, _, message = await at_review(co, customer, [(shirt.id, 2)])
    use = views.discount_use_id(session.id, coupon.id)
    assert use in message.custom_ids()
    await press(co, customer, message, use)
    assert discount_text(co, "WAREHOUSE", 1000, k.SUMMARY_BOGO_LINE) in summary(co, message)
    assert use not in message.custom_ids() and views.discount_remove_id(session.id) in message.custom_ids()


async def test_coupon_buttons_newest_first_at_most_two(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    codes = [await make_code(co, name) for name in ("FIRST", "SECOND", "THIRD")]
    for code in codes:
        await claim(co, customer, code.id)
        co.clock.advance(60)
    session, _, _ = await at_review(co, customer, [(shirt.id, 1)])
    data = await workflow(co, session.id)
    assert data.offer.coupons == ((codes[2].id, "THIRD"), (codes[1].id, "SECOND"))
    buttons = views.review_discount_buttons(co.app.ui, co.app.settings, data)
    assert [b.custom_id for b in buttons] == [
        views.discount_use_id(session.id, codes[2].id),
        views.discount_use_id(session.id, codes[1].id),
        views.discount_open_id(session.id),
    ]
    # Secondary: Confirm order stays the one main action of the step (B2).
    assert [b.style for b in buttons] == ["secondary", "secondary", "secondary"]


async def test_a_coupon_the_customer_never_claimed_is_refused(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    coupon = await make_code(co, "SECRET")
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    forged = await press(co, customer, message, views.discount_use_id(session.id, coupon.id))
    assert error_text(forged) == render(co, core.ERROR_NOT_FOUND)
    assert (await reload_session(co, session.id)).discount_code_id is None


async def test_a_coupon_that_does_not_fit_the_cart_says_why(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    coupon = await make_code(co, "BIG", minimum=3000)
    await claim(co, customer, coupon.id)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    reply = await press(co, customer, message, views.discount_use_id(session.id, coupon.id))
    assert error_text(reply) == reason_text(co, d.MINIMUM, "BIG", minimum=money(co, 3000))


@pytest.mark.parametrize("case", ["ended", "expired", "used_up", "off", "deleted"])
async def test_a_claim_button_of_an_offer_that_ended(co: TestEnv, case: str) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    coupon = await make_code(co, "DONE", days=1, max_uses=1)
    if case == "ended":
        await end_code(co, "DONE")
    elif case == "expired":
        co.clock.advance(DAY)
    elif case == "used_up":
        await place_with_code(co, co.member(name="first"), [(shirt.id, 1)], "DONE")
    elif case == "deleted":  # /reset wipe_shop_data deletes the codes and keeps the announcement with its button
        await co.app.db.transaction(lambda conn: conn.execute("DELETE FROM discount_codes WHERE id = ?", (coupon.id,)))
    else:
        co.app.settings = replace(co.app.settings, checkout=replace(co.app.settings.checkout, discount_codes=False))
    reply = await claim(co, customer, coupon.id)
    assert reply.last_sent is not None
    assert reply.last_sent.embeds[0].title == render(co, k.CLAIM_ENDED_TITLE)
    assert reply.last_sent.embeds[0].description == render(co, k.CLAIM_ENDED)
    assert await co.app.db.run(discount_repo.claimed, coupon.id, customer.id) is False


async def test_claiming_a_coupon_already_used(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    coupon = await make_code(co, "ONCE", once=True)
    await place_with_code(co, customer, [(shirt.id, 1)], "ONCE")
    reply = await claim(co, customer, coupon.id)
    assert error_text(reply) == reason_text(co, d.ALREADY_USED, "ONCE")


# -- a code that stops applying -------------------------------------------------------------------------------------


async def test_a_code_that_stops_applying_shows_why_and_keeps_remove(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "SAVE10", minimum=2000)
    session, _, message = await at_review(co, customer, [(shirt.id, 2)])
    await enter_code(co, customer, message, session.id, "SAVE10")
    assert discount_text(co, "SAVE10", 200) in summary(co, message)
    # The cart changes below the minimum: "not applied" in the discount's place, the total has no discount, and the
    # note under the total says why.
    await set_qty(co, customer, message, session.id, shirt.id, 1)
    note = render(co, k.SUMMARY_DISCOUNT_NOTE, reason=reason_text(co, d.MINIMUM, "SAVE10", minimum=money(co, 2000)))
    assert summary(co, message) == [
        amount_line(co, co.text(k.CART_SUBTOTAL_FIELD), 1000),
        render(co, k.SUMMARY_DISCOUNT_OFF_LINE, code="SAVE10"),
        total_line(co, 1000),
        note,
    ]
    data = await workflow(co, session.id)
    assert data.discount is not None and data.discount.reason == d.MINIMUM and data.totals.discount_minor == 0
    # Back to the review: Remove discount stays; the add button does not come back while a code is stored.
    await press(co, customer, message, views.go_id(session.id, m.REVIEW))
    assert views.discount_remove_id(session.id) in message.custom_ids()
    assert views.discount_open_id(session.id) not in message.custom_ids()
    # Ended by staff: the same note with its reason.
    await set_qty(co, customer, message, session.id, shirt.id, 2)
    await end_code(co, "SAVE10")
    await press(co, customer, message, views.go_id(session.id, m.REVIEW))
    ended = render(co, k.SUMMARY_DISCOUNT_NOTE, reason=reason_text(co, d.ENDED, "SAVE10"))
    assert ended in summary(co, message) and total_line(co, 2000) in summary(co, message)


async def test_discount_codes_switched_off(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "SAVE10")
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    await enter_code(co, customer, message, session.id, "SAVE10")
    co.app.settings = replace(co.app.settings, checkout=replace(co.app.settings.checkout, discount_codes=False))
    data = await workflow(co, session.id)
    # A stored code is ignored: no line, no note, no buttons, totals without it.
    assert data.discount is None and data.totals.discount_minor == 0 and data.offer == m.DiscountOffer()
    assert views.review_discount_buttons(co.app.ui, co.app.settings, data) == []
    reply = await enter_code(co, customer, message, session.id, "SAVE10")
    assert error_text(reply) == render(co, core.ERROR_UNAVAILABLE)
    await submit(co, customer, message, session.id)
    placed = (await orders(co))[0]
    assert placed.discount_code is None and placed.total_minor == 1000


# -- submission -------------------------------------------------------------------------------------------------------


async def test_the_order_keeps_the_code_and_shows_it_everywhere(co: TestEnv) -> None:
    customer = co.member()
    staff_member = co.member(name="staff", staff=True)
    shirt = await add_product(co, "[shirt]", price=1250)
    code = await make_code(co, "TENOFF", d.AMOUNT, 1000)
    await give_credits(co, customer.id, 300)
    session, _, message = await at_review(co, customer, [(shirt.id, 2)])
    await enter_code(co, customer, message, session.id, "TENOFF")
    await press(co, customer, message, views.credits_id(session.id, True))
    await submit(co, customer, message, session.id)
    placed = await order(co, (await reload_session(co, session.id)).order_id or 0)
    assert (placed.discount_code_id, placed.discount_code, placed.discount_kind) == (code.id, "TENOFF", d.AMOUNT)
    assert (placed.subtotal_minor, placed.discount_minor, placed.credits_applied_minor) == (2500, 1000, 300)
    assert placed.total_minor == 1200
    payment = await co.app.db.run(repo.payment_for, placed.id)
    assert payment is not None and payment.amount_minor == 1200  # what is paid is the total after the discount
    line = discount_text(co, "TENOFF", 1000)
    # The order card (every status shows the same summary).
    assert summary(co, message)[:2] == [amount_line(co, co.text(k.CART_SUBTOTAL_FIELD), 2500), line]
    await service.change_status(co.app, placed.id, m.PAID, actor_id=staff_member.id)
    assert summary(co, message)[1] == line
    # The live staff record and the full record (/order view, Details).
    record = staff_message(co, await order(co, placed.id))
    amounts = next(f.value for f in record.embeds[0].fields if f.name == render(co, sk.AMOUNT_FIELD))
    assert line in amounts.split("\n")
    full = "\n".join(s.embeds[0].description or "" for s in await staff.lookup_messages(co.app, placed))
    full += "\n".join(f.value for s in await staff.lookup_messages(co.app, placed) for f in s.embeds[0].fields)
    assert line in full


async def test_refunds_and_store_credit_ignore_the_discount(co: TestEnv) -> None:
    customer = co.member()
    staff_member = co.member(name="staff", staff=True)
    shirt = await add_product(co, "[shirt]", price=2000)
    await make_code(co, "HALF", d.PERCENT, 5000)
    await give_credits(co, customer.id, 400)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    await enter_code(co, customer, message, session.id, "HALF")
    await press(co, customer, message, views.credits_id(session.id, True))
    await submit(co, customer, message, session.id)
    placed = await order(co, (await reload_session(co, session.id)).order_id or 0)
    assert (placed.discount_minor, placed.credits_applied_minor, placed.total_minor) == (1000, 400, 600)
    await service.change_status(co.app, placed.id, m.PAID, actor_id=staff_member.id)
    paid = await order(co, placed.id)
    # What can be refunded is what was paid (the total), never the discount.
    assert paid.refundable_minor == 600
    with pytest.raises(service.RefundError):
        await service.refund(co.app, placed.id, amount_minor=601, to_credit=False, reason=None, actor_id=1)
    await service.refund(co.app, placed.id, amount_minor=600, to_credit=False, reason=None, actor_id=1)
    assert not (await order(co, placed.id)).fully_refunded  # the applied store credit is still to give back
    await service.return_credit(co.app, placed.id, reason=None, actor_id=1)
    final = await order(co, placed.id)
    assert final.fully_refunded and final.status == m.REFUNDED and final.discount_minor == 1000


async def test_a_code_that_expires_before_submit_shows_the_price_changed(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "SOON", days=1)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    await enter_code(co, customer, message, session.id, "SOON")
    shown = await workflow(co, session.id)
    assert shown.totals.total_minor == 900
    co.clock.advance(DAY)
    await press(co, customer, message, views.submit_id(session.id, 900, shown.totals.gross_minor))
    assert await orders(co) == []
    assert render(co, k.REVIEW_PRICE_CHANGED, total=money(co, 1000)) in (message.embeds[0].description or "")
    # The review now shows why the code no longer applies; submitting what it shows places the order without it.
    assert render(co, k.SUMMARY_DISCOUNT_NOTE, reason=reason_text(co, d.EXPIRED, "SOON")) in summary(co, message)
    await submit(co, customer, message, session.id)
    placed = (await orders(co))[0]
    assert placed.discount_code is None and placed.discount_minor == 0 and placed.total_minor == 1000


async def test_a_code_used_up_by_another_order_before_submit(co: TestEnv) -> None:
    first, second = co.member(name="first"), co.member(name="second")
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "LAST", max_uses=1)
    s1, _, m1 = await at_review(co, first, [(shirt.id, 1)])
    s2, _, m2 = await at_review(co, second, [(shirt.id, 1)])
    await enter_code(co, first, m1, s1.id, "LAST")
    await enter_code(co, second, m2, s2.id, "LAST")
    shown = await workflow(co, s2.id)
    await submit(co, first, m1, s1.id)
    await press(co, second, m2, views.submit_id(s2.id, shown.totals.total_minor, shown.totals.gross_minor))
    assert len(await orders(co)) == 1
    assert render(co, k.SUMMARY_DISCOUNT_NOTE, reason=reason_text(co, d.USED_UP, "LAST")) in summary(co, m2)


async def test_concurrent_submits_respect_max_uses(co: TestEnv) -> None:
    first, second = co.member(name="first"), co.member(name="second")
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "ONLYONE", max_uses=1)
    sessions = []
    for user in (first, second):
        session, _, message = await at_review(co, user, [(shirt.id, 1)])
        await enter_code(co, user, message, session.id, "ONLYONE")
        sessions.append(await reload_session(co, session.id))
    results = await asyncio.gather(*(service.submit(co.app, s, 900, 900) for s in sessions), return_exceptions=True)
    placed = [r for r in results if isinstance(r, m.Order)]
    refused = [r for r in results if isinstance(r, repo.PriceChanged)]
    assert len(placed) == 1 and len(refused) == 1, results
    assert placed[0].discount_minor == 100 and refused[0].totals.total_minor == 1000
    assert (await co.app.db.run(discount_repo.usage, placed[0].discount_code_id or 0, 0)).uses == 1


async def _second_session(tenv: TestEnv, user: FakeMember, product_id: int) -> m.Session:
    """Another unfinished order of the same customer (in its own ticket), at the review step, built directly."""

    def build(conn: Any) -> int:
        cursor = conn.execute(
            "INSERT INTO tickets(guild_id, number, kind, category_id, user_id, channel_id, status, created_at, "
            "updated_at, last_activity_at) VALUES (?, 999, 'order', 'checkout', ?, NULL, 'open', 0, 0, 0)",
            (GUILD_ID, user.id),
        )
        return int(cursor.lastrowid)

    ticket_id = await tenv.app.db.transaction(build)
    session = await tenv.app.db.transaction(
        repo.create_session,
        guild_id=GUILD_ID,
        user_id=user.id,
        ticket_id=ticket_id,
        now=tenv.clock.now(),
        expiry_seconds=0,
    )
    await tenv.app.db.transaction(
        repo.set_quantity,
        guild_id=GUILD_ID,
        session_id=session.id,
        user_id=user.id,
        product_id=product_id,
        quantity=1,
        settings=tenv.app.settings.checkout,
        now=tenv.clock.now(),
    )
    await tenv.app.db.transaction(
        repo.update_session,
        guild_id=GUILD_ID,
        session_id=session.id,
        user_id=user.id,
        now=tenv.clock.now(),
        expiry_seconds=0,
        step=m.REVIEW,
    )
    return await tenv.app.db.transaction(
        repo.apply_discount,
        guild_id=GUILD_ID,
        session_id=session.id,
        user_id=user.id,
        now=tenv.clock.now(),
        expiry_seconds=0,
        code="ONCEONLY",
    )


async def test_concurrent_submits_respect_once_per_customer(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    await make_code(co, "ONCEONLY", once=True)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    await enter_code(co, customer, message, session.id, "ONCEONLY")
    sessions = [await reload_session(co, session.id), await _second_session(co, customer, shirt.id)]
    results = await asyncio.gather(*(service.submit(co.app, s, 900, 900) for s in sessions), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Order", "PriceChanged"], results


# -- uses -------------------------------------------------------------------------------------------------------------


async def test_uses_are_counted_from_orders(co: TestEnv) -> None:
    staff_member = co.member(name="staff", staff=True)
    shirt = await add_product(co, "[shirt]", price=1000)
    code = await make_code(co, "COUNT")

    async def placed_by(name: str) -> m.Order:
        return await place_with_code(co, co.member(name=name), [(shirt.id, 1)], "COUNT")

    async def uses() -> int:
        return (await co.app.db.run(discount_repo.usage, code.id, 0)).uses

    pending = await placed_by("pending")
    assert await uses() == 1  # waiting for payment counts
    cancelled = await placed_by("cancelled")
    await service.change_status(co.app, cancelled.id, m.CANCELLED, actor_id=staff_member.id)
    expired = await placed_by("expired")
    await service.change_status(co.app, expired.id, m.EXPIRED, actor_id=None)
    rejected = await placed_by("rejected")
    await service.change_status(co.app, rejected.id, m.REJECTED, actor_id=staff_member.id)
    assert await uses() == 1  # ended without ever being paid: not counted
    refunded = await placed_by("refunded")
    await service.change_status(co.app, refunded.id, m.PAID, actor_id=staff_member.id)
    await service.refund(co.app, refunded.id, amount_minor=900, to_credit=False, reason=None, actor_id=1)
    assert (await order(co, refunded.id)).status == m.REFUNDED
    paid_cancelled = await placed_by("paid_cancelled")
    await service.change_status(co.app, paid_cancelled.id, m.PAID, actor_id=staff_member.id)
    await service.change_status(co.app, paid_cancelled.id, m.CANCELLED, actor_id=staff_member.id)
    assert await uses() == 3  # pending, paid then refunded, paid then cancelled
    usage = await co.app.db.run(discount_repo.usage, code.id, pending.user_id)
    assert usage.customer_uses == 1


async def test_ending_a_code_publishes_the_event(co: TestEnv) -> None:
    received: list[dict[str, Any]] = []

    async def handler(**payload: Any) -> None:
        received.append(payload)

    co.app.events.subscribe(DISCOUNT_CODE_ENDED, handler)
    admin = co.member(name="admin", admin=True)
    code = await make_code(co, "BYE")
    reply = await discount_codes.end(co.app, actor_id=admin.id, code="bye")
    assert reply.tone == "success"
    assert received == [{"code_id": code.id, "code": "BYE", "actor_id": admin.id}]
    with pytest.raises(discount_codes.Refused):
        await discount_codes.end(co.app, actor_id=admin.id, code="BYE")
    assert len(received) == 1


async def test_lookup_api_for_other_features(co: TestEnv) -> None:
    shirt = await add_product(co, "[shirt]", price=1000)
    code = await make_code(co, "WAREHOUSE", d.BOGO, 0, once=True, products=(shirt.id,))
    found = await discount_codes.usable_code(co.app, " warehouse ")
    assert found is not None and found.id == code.id
    assert await discount_codes.usable_code(co.app, "NOPE") is None
    terms = await discount_codes.terms_text(co.app, code)
    assert co.app.op("checkout.discounts.terms.bogo") in terms and views.escape("[shirt]") in terms
    button = discount_views.claim_button(co.app.ui, k.DISCOUNT_BUTTON_ADD, code.id)
    assert button.custom_id == f"sb:dc:claim:{code.id}" and button.style == "success"
    await end_code(co, "WAREHOUSE")
    assert await discount_codes.usable_code(co.app, "WAREHOUSE") is None
