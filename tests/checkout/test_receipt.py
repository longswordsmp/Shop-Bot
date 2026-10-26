"""The order ticket reads like a shop receipt: the key facts in one line, the items and the summary as sections, the
progress tracker (stages reached with one date each, then the ones to come), the shipment of a shipped order, the
compact payment methods, and the link to the place-order channel in the texts that point to Report a problem."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, preview, views
from shopbot.features.checkout import models as m
from shopbot.features.checkout import payment_methods as pm
from shopbot.features.checkout import staff_keys as sk
from shopbot.features.checkout.handling_repo import Shipment
from shopbot.ui.spec import ButtonSpec, EmbedField, EmbedSpec, MessageSpec
from tests.checkout.util import amount_line, make_env, money, progress_line, render, total_line
from tests.helpers import TestEnv

SHIPMENT = Shipment("[sample carrier]", "[sample *tracking*]", "https://example.com/track")


def _lines(tenv: TestEnv) -> tuple[m.CartLine, ...]:
    products = preview._products(2)
    return (m.CartLine(products[0], 2), m.CartLine(products[1], 1))


def _view(tenv: TestEnv, status: str, *events: tuple[str, int], ships: bool = False) -> m.OrderView:
    """A sample order in ``status`` whose history is ``events`` ((status, time) in order); ``ships``: it has a
    shipping address."""
    view = preview._order(tenv.app.settings, status, _lines(tenv))
    timeline = tuple(
        m.OrderEvent(
            id=i, order_id=view.order.id, from_status=None, to_status=s, actor_id=None, note=None, created_at=t
        )
        for i, (s, t) in enumerate(events, start=1)
    )
    details = {**view.order.details, flow.ADDRESS_COUNTRY: "US"} if ships else view.order.details
    return replace(view, order=replace(view.order, details=details, updated_at=900), timeline=timeline)


def _section(spec: MessageSpec, tenv: TestEnv, heading_key: str) -> list[str] | None:
    heading = f"**{render(tenv, heading_key)}**"
    for paragraph in "\n\n".join(e.description or "" for e in spec.embeds).split("\n\n"):
        first, *lines = paragraph.split("\n")
        if first == heading:
            return lines
    return None


def _progress(tenv: TestEnv, view: m.OrderView) -> list[str]:
    return views.progress_lines(tenv.app.ui, view.order, view.timeline)


# -- items and summary ----------------------------------------------------------------------------------------------


async def test_a_placed_order_lists_its_items_and_amounts_as_sections(cof: TestEnv) -> None:
    tenv = cof
    view = _view(tenv, m.PAID, (m.PENDING, 100), (m.PAID, 200))
    spec = views.order_message(tenv.app.ui, tenv.app.settings, view, instructions=None)
    assert not any(f.inline for e in spec.embeds for f in e.fields)  # no grid of small fields
    products = preview._products(2)
    assert _section(spec, tenv, k.ITEMS_HEADING) == [
        views.line_text(tenv.app.ui, products[0].name, 2, products[0].price_minor, 2 * products[0].price_minor),
        views.line_text(tenv.app.ui, products[1].name, 1, products[1].price_minor, products[1].price_minor),
    ]
    order = view.order
    option = tenv.text(k.option_key(order.fulfillment_option or "", "label"))
    fees = [amount_line(tenv, tenv.text(k.fee_label_key(f.fee_id)), f.amount_minor) for f in view.fees]
    assert _section(spec, tenv, k.SUMMARY_HEADING) == [
        amount_line(tenv, tenv.text(k.CART_SUBTOTAL_FIELD), order.subtotal_minor),
        amount_line(tenv, option, order.fulfillment_fee_minor),
        *fees,
        total_line(tenv, order.total_minor),  # the total in bold, last
    ]


async def test_the_store_credit_applied_is_taken_off_in_the_summary(co: TestEnv) -> None:
    view = preview._order(co.app.settings, m.PAID, _lines(co), credits=500)
    spec = views.order_message(co.app.ui, co.app.settings, view, instructions=None)
    credit = render(co, k.SUMMARY_CREDIT_LINE, label=co.text(k.REVIEW_CREDITS_FIELD), amount=money(co, 500))
    assert _section(spec, co, k.SUMMARY_HEADING) == [
        amount_line(co, co.text(k.CART_SUBTOTAL_FIELD), view.order.subtotal_minor),
        credit,
        total_line(co, view.order.total_minor),
    ]


# -- the progress tracker -------------------------------------------------------------------------------------------


async def test_the_tracker_lists_the_stages_reached_with_one_date_then_the_ones_to_come(co: TestEnv) -> None:
    view = _view(co, m.PAID, (m.PENDING, 100), (m.PAYMENT_SUBMITTED, 200), (m.PAID, 300), ships=True)
    assert _progress(co, view) == [
        progress_line(co, k.PROGRESS_DONE, m.PENDING, 100),
        progress_line(co, k.PROGRESS_DONE, m.PAYMENT_SUBMITTED, 200),
        progress_line(co, k.PROGRESS_CURRENT, m.PAID, 300),
        progress_line(co, k.PROGRESS_NEXT, m.SHIPPED),  # an order with a shipping address ships
        progress_line(co, k.PROGRESS_NEXT, m.FULFILLED),
    ]


async def test_a_refused_gift_card_takes_the_order_back_to_its_first_stage(co: TestEnv) -> None:
    view = _view(co, m.PENDING, (m.PENDING, 100), (m.PAYMENT_SUBMITTED, 200), (m.PENDING, 300))
    assert _progress(co, view) == [
        progress_line(co, k.PROGRESS_CURRENT, m.PENDING, 100),  # placed when it first waited for payment
        progress_line(co, k.PROGRESS_NEXT, m.PAID),
        progress_line(co, k.PROGRESS_NEXT, m.FULFILLED),
    ]


async def test_an_order_refunded_after_delivery_ends_with_the_refund(co: TestEnv) -> None:
    view = _view(co, m.REFUNDED, (m.PENDING, 100), (m.PAID, 200), (m.FULFILLED, 300), (m.REFUNDED, 400))
    assert _progress(co, view) == [
        progress_line(co, k.PROGRESS_DONE, m.PENDING, 100),
        progress_line(co, k.PROGRESS_DONE, m.PAID, 200),
        progress_line(co, k.PROGRESS_DONE, m.FULFILLED, 300),
        progress_line(co, k.PROGRESS_STOPPED, m.REFUNDED, 400),
    ]


async def test_a_status_without_its_history_is_still_shown(co: TestEnv) -> None:
    assert _progress(co, _view(co, m.PAID)) == [
        progress_line(co, k.PROGRESS_CURRENT, m.PAID, 900),  # when the order last changed
        progress_line(co, k.PROGRESS_NEXT, m.FULFILLED),
    ]
    assert _progress(co, _view(co, m.EXPIRED, (m.PENDING, 100))) == [
        progress_line(co, k.PROGRESS_DONE, m.PENDING, 100),
        progress_line(co, k.PROGRESS_STOPPED, m.EXPIRED, 900),
    ]


# -- the shipped card -----------------------------------------------------------------------------------------------


async def test_the_shipped_card_shows_the_carrier_tracking_number_and_track_button(co: TestEnv) -> None:
    ui, settings = co.app.ui, co.app.settings
    shipped = _view(co, m.SHIPPED, (m.PENDING, 100), (m.PAID, 200), (m.SHIPPED, 300), ships=True)
    spec = views.order_message(ui, settings, shipped, instructions=None, shipment=SHIPMENT)
    assert [(f.name, f.value, f.inline) for f in spec.embeds[-1].fields] == [
        (co.text(sk.SHIPMENT_CARRIER_FIELD), "[sample carrier]", True),
        (co.text(sk.SHIPMENT_NUMBER_FIELD), views.escape("[sample *tracking*]"), True),  # staff-typed: escaped
    ]
    (button,) = [b for row in spec.rows for b in row if isinstance(b, ButtonSpec)]
    assert (button.label, button.style, button.url) == (
        co.text(sk.SHIPMENT_TRACK_BUTTON),
        "link",
        SHIPMENT.tracking_url,
    )
    # Without a tracking link: the fields, no button; before Mark shipped (another status): neither.
    plain = views.order_message(ui, settings, shipped, instructions=None, shipment=replace(SHIPMENT, tracking_url=None))
    assert len(plain.embeds[-1].fields) == 2 and plain.rows == ()
    paid = views.order_message(ui, settings, _view(co, m.PAID), instructions=None, shipment=SHIPMENT)
    assert all(e.fields == () for e in paid.embeds) and paid.rows == ()


# -- payment methods ------------------------------------------------------------------------------------------------

METHODS = {
    "features": {"credits": True},
    "payments": {
        "provider": "gift_card",
        "gift_card": {"buy_url": "https://example.com/buy"},
        "methods": [
            {"id": "card", "status": "down", "kind": "staff"},
            {"id": "paypal", "status": "down", "kind": "staff"},
            {"id": "gift_card", "status": "active", "kind": "gift_card"},
        ],
    },
}


async def test_methods_that_are_down_share_one_line_and_only_an_unpaid_order_shows_buttons(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, METHODS)
    try:
        ui, settings = tenv.app.ui, tenv.app.settings
        names = [pm.method_name(ui, method_id) for method_id in ("card", "paypal", "gift_card")]
        text = pm.section_text(ui, settings)
        assert text is not None and text.split("\n")[1:3] == [
            render(tenv, pm.LINE_ACTIVE, method=names[2]),
            render(tenv, pm.LINE_DOWN, method=f"{names[0]}, {names[1]}"),
        ]
        for status in (m.PAYMENT_SUBMITTED, m.PAID, m.SHIPPED, m.FULFILLED, m.CANCELLED):
            spec = views.order_message(ui, settings, _view(tenv, status), instructions=None)
            assert spec.rows == (), status  # a paid or closed order has nothing to pay
            assert pm.SECTION_HEADING not in "".join(e.description or "" for e in spec.embeds)
    finally:
        await tenv.close()


# -- Report a problem links the place-order channel -----------------------------------------------------------------


async def test_report_a_problem_texts_link_the_place_order_channel(co: TestEnv) -> None:
    ui, settings = co.app.ui, co.app.settings
    (placeholder,) = k.purchase_placeholder(settings)
    registry = co.app.content.registry
    for status in k.REPORT_STATUSES:
        assert placeholder in registry.get(k.notify_description_key(status)).placeholders  # type: ignore[union-attr]
        assert placeholder in registry.get(k.status_description_key(status)).placeholders  # type: ignore[union-attr]
    assert placeholder not in registry.get(k.notify_description_key(m.REJECTED)).placeholders  # type: ignore[union-attr]
    view = _view(co, m.CANCELLED, (m.PENDING, 100), (m.CANCELLED, 200))
    linked = views.status_notification(ui, settings, view, m.CANCELLED, links={placeholder: "<#123>"})
    assert "<#123>" in (linked.embeds[0].description or "")
    card = views.order_message(ui, settings, view, instructions=None, links={placeholder: "<#123>"})
    assert "<#123>" in (card.embeds[0].description or "")
    # Before /setup created the channel (no link known): its name, never an empty gap.
    name = co.text(f"server.channel.{settings.checkout.panel_channel}.name")
    unlinked = views.status_notification(ui, settings, view, m.CANCELLED)
    assert f"#{name}" in (unlinked.embeds[0].description or "")


# -- a card longer than one description ----------------------------------------------------------------------------


async def test_a_long_card_continues_in_a_second_card_that_reads_as_one(co: TestEnv) -> None:
    head = EmbedSpec(
        title="[title]",
        color=1,
        footer_text="[footer]",
        timestamp=5,
        fields=(EmbedField("[name]", "[value]", inline=True),),
    )
    first, second = "a" * 3000, "\n".join(["b" * 100] * 20)
    embeds = views._card_embeds(head, ["[facts]", first, second])
    assert len(embeds) == 2 and all(len(e.description or "") <= 4096 for e in embeds)
    assert embeds[0].title == "[title]" and embeds[0].footer_text is None and embeds[0].fields == ()
    assert embeds[1].description == second  # the section moved whole to the second card
    assert (embeds[1].footer_text, embeds[1].timestamp, embeds[1].fields) == ("[footer]", 5, head.fields)
