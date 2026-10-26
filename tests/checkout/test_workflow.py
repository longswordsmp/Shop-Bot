"""The workflow message: products, quantities, paging, cart, fulfillment, details, credits, cancelling."""

from __future__ import annotations

from pathlib import Path

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views
from tests.checkout.util import (
    FULL_SETTINGS,
    add_product,
    amount_line,
    card_text,
    component,
    error_text,
    give_credits,
    go,
    make_env,
    money,
    paragraphs,
    press,
    reload_session,
    render,
    section_lines,
    set_qty,
    start,
    ticket_status,
    total_line,
)
from tests.fakes import modal_input_ids, modal_of, submit_modal
from tests.helpers import TestEnv, deep_merge


def _line(tenv: TestEnv, name: str, quantity: int, price: int) -> str:
    return render(
        tenv,
        k.CART_LINE,
        name=views.escape(name),
        quantity=quantity,
        unit_price=money(tenv, price),
        line_total=money(tenv, price * quantity),
    )


async def test_pick_opens_quantity_form_and_cart_is_rendered(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p_1*]", price=1250, stock=5)
    _, session, _, message = await start(co, customer)
    select = component(message, views.pick_id(session.id, 0))
    assert [o.label for o in select.options] == ["[p_1*]"]  # select menus show names as they are
    assert select.options[0].description == money(co, 1250)

    picked = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    modal = modal_of(picked)
    assert modal is not None and modal.title == co.text(k.QUANTITY_TITLE)
    assert modal_input_ids(modal) == [views.QUANTITY_INPUT]

    await set_qty(co, customer, message, session.id, p1.id, 2)
    assert message.embeds[0].fields == []  # the items and amounts are sections of the card, not fields
    assert section_lines(co, message, k.ITEMS_HEADING) == [_line(co, "[p_1*]", 2, 1250)]  # escaped in the embed
    # Nothing but the subtotal is known yet: it is the summary's only (bold) line.
    assert section_lines(co, message, k.SUMMARY_HEADING) == [total_line(co, 2500, k.CART_SUBTOTAL_FIELD)]

    again = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    prefilled = modal_of(again)
    assert prefilled is not None
    text_input = next(item for item in prefilled.walk_children() if getattr(item, "custom_id", None) == "quantity")
    assert text_input.default == "2"


async def test_quantity_validation(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"max_line_quantity": 5, "max_cart_lines": 2}})
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", stock=3)
        p2 = await add_product(tenv, "[p2]", max_per_order=2)
        p3 = await add_product(tenv, "[p3]")
        _, session, _, message = await start(tenv, customer)

        bad = await set_qty(tenv, customer, message, session.id, p1.id, "abc")
        assert error_text(bad) == tenv.text(core.ERROR_INVALID_INPUT)
        negative = await set_qty(tenv, customer, message, session.id, p1.id, "-1")
        assert error_text(negative) == tenv.text(core.ERROR_INVALID_INPUT)
        decimal = await set_qty(tenv, customer, message, session.id, p1.id, "1.5")
        assert error_text(decimal) == tenv.text(core.ERROR_INVALID_INPUT)
        too_many = await set_qty(tenv, customer, message, session.id, p3.id, 6)
        assert error_text(too_many) == render(tenv, k.ERROR_QUANTITY_INVALID, max=5)
        per_order = await set_qty(tenv, customer, message, session.id, p2.id, 3)
        assert error_text(per_order) == render(tenv, k.ERROR_QUANTITY_INVALID, max=2)
        stock = await set_qty(tenv, customer, message, session.id, p1.id, 4)
        assert error_text(stock) == render(tenv, k.ERROR_OUT_OF_STOCK, name=views.escape("[p1]"), available=3)

        await set_qty(tenv, customer, message, session.id, p1.id, 3)
        await set_qty(tenv, customer, message, session.id, p2.id, 2)
        full = await set_qty(tenv, customer, message, session.id, p3.id, 1)
        assert error_text(full) == render(tenv, k.ERROR_CART_FULL, max=2)
        # changing an existing line is still allowed, 0 removes it
        await set_qty(tenv, customer, message, session.id, p2.id, 1)
        await set_qty(tenv, customer, message, session.id, p1.id, 0)
        added = await set_qty(tenv, customer, message, session.id, p3.id, 1)
        assert added.sent == []
        from shopbot.features.checkout import repo

        assert await tenv.app.db.run(repo.cart_quantities, session.id) == {p2.id: 1, p3.id: 1}
    finally:
        await tenv.close()


