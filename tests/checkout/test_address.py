"""The shipping address step (``checkout.ask_address``): region and country menus built from ``[shipping]``, a private
address form, excluded areas refused (answers kept), and the address stored with the order for staff."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, repo, views
from shopbot.features.checkout import models as m
from tests.checkout.util import (
    add_product,
    error_text,
    go,
    make_env,
    press,
    reload_session,
    set_qty,
    start,
    submit,
)
from tests.fakes import submit_modal
from tests.helpers import GUILD_ID, TestEnv

EUROPE = ["AL", "AD", "AT", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE",
          "IT", "XK", "LV", "LI", "LT", "LU", "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "SM", "RS",
          "SK", "SI", "ES", "SE", "CH", "UA", "GB", "VA"]  # fmt: skip
ADDRESS: dict[str, Any] = {
    "checkout": {"ask_address": True, "fulfillment_options": [{"id": "standard", "fee_minor": 500}]},
    "shipping": {
        "groups": [{"id": "north_america", "countries": ["CA", "US", "MX"]}, {"id": "europe", "countries": EUROPE}],
        "excluded_areas": {"US": ["AK", "ND"]},
    },
}
TEXAS = {"a.name": "Ada Lovelace", "a.street": "1 Main St", "a.city": "Austin", "a.region": "TX", "a.postal": "73301"}


def _selects(message: Any) -> dict[str, Any]:
    items = [getattr(item, "item", item) for item in message.components()]
    return {s.custom_id: s for s in items if hasattr(s, "options")}


def _button(message: Any, custom_id: str) -> Any:
    items = [getattr(item, "item", item) for item in message.components()]
    return next(b for b in items if getattr(b, "custom_id", None) == custom_id)


async def _at_address(tenv: TestEnv) -> tuple[Any, m.Session, Any]:
    p1 = await add_product(tenv, "[p1]", price=1000)
    user = tenv.member()
    _, session, _, message = await start(tenv, user)
    await set_qty(tenv, user, message, session.id, p1.id, 1)
    await go(tenv, user, message, session.id, m.CART)
    await go(tenv, user, message, session.id, m.DETAILS)
    return user, session, message


async def _address(tenv: TestEnv, user: Any, message: Any, session_id: int, fields: dict[str, str]) -> Any:
    return await submit_modal(
        tenv.app, user=user, custom_id=views.address_form_id(session_id), fields=fields, message=message
    )


async def test_the_address_comes_before_delivery_and_the_country_menus_follow_the_region(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        assert flow.steps_for(tenv.app.settings.checkout, None) == (
            m.BROWSE,
            m.CART,
            m.DETAILS,
            m.FULFILLMENT,
            m.REVIEW,
        )
        user, session, message = await _at_address(tenv)
        assert list(_selects(message)) == [views.address_group_id(session.id)]  # no country menu before a region
        assert _button(message, views.address_open_id(session.id)).disabled  # no form before a country
        await press(tenv, user, message, views.address_group_id(session.id), values=["europe"])
        menus = _selects(message)
        assert [len(menus[views.address_country_id(session.id, i)].options) for i in (0, 1)] == [25, 19]
        assert menus[views.address_country_id(session.id, 0)].options[0].label == "Albania"
        await press(tenv, user, message, views.address_group_id(session.id), values=["north_america"])
        await press(tenv, user, message, views.address_country_id(session.id, 0), values=["US"])
        assert not _button(message, views.address_open_id(session.id)).disabled
        opened = await press(tenv, user, message, views.address_open_id(session.id))
        modal = opened.response.modal
        assert modal is not None and len(modal.children) == 5
        # A US address must name its state (Alaska and North Dakota are checked).
        region = next(c for c in modal.walk_children() if getattr(c, "custom_id", "") == "a.region")
        assert region.required
    finally:
        await tenv.close()


async def test_an_address_in_an_excluded_state_is_refused_and_its_answers_are_kept(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        user, session, message = await _at_address(tenv)
        await press(tenv, user, message, views.address_country_id(session.id, 0), values=["US"])
        refused = await _address(tenv, user, message, session.id, {**TEXAS, "a.region": "North Dakota"})
        expected = tenv.app.content.render(k.ERROR_AREA_EXCLUDED, area="North Dakota", country="United States")
        assert error_text(refused) == expected
        stored = (await reload_session(tenv, session.id)).details
        assert stored["a.region"] == "North Dakota" and stored["a.name"] == "Ada Lovelace"  # kept for correcting
        refused_go = await go(tenv, user, message, session.id, m.FULFILLMENT)
        assert error_text(refused_go) is not None  # cannot continue with that address
        await _address(tenv, user, message, session.id, TEXAS)
        await go(tenv, user, message, session.id, m.FULFILLMENT)
        assert (await reload_session(tenv, session.id)).step == m.FULFILLMENT
    finally:
        await tenv.close()


async def test_the_order_stores_the_address_and_staff_see_the_country_by_name(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        user, session, message = await _at_address(tenv)
        await press(tenv, user, message, views.address_country_id(session.id, 0), values=["US"])
        await _address(tenv, user, message, session.id, {**TEXAS, "a.street": "  1   Main\nSt  "})
        await go(tenv, user, message, session.id, m.FULFILLMENT)
        await press(tenv, user, message, views.fulfillment_id(session.id), values=["standard"])
        await go(tenv, user, message, session.id, m.REVIEW)
        review = message.embeds[0]
        destination = tenv.app.content.render(
            k.ADDRESS_SHIP_TO, name="Ada Lovelace", city="Austin", region="TX", postal="73301", country="United States"
        )
        assert any(f.value == destination for f in review.fields)
        assert "1 Main St" not in message.text()  # the street is never shown in the channel
        await submit(tenv, user, message, session.id)
        order_id = (await reload_session(tenv, session.id)).order_id
        assert order_id is not None
        placed = await tenv.app.db.run(repo.get_order, order_id)
        assert placed is not None
        assert placed.details == {"a.country": "US", **TEXAS, "a.street": "1 Main St"}  # one clean line
        answers = dict(views.ordered_answers(tenv.app.settings, placed))
        assert next(iter(answers)) == "a.country" and answers["a.country"] == "United States"
    finally:
        await tenv.close()


async def test_a_country_we_do_not_ship_to_cannot_be_chosen(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        user, session, message = await _at_address(tenv)
        forged = await press(tenv, user, message, views.address_country_id(session.id, 0), values=["CU"])
        assert error_text(forged) == tenv.text("common.error.invalid_input")
        early = await press(tenv, user, message, views.address_open_id(session.id))
        assert error_text(early) == tenv.text(k.ERROR_COUNTRY_FIRST)
        data = await tenv.app.db.run(repo.load_workflow, GUILD_ID, session.id, tenv.app.settings, False)
        assert data is not None and flow.ADDRESS_COUNTRY not in data.session.details
    finally:
        await tenv.close()


async def test_without_shipping_countries_the_address_step_is_a_problem(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"ask_address": True}})
    try:
        assert not tenv.app.ready("checkout")
        problems = [p.message for p in tenv.app.readiness.statuses["checkout"].problems]
        assert any("checkout.ask_address: list the countries" in p for p in problems)
    finally:
        await tenv.close()
