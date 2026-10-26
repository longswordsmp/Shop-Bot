"""Payment methods (payments.methods): the shop's methods listed with their icons ({payment_methods}) wherever a text
presents the ways to pay; the order card asking how to pay with a button per method (nothing says a method is down
before the customer presses it); the private pop-up of a method that is down (the methods we accept at the moment,
with buttons); the gift card's private how-to; a method our team handles (posted once, the staff alerted); a payment
page; and the settings and icon checks."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import PaymentMethod, Settings, SettingsError
from shopbot.db.repos import audit as audit_repo
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import models as m
from shopbot.features.checkout import panel as checkout_panel
from shopbot.features.checkout import payment_methods as pm
from shopbot.features.checkout import service as checkout_service
from shopbot.features.checkout import views
from shopbot.features.checkout.payments import gift_card
from shopbot.features.guides import common as guides_common
from shopbot.features.guides import faq
from shopbot.features.operations import content_keys as ops_keys
from shopbot.ui.spec import ButtonSpec
from tests.checkout.util import add_product, error_text, make_env, order, place_order, press
from tests.fakes import FakeInteraction, FakeMessage, modal_of, submit_modal
from tests.helpers import GUILD_ID, ROOT, TestEnv, deep_merge, make_settings, tiny_png

BUY_URL = "https://example.com/buy"
PAGE_URL = "https://example.com/pay"


def methods(*entries: tuple[str, str, str], **urls: str) -> list[dict[str, str]]:
    out = []
    for method_id, status, kind in entries:
        entry = {"id": method_id, "status": status, "kind": kind}
        if method_id in urls:
            entry["url"] = urls[method_id]
        out.append(entry)
    return out


SHIPPED_LIKE = methods(("card", "down", "staff"), ("paypal", "down", "staff"), ("gift_card", "active", "gift_card"))
ALL = ("card", "paypal", "gift_card")


def config(method_list: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "features": {"credits": True},
        "payments": {"provider": "gift_card", "gift_card": {"buy_url": BUY_URL}, "methods": method_list},
    }


@pytest.fixture
async def pay(tmp_path: Path) -> AsyncIterator[TestEnv]:
    env = await make_env(tmp_path, config(SHIPPED_LIKE))
    try:
        yield env
    finally:
        await env.close()


async def _placed(tenv: TestEnv) -> tuple[Any, m.Order, FakeMessage]:
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=1000)
    placed, _, message = await place_order(tenv, customer, [(p1.id, 1)])
    assert placed.status == m.PENDING
    return customer, placed, message


def rendered(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.app.ui.text(key, **values)
    assert text is not None, key
    return text


def steps(tenv: TestEnv, placed: m.Order, total_minor: int) -> str:
    """The gift card's steps for ``total_minor`` still to pay."""
    total = views.order_money(tenv.app.ui, tenv.app.settings, placed.currency)(total_minor)
    return rendered(tenv, gift_card.INSTRUCTIONS, order_number=str(placed.number), total=total)


def _buttons(message: Any) -> list[Any]:
    return [getattr(item, "item", item) for item in message.components()]


def _rows(message: Any) -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for button in _buttons(message):
        rows.setdefault(button.row if button.row is not None else -1, []).append(button.custom_id or button.url)
    return rows


def _private(interaction: FakeInteraction) -> FakeMessage:
    sent = interaction.last_sent
    assert sent is not None and getattr(sent, "ephemeral", False)
    return sent


async def _submit_card(tenv: TestEnv, user: Any, placed: m.Order, amount: str) -> None:
    await submit_modal(
        tenv.app,
        user=user,
        custom_id=views.payment_form_submit_id(placed.id),
        fields={gift_card.CODE_INPUT: "ABCD-EFGH-1234-5678", gift_card.PIN_INPUT: "", gift_card.AMOUNT_INPUT: amount},
    )


def write_icons(config_dir: Path, *names: str) -> None:
    folder = config_dir / "emojis"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.png").write_bytes(tiny_png(name))


def _with(settings: Settings, method_id: str, **changes: str) -> Settings:
    """``settings`` with one method changed (e.g. back to active): only the settings, never a text."""
    changed = tuple(replace(x, **changes) if x.id == method_id else x for x in settings.payments.methods)
    return replace(settings, payments=replace(settings.payments, methods=changed))


# -- the order card: choose how to pay ------------------------------------------------------------------------------