async def test_out_of_stock_and_inactive_products_are_rejected(co: TestEnv) -> None:
    from shopbot.features.catalog import repo as catalog_repo

    customer = co.member()
    empty = await add_product(co, "[empty]", stock=0)
    gone = await add_product(co, "[gone]")
    _, session, _, message = await start(co, customer)
    assert len(component(message, views.pick_id(session.id, 0)).options) == 2  # shown, but not orderable
    picked = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(empty.id)])
    assert error_text(picked) == render(co, k.ERROR_OUT_OF_STOCK, name=views.escape("[empty]"), available=0)
    assert modal_of(picked) is None
    await co.app.db.transaction(catalog_repo.update_product, gone.id, gone.version, now=co.clock.now(), active=False)
    stale = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(gone.id)])
    assert error_text(stale) == render(co, k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape("[gone]"))
    forged = await set_qty(co, customer, message, session.id, gone.id, 1)
    assert error_text(forged) == render(co, k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape("[gone]"))
    missing = await set_qty(co, customer, message, session.id, 999, 1)
    assert error_text(missing) == co.text(core.ERROR_NOT_FOUND)


async def test_paging_through_products(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"products_per_page": 2}})
    try:
        customer = tenv.member()
        products = [await add_product(tenv, f"[p{i}]") for i in range(5)]
        _, session, _, message = await start(tenv, customer)
        assert [o.label for o in component(message, views.pick_id(session.id, 0)).options] == ["[p0]", "[p1]"]
        previous = component(message, views.page_id(session.id, 0))
        following = component(message, views.page_id(session.id, 1))
        assert previous.disabled and not following.disabled
        assert previous.label == tenv.text(core.BUTTON_PREV) and following.label == tenv.text(core.BUTTON_NEXT)
        assert render(tenv, core.PAGE_INDICATOR, page=1, pages=3) in (message.embeds[0].footer.text or "")

        await press(tenv, customer, message, views.page_id(session.id, 2))
        assert [o.label for o in component(message, views.pick_id(session.id, 2)).options] == ["[p4]"]
        assert component(message, views.page_id(session.id, 2)).disabled  # next is disabled on the last page
        assert (await reload_session(tenv, session.id)).page == 2

        await press(tenv, customer, message, views.page_id(session.id, 99))  # clamped
        assert [o.label for o in component(message, views.pick_id(session.id, 2)).options] == ["[p4]"]
        assert len(products) == 5
    finally:
        await tenv.close()


async def test_empty_catalog(co: TestEnv) -> None:
    customer = co.member()
    _, session, _, message = await start(co, customer)
    assert len(message.embeds) == 1 and co.text(k.BROWSE_EMPTY_CATALOG) in paragraphs(message)
    assert section_lines(co, message, k.ITEMS_HEADING) == [co.text(k.CART_EMPTY)]
    assert section_lines(co, message, k.SUMMARY_HEADING) is None  # nothing to sum up yet
    assert views.pick_id(session.id, 0) not in message.custom_ids()
    assert component(message, views.go_id(session.id, m.CART)).disabled  # empty cart: cannot continue


async def test_cart_step_edit_and_navigation(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=500)
    _, session, _, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 2)
    await go(co, customer, message, session.id, m.CART)
    assert message.embeds[0].title == co.text(k.CART_TITLE)
    edit = component(message, views.edit_id(session.id))
    assert [o.label for o in edit.options] == ["[p1]"]
    assert edit.options[0].description == render(co, k.CART_EDIT_OPTION, quantity=2, line_total=money(co, 1000))
    opened = await press(co, customer, message, views.edit_id(session.id), values=[str(p1.id)])
    assert modal_of(opened) is not None
    not_in_cart = await press(co, customer, message, views.edit_id(session.id), values=["12345"])
    assert error_text(not_in_cart) == co.text(core.ERROR_NOT_FOUND)
    back = component(message, views.go_id(session.id, m.BROWSE))
    assert back.label == co.text(core.BUTTON_BACK)
    await set_qty(co, customer, message, session.id, p1.id, 0)  # empty cart -> continue disabled
    assert component(message, views.go_id(session.id, m.REVIEW)).disabled
    forged = await go(co, customer, message, session.id, m.REVIEW)
    assert error_text(forged) == co.text(core.ERROR_INVALID_INPUT)
    invalid_step = await press(co, customer, message, f"sb:co:go:{session.id}:nowhere")
    assert error_text(invalid_step) == co.text(core.ERROR_INVALID_INPUT)


