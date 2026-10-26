"""Payment methods in the order ticket (payments.methods): the methods and their state on the order card, the private
pop-up of a method that is down (with buttons for the methods that work), a method our team handles (posted once in
the ticket, the staff alerted), a payment page, and the settings checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import SettingsError
from shopbot.db.repos import audit as audit_repo
from shopbot.features import core_keys as core
from shopbot.features.checkout import models as m
from shopbot.features.checkout import payment_methods as pm
from shopbot.features.checkout import views
from shopbot.features.checkout.payments import gift_card
from shopbot.features.operations import content_keys as ops_keys
from tests.checkout.util import add_product, error_text, make_env, place_order, press
from tests.fakes import FakeInteraction, FakeMessage, modal_of
from tests.helpers import GUILD_ID, TestEnv, make_settings
from tests.operations.util import make_ops, run_tasks, staff_channel

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


def _buttons(message: Any) -> list[Any]:
    return [getattr(item, "item", item) for item in message.components()]


def _rows(message: Any) -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for button in _buttons(message):
        rows.setdefault(button.row if button.row is not None else -1, []).append(button.custom_id or button.url)
    return rows


# -- the order card -------------------------------------------------------------------------------------------------


async def test_the_order_card_lists_the_methods_and_shows_a_button_for_each(pay: TestEnv) -> None:
    _, placed, message = await _placed(pay)
    description = message.embeds[0].description or ""
    assert pay.text(pm.SECTION_HEADING) in description
    card, paypal, gift = (pm.method_name(pay.app.ui, i) for i in ("card", "paypal", "gift_card"))
    # The method that works first, then one line for every method that is down, then the note.
    section = next(p for p in description.split("\n\n") if p.startswith(f"**{pay.text(pm.SECTION_HEADING)}**"))
    assert section.split("\n")[1:] == [
        rendered(pay, pm.LINE_ACTIVE, method=gift),
        rendered(pay, pm.LINE_DOWN, method=f"{card}, {paypal}"),
        pay.text(pm.NOTE),
    ]
    rows = _rows(message)
    # What works first (the gift card's own buttons), then the methods that are down, then Cancel order alone.
    assert rows[0] == [views.payment_form_id(placed.id), BUY_URL]
    assert rows[1] == [pm.method_custom_id(placed.id, "card"), pm.method_custom_id(placed.id, "paypal")]
    assert rows[2] == [views.order_cancel_id(placed.id)]
    styles = {b.custom_id: b.style.name for b in _buttons(message) if b.custom_id}
    assert styles[pm.method_custom_id(placed.id, "card")] == "secondary"


async def test_without_methods_the_order_card_is_unchanged(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, config([]))
    try:
        _, placed, message = await _placed(tenv)
        assert pm.SECTION_HEADING not in tenv.app.content.registry
        assert not any(cid.startswith("sb:co:paym:") for cid in message.custom_ids())
        assert views.payment_form_id(placed.id) in message.custom_ids()
    finally:
        await tenv.close()


# -- a method that is down: the pop-up -------------------------------------------------------------------------------


async def test_a_method_that_is_down_opens_the_popup_with_the_working_methods(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    edits = len(message.edits)
    popup = await press(pay, customer, message, pm.method_custom_id(placed.id, "card"))
    assert modal_of(popup) is None
    sent = popup.last_sent
    assert sent is not None and getattr(sent, "ephemeral", False)
    card = pm.method_name(pay.app.ui, "card")
    assert sent.embeds[0].title == rendered(pay, pm.DOWN_TITLE, method=card)
    text = sent.embeds[0].description or ""
    assert rendered(pay, pm.DOWN_DESCRIPTION, method=card) in text
    assert pay.text(pm.DOWN_ACTIVE_HEADING) in text
    assert pm.method_name(pay.app.ui, "gift_card") in text
    assert pm.method_name(pay.app.ui, "paypal") not in text  # only the methods that work
    buttons = _buttons(sent)
    assert [b.custom_id for b in buttons if b.custom_id] == [views.payment_form_id(placed.id)]
    assert [b.url for b in buttons if b.url] == [BUY_URL]
    assert len(message.edits) == edits  # the order card is untouched
    assert (await pay.app.db.run(lambda c: c.execute("SELECT status FROM orders").fetchone()[0])) == m.PENDING


async def test_the_popups_gift_card_button_opens_the_gift_card_form(pay: TestEnv) -> None:
    customer, placed, message = await _placed(pay)
    popup = await press(pay, customer, message, pm.method_custom_id(placed.id, "paypal"))
    assert popup.last_sent is not None
    form = modal_of(await press(pay, customer, None, views.payment_form_id(placed.id)))
    assert form is not None and form.custom_id == views.payment_form_submit_id(placed.id)


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


# -- an active method our team handles ---------------------------------------------------------------------------


async def test_a_method_our_team_handles_is_posted_once_and_alerts_the_staff(tmp_path: Path) -> None:
    active = methods(("card", "active", "staff"), ("gift_card", "active", "gift_card"))
    tenv = await make_ops(tmp_path, config(active))
    try:
        customer, placed, message = await _placed(tenv)
        button = next(b for b in _buttons(message) if b.custom_id == pm.method_custom_id(placed.id, "card"))
        assert button.style.name == "primary"
        # The gift card's buttons, then the other method that works on its own row (one main action per row).
        rows = _rows(message)
        assert rows[0] == [views.payment_form_id(placed.id), BUY_URL]
        assert rows[1] == [pm.method_custom_id(placed.id, "card")]
        assert rows[2] == [views.order_cancel_id(placed.id)]
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
        assert PAGE_URL in [b.url for b in _buttons(message) if b.url]
        # Every method works: nothing on the card says one is down (no down line, no note).
        description = message.embeds[0].description or ""
        assert tenv.text(pm.NOTE) not in description
        assert rendered(tenv, pm.LINE_ACTIVE, method=pm.method_name(tenv.app.ui, "card")) in description
        assert pm.method_custom_id(placed.id, "card") not in message.custom_ids()
        # An older button of the method (from before it became a page) offers the page privately.
        older: FakeInteraction = await press(tenv, customer, message, pm.method_custom_id(placed.id, "card"))
        assert older.last_sent is not None
        assert [b.url for b in _buttons(older.last_sent) if b.url] == [PAGE_URL]
    finally:
        await tenv.close()


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


def test_the_shipped_methods_are_valid() -> None:
    import tomllib

    from shopbot.config.settings import parse_settings
    from tests.helpers import ROOT

    shipped = parse_settings(tomllib.loads((ROOT / "config" / "settings.toml").read_text(encoding="utf-8")))
    states = {method.id: (method.status, method.kind) for method in shipped.payments.methods}
    assert states["gift_card"] == ("active", "gift_card")
    assert [i for i, (status, _) in states.items() if status == "active"] == ["gift_card"]
    assert shipped.payments.provider == gift_card.PROVIDER_ID
