"""Tips for our team: the totals (added after the discount and fees, never discounted, no percent fee on it, store
credit may cover it), the [checkout.tips] settings, the typed amount, the review step's tip menu (choose, change, take
off, Other amount, refused amounts), submission (stored inside the order's fees and checked again: tips switched off
or an amount no longer offered gives the price-changed review), every place an order's amounts are shown, refunds, and
/stats (tips on their own line, never in the revenue)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import FeeDef, SettingsError, TipSettings, parse_settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import MODULE, discount_repo, repo, service, staff, stats, tips, views
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys as sk
from shopbot.features.checkout.order_money import order_money
from shopbot.features.checkout.totals import compute_totals
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.checkout.util import (
    FULL_SETTINGS,
    add_product,
    amount_line,
    card_text,
    component,
    error_text,
    give_credits,
    make_env,
    money,
    order,
    orders,
    press,
    reload_session,
    render,
    section_lines,
    staff_message,
    start,
    submit,
    to_review,
    total_line,
)
from tests.fakes import FakeInteraction, FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, base_settings_dict, deep_merge, make_settings, shipped_settings_dict

TIPS: dict[str, Any] = {"checkout": {"tips": {"enabled": True}}}
FLAT = FeeDef(id="service", kind="flat", amount_minor=150)
TAX = FeeDef(id="tax", kind="percent", basis_points=1000)


@pytest.fixture
async def ct(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """The test checkout with tips on ($2, $5, $10 in the menu, Other amount from $1.00 to $100.00)."""
    env = await make_env(tmp_path, TIPS)
    try:
        yield env
    finally:
        await env.close()


async def at_review(
    tenv: TestEnv, user: FakeMember, items: list[tuple[int, int]]
) -> tuple[m.Session, FakeTextChannel, FakeMessage]:
    _, session, ch, message = await start(tenv, user)
    await to_review(tenv, user, message, session.id, items)
    return session, ch, message


async def pick(tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, value: str) -> FakeInteraction:
    """Pick an option of the review step's tip menu (an amount in minor units, "0" or ``views.TIP_OTHER``)."""
    return await click(tenv.app, user=user, custom_id=views.tip_id(session_id), message=message, values=(value,))


async def type_tip(
    tenv: TestEnv, user: FakeMember, message: FakeMessage, session_id: int, text: str
) -> FakeInteraction:
    return await submit_modal(
        tenv.app, user=user, custom_id=views.tip_form_id(session_id), fields={views.TIP_INPUT: text}, message=message
    )


async def workflow(tenv: TestEnv, session_id: int) -> m.WorkflowData:
    data = await service.workflow_data(tenv.app, session_id)
    assert data is not None
    return data


def summary(tenv: TestEnv, message: FakeMessage) -> list[str]:
    lines = section_lines(tenv, message, k.SUMMARY_HEADING)
    assert lines is not None
    return lines


def tip_text(tenv: TestEnv, minor: int) -> str:
    return render(tenv, k.SUMMARY_TIP_LINE, amount=money(tenv, minor))


def menu(message: FakeMessage, session_id: int) -> list[tuple[str, bool]]:
    """The tip menu's options as (value, shown as selected)."""
    select = component(message, views.tip_id(session_id))
    return [(option.value, bool(option.default)) for option in select.options]


def limits(tenv: TestEnv) -> dict[str, str | int]:
    return {"minimum": money(tenv, 100), "maximum": money(tenv, 10_000)}


def with_tips(tenv: TestEnv, **changes: Any) -> None:
    """Change ``[checkout.tips]`` of a running environment."""
    settings = tenv.app.settings
    checkout = replace(settings.checkout, tips=replace(settings.checkout.tips, **changes))
    tenv.app.settings = replace(settings, checkout=checkout)


