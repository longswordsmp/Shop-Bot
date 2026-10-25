"""#orders privacy (specification section 5): the customer's typed form answers are not posted in the staff channel
unless ``operations.answers_in_orders_channel`` is on; staff see them privately (the record's Details button,
``/order view``) after a server-side staff check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import SettingsError, parse_settings
from shopbot.db.repos import notifications as notif_repo
from shopbot.features import core_keys as core
from shopbot.features.checkout import MODULE as CHECKOUT
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, staff_views, views
from shopbot.features.checkout import tasks as checkout_tasks
from shopbot.ui.spec import MessageSpec
from tests.checkout.util import (
    ANSWERS_IN_CHANNEL,
    add_product,
    channel,
    error_text,
    go,
    press,
    restarted_app,
    run_command,
    set_qty,
    start,
    texts,
)
from tests.fakes import FakeMember, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, base_settings_dict, deep_merge, shipped_settings_dict
from tests.operations.util import OPS_SETTINGS
from tests.orders_privacy.util import FORMS, env, public_texts, run_tasks

NAME, ADDRESS, REFERENCE = "[sample name]", "[sample address line]", "[sample reference]"


async def _order_with_answers(tenv: TestEnv, customer: FakeMember) -> tuple[m.Order, FakeTextChannel]:
    """An order whose details form, fulfillment option form and submission form were all answered."""
    product = await add_product(tenv, "[sample product 1]", price=1000)
    _, session, ch, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, product.id, 1)
    await go(tenv, customer, message, session.id, m.CART)
    await go(tenv, customer, message, session.id, m.FULFILLMENT)
    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["delivery"])
    await go(tenv, customer, message, session.id, m.DETAILS)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": NAME, "o.delivery.address": ADDRESS},
        message=message,
    )
    await go(tenv, customer, message, session.id, m.REVIEW)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.submission_form_id(session.id, 1000, 1000),
        fields={"s.reference": REFERENCE},
        message=message,
    )
    orders = await tenv.app.db.run(repo.list_orders, GUILD_ID, limit=10, offset=0)
    assert len(orders) == 1
    placed = orders[0]
    assert placed.details == {"d.name": NAME, "o.delivery.address": ADDRESS, "s.reference": REFERENCE}
    return placed, ch


async def _staff_detail_records(tenv: TestEnv) -> int:
    return await tenv.app.db.run(
        lambda conn: int(
            conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE kind = 'checkout.staff_details'").fetchone()[
                "n"
            ]
        )
    )


def _labelled(tenv: TestEnv) -> list[tuple[str, str]]:
    return [
        (tenv.text(k.form_field_key("name", "label")), NAME),
        (tenv.text(k.option_form_key("delivery", "address", "label")), ADDRESS),
        (tenv.text(k.submission_field_key("reference", "label")), REFERENCE),
    ]


def _fields(spec_texts: list[Any]) -> list[tuple[str, str]]:
    return [(f.name or "", f.value or "") for message in spec_texts for e in message.embeds for f in e.fields]


# -- default: nothing typed by the customer is posted in the staff channel --------------------------------------


async def test_answers_are_not_posted_in_the_staff_channel_by_default(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS, OPS_SETTINGS)
    try:
        assert tenv.app.settings.operations.answers_in_orders_channel is False
        customer = tenv.member()
        placed, ch = await _order_with_answers(tenv, customer)
        await run_tasks(tenv)  # alerts, follow-ups
        staff_channel = channel(tenv, "order_queue")
        assert [msg.id for msg in staff_channel.messages] == [placed.staff_message_id or -1]
        assert await _staff_detail_records(tenv) == 0
        # No typed answer anywhere outside the customer's own ticket (#orders, #staff-chat, audit log, transcripts).
        assert channel(tenv, "staff_alerts").messages  # the new-order alert was posted
        visible = public_texts(tenv, except_channel=ch)
        for answer in (NAME, ADDRESS, REFERENCE):
            assert answer not in visible
    finally:
        await tenv.close()


async def test_staff_see_the_answers_privately_with_details_and_order_view(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS)
    try:
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        placed, _ = await _order_with_answers(tenv, customer)
        record = channel(tenv, "order_queue").messages[0]
        assert staff_views.record_details_id(placed.id) in record.custom_ids()

        details = await click(
            tenv.app, user=staffer, custom_id=staff_views.record_details_id(placed.id), message=record
        )
        assert details.sent and all(message.ephemeral for message in details.sent)
        shown = _fields(details.sent)
        for pair in _labelled(tenv):
            assert pair in shown

        viewed = await run_command(tenv, "view", user=staffer, number=placed.number)
        assert all(message.ephemeral for message in viewed.sent)
        shown = _fields(viewed.sent)
        for pair in _labelled(tenv):
            assert pair in shown
        # Still nothing posted in the channel itself.
        assert len(channel(tenv, "order_queue").messages) == 1
    finally:
        await tenv.close()


async def test_customers_cannot_open_the_private_details(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS)
    try:
        customer = tenv.member()
        placed, _ = await _order_with_answers(tenv, customer)
        for user in (customer, tenv.member(name="other")):
            refused = await click(tenv.app, user=user, custom_id=staff_views.record_details_id(placed.id))
            assert error_text(refused) == tenv.text(core.ERROR_PERMISSION)
            assert REFERENCE not in texts(refused)
            command = await run_command(tenv, "view", user=user, number=placed.number)
            assert REFERENCE not in texts(command)
    finally:
        await tenv.close()


async def test_details_keep_working_after_a_restart(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS)
    try:
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        placed, _ = await _order_with_answers(tenv, customer)
        app = restarted_app(tenv)
        await app.resources.refresh()
        await checkout_tasks.on_ready(app)  # restart recovery does not post the answers either
        assert len(channel(tenv, "order_queue").messages) == 1
        assert await _staff_detail_records(tenv) == 0
        details = await click(app, user=staffer, custom_id=staff_views.record_details_id(placed.id))
        assert (tenv.text(k.submission_field_key("reference", "label")), REFERENCE) in _fields(details.sent)
    finally:
        await tenv.close()


async def test_post_staff_details_is_a_no_op_while_off(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS)
    try:
        placed, _ = await _order_with_answers(tenv, tenv.member())
        assert not service.answers_in_staff_channel(tenv.app)
        await service.post_staff_details(tenv.app, placed.id)
        assert len(channel(tenv, "order_queue").messages) == 1
        assert await tenv.app.db.run(notif_repo.already_sent, service.staff_details_key(placed.id, 0)) is False
    finally:
        await tenv.close()


# -- on: the previous behaviour ---------------------------------------------------------------------------------


async def test_answers_are_posted_under_the_record_when_switched_on(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FORMS, ANSWERS_IN_CHANNEL)
    try:
        placed, _ = await _order_with_answers(tenv, tenv.member())
        staff_channel = channel(tenv, "order_queue")
        assert len(staff_channel.messages) == 2
        answers = staff_channel.messages[1]
        assert answers.embeds[0].title == tenv.app.content.render(
            k.STAFF_DETAILS_TITLE, order_number=str(placed.number)
        )
        assert _fields([answers]) == _labelled(tenv)
        # Deduplicated: the follow-up and restart recovery never post them twice.
        await service.post_staff_details(tenv.app, placed.id)
        app = restarted_app(tenv)
        await app.resources.refresh()
        await checkout_tasks.on_ready(app)
        assert len(staff_channel.messages) == 2
    finally:
        await tenv.close()


# -- settings, preview -------------------------------------------------------------------------------------------


def test_the_setting_is_shipped_off_and_validated() -> None:
    assert parse_settings(shipped_settings_dict()).operations.answers_in_orders_channel is False
    assert parse_settings(base_settings_dict()).operations.answers_in_orders_channel is False  # default
    on = parse_settings(deep_merge(base_settings_dict(), ANSWERS_IN_CHANNEL))
    assert on.operations.answers_in_orders_channel is True
    with pytest.raises(SettingsError) as exc:
        parse_settings(deep_merge(base_settings_dict(), {"operations": {"answers_in_orders_channel": "yes"}}))
    assert any("operations.answers_in_orders_channel" in error for error in exc.value.errors)


@pytest.mark.parametrize("on", [False, True])
async def test_the_preview_shows_the_posted_answers_only_when_they_are_posted(tmp_path: Path, on: bool) -> None:
    tenv = await env(tmp_path, FORMS, ANSWERS_IN_CHANNEL if on else {})
    try:
        assert CHECKOUT.preview is not None
        states = {state.name: state.spec for state in CHECKOUT.preview(tenv.app)}
        assert ("staff_details_1" in states) is on
        # The private record details always show the answers (as staff see them).
        record = states["staff_record_details_1"]
        assert isinstance(record, MessageSpec)
        values = [f.value for e in record.embeds for f in e.fields]
        assert any(value.startswith("[sample answer") for value in values)
    finally:
        await tenv.close()
