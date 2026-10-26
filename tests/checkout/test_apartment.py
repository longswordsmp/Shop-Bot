"""Optional apartment details on the address step: a second private form (apartment or unit, building or floor, entry
code, delivery instructions), every field optional, stored with the address and shown to staff only, in shipping-label
order (like the address: in #orders only with ``operations.answers_in_orders_channel``). The order ticket only says
that apartment details were added. The step's address buttons and its navigation get rows of their own when the rows
allow it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.core.countries import COUNTRIES
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_repo, flow, repo, service, staff_views, views
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.ui.limits import ACTION_ROWS, validate_message
from shopbot.ui.spec import MessageSpec, SelectSpec
from tests.checkout.test_address import ADDRESS, TEXAS
from tests.checkout.util import (
    add_product,
    channel,
    error_text,
    give_credits,
    go,
    make_env,
    order,
    post_answers_in_channel,
    press,
    reload_session,
    run_command,
    section_lines,
    set_qty,
    staff_message,
    start,
    submit,
)
from tests.fakes import FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, deep_merge, make_settings
from tests.orders_privacy.util import public_texts, run_tasks

APARTMENT = {
    "a.unit": "[sample unit 4B]",
    "a.building": "[sample building C]",
    "a.access": "[sample code 4711]",
    "a.notes": "[sample leave it at the desk]",
}
# The address as staff read it: the label's lines, the country, then what the courier needs at the door.
LABEL_ORDER = ["name", "street", "unit", "building", "city", "region", "postal", "country", "access", "notes"]


def _component(message: FakeMessage, custom_id: str) -> Any:
    items = [getattr(item, "item", item) for item in message.components()]
    return next(c for c in items if getattr(c, "custom_id", None) == custom_id)


def _inputs(interaction: Any) -> list[Any]:
    modal = interaction.response.modal
    assert modal is not None
    return [c for c in modal.walk_children() if str(getattr(c, "custom_id", "")).startswith(flow.ADDRESS_PREFIX)]


async def _at_address(tenv: TestEnv, customer: FakeMember) -> tuple[m.Session, FakeTextChannel, FakeMessage]:
    shirt = await add_product(tenv, "[sample product 1]", price=1000)
    _, session, ticket, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, shirt.id, 1)
    await go(tenv, customer, message, session.id, m.CART)
    await go(tenv, customer, message, session.id, m.DETAILS)
    return session, ticket, message


async def _apartment(tenv: TestEnv, customer: FakeMember, message: FakeMessage, session_id: int, fields: Any) -> Any:
    return await submit_modal(
        tenv.app, user=customer, custom_id=views.apartment_form_id(session_id), fields=fields, message=message
    )


async def _placed(tenv: TestEnv, customer: FakeMember) -> tuple[m.Order, FakeTextChannel, FakeMessage]:
    """An order to Texas with every apartment detail, after checking the review's SHIP TO section."""
    session, ticket, message = await _at_address(tenv, customer)
    await press(tenv, customer, message, views.address_country_id(session.id, 0), values=["US"])
    await submit_modal(
        tenv.app, user=customer, custom_id=views.address_form_id(session.id), fields=TEXAS, message=message
    )
    await _apartment(tenv, customer, message, session.id, APARTMENT)
    await go(tenv, customer, message, session.id, m.FULFILLMENT)
    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["standard"])
    await go(tenv, customer, message, session.id, m.REVIEW)
    destination = tenv.app.content.render(
        k.ADDRESS_SHIP_TO, name="Ada Lovelace", city="Austin", region="TX", postal="73301", country="United States"
    )
    assert section_lines(tenv, message, k.ADDRESS_HEADING) == [destination, tenv.text(k.APARTMENT_ADDED_LINE)]
    assert all(value not in message.text() for value in APARTMENT.values())
    await submit(tenv, customer, message, session.id)
    order_id = (await reload_session(tenv, session.id)).order_id
    assert order_id is not None
    return await order(tenv, order_id), ticket, message