async def place_with_tip(
    tenv: TestEnv, user: FakeMember, items: list[tuple[int, int]], tip: int
) -> tuple[m.Order, FakeMessage]:
    session, _, message = await at_review(tenv, user, items)
    if tip:
        await pick(tenv, user, message, session.id, str(tip))
    await submit(tenv, user, message, session.id)
    placed = await order(tenv, (await reload_session(tenv, session.id)).order_id or 0)
    return placed, message


# -- the totals -------------------------------------------------------------------------------------------------------


def test_the_tip_is_added_after_the_discount_and_fees_and_store_credit_may_cover_it() -> None:
    lines = [(1000, 2), (500, 1)]
    without = compute_totals(lines, fulfillment_fee_minor=500, fees=[FLAT, TAX], discount_minor=500)
    totals = compute_totals(lines, fulfillment_fee_minor=500, fees=[FLAT, TAX], discount_minor=500, tip_minor=300)
    # Items 2500 - 500 discount = 2000; the percent fee is 10% of those items, never of the tip.
    assert totals.fees_minor == without.fees_minor == 150 + 200
    assert totals.discount_minor == 500 and totals.tip_minor == 300
    assert totals.gross_minor == 2000 + 500 + 350 + 300 == without.gross_minor + 300
    assert totals.stored_fees_minor == 350 + 300  # what the order stores as fees_minor
    credited = compute_totals(
        lines,
        fulfillment_fee_minor=500,
        fees=[FLAT, TAX],
        discount_minor=500,
        tip_minor=300,
        credits_available_minor=1_000,
        apply_credits=True,
    )
    assert credited.credits_applied_minor == 1_000 and credited.total_minor == 3150 - 1_000
    # Store credit covers the tip like the rest: all of it, when the balance is large enough.
    covered = compute_totals(lines, tip_minor=300, credits_available_minor=99_999, apply_credits=True)
    assert covered.credits_applied_minor == 2800 and covered.total_minor == 0
    # The orders table CHECK: total = subtotal + delivery + fees (tip included) - discount - credit.
    assert credited.total_minor == (
        credited.subtotal_minor
        + credited.fulfillment_fee_minor
        + credited.stored_fees_minor
        - credited.discount_minor
        - credited.credits_applied_minor
    )
    with pytest.raises(ValueError):
        compute_totals(lines, tip_minor=-1)


# -- settings ---------------------------------------------------------------------------------------------------------


def test_the_shipped_settings_offer_two_five_and_ten_dollars() -> None:
    tips_settings = parse_settings(shipped_settings_dict()).checkout.tips
    assert tips_settings == TipSettings(enabled=True, amounts=(200, 500, 1000), allow_custom=True, max_minor=10_000)
    assert make_settings().checkout.tips.enabled is False  # off unless [checkout.tips] switches it on


@pytest.mark.parametrize(
    ("tips_table", "expected"),
    [
        ({"amounts": []}, "checkout.tips.amounts: list 1 to 5 amounts"),
        ({"amounts": [100, 200, 300, 400, 500, 600]}, "checkout.tips.amounts: list 1 to 5 amounts"),
        ({"amounts": [200, 200]}, "checkout.tips.amounts: contains duplicates"),
        ({"amounts": [0, 500]}, "checkout.tips.amounts: every amount must be between 1 and max_minor"),
        ({"amounts": [20_000]}, "checkout.tips.amounts: every amount must be between 1 and max_minor"),
        ({"max_minor": 500}, "checkout.tips.amounts: every amount must be between 1 and max_minor"),
        ({"amounts": ["5"]}, "checkout.tips.amounts: expected a list of whole numbers"),
        ({"amounts": [True]}, "checkout.tips.amounts: expected a list of whole numbers"),
        ({"amounts": [50], "max_minor": 50}, "checkout.tips.max_minor: must be at least 100"),
        ({"max_minor": 0}, "checkout.tips.max_minor: must be >= 1"),
        ({"enabled": "yes"}, "checkout.tips.enabled: expected true or false"),
        ({"percent": 10}, "checkout.tips.percent: unknown setting"),
    ],
)
def test_tip_settings_are_validated(tips_table: dict[str, Any], expected: str) -> None:
    data = deep_merge(base_settings_dict(), {"checkout": {"tips": {"enabled": True, **tips_table}}})
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    assert any(error.startswith(expected) for error in exc.value.errors), exc.value.errors