async def test_minimum_order_total(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"min_order_total_minor": 2000}})
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=500)
        _, session, _, message = await start(tenv, customer)
        await set_qty(tenv, customer, message, session.id, p1.id, 2)
        await go(tenv, customer, message, session.id, m.CART)
        # The notice follows the step's text, before the items.
        notice = render(tenv, k.CART_MINIMUM_NOT_MET, minimum=money(tenv, 2000))
        assert paragraphs(message)[1:3] == [tenv.text(k.CART_DESCRIPTION), notice]
        assert component(message, views.go_id(session.id, m.REVIEW)).disabled
        forged = await go(tenv, customer, message, session.id, m.REVIEW)
        assert error_text(forged) == render(tenv, k.CART_MINIMUM_NOT_MET, minimum=money(tenv, 2000))
        await set_qty(tenv, customer, message, session.id, p1.id, 4)
        assert len(message.embeds) == 1
        assert not component(message, views.go_id(session.id, m.REVIEW)).disabled
    finally:
        await tenv.close()


async def test_only_the_owner_can_use_the_workflow(co: TestEnv) -> None:
    customer = co.member()
    other = co.member(name="other")
    staffer = co.member(name="staffer", staff=True)
    p1 = await add_product(co, "[p1]")
    _, session, _, message = await start(co, customer)
    for user in (other, staffer):
        denied = await press(co, user, message, views.pick_id(session.id, 0), values=[str(p1.id)])
        assert error_text(denied) == co.text(core.ERROR_PERMISSION)
        assert modal_of(denied) is None
        forged = await set_qty(co, user, message, session.id, p1.id, 1)
        assert error_text(forged) == co.text(core.ERROR_PERMISSION)
        cancel = await press(co, user, message, views.cancel_yes_id(session.id))
        assert error_text(cancel) == co.text(core.ERROR_PERMISSION)
    assert (await reload_session(co, session.id)).status == m.ACTIVE
    missing = await press(co, customer, message, views.go_id(999, m.CART))
    assert error_text(missing) == co.text(core.ERROR_NOT_FOUND)


async def test_cancel_confirmation_flow(co: TestEnv) -> None:
    customer = co.member()
    _, session, _, message = await start(co, customer)
    await press(co, customer, message, views.cancel_id(session.id))
    assert message.embeds[0].description == co.text(k.CANCEL_CONFIRM_DESCRIPTION)
    assert message.embeds[0].title == co.text(k.CANCEL_CONFIRM_TITLE)
    assert component(message, views.cancel_yes_id(session.id)).label == co.text(k.BUTTON_CANCEL_YES)
    assert component(message, views.cancel_no_id(session.id)).label == co.text(k.BUTTON_KEEP_ORDER)
    await press(co, customer, message, views.cancel_no_id(session.id))
    assert message.embeds[0].title == co.text(k.BROWSE_TITLE)
    await press(co, customer, message, views.cancel_id(session.id))
    await press(co, customer, message, views.cancel_yes_id(session.id))
    assert paragraphs(message) == [
        render(co, k.TICKET_REFERENCE_LINE, ticket_number="0001"),
        co.text(k.CANCELLED_DESCRIPTION),
    ]
    assert message.custom_ids() == []
    assert (await reload_session(co, session.id)).status == m.SESSION_CANCELLED
    assert await ticket_status(co, session.ticket_id) == "closed"
    late = await press(co, customer, message, views.go_id(session.id, m.CART))
    assert error_text(late) == co.text(k.ERROR_SESSION_CLOSED)
    twice = await press(co, customer, message, views.cancel_yes_id(session.id))
    assert error_text(twice) == co.text(k.ERROR_SESSION_CLOSED)


