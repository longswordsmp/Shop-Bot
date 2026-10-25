"""The optional submission form (``checkout.submission_form``): asked in a form when the customer presses Submit,
validated server-side, stored with the order and shown to staff; no form = submit directly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import discord
import pytest

from shopbot.config.settings import SettingsError
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, repo, service, views
from shopbot.features.checkout import models as m
from tests.checkout.util import (
    add_product,
    channel,
    error_text,
    orders,
    post_answers_in_channel,
    press,
    reload_session,
    restarted_app,
    set_price,
    start,
    to_review,
)
from tests.customer_panel.util import SUBMISSION_FORM, env, field_map
from tests.fakes import FakeMember, FakeMessage, modal_input_ids, modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv, make_settings


async def _review(tenv: TestEnv, price: int = 1000) -> tuple[FakeMember, m.Session, FakeMessage]:
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=price)
    _, session, _, message = await start(tenv, customer)
    data = await to_review(tenv, customer, message, session.id, [(p1.id, 1)])
    return customer, data.session, message


async def _totals(tenv: TestEnv, session_id: int) -> tuple[int, int]:
    data = await tenv.app.db.run(repo.load_workflow, GUILD_ID, session_id, tenv.app.settings, True)
    assert data is not None
    return data.totals.total_minor, data.totals.gross_minor


@pytest.fixture
async def sf(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await env(tmp_path, SUBMISSION_FORM)
    try:
        yield tenv
    finally:
        await tenv.close()


async def test_submit_opens_the_form_and_the_answers_are_stored_with_the_order(sf: TestEnv) -> None:
    tenv = sf
    post_answers_in_channel(tenv)  # operations.answers_in_orders_channel (off by default: see tests/orders_privacy)
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    opened = await press(tenv, customer, message, views.submit_id(session.id, total, gross))
    modal = modal_of(opened)
    assert modal is not None and modal.title == tenv.text(k.SUBMISSION_MODAL_TITLE)
    assert modal_input_ids(modal) == ["s.reference", "s.note"]
    assert await orders(tenv) == []  # nothing is submitted by opening the form

    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.submission_form_id(session.id, total, gross),
        fields={"s.reference": "[ref-123]", "s.note": ""},
        message=message,
    )
    (placed,) = await orders(tenv)
    assert placed.details == {"s.reference": "[ref-123]"}
    assert placed.submission_answers == {"s.reference": "[ref-123]"}
    assert message.embeds[0].title == tenv.app.content.render(
        k.SUBMITTED_TITLE, order_number="1", total=tenv.app.ui.money(1000) or ""
    )
    # Staff see the answer with its configured label under the order controls.
    staff_channel = channel(tenv, "order_queue")
    details = staff_channel.messages[1]
    assert [(f.name, f.value) for f in details.embeds[0].fields] == [
        (tenv.text(k.submission_field_key("reference", "label")), "[ref-123]")
    ]


async def test_answers_are_validated_on_the_server(sf: TestEnv) -> None:
    tenv = sf
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    custom_id = views.submission_form_id(session.id, total, gross)
    for fields in ({"s.reference": " ", "s.note": ""}, {"s.reference": "ab"}, {"s.reference": "x" * 41}):
        refused = await submit_modal(tenv.app, user=customer, custom_id=custom_id, fields=fields, message=message)
        assert error_text(refused) == tenv.text(core.ERROR_INVALID_INPUT)
    assert await orders(tenv) == []
    assert (await reload_session(tenv, session.id)).status == m.ACTIVE


async def test_a_price_change_before_the_form_shows_the_notice(sf: TestEnv) -> None:
    tenv = sf
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    product_id = next(iter(await tenv.app.db.run(repo.cart_quantities, session.id)))
    await set_price(tenv, product_id, 1500)
    clicked = await press(tenv, customer, message, views.submit_id(session.id, total, gross))
    assert modal_of(clicked) is None
    assert message.embeds[-1].description == tenv.app.content.render(
        k.REVIEW_PRICE_CHANGED, total=tenv.app.ui.money(1500) or ""
    )
    assert views.submit_id(session.id, 1500, 1500) in message.custom_ids()


async def test_a_price_change_after_the_form_keeps_the_answers_for_the_next_attempt(sf: TestEnv) -> None:
    tenv = sf
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    product_id = next(iter(await tenv.app.db.run(repo.cart_quantities, session.id)))
    await set_price(tenv, product_id, 1500)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.submission_form_id(session.id, total, gross),
        fields={"s.reference": "[ref-123]", "s.note": "[a note]"},
        message=message,
    )
    assert await orders(tenv) == []
    kept = await reload_session(tenv, session.id)
    assert kept.status == m.ACTIVE and kept.details == {"s.reference": "[ref-123]", "s.note": "[a note]"}
    again = await press(tenv, customer, message, views.submit_id(session.id, 1500, 1500))
    modal = modal_of(again)
    assert modal is not None
    defaults = {
        item.custom_id: item.default for item in modal.walk_children() if isinstance(item, discord.ui.TextInput)
    }
    assert defaults == {"s.reference": "[ref-123]", "s.note": "[a note]"}
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.submission_form_id(session.id, 1500, 1500),
        fields={"s.reference": "[ref-123]", "s.note": ""},
        message=message,
    )
    (placed,) = await orders(tenv)
    assert placed.total_minor == 1500 and placed.details == {"s.reference": "[ref-123]"}


async def test_a_repeated_form_creates_one_order(sf: TestEnv) -> None:
    tenv = sf
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    custom_id = views.submission_form_id(session.id, total, gross)
    for _ in range(2):
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=custom_id,
            fields={"s.reference": "[ref-123]"},
            message=message,
        )
    assert len(await orders(tenv)) == 1
    again = await press(tenv, customer, message, views.submit_id(session.id, total, gross))
    assert modal_of(again) is None and len(await orders(tenv)) == 1  # the order is shown again


async def test_another_member_cannot_send_the_form(sf: TestEnv) -> None:
    tenv = sf
    _, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    other = await submit_modal(
        tenv.app,
        user=tenv.member(name="other"),
        custom_id=views.submission_form_id(session.id, total, gross),
        fields={"s.reference": "[ref-123]"},
        message=message,
    )
    assert error_text(other) == tenv.text(core.ERROR_PERMISSION)
    assert await orders(tenv) == []


async def test_the_form_works_after_a_restart(sf: TestEnv) -> None:
    tenv = sf
    customer, session, message = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    app = restarted_app(tenv)
    await submit_modal(
        app,
        user=customer,
        custom_id=views.submission_form_id(session.id, total, gross),
        fields={"s.reference": "[ref-123]"},
        message=message,
    )
    (placed,) = await orders(tenv)
    assert placed.details == {"s.reference": "[ref-123]"}


async def test_the_form_cannot_be_skipped(sf: TestEnv) -> None:
    tenv = sf
    _, session, _ = await _review(tenv)
    total, gross = await _totals(tenv, session.id)
    with pytest.raises(repo.DetailsIncomplete):
        await service.submit(tenv.app, session, total, gross)
    assert await orders(tenv) == []


async def test_without_a_form_submit_creates_the_order_directly(tmp_path: Path) -> None:
    tenv = await env(tmp_path)
    try:
        customer, session, message = await _review(tenv)
        total, gross = await _totals(tenv, session.id)
        clicked = await press(tenv, customer, message, views.submit_id(session.id, total, gross))
        assert modal_of(clicked) is None
        assert len(await orders(tenv)) == 1
        assert tenv.text(k.TICKET_ORDER_NUMBER_FIELD) in field_map(message)
    finally:
        await tenv.close()


def test_settings_and_keys() -> None:
    settings = make_settings(SUBMISSION_FORM)
    assert [i.answer_key for i in flow.submission_inputs(settings.checkout)] == ["s.reference", "s.note"]
    keys = {key.key: key for key in k.content_keys(settings)}
    assert keys[k.SUBMISSION_MODAL_TITLE].required
    assert keys[k.submission_field_key("reference", "label")].required
    assert not keys[k.submission_field_key("reference", "placeholder")].required
    assert k.SUBMISSION_MODAL_TITLE not in {key.key for key in k.content_keys(make_settings())}
    assert flow.label_key_for_answer("s.reference") == k.submission_field_key("reference", "label")
    with pytest.raises(SettingsError) as raised:
        make_settings({"checkout": {"submission_form": [{"id": f"f{i}"} for i in range(6)]}})
    assert any("submission_form" in error for error in raised.value.errors)
    with pytest.raises(SettingsError):
        make_settings({"checkout": {"submission_form": [{"id": "Bad Id"}]}})


async def test_an_earlier_answer_that_no_longer_fits_is_not_filled_in(sf: TestEnv) -> None:
    tenv = sf
    _, session, _ = await _review(tenv)
    stale = {**session.details, "s.reference": "x" * 41, "s.note": "[kept]"}
    modal = views.submission_modal(tenv.app.ui, tenv.app.settings, replace(session, details=stale), 1, 1)
    assert [(i.custom_id, i.default) for i in modal.inputs] == [("s.reference", None), ("s.note", "[kept]")]