def test_small_amounts_are_fine_without_other_amount() -> None:
    data = {"checkout": {"tips": {"enabled": True, "amounts": [25, 50], "max_minor": 50, "allow_custom": False}}}
    parsed = parse_settings(deep_merge(base_settings_dict(), data)).checkout.tips
    assert parsed.amounts == (25, 50) and not parsed.allow_custom


# -- the rules --------------------------------------------------------------------------------------------------------


def test_which_tip_counts() -> None:
    c = make_settings(TIPS).checkout
    assert [tips.allowed(c, amount) for amount in (0, 200, 500, 1000, 750, 100, 10_000)] == [True] * 7
    assert [tips.allowed(c, amount) for amount in (-1, 50, 99, 10_001)] == [False] * 4  # below 1.00, above the max
    listed_only = replace(c, tips=replace(c.tips, allow_custom=False))
    assert tips.allowed(listed_only, 500) and not tips.allowed(listed_only, 750)
    off = replace(c, tips=replace(c.tips, enabled=False))
    assert tips.allowed(off, 0) and not tips.allowed(off, 500)
    assert tips.effective(off, 500) == 0 and tips.effective(c, 750) == 750


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("7.50", 750),
        (" $7.50 ", 750),
        ("$ 7.50", 750),
        ("7.50$", 750),
        ("7.50 USD", 750),
        ("$7.50 USD", 750),
        ("USD $7.50", 750),
        ("usd 7", 700),
        ("12", 1_200),
        ("1.00", 100),
        ("100", 10_000),
        ("0.99", tips.INVALID),
        ("0", tips.INVALID),
        ("abc", tips.INVALID),
        ("-5", tips.INVALID),
        ("7.555", tips.INVALID),
        ("", tips.INVALID),
        # Another currency's sign is never read as the shop's currency (USD here).
        ("€5", tips.INVALID),
        ("£5", tips.INVALID),
        ("¥5", tips.INVALID),
        ("5 EUR", tips.INVALID),
        ("$$7", tips.INVALID),
        ("100.01", tips.TOO_HIGH),
        ("1,000", tips.TOO_HIGH),
    ],
)
def test_typed_amounts(typed: str, expected: int | str) -> None:
    c = make_settings(TIPS).checkout
    if isinstance(expected, int):
        assert tips.parse(c, typed) == expected
        return
    with pytest.raises(tips.TipRefused) as exc:
        tips.parse(c, typed)
    assert exc.value.reason == expected


# -- the tip menu on the review step ----------------------------------------------------------------------------------


async def test_the_menu_adds_changes_and_takes_off_a_tip(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 2)])
    # No tip yet: the menu shows its placeholder (nothing selected) and the note says who tips go to.
    nothing_selected = [("0", False), ("200", False), ("500", False), ("1000", False), ("other", False)]
    assert menu(message, session.id) == nothing_selected
    assert ct.text(k.TIP_NOTE) in card_text(message)
    assert summary(ct, message) == [total_line(ct, 2000)]
    await pick(ct, customer, message, session.id, "500")
    assert (await reload_session(ct, session.id)).tip_minor == 500
    subtotal = ct.text(k.CART_SUBTOTAL_FIELD)
    assert summary(ct, message) == [amount_line(ct, subtotal, 2000), tip_text(ct, 500), total_line(ct, 2500)]
    assert ct.text(k.TIP_NOTE_THANKS) in card_text(message) and ct.text(k.TIP_NOTE) not in card_text(message)
    assert ("500", True) in menu(message, session.id)
    data = await workflow(ct, session.id)
    assert (data.totals.tip_minor, data.totals.gross_minor, data.totals.total_minor) == (500, 2500, 2500)
    # The Confirm order button carries the totals with the tip.
    assert views.submit_id(session.id, 2500, 2500) in message.custom_ids()
    await pick(ct, customer, message, session.id, "200")
    assert summary(ct, message)[1:] == [tip_text(ct, 200), total_line(ct, 2200)]
    await pick(ct, customer, message, session.id, "0")
    assert (await reload_session(ct, session.id)).tip_minor == 0
    assert summary(ct, message) == [total_line(ct, 2000)]