async def test_fulfillment_details_and_review(cof: TestEnv) -> None:
    tenv = cof
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=1000)
    _, session, _, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, p1.id, 2)
    await go(tenv, customer, message, session.id, m.CART)
    await go(tenv, customer, message, session.id, m.FULFILLMENT)
    assert message.embeds[0].title == tenv.text(k.FULFILLMENT_TITLE)
    select = component(message, views.fulfillment_id(session.id))
    assert [o.label for o in select.options] == [
        tenv.text(k.option_key("pickup", "label")),
        tenv.text(k.option_key("delivery", "label")),
    ]
    assert component(message, views.go_id(session.id, m.DETAILS)).disabled
    forged = await go(tenv, customer, message, session.id, m.DETAILS)
    assert error_text(forged) == tenv.text(core.ERROR_INVALID_INPUT)
    invalid = await press(tenv, customer, message, views.fulfillment_id(session.id), values=["teleport"])
    assert error_text(invalid) == tenv.text(core.ERROR_INVALID_INPUT)

    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["pickup"])
    free = render(
        tenv, k.SUMMARY_LINE, label=tenv.text(k.option_key("pickup", "label")), amount=tenv.text(k.SUMMARY_FREE)
    )
    assert free in (section_lines(tenv, message, k.SUMMARY_HEADING) or [])
    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["delivery"])
    # The chosen option is listed with its fee in the summary, with the fees and the total so far.
    delivery = amount_line(tenv, tenv.text(k.option_key("delivery", "label")), 500)
    summary = section_lines(tenv, message, k.SUMMARY_HEADING)
    assert summary is not None and summary[1] == delivery and summary[-1] == total_line(tenv, 2000 + 500 + 150 + 200)

    await go(tenv, customer, message, session.id, m.DETAILS)
    assert message.embeds[0].title == tenv.text(k.DETAILS_TITLE)
    assert component(message, views.go_id(session.id, m.REVIEW)).disabled
    opened = await press(tenv, customer, message, views.details_id(session.id, 0))
    modal = modal_of(opened)
    assert modal is not None and modal.title == tenv.text(k.DETAILS_MODAL_TITLE)
    assert modal_input_ids(modal) == ["d.name", "o.delivery.address", "o.delivery.phone"]
    missing = await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": "Al", "o.delivery.address": " ", "o.delivery.phone": ""},
        message=message,
    )
    assert error_text(missing) == tenv.text(core.ERROR_INVALID_INPUT)
    too_short = await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": "A", "o.delivery.address": "x", "o.delivery.phone": ""},
        message=message,
    )
    assert error_text(too_short) == tenv.text(core.ERROR_INVALID_INPUT)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": "[sample *name*]", "o.delivery.address": "[sample address]", "o.delivery.phone": ""},
        message=message,
    )
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields[tenv.text(k.form_field_key("name", "label"))] == views.escape("[sample *name*]")
    assert tenv.text(k.option_form_key("delivery", "phone", "label")) not in fields  # empty optional answer
    assert not component(message, views.go_id(session.id, m.REVIEW)).disabled

    await go(tenv, customer, message, session.id, m.REVIEW)
    assert section_lines(tenv, message, k.SUMMARY_HEADING) == [
        amount_line(tenv, tenv.text(k.CART_SUBTOTAL_FIELD), 2000),
        amount_line(tenv, tenv.text(k.option_key("delivery", "label")), 500),
        amount_line(tenv, tenv.text(k.fee_label_key("service")), 150),
        amount_line(tenv, tenv.text(k.fee_label_key("tax")), 200),  # 10 % of the 20.00 subtotal
        total_line(tenv, 2000 + 500 + 150 + 200),
    ]
    # The customer's answer is shown (escaped) as a field under the card.
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields[tenv.text(k.form_field_key("name", "label"))] == views.escape("[sample *name*]")
    assert views.submit_id(session.id, 2850, 2850) in message.custom_ids()
    # Items (choosing and the cart) · details · delivery · review · payment: the review is step 4 of 5.
    assert render(tenv, core.PROGRESS_FORMAT, current=4, total=5, bar="") in (message.embeds[0].footer.text or "")
    # going back to details keeps the answers; the stored answers survive a second (partial) submission
    session_now = await reload_session(tenv, session.id)
    assert session_now.details == {"d.name": "[sample *name*]", "o.delivery.address": "[sample address]"}


