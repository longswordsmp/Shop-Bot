"""Delivery extras (``[[checkout.extras]]``): optional flat-price add-ons the customer toggles on the delivery step;
an added extra is a fee of the order (``x.<id>``) with the extra's label everywhere."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import SettingsError, parse_settings
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, repo, views
from shopbot.features.checkout import models as m
from tests.checkout.util import add_product, error_text, go, make_env, money, press, reload_session, start, submit
from tests.helpers import GUILD_ID, TestEnv, base_settings_dict, deep_merge

EXTRAS: dict[str, Any] = {
    "checkout": {
        "fulfillment_options": [{"id": "standard", "fee_minor": 500}],
        "extras": [{"id": "protection", "amount_minor": 300}, {"id": "discreet", "amount_minor": 200}],
    }
}


async def _at_delivery(tenv: TestEnv) -> tuple[Any, m.Session, Any]:
    p1 = await add_product(tenv, "[p1]", price=1000)
    user = tenv.member()
    _, session, _, message = await start(tenv, user)
    from tests.checkout.util import set_qty

    await set_qty(tenv, user, message, session.id, p1.id, 2)
    await go(tenv, user, message, session.id, m.CART)
    await go(tenv, user, message, session.id, m.FULFILLMENT)
    await press(tenv, user, message, views.fulfillment_id(session.id), values=["standard"])
    return user, session, message


def _buttons(message: Any) -> dict[str, Any]:
    items = [getattr(item, "item", item) for item in message.components()]
    return {getattr(b, "custom_id", None): b for b in items}


async def test_extras_are_toggled_on_the_delivery_step_and_charged_as_fees(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, EXTRAS)
    try:
        user, session, message = await _at_delivery(tenv)
        protection = views.extra_id(session.id, "protection")
        button = _buttons(message)[protection]
        assert button.label == tenv.app.content.render(
            k.EXTRA_BUTTON_OFF, label=tenv.text(k.extra_key("protection", "label")), price=money(tenv, 300)
        )
        await press(tenv, user, message, protection)
        session = await reload_session(tenv, session.id)
        assert flow.chosen_extras(tenv.app.settings.checkout, session.details)[0].id == "protection"
        assert _buttons(message)[protection].style.name == "success"  # added: green
        # The totals include it: 2 x 10.00 + 5.00 delivery + 3.00 protection.
        data = await tenv.app.db.run(repo.load_workflow, GUILD_ID, session.id, tenv.app.settings, False)
        assert data is not None and data.totals.total_minor == 2000 + 500 + 300
        # Pressing again removes it.
        await press(tenv, user, message, protection)
        assert flow.chosen_extras(tenv.app.settings.checkout, (await reload_session(tenv, session.id)).details) == ()
        await press(tenv, user, message, views.extra_id(session.id, "discreet"))
        await go(tenv, user, message, session.id, m.REVIEW)
        await submit(tenv, user, message, session.id)
        session = await reload_session(tenv, session.id)
        assert session.order_id is not None
        placed = await tenv.app.db.run(repo.get_order, session.order_id)
        assert placed is not None and placed.total_minor == 2000 + 500 + 200 and placed.fees_minor == 200
        view = await tenv.app.db.run(repo.order_view, placed.id)
        assert view is not None and [(f.fee_id, f.amount_minor) for f in view.fees] == [("x.discreet", 200)]
        assert k.fee_label_key("x.discreet") == k.extra_key("discreet", "label")  # shown with the extra's name
    finally:
        await tenv.close()


async def test_an_unknown_extra_is_refused(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, EXTRAS)
    try:
        user, session, message = await _at_delivery(tenv)
        refused = await press(tenv, user, message, views.extra_id(session.id, "gold_plating"))
        assert error_text(refused) == tenv.text("common.error.invalid_input")
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    ("checkout", "expected"),
    [
        ({"extras": [{"id": "a", "amount_minor": 100}]}, "checkout.extras: extras are chosen on the delivery step"),
        (
            {
                "fulfillment_options": [{"id": "s"}],
                "extras": [{"id": f"e{n}", "amount_minor": 100} for n in range(5)],
            },
            "checkout.extras: at most 4 extras",
        ),
        ({"fulfillment_options": [{"id": "s"}], "extras": [{"id": "a"}]}, "checkout.extras[0].amount_minor"),
        (
            {"fulfillment_options": [{"id": "s"}], "extras": [{"id": "a", "amount_minor": 1}] * 2},
            "checkout.extras: duplicate id 'a'",
        ),
    ],
)
def test_extras_settings_are_validated(checkout: dict[str, Any], expected: str) -> None:
    data = deep_merge(base_settings_dict(), {"checkout": checkout})
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    assert any(error.startswith(expected) for error in exc.value.errors), exc.value.errors