async def test_other_amount_opens_the_form_and_the_amount_is_checked(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    opened = await pick(ct, customer, message, session.id, views.TIP_OTHER)
    assert opened.response.modal is not None  # the private amount form
    await type_tip(ct, customer, message, session.id, "$7.50")
    assert (await reload_session(ct, session.id)).tip_minor == 750
    assert tip_text(ct, 750) in summary(ct, message)
    # The customer's own amount is its own (selected) option, so Other amount can be picked again to change it.
    assert menu(message, session.id) == [
        ("0", False),
        ("200", False),
        ("500", False),
        ("1000", False),
        ("750", True),
        ("other", False),
    ]
    invalid = await type_tip(ct, customer, message, session.id, "a lot")
    assert error_text(invalid) == render(ct, k.TIP_ERROR_INVALID, **limits(ct))
    too_little = await type_tip(ct, customer, message, session.id, "0.50")
    assert error_text(too_little) == render(ct, k.TIP_ERROR_INVALID, **limits(ct))
    too_high = await type_tip(ct, customer, message, session.id, "250")
    assert error_text(too_high) == render(ct, k.TIP_ERROR_TOO_HIGH, maximum=money(ct, 10_000))
    assert (await reload_session(ct, session.id)).tip_minor == 750  # nothing changed


async def test_amounts_the_menu_does_not_offer_are_refused(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    for forged in ("50", "10001", "-5", "five"):  # below 1.00, above the maximum, not an amount
        refused = await pick(ct, customer, message, session.id, forged)
        assert error_text(refused) == render(ct, core.ERROR_INVALID_INPUT)
    assert (await reload_session(ct, session.id)).tip_minor == 0
    # Without Other amount only the listed amounts can be picked, and the form is not offered.
    with_tips(ct, allow_custom=False)
    refused = await pick(ct, customer, message, session.id, "750")
    assert error_text(refused) == render(ct, core.ERROR_INVALID_INPUT)
    other = await pick(ct, customer, message, session.id, views.TIP_OTHER)
    assert other.response.modal is None and error_text(other) == render(ct, core.ERROR_UNAVAILABLE)
    await pick(ct, customer, message, session.id, "1000")
    assert (await reload_session(ct, session.id)).tip_minor == 1000
    # Only on the review step: back on the cart, the menu's stale control changes nothing.
    await press(ct, customer, message, views.go_id(session.id, m.CART))
    stale = await pick(ct, customer, message, session.id, "200")
    assert error_text(stale) == render(ct, core.ERROR_INVALID_INPUT)
    assert (await reload_session(ct, session.id)).tip_minor == 1000
    # Tips switched off: no menu, no note, and a stale control is unavailable.
    with_tips(ct, enabled=False)
    await press(ct, customer, message, views.go_id(session.id, m.REVIEW))
    assert views.tip_id(session.id) not in message.custom_ids()
    assert ct.text(k.TIP_NOTE) not in card_text(message) and ct.text(k.TIP_NOTE_THANKS) not in card_text(message)
    off = await pick(ct, customer, message, session.id, "500")
    assert error_text(off) == render(ct, core.ERROR_UNAVAILABLE)


async def test_the_shop_without_tips_shows_no_menu(co: TestEnv) -> None:
    customer = co.member()
    shirt = await add_product(co, "[shirt]", price=1000)
    session, _, message = await at_review(co, customer, [(shirt.id, 1)])
    assert views.tip_id(session.id) not in message.custom_ids()
    assert co.text(k.TIP_NOTE) not in card_text(message)


# -- submission -------------------------------------------------------------------------------------------------------


async def test_the_order_keeps_the_tip_and_shows_it_everywhere(ct: TestEnv) -> None:
    customer = ct.member()
    staff_member = ct.member(name="staff", staff=True)
    shirt = await add_product(ct, "[shirt]", price=1250)
    await give_credits(ct, customer.id, 300)
    session, _, message = await at_review(ct, customer, [(shirt.id, 2)])
    await pick(ct, customer, message, session.id, "500")
    await press(ct, customer, message, views.credits_id(session.id, True))
    await submit(ct, customer, message, session.id)
    placed = await order(ct, (await reload_session(ct, session.id)).order_id or 0)
    # The tip is counted inside the fees (no extras here) and is its own column; the total includes it.
    assert (placed.tip_minor, placed.fees_minor, placed.credits_applied_minor) == (500, 500, 300)
    assert placed.total_minor == 2500 + 500 - 300
    payment = await ct.app.db.run(repo.payment_for, placed.id)
    assert payment is not None and payment.amount_minor == placed.total_minor  # the gift card amount includes it
    line = tip_text(ct, 500)
    credit = render(ct, k.SUMMARY_CREDIT_LINE, label=ct.text(k.REVIEW_CREDITS_FIELD), amount=money(ct, 300))
    # The order card: after the fees, before the store credit and the total.
    assert summary(ct, message)[1:] == [line, credit, total_line(ct, 2700)]
    await service.change_status(ct.app, placed.id, m.PAID, actor_id=staff_member.id)
    assert line in summary(ct, message)
    # The live staff record and the full record (/order view, Details): its own line, never among the extras.
    record = staff_message(ct, await order(ct, placed.id))
    fields = {str(f.name): str(f.value) for f in record.embeds[0].fields}
    assert line in fields[render(ct, sk.AMOUNT_FIELD)].split("\n")
    assert all(line not in value for name, value in fields.items() if name != render(ct, sk.AMOUNT_FIELD))
    looked_up = await staff.lookup_messages(ct.app, await order(ct, placed.id))
    full = "\n".join(f.value for spec in looked_up for embed in spec.embeds for f in embed.fields)
    assert line in full.split("\n")


async def test_the_record_shows_the_tip_with_operator_text_alone(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    placed, _ = await place_with_tip(ct, customer, [(shirt.id, 1)], 1000)
    view = await ct.app.db.run(repo.order_view, placed.id)
    assert view is not None
    ct.app.content.replace(file_values={})  # the shop's texts are not configured: the record uses operator wording
    lines = staff._amount_lines(ct.app, view)
    tip = ct.app.op("checkout.lookup.fact", label=ct.app.op("checkout.lookup.tip"), value="")
    assert any(entry.startswith(tip) for entry in lines), lines


async def test_a_discount_never_applies_to_the_tip(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=2000)
    await ct.app.db.transaction(
        discount_repo.create_code,
        guild_id=GUILD_ID,
        code="HALF",
        kind=d.PERCENT,
        value=5000,
        min_subtotal_minor=0,
        max_uses=None,
        once_per_customer=False,
        expires_at=None,
        product_ids=[],
        actor_id=1,
        now=ct.clock.now(),
    )
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    await pick(ct, customer, message, session.id, "1000")
    await submit_modal(
        ct.app,
        user=customer,
        custom_id=views.discount_form_id(session.id),
        fields={views.DISCOUNT_INPUT: "HALF"},
        message=message,
    )
    data = await workflow(ct, session.id)
    assert (data.totals.discount_minor, data.totals.tip_minor, data.totals.total_minor) == (1000, 1000, 2000)
    await submit(ct, customer, message, session.id)
    placed = (await orders(ct))[0]
    assert (placed.discount_minor, placed.tip_minor, placed.total_minor) == (1000, 1000, 2000)


async def test_tips_switched_off_before_submit_give_the_price_changed_review(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    await pick(ct, customer, message, session.id, "500")
    shown = (await workflow(ct, session.id)).totals
    with_tips(ct, enabled=False)
    await press(ct, customer, message, views.submit_id(session.id, shown.total_minor, shown.gross_minor))
    assert await orders(ct) == []  # nothing was placed at a price nobody saw
    assert render(ct, k.REVIEW_PRICE_CHANGED, total=money(ct, 1000)) in card_text(message)
    await submit(ct, customer, message, session.id)  # with the totals the review shows now
    placed = (await orders(ct))[0]
    assert (placed.tip_minor, placed.fees_minor, placed.total_minor) == (0, 0, 1000)


async def test_an_amount_no_longer_offered_gives_the_price_changed_review(ct: TestEnv) -> None:
    customer = ct.member()
    shirt = await add_product(ct, "[shirt]", price=1000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    await pick(ct, customer, message, session.id, views.TIP_OTHER)
    await type_tip(ct, customer, message, session.id, "60")
    shown = (await workflow(ct, session.id)).totals
    assert shown.tip_minor == 6_000
    with_tips(ct, max_minor=5_000)  # the largest tip was lowered since
    await press(ct, customer, message, views.submit_id(session.id, shown.total_minor, shown.gross_minor))
    assert await orders(ct) == []
    assert (await workflow(ct, session.id)).totals.tip_minor == 0  # it no longer counts
    await submit(ct, customer, message, session.id)
    assert (await orders(ct))[0].tip_minor == 0


async def test_the_tip_is_refunded_like_the_rest(ct: TestEnv) -> None:
    customer = ct.member()
    staff_member = ct.member(name="staff", staff=True)
    shirt = await add_product(ct, "[shirt]", price=1000)
    placed, _ = await place_with_tip(ct, customer, [(shirt.id, 1)], 500)
    await service.change_status(ct.app, placed.id, m.PAID, actor_id=staff_member.id)
    assert (await order(ct, placed.id)).refundable_minor == 1500
    await service.refund(ct.app, placed.id, amount_minor=1500, to_credit=False, reason=None, actor_id=1)
    final = await order(ct, placed.id)
    assert final.status == m.REFUNDED and final.refunded_minor == 1500 and final.tip_minor == 500


# -- texts and previews -----------------------------------------------------------------------------------------------


def test_tip_texts_are_required_only_while_tips_are_on() -> None:
    def required(overrides: dict[str, Any]) -> set[str]:
        keys = k.content_keys(make_settings(overrides))
        return {key.key for key in keys if key.key.startswith(("checkout.tip.", k.SUMMARY_TIP_LINE)) and key.required}

    registered = {key.key for key in k.content_keys(make_settings()) if key.key.startswith("checkout.tip.")}
    assert k.TIP_OPTION_AMOUNT in registered  # registered always: an order placed with a tip keeps showing it
    # The summary line is required also while tips are off: orders placed with a tip keep showing it, and their
    # totals would not add up without it.
    assert required({}) == {k.SUMMARY_TIP_LINE}
    form = {k.TIP_OPTION_CUSTOM, k.TIP_MODAL_TITLE, k.TIP_INPUT_LABEL, k.TIP_ERROR_INVALID, k.TIP_ERROR_TOO_HIGH}
    menu_texts = {k.TIP_PLACEHOLDER, k.TIP_OPTION_NONE, k.TIP_OPTION_AMOUNT, k.TIP_NOTE, k.TIP_NOTE_THANKS}
    assert required(TIPS) == menu_texts | form | {k.SUMMARY_TIP_LINE}
    assert required(deep_merge(TIPS, {"checkout": {"tips": {"allow_custom": False}}})) == menu_texts | {
        k.SUMMARY_TIP_LINE
    }


async def test_the_tip_preview_states_fit_and_add_up(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, deep_merge(FULL_SETTINGS, TIPS))
    try:
        assert MODULE.preview is not None
        states = {s.name: s.spec for s in MODULE.preview(tenv.app)}
        names = (
            "workflow_review_tip",
            "workflow_review_tip_custom_discount_credit",
            "tip_form",
            "tip_error_invalid",
            "tip_error_too_high",
            "order_tip_submitted",
            "order_tip_fulfilled",
            "staff_tip_record",
            "staff_tip_record_details",
        )
        for name in names:
            spec = states[name]
            problems = validate_modal(spec) if isinstance(spec, ModalSpec) else validate_message(spec)
            assert problems == [], (name, problems)
        review = states["workflow_review"]
        assert isinstance(review, MessageSpec)
        tip_rows = [row for row in review.rows if any(c.custom_id == views.tip_id(1) for c in row)]
        assert len(tip_rows) == 1 and len(review.rows) <= 5  # one row for the menu, within Discord's five
        combined = states["workflow_review_tip_custom_discount_credit"]
        assert isinstance(combined, MessageSpec)
        text = "\n".join(e.description or "" for e in combined.embeds)
        assert tip_text(tenv, 750) in text.split("\n")
        # The buttons that take money off come first, then the tip menu, then Back / Confirm order / Cancel.
        tip_row = next(i for i, row in enumerate(combined.rows) if any(c.custom_id == views.tip_id(1) for c in row))
        assert 0 < tip_row == len(combined.rows) - 2
        assert views.credits_id(1, False) in [c.custom_id for c in combined.rows[0]]
        # The tip note is the card's last line, after the address.
        assert text.rstrip().endswith(tenv.text(k.TIP_NOTE_THANKS))
    finally:
        await tenv.close()


# -- /stats -----------------------------------------------------------------------------------------------------------


async def test_stats_show_kept_tips_apart_and_leave_them_out_of_the_revenue(ct: TestEnv) -> None:
    staff_member = ct.member(name="staff", staff=True)
    shirt = await add_product(ct, "[shirt]", price=1000)
    kept, _ = await place_with_tip(ct, ct.member(name="a"), [(shirt.id, 1)], 500)
    partly, _ = await place_with_tip(ct, ct.member(name="b"), [(shirt.id, 2)], 200)
    returned, _ = await place_with_tip(ct, ct.member(name="c"), [(shirt.id, 1)], 1000)
    unpaid, _ = await place_with_tip(ct, ct.member(name="d"), [(shirt.id, 1)], 500)
    for placed in (kept, partly, returned):
        await service.change_status(ct.app, placed.id, m.PAID, actor_id=staff_member.id)
    await service.refund(ct.app, partly.id, amount_minor=300, to_credit=False, reason=None, actor_id=1)
    await service.refund(ct.app, returned.id, amount_minor=2000, to_credit=False, reason=None, actor_id=1)
    counted = await ct.app.db.run(stats.collect, GUILD_ID, ct.app.settings.checkout.currency, None)
    # Kept: the tips of paid orders not refunded in full (the unpaid order's tip was never paid).
    assert counted.tips_minor == 500 + 200
    paid_totals = kept.total_minor + partly.total_minor + returned.total_minor
    assert counted.revenue_minor == paid_totals - (300 + 2000) - (500 + 200) == 1000 + 2000 - 300
    assert unpaid.tip_minor == 500 and counted.sold == 2
    money_text = order_money(ct.app.ui, ct.app.settings, counted.currency)
    text = stats.stats_message(ct.app.ui, ct.app.op, money_text, counted, "all").embeds[0].description or ""
    assert ct.app.op("checkout.stats.line.tips", amount=money(ct, 700)) in text.split("\n")
    nothing = stats.stats_message(ct.app.ui, ct.app.op, money_text, stats.Stats(currency="USD"), "all")
    assert ct.app.op("checkout.stats.line.tips", amount=money(ct, 0)) not in (nothing.embeds[0].description or "")


async def test_stats_keep_at_most_what_a_refunded_order_still_holds(ct: TestEnv) -> None:
    staff_member = ct.member(name="staff", staff=True)
    bag = await add_product(ct, "[bag]", price=1500)
    shirt = await add_product(ct, "[shirt]", price=1000)
    # $15 + a $10 tip, $10 refunded: a refund counts against the items first, so the whole tip is still kept (a
    # refund of the tip alone cannot be told apart from a refund of an item).
    tip_back, _ = await place_with_tip(ct, ct.member(name="a"), [(bag.id, 1)], 1000)
    # $10 + a $5 tip, $12 refunded: the order holds only $3 more, so only $3 of its tip is kept.
    most_back, _ = await place_with_tip(ct, ct.member(name="b"), [(shirt.id, 1)], 500)
    for placed in (tip_back, most_back):
        await service.change_status(ct.app, placed.id, m.PAID, actor_id=staff_member.id)
    await service.refund(ct.app, tip_back.id, amount_minor=1000, to_credit=False, reason=None, actor_id=1)
    await service.refund(ct.app, most_back.id, amount_minor=1200, to_credit=False, reason=None, actor_id=1)
    counted = await ct.app.db.run(stats.collect, GUILD_ID, ct.app.settings.checkout.currency, None)
    assert counted.tips_minor == 1000 + 300
    # Per order: 25 - 10 - 10 = 5 and 15 - 12 - 3 = 0: a refund never takes an order's revenue below zero.
    assert counted.revenue_minor == (2500 + 1500) - (1000 + 1200) - (1000 + 300) == 500


async def test_stats_take_a_tip_paid_with_store_credit_off_the_revenue(ct: TestEnv) -> None:
    staff_member = ct.member(name="staff", staff=True)
    shirt = await add_product(ct, "[shirt]", price=1000)
    # $10 + a $5 tip, all paid with store credit: still a tip for the team, which the shop pays, so it is taken off
    # the revenue (the money behind the credit was counted when it came in, or the credit was a gift).
    customer = ct.member(name="a")
    await give_credits(ct, customer.id, 5000)
    session, _, message = await at_review(ct, customer, [(shirt.id, 1)])
    await pick(ct, customer, message, session.id, "500")
    await press(ct, customer, message, views.credits_id(session.id, True))
    await submit(ct, customer, message, session.id)
    covered = await order(ct, (await reload_session(ct, session.id)).order_id or 0)
    assert (covered.total_minor, covered.credits_applied_minor, covered.tip_minor) == (0, 1500, 500)
    if covered.paid_at is None:
        await service.change_status(ct.app, covered.id, m.PAID, actor_id=staff_member.id)
    counted = await ct.app.db.run(stats.collect, GUILD_ID, ct.app.settings.checkout.currency, None)
    assert (counted.tips_minor, counted.revenue_minor) == (500, -500)
    # $10 + a $5 tip refunded in full as store credit: it keeps no tip, and like every refund to store credit it is
    # not taken off the revenue until the credit is spent.
    credited, _ = await place_with_tip(ct, ct.member(name="b"), [(shirt.id, 1)], 500)
    await service.change_status(ct.app, credited.id, m.PAID, actor_id=staff_member.id)
    await service.refund(ct.app, credited.id, amount_minor=1500, to_credit=True, reason=None, actor_id=1)
    counted = await ct.app.db.run(stats.collect, GUILD_ID, ct.app.settings.checkout.currency, None)
    assert (counted.tips_minor, counted.revenue_minor, counted.credit_refunded_minor) == (500, -500 + 1500, 1500)