def _label(tenv: TestEnv, field_id: str) -> str:
    return tenv.text(k.ADDRESS_COUNTRY_LABEL if field_id == "country" else k.address_field_key(field_id, "label"))


def _address_fields(tenv: TestEnv, sent: list[Any]) -> list[tuple[str, str]]:
    """The address fields of private staff replies, in the order shown."""
    labels = {_label(tenv, field_id) for field_id in LABEL_ORDER}
    return [(f.name, f.value) for message in sent for embed in message.embeds for f in embed.fields if f.name in labels]


async def test_the_form_is_optional_filled_in_again_and_emptying_it_removes_the_details(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        customer = tenv.member()
        session, _, message = await _at_address(tenv, customer)
        button = _component(message, views.apartment_open_id(session.id))
        assert button.disabled and button.label == tenv.text(k.APARTMENT_BUTTON)  # shown from the start
        await press(tenv, customer, message, views.address_country_id(session.id, 0), values=["US"])
        assert not _component(message, views.apartment_open_id(session.id)).disabled

        opened = await press(tenv, customer, message, views.apartment_open_id(session.id))
        inputs = _inputs(opened)
        assert [i.custom_id for i in inputs] == ["a.unit", "a.building", "a.access", "a.notes"]
        assert not any(i.required for i in inputs) and not any(i.default for i in inputs)
        assert [i.max_length for i in inputs] == [50, 50, 50, 200]

        # Only some fields: each kept on one line; the others stay empty. Nothing is required, not even the address.
        await _apartment(tenv, customer, message, session.id, {"a.unit": "  [sample\n unit 4B] ", "a.notes": ""})
        details = (await reload_session(tenv, session.id)).details
        assert {key: details[key] for key in details if key in APARTMENT} == {"a.unit": "[sample unit 4B]"}
        assert _component(message, views.apartment_open_id(session.id)).label == tenv.text(k.APARTMENT_BUTTON_CHANGE)
        lines = section_lines(tenv, message, k.ADDRESS_HEADING)
        assert lines is not None and lines[-1] == tenv.text(k.APARTMENT_ADDED_LINE)
        assert "[sample unit 4B]" not in message.text()

        await _apartment(tenv, customer, message, session.id, APARTMENT)
        reopened = await press(tenv, customer, message, views.apartment_open_id(session.id))
        assert {i.custom_id: i.default for i in _inputs(reopened)} == APARTMENT  # filled in to edit

        await _apartment(tenv, customer, message, session.id, dict.fromkeys(APARTMENT, " "))
        details = (await reload_session(tenv, session.id)).details
        assert not any(key in details for key in APARTMENT) and not flow.has_apartment(details)
        assert _component(message, views.apartment_open_id(session.id)).label == tenv.text(k.APARTMENT_BUTTON)
        assert tenv.text(k.APARTMENT_ADDED_LINE) not in message.text()
    finally:
        await tenv.close()


async def test_the_details_are_checked_on_the_server(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        customer = tenv.member()
        session, _, message = await _at_address(tenv, customer)
        early = await press(tenv, customer, message, views.apartment_open_id(session.id))
        assert error_text(early) == tenv.text(k.ERROR_COUNTRY_FIRST)
        forged = await _apartment(tenv, customer, message, session.id, APARTMENT)
        assert error_text(forged) == tenv.text(k.ERROR_COUNTRY_FIRST)

        await press(tenv, customer, message, views.address_country_id(session.id, 0), values=["US"])
        for key, limit in (("a.unit", 50), ("a.building", 50), ("a.access", 50), ("a.notes", 200)):
            too_long = await _apartment(tenv, customer, message, session.id, {**APARTMENT, key: "x" * (limit + 1)})
            assert error_text(too_long) == tenv.text(core.ERROR_INVALID_INPUT)
        assert not flow.has_apartment((await reload_session(tenv, session.id)).details)
        exact = await _apartment(tenv, customer, message, session.id, {"a.notes": "x" * 200})
        assert error_text(exact) is None
        # Apartment details alone are not an address: Continue stays refused until the address is entered.
        refused = await go(tenv, customer, message, session.id, m.FULFILLMENT)
        assert error_text(refused) is not None
    finally:
        await tenv.close()


async def test_the_order_keeps_them_and_only_staff_see_them_in_shipping_label_order(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, ADDRESS)
    try:
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        placed, ticket, _ = await _placed(tenv, customer)
        assert placed.details == {"a.country": "US", **TEXAS, **APARTMENT}
        expected_keys = [flow.ADDRESS_COUNTRY if i == "country" else f"a.{i}" for i in LABEL_ORDER]
        assert [key for key, _ in views.ordered_answers(tenv.app.settings, placed)] == expected_keys
        assert list(flow.address_answers({**placed.details, flow.ADDRESS_GROUP: "north_america"})) == expected_keys

        # Never in a channel (with operations.answers_in_orders_channel off, as shipped): not in the ticket, #orders or
        # anywhere else.
        await run_tasks(tenv)
        visible = public_texts(tenv)
        assert channel(tenv, "order_queue").messages and ticket.messages
        assert all(value not in visible for value in APARTMENT.values())

        # Staff see them privately, wherever they see the address, each on its own labelled line.
        typed = {**TEXAS, **APARTMENT, flow.ADDRESS_COUNTRY: "United States"}
        shown = [(_label(tenv, i), typed[key]) for i, key in zip(LABEL_ORDER, expected_keys, strict=True)]
        record = staff_message(tenv, await order(tenv, placed.id))
        details = await click(
            tenv.app, user=staffer, custom_id=staff_views.record_details_id(placed.id), message=record
        )
        assert details.sent and all(sent.ephemeral for sent in details.sent)
        assert _address_fields(tenv, details.sent) == shown
        viewed = await run_command(tenv, "view", user=staffer, number=placed.number)
        assert all(sent.ephemeral for sent in viewed.sent)
        assert _address_fields(tenv, viewed.sent) == shown

        # With operations.answers_in_orders_channel they are part of the address like everywhere else: the record
        # itself names only the destination, and the form answers posted under it in #orders show the whole address,
        # apartment details included, in the same order. The ticket still never shows them.
        post_answers_in_channel(tenv)
        await service.refresh_staff_message(tenv.app, placed.id)
        record = staff_message(tenv, await order(tenv, placed.id))
        assert "Austin" in record.text()
        assert all(value not in record.text() for value in APARTMENT.values())
        await service.post_staff_details(tenv.app, placed.id)
        posted = [message for message in channel(tenv, "order_queue").messages if message is not record]
        assert posted and _address_fields(tenv, posted) == shown
        assert all(
            value not in public_texts(tenv, except_channel=channel(tenv, "order_queue")) for value in APARTMENT.values()
        )
    finally:
        await tenv.close()


def test_every_address_field_has_its_place_in_the_shipping_label_order() -> None:
    """A field added to the address or apartment form but not to the label order would be left out of every order and
    every staff view without notice."""
    typed = (*flow.ADDRESS_FIELDS, *flow.APARTMENT_FIELDS)
    assert len(set(flow.ADDRESS_LABEL_ORDER)) == len(flow.ADDRESS_LABEL_ORDER)
    assert set(flow.ADDRESS_LABEL_ORDER) == {flow.ADDRESS_COUNTRY, *(f.answer_key for f in typed)}


async def test_the_address_buttons_and_the_navigation_get_rows_of_their_own_when_they_fit(tmp_path: Path) -> None:
    others = sorted(code for code in COUNTRIES if code not in ("CA", "US", "MX"))
    groups = [{"id": "north_america", "countries": ["CA", "US", "MX"]}, {"id": "world", "countries": others[:75]}]
    tenv = await make_env(tmp_path, deep_merge(ADDRESS, {"shipping": {"groups": groups}}))
    try:
        customer = tenv.member()
        session, _, message = await _at_address(tenv, customer)
        address = [views.address_open_id(session.id), views.apartment_open_id(session.id)]
        navigation = [
            views.go_id(session.id, m.CART),
            views.go_id(session.id, m.FULFILLMENT),
            views.cancel_id(session.id),
        ]
        await press(tenv, customer, message, views.address_group_id(session.id), values=["north_america"])
        spec = await _workflow_spec(tenv, session.id, m.DETAILS)
        assert _button_rows(spec) == [address, navigation] and len(spec.rows) == 4
        # Three country menus leave one row for the buttons: packed together, Cancel still last.
        await press(tenv, customer, message, views.address_group_id(session.id), values=["world"])
        spec = await _workflow_spec(tenv, session.id, m.DETAILS)
        assert _button_rows(spec) == [address + navigation] and len(spec.rows) == ACTION_ROWS
    finally:
        await tenv.close()


def _button_rows(spec: MessageSpec) -> list[list[str]]:
    """The custom ids of the message's button rows (menus left out)."""
    return [[c.custom_id or "" for c in row] for row in spec.rows if not isinstance(row[0], SelectSpec)]


async def test_the_address_step_and_the_review_stay_within_discords_limits(tmp_path: Path) -> None:
    tips = {"checkout": {"tips": {"enabled": True}}}
    tenv = await make_env(tmp_path, deep_merge(ADDRESS, tips))
    try:
        customer = tenv.member()
        await give_credits(tenv, customer.id, 500)
        await tenv.app.db.transaction(
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
            now=tenv.clock.now(),
        )
        session, _, message = await _at_address(tenv, customer)
        await press(tenv, customer, message, views.address_group_id(session.id), values=["europe"])  # two menus
        await press(tenv, customer, message, views.address_country_id(session.id, 0), values=["AT"])
        austria = {**TEXAS, "a.region": "", "a.city": "Vienna", "a.postal": "1010"}
        await submit_modal(
            tenv.app, user=customer, custom_id=views.address_form_id(session.id), fields=austria, message=message
        )
        longest = {"a.unit": "x" * 50, "a.building": "x" * 50, "a.access": "x" * 50, "a.notes": "x" * 200}
        await _apartment(tenv, customer, message, session.id, longest)
        await _check_limits(tenv, session.id, m.DETAILS)
        await go(tenv, customer, message, session.id, m.FULFILLMENT)
        await press(tenv, customer, message, views.fulfillment_id(session.id), values=["standard"])
        await go(tenv, customer, message, session.id, m.REVIEW)
        await _check_limits(tenv, session.id, m.REVIEW)
        # The review keeps its store credit, discount and tip controls next to the navigation.
        assert {views.discount_open_id(session.id), views.tip_id(session.id)} <= set(message.custom_ids())
    finally:
        await tenv.close()


async def _workflow_spec(tenv: TestEnv, session_id: int, step: str) -> MessageSpec:
    """The workflow message the session shows at ``step``."""
    data = await tenv.app.db.run(repo.load_workflow, GUILD_ID, session_id, tenv.app.settings, True, tenv.clock.now())
    assert data is not None and data.session.step == step
    return views.workflow_message(tenv.app.ui, tenv.app.settings, data)


async def _check_limits(tenv: TestEnv, session_id: int, step: str) -> None:
    """The workflow message at ``step``: one card, at most 5 rows, within every Discord limit."""
    spec = await _workflow_spec(tenv, session_id, step)
    assert len(spec.embeds) == 1 and len(spec.rows) <= ACTION_ROWS
    assert validate_message(spec) == []


def test_a_region_too_large_for_the_address_step_is_a_settings_problem() -> None:
    codes = sorted(COUNTRIES)

    def problems(count: int, *, details_form: bool = False) -> list[str]:
        overrides: dict[str, Any] = {
            "checkout": {"ask_address": True},
            "shipping": {"groups": [{"id": "world", "countries": codes[:count]}]},
        }
        if details_form:
            overrides["checkout"]["details_form"] = [{"id": "name", "style": "short"}]
        return [p for p in k.settings_problems(make_settings(overrides)) if "address step's country menus" in p]

    # The region menu, three country menus and the buttons fill the 5 rows; a details form needs a second button row.
    assert problems(75) == [] and problems(76)
    assert problems(50, details_form=True) == [] and problems(51, details_form=True)