async def test_the_order_card_asks_how_to_pay_with_a_button_per_method(pay: TestEnv) -> None:
    _, placed, message = await _placed(pay)
    description = message.embeds[0].description or ""
    # Only the heading and its lead: no method is called down (or available) before the customer presses one.
    section = next(p for p in description.split("\n\n") if p.startswith(f"**{pay.text(pm.SECTION_HEADING)}**"))
    assert section.split("\n")[1:] == [pay.text(pm.LEAD)]
    # The gift card's steps belong to its method: they are not on the card before the customer chose it.
    assert steps(pay, placed, placed.total_minor) not in description
    # A button per method in the settings' order (its name; its icon when uploaded), then Cancel order alone.
    rows = _rows(message)
    assert rows[0] == [pm.method_custom_id(placed.id, method_id) for method_id in ALL]
    assert rows[1] == [views.order_cancel_id(placed.id)]
    buttons = {b.custom_id: b for b in _buttons(message)}
    assert [buttons[pm.method_custom_id(placed.id, i)].label for i in ALL] == [
        pm.method_name(pay.app.ui, i) for i in ALL
    ]
    assert {buttons[pm.method_custom_id(placed.id, i)].style.name for i in ALL} == {"secondary"}
    assert views.payment_form_id(placed.id) not in message.custom_ids()
    assert BUY_URL not in [b.url for b in _buttons(message)]


NINE = [{"id": f"m{i}", "status": "down", "kind": "staff"} for i in range(9)]


async def test_the_method_buttons_share_rows_evenly_and_always_fit(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, config([*NINE, *methods(("gift_card", "active", "gift_card"))]))
    try:
        ui, settings = tenv.app.ui, tenv.app.settings
        *others, gift = settings.payments.methods
        cancel = ButtonSpec(label="[cancel]", custom_id="sb:co:ocancel:1", style="danger")
        provider = (ButtonSpec(label="[form]", custom_id="sb:co:payform:1"),)

        def shape(method_list: list[PaymentMethod]) -> list[int]:
            rows = pm.payment_rows(ui, _methods_only(settings, method_list), 1, provider, cancel, choosing=True)
            assert rows[-1] == (cancel,) and len(rows) <= 5
            return [len(row) for row in rows]

        # The gift card's own buttons are in its how-to: the methods have the rows above Cancel order.
        assert shape([*others[:5], gift]) == [3, 3, 1]
        assert shape([*others[:3], gift]) == [2, 2, 1]
        assert shape([*others, gift]) == [3, 3, 3, 1, 1]
        # Without a gift card method (e.g. the manual provider) its own buttons come first.
        assert shape(others) == [1, 3, 3, 3, 1]
        # Five per row when rows of three would not fit next to them (ten methods).
        ten = [ButtonSpec(label=f"[m{i}]", custom_id=f"sb:co:paym:1:m{i}") for i in range(10)]
        assert [len(row) for row in pm._method_rows(ten, free_rows=3)] == [5, 5]
    finally:
        await tenv.close()


def _methods_only(settings: Settings, method_list: list[PaymentMethod]) -> Settings:
    return replace(settings, payments=replace(settings.payments, methods=tuple(method_list)))


async def test_a_method_that_comes_back_needs_only_a_settings_change(pay: TestEnv) -> None:
    _, placed, _ = await _placed(pay)
    ui, settings = pay.app.ui, pay.app.settings
    view = await checkout_service.order_view(pay.app, placed.id)
    instructions = await checkout_service.payment_instructions(pay.app, placed)
    assert view is not None and instructions is not None
    back = _with(settings, "paypal", status="active")
    before = views.order_message(ui, settings, view, instructions=instructions)
    after = views.order_message(ui, back, view, instructions=instructions)
    # The same card and the same buttons (PayPal now does what its kind says); the list of methods is unchanged.
    assert after.embeds == before.embeds and after.rows == before.rows
    assert pm.methods_list(ui, back) == pm.methods_list(ui, settings)
    # The pop-up of a method that is still down names every method we accept now, with a button for each.
    popup = pm.down_message(ui, back, placed.id, back.payments.method("card"))  # type: ignore[arg-type]
    names = ", ".join(f"**{pm.method_name(ui, i)}**" for i in ("paypal", "gift_card"))
    assert rendered(pay, pm.DOWN_DESCRIPTION, method=pm.method_name(ui, "card"), available=names) in (
        popup.embeds[0].description or ""
    )
    ids = [b.custom_id for row in popup.rows for b in row if isinstance(b, ButtonSpec) and b.custom_id]
    assert ids == [pm.method_custom_id(placed.id, "paypal"), pm.method_custom_id(placed.id, "gift_card")]