async def test_details_split_into_two_forms(tmp_path: Path) -> None:
    overrides = deep_merge(
        FULL_SETTINGS,
        {"checkout": {"details_form": [{"id": f"f{i}", "required": i == 0, "max_length": 50} for i in range(4)]}},
    )
    tenv = await make_env(tmp_path, overrides)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        _, session, _, message = await start(tenv, customer)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)
        await go(tenv, customer, message, session.id, m.CART)
        await go(tenv, customer, message, session.id, m.FULFILLMENT)
        await press(tenv, customer, message, views.fulfillment_id(session.id), values=["delivery"])
        await go(tenv, customer, message, session.id, m.DETAILS)
        assert component(message, views.details_id(session.id, 1)).label == tenv.text(k.BUTTON_ENTER_MORE_DETAILS)
        first = modal_of(await press(tenv, customer, message, views.details_id(session.id, 0)))
        second = modal_of(await press(tenv, customer, message, views.details_id(session.id, 1)))
        assert first is not None and second is not None
        assert len(modal_input_ids(first)) == 5 and modal_input_ids(second) == ["o.delivery.phone"]
        bad_part = await press(tenv, customer, message, views.details_id(session.id, 7))
        assert error_text(bad_part) == tenv.text(core.ERROR_INVALID_INPUT)
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.details_form_id(session.id, 0),
            fields={"d.f0": "[a]", "d.f1": "", "d.f2": "", "d.f3": "", "o.delivery.address": "[addr]"},
            message=message,
        )
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.details_form_id(session.id, 1),
            fields={"o.delivery.phone": "[phone]"},
            message=message,
        )
        details = (await reload_session(tenv, session.id)).details
        assert details == {"d.f0": "[a]", "o.delivery.address": "[addr]", "o.delivery.phone": "[phone]"}
    finally:
        await tenv.close()


async def test_credits_toggle(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=1000)
    await give_credits(co, customer.id, 300)
    _, session, _, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 1)
    await go(co, customer, message, session.id, m.CART)
    await go(co, customer, message, session.id, m.REVIEW)
    # The credit that could be applied is shown under the total (it is not taken off yet).
    assert section_lines(co, message, k.SUMMARY_HEADING) == [
        total_line(co, 1000),
        amount_line(co, co.text(k.REVIEW_CREDITS_AVAILABLE_FIELD), 300),
    ]
    apply = component(message, views.credits_id(session.id, True))
    assert apply.label == co.text(k.BUTTON_APPLY_CREDITS)
    await press(co, customer, message, views.credits_id(session.id, True))
    credit = render(co, k.SUMMARY_CREDIT_LINE, label=co.text(k.REVIEW_CREDITS_FIELD), amount=money(co, 300))
    assert section_lines(co, message, k.SUMMARY_HEADING) == [
        amount_line(co, co.text(k.CART_SUBTOTAL_FIELD), 1000),
        credit,
        total_line(co, 700),
    ]
    assert money(co, 300) in card_text(message)
    assert views.submit_id(session.id, 700, 1000) in message.custom_ids()
    await press(co, customer, message, views.credits_id(session.id, False))
    assert views.submit_id(session.id, 1000, 1000) in message.custom_ids()


async def test_no_credit_buttons_without_balance_or_credits_feature(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"credits": False}})
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=1000)
        await give_credits(tenv, customer.id, 300)
        _, session, _, message = await start(tenv, customer)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)
        await go(tenv, customer, message, session.id, m.CART)
        await go(tenv, customer, message, session.id, m.REVIEW)
        assert views.credits_id(session.id, True) not in message.custom_ids()
        forged = await press(tenv, customer, message, views.credits_id(session.id, True))
        assert error_text(forged) == tenv.text(core.ERROR_UNAVAILABLE)
    finally:
        await tenv.close()


async def test_unavailable_product_can_be_removed_from_the_cart(co: TestEnv) -> None:
    from shopbot.features.catalog import repo as catalog_repo
    from shopbot.features.checkout import repo

    customer = co.member()
    p1 = await add_product(co, "[p1]")
    _, session, _, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 2)
    await go(co, customer, message, session.id, m.CART)
    current = await co.app.db.run(catalog_repo.get_product, p1.guild_id, p1.id)
    assert current is not None
    await co.app.db.transaction(catalog_repo.update_product, p1.id, current.version, now=co.clock.now(), archived=True)
    opened = await press(co, customer, message, views.edit_id(session.id), values=[str(p1.id)])
    assert modal_of(opened) is not None
    more = await set_qty(co, customer, message, session.id, p1.id, 3)
    assert error_text(more) == render(co, k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape("[p1]"))
    await set_qty(co, customer, message, session.id, p1.id, 0)
    assert await co.app.db.run(repo.cart_quantities, session.id) == {}