async def test_an_order_without_payment_instructions_still_offers_every_method(pay: TestEnv) -> None:
    # E.g. an order placed before payments.provider changed: its provider's texts are gone, its methods stay the way
    # to pay (the gift card method then opens the form, which explains why).
    _, placed, _ = await _placed(pay)
    view = await checkout_service.order_view(pay.app, placed.id)
    assert view is not None
    card = views.order_message(pay.app.ui, pay.app.settings, view, instructions=None)
    rows = [[b.custom_id for b in row] for row in card.rows]
    assert rows == [[pm.method_custom_id(placed.id, i) for i in ALL], [views.order_cancel_id(placed.id)]]
    assert f"**{pay.text(pm.SECTION_HEADING)}**" in (card.embeds[0].description or "")


async def test_without_methods_the_order_card_is_unchanged(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, config([]))
    try:
        _, placed, message = await _placed(tenv)
        assert pm.SECTION_HEADING not in tenv.app.content.registry
        assert not any(cid.startswith("sb:co:paym:") for cid in message.custom_ids())
        assert views.payment_form_id(placed.id) in message.custom_ids()
        assert steps(tenv, placed, placed.total_minor) in (message.embeds[0].description or "")
    finally:
        await tenv.close()


# -- the gift card: its private how-to, then its progress on the card ----------------------------------------------


async def test_the_gift_card_method_shows_its_how_to_privately(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    edits = len(message.edits)
    howto = await press(pay, customer, message, pm.method_custom_id(placed.id, "gift_card"))
    assert modal_of(howto) is None
    sent = _private(howto)
    assert steps(pay, placed, placed.total_minor) in (sent.embeds[0].description or "")
    buttons = _buttons(sent)
    assert [b.custom_id for b in buttons if b.custom_id] == [views.payment_form_id(placed.id)]
    assert [b.url for b in buttons if b.url] == [BUY_URL]
    # Submit gift card opens the private form, as on the order cards posted before this change.
    form = modal_of(await press(pay, customer, None, views.payment_form_id(placed.id)))
    assert form is not None and form.custom_id == views.payment_form_submit_id(placed.id)
    assert len(message.edits) == edits  # the order card is untouched


async def test_after_a_first_card_the_order_card_shows_the_steps_and_the_cards_received(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    await _submit_card(pay, customer, placed, "4.00")
    assert (await order(pay, placed.id)).status == m.PENDING
    description = message.embeds[0].description or ""
    assert steps(pay, placed, placed.total_minor - 400) in description  # the rest, not the whole total again
    assert f"**{pay.text(gift_card.CARDS_FIELD)}**" in description
    assert f"**{pay.text(pm.SECTION_HEADING)}**" not in description  # the customer chose: no picker any more
    rows = _rows(message)
    assert rows[0] == [views.payment_form_id(placed.id), BUY_URL]
    assert rows[1] == [views.order_cancel_id(placed.id)]
    # The reminder then carries the same steps and cards; before a card it only sends the customer to the ticket.
    view = await checkout_service.order_view(pay.app, placed.id)
    instructions = await checkout_service.payment_instructions(pay.app, placed)
    assert view is not None and instructions is not None
    paying = views.reminder_notification(pay.app.ui, pay.app.settings, view, instructions)
    assert steps(pay, placed, placed.total_minor - 400) in "".join(e.description or "" for e in paying.embeds)
    choosing = views.reminder_notification(pay.app.ui, pay.app.settings, view, replace(instructions, fields=()))
    assert steps(pay, placed, placed.total_minor) not in "".join(e.description or "" for e in choosing.embeds)
    assert choosing.rows == ()


# -- a method that is down: the pop-up ------------------------------------------------------------------------------


async def test_a_method_that_is_down_opens_the_popup_with_the_methods_we_accept(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    edits = len(message.edits)
    popup = await press(pay, customer, message, pm.method_custom_id(placed.id, "card"))
    assert modal_of(popup) is None
    sent = _private(popup)
    card, gift = pm.method_name(pay.app.ui, "card"), pm.method_name(pay.app.ui, "gift_card")
    assert sent.embeds[0].title == rendered(pay, pm.DOWN_TITLE, method=card)
    text = sent.embeds[0].description or ""
    description = rendered(pay, pm.DOWN_DESCRIPTION, method=card, available=f"**{gift}**")
    assert text == f"{description}\n{pay.text(pm.DOWN_HINT)}"
    assert pm.method_name(pay.app.ui, "paypal") not in text  # only the methods we accept now
    buttons = _buttons(sent)
    assert [(b.custom_id, b.label) for b in buttons if b.custom_id] == [
        (pm.method_custom_id(placed.id, "gift_card"), pay.text(pm.GIFT_CARD_BUTTON))
    ]
    assert [b.url for b in buttons if b.url] == [BUY_URL]
    assert len(message.edits) == edits  # the order card is untouched
    assert (await order(pay, placed.id)).status == m.PENDING
    # Pay with gift card shows the gift card's how-to.
    howto = await press(pay, customer, None, pm.method_custom_id(placed.id, "gift_card"))
    assert steps(pay, placed, placed.total_minor) in (_private(howto).embeds[0].description or "")


async def test_method_buttons_refuse_others_orders_paid_orders_and_unknown_methods(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    stranger = pay.member(name="stranger")
    refused = await press(pay, stranger, message, pm.method_custom_id(placed.id, "card"))
    assert error_text(refused) == pay.text(core.ERROR_PERMISSION)
    unknown = await press(pay, customer, message, pm.method_custom_id(placed.id, "bank"))
    assert error_text(unknown) == pay.text(pm.ERROR_UNAVAILABLE)
    await pay.app.db.run(lambda c: c.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (placed.id,)))
    late = await press(pay, customer, message, pm.method_custom_id(placed.id, "card"))
    assert error_text(late) == pay.text(pm.ERROR_STATE)
    late_gift = await press(pay, customer, message, pm.method_custom_id(placed.id, "gift_card"))
    assert error_text(late_gift) == pay.text(pm.ERROR_STATE)


# -- an active method our team handles ---------------------------------------------------------------------------


async def test_a_method_our_team_handles_is_posted_once_and_alerts_the_staff(tmp_path: Path) -> None:
    from tests.operations.util import make_ops, run_tasks, staff_channel

    active = methods(("card", "active", "staff"), ("gift_card", "active", "gift_card"))
    tenv = await make_ops(tmp_path, config(active))
    try:
        customer, placed, message = await _placed(tenv)
        rows = _rows(message)
        assert rows[0] == [pm.method_custom_id(placed.id, "card"), pm.method_custom_id(placed.id, "gift_card")]
        assert rows[1] == [views.order_cancel_id(placed.id)]
        ticket = message.channel
        before = len(ticket.messages)
        first = await press(tenv, customer, message, pm.method_custom_id(placed.id, "card"))
        card = pm.method_name(tenv.app.ui, "card")
        assert error_text(first) == rendered(tenv, pm.STAFF_REPLY, method=card)
        posted = ticket.messages[before:]
        assert [p.embeds[0].title for p in posted] == [rendered(tenv, pm.STAFF_TITLE, method=card)]
        again = await press(tenv, customer, message, pm.method_custom_id(placed.id, "card"))
        assert error_text(again) == rendered(tenv, pm.STAFF_ALREADY, method=card)
        assert len(ticket.messages) == before + 1  # posted once
        entries = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=50)
        [audited] = [e for e in entries if e.action == pm.AUDIT_ACTION]
        assert audited.details == {"number": placed.number, "method": "card"}
        await run_tasks(tenv)
        staff = staff_channel(tenv)
        alerts = [msg for msg in staff.messages if msg.embeds and card in (msg.embeds[0].description or "")]
        assert len(alerts) == 1, [msg.embeds[0].description for msg in staff.messages if msg.embeds]
        template = tenv.text(ops_keys.alert_key(ops_keys.PAYMENT_METHOD, "description"))
        description = alerts[0].embeds[0].description or ""
        assert description.startswith(template.split(" ", 1)[0]) and str(placed.number) in description
    finally:
        await tenv.close()


# -- an active payment page --------------------------------------------------------------------------------------


async def test_a_payment_page_is_a_link_button(tmp_path: Path) -> None:
    active = methods(("card", "active", "link"), ("gift_card", "active", "gift_card"), card=PAGE_URL)
    tenv = await make_env(tmp_path, config(active))
    try:
        customer, placed, message = await _placed(tenv)
        rows = _rows(message)
        assert rows[0] == [PAGE_URL, pm.method_custom_id(placed.id, "gift_card")]
        assert pm.method_custom_id(placed.id, "card") not in message.custom_ids()
        # An older button of the method (from before it became a page) offers the page privately.
        older: FakeInteraction = await press(tenv, customer, message, pm.method_custom_id(placed.id, "card"))
        sent = _private(older)
        assert [b.url for b in _buttons(sent) if b.url] == [PAGE_URL]
        name = pm.method_name(tenv.app.ui, "card")
        description = tenv.app.ui.text(pm.description_key("card")) or ""
        assert sent.embeds[0].description == rendered(tenv, pm.LINK_LINE, method=name, description=description).strip()
    finally:
        await tenv.close()


# -- the list of methods and the icons ---------------------------------------------------------------------------

ICONS = {"card": 111111111111111111, "brand_paypal": 222222222222222222, "gift": 333333333333333333}


async def test_the_methods_list_follows_the_settings_with_each_icon(tmp_path: Path) -> None:
    write_icons(tmp_path / "config", *ICONS)
    overrides = deep_merge(config(SHIPPED_LIKE), {"icons": {"enabled": True}})
    overrides["faq"] = {"channel": "faq", "sections": [{"id": "payment", "questions": 1}]}
    tenv = await make_env(tmp_path, overrides)
    try:
        app, ui = tenv.app, tenv.app.ui
        tenv.app.icons.ids.update(ICONS)
        icon_of = {"card": "card", "paypal": "brand_paypal", "gift_card": "gift"}
        entries = [f"<:{icon_of[i]}:{ICONS[icon_of[i]]}> {pm.method_name(ui, i)}" for i in ALL]
        listed = pm.methods_list(ui, app.settings)
        assert listed == "\n".join(entries)  # one per line, its icon first
        # The shop panel, the guides and the FAQ show it where their text says {payment_methods}.
        app.content.set_override(checkout_keys.PANEL_DESCRIPTION, "[pay your way]\n{payment_methods}")
        assert checkout_panel.panel_spec(app).embeds[0].description == f"[pay your way]\n{listed}"
        app.content.set_override(faq.answer_key("payment", 1), "{payment_methods}\n[you choose when you pay]")
        texts = guides_common.runtime_texts(app, tenv.guild)  # type: ignore[arg-type]
        assert texts.required(faq.answer_key("payment", 1)) == f"{listed}\n[you choose when you pay]"
        # Each button shows the method's icon as its emoji, before its name.
        _, placed, message = await _placed(tenv)
        shown = {b.custom_id: (str(b.emoji), b.label) for b in _buttons(message) if b.custom_id}
        assert shown[pm.method_custom_id(placed.id, "paypal")] == (
            f"<:brand_paypal:{ICONS['brand_paypal']}>",
            pm.method_name(ui, "paypal"),
        )
        # A method switched back on changes nothing in the list: only its button does what its kind says.
        assert pm.methods_list(ui, _with(app.settings, "paypal", status="active")) == listed
    finally:
        await tenv.close()


async def test_a_method_icon_that_is_not_a_picture_is_named_in_the_setup_report(tmp_path: Path) -> None:
    write_icons(tmp_path / "config", "card", "gift")  # no brand_paypal.png
    tenv = await make_env(tmp_path, deep_merge(config(SHIPPED_LIKE), {"icons": {"enabled": True}}))
    try:
        # Like any missing icon it only shows nothing (the button keeps its name): checkout stays ready.
        assert tenv.app.ready("checkout")
        assert [x.id for x in pm.missing_icons(tenv.app.settings, tenv.app.content.icon_names())] == ["paypal"]
        missing = tenv.app.op("checkout.publish.method_icon_missing", method="paypal", icon="brand_paypal")
        assert missing in await checkout_panel.publish(tenv.app)
        assert pm.missing_icons(tenv.app.settings, None) == []  # icons switched off: nothing to report
    finally:
        await tenv.close()


def test_methods_get_their_usual_icon_and_a_bad_icon_name_is_refused() -> None:
    entries = [
        {"id": "paypal", "status": "active"},
        {"id": "stripe", "status": "active", "kind": "link", "url": PAGE_URL},
        {"id": "bank", "status": "active"},
        {"id": "zelle", "status": "active", "icon": ""},
        {"id": "venmo", "status": "active", "icon": "brand_paypal"},
    ]
    settings = make_settings({"payments": {"provider": "manual", "methods": entries}})
    assert {x.id: x.icon for x in settings.payments.methods} == {
        "paypal": "brand_paypal",
        "stripe": "brand_stripe",
        "bank": "wallet",
        "zelle": "",
        "venmo": "brand_paypal",
    }
    for bad in ("{icon_brand_paypal}", "Brand PayPal", "brand_paypal.png"):
        with pytest.raises(SettingsError) as raised:
            make_settings({"payments": {"provider": "manual", "methods": [{"id": "paypal", "icon": bad}]}})
        assert any("icon: must be the name of a picture" in error for error in raised.value.errors), bad


# -- settings ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_list", "provider", "message"),
    [
        (methods(("card", "down", "staff")), "manual", 'at least one method must be status = "active"'),
        (methods(("gift_card", "active", "gift_card")), "manual", 'needs payments.provider = "gift_card"'),
        (methods(("card", "active", "link")), "manual", "url: is required"),
        (methods(("card", "active", "staff"), card=PAGE_URL), "manual", 'only used with kind = "link"'),
        (methods(("card", "active", "staff"), ("card", "down", "staff")), "manual", "duplicate id 'card'"),
        (methods(("card", "active", "staff")), "gift_card", "list the gift card as an active method"),
        (methods(("card", "active", "staff"), ("gift_card", "down", "gift_card")), "gift_card", "active method"),
        (methods(("card", "soon", "staff")), "manual", "status"),
        (methods(("card", "active", "bank")), "manual", "kind"),
        ([{"id": f"m{i}", "status": "active", "kind": "staff"} for i in range(11)], "manual", "at most 10"),
    ],
)
def test_settings_refuse_inconsistent_methods(method_list: list[dict[str, str]], provider: str, message: str) -> None:
    with pytest.raises(SettingsError) as raised:
        make_settings({"payments": {"provider": provider, "methods": method_list}})
    assert any(message in error for error in raised.value.errors), raised.value.errors


def test_the_shipped_methods_are_valid_and_each_icon_is_a_shipped_picture() -> None:
    import tomllib

    from shopbot.config.settings import parse_settings

    shipped = parse_settings(tomllib.loads((ROOT / "config" / "settings.toml").read_text(encoding="utf-8")))
    states = {method.id: (method.status, method.kind) for method in shipped.payments.methods}
    assert states["gift_card"] == ("active", "gift_card")
    assert shipped.payments.provider == gift_card.PROVIDER_ID
    assert "stripe" not in states  # no Stripe account yet: its icon is ready for when one is added
    pictures = ROOT / "config" / "emojis"
    for method in shipped.payments.methods:
        assert method.icon and (pictures / f"{method.icon}.png").is_file(), method.id
    for brand in ("brand_stripe", "brand_visa", "brand_mastercard", "brand_doordash"):
        assert (pictures / f"{brand}.png").is_file() and (pictures / f"{brand}_w.png").is_file()


# -- the shipped texts ---------------------------------------------------------------------------------------------

# What a customer sees before pressing a payment method, per feature preview state.
PRE_PAYMENT = {
    "checkout": (
        "shop_panel",
        "ticket_intro",
        "workflow_review",
        "order_submitted",
        "order_cancel_confirm",
        "notify_submitted",
        "notify_payment_reminder",
    ),
    "how_to_buy": ("guide",),
    "faq": ("faq",),
}
# Words that say which methods work right now, or claim that every one does: only the pop-up says which work.
TELLS = re.compile(
    r"only accept|cards? only|only way to pay|temporarily|unavailable|\bdown\b|available now|any payment method|"
    r"all (payment )?methods",
    re.IGNORECASE,
)


def _text_of(spec: Any) -> str:
    parts: list[str] = []
    for embed in spec.embeds:
        parts += [embed.title or "", embed.description or ""]
        parts += [f"{item.name} {item.value}" for item in embed.fields]
    parts += [getattr(c, "label", None) or "" for row in spec.rows for c in row]
    return "\n".join(parts)


async def test_no_shipped_text_before_the_popup_says_which_methods_work() -> None:
    from tools.offline import build_offline_app

    app = await build_offline_app(ROOT / "config", content_mode="actual", all_features=True)
    try:
        states = {
            (module.name, s.name): s.spec for module in app.modules if module.preview for s in module.preview(app)
        }
        for feature, names in PRE_PAYMENT.items():
            for name in names:
                text = _text_of(states[(feature, name)])
                assert not TELLS.search(text), (feature, name, TELLS.search(text))
        # The ways to pay are presented with every configured method; the pop-up is where we say which we accept.
        method_names = [pm.method_name(app.ui, method.id) for method in app.settings.payments.methods]
        for state in (("checkout", "shop_panel"), ("how_to_buy", "guide"), ("faq", "faq")):
            assert all(name in _text_of(states[state]) for name in method_names), state
        assert "only accept" in _text_of(states[("checkout", "payment_method_down")])
    finally:
        await app.db.close()
