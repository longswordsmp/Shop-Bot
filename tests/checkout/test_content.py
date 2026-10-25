"""Content rules: optional text is omitted (never invented), missing text at runtime sends nothing incomplete."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import discord

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views
from tests.checkout.util import (
    add_product,
    component,
    go,
    make_env,
    order,
    place_order,
    press,
    set_qty,
    staff_message,
    start,
)

OPTIONAL_EMPTY = {
    k.BROWSE_DESCRIPTION: "",
    k.CART_DESCRIPTION: "",
    k.CART_EDIT_OPTION: "",
    k.SUBMITTED_DESCRIPTION: "",
    "checkout.payment.manual.title": "",
    k.CHANNEL_TOPIC: "",
    k.notify_title_key(m.PAID): "",
    k.status_description_key(m.PAID): "",
    "branding.footer_text": "",
    "branding.author_name": "",
}


async def test_optional_text_is_omitted(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content=OPTIONAL_EMPTY)
    try:
        assert tenv.app.ready("checkout")
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        _, session, ch, message = await start(tenv, customer)
        assert ch.topic in (None, discord.utils.MISSING)  # no topic is set
        embed = message.embeds[0]
        assert embed.title == tenv.text(k.BROWSE_TITLE) and embed.description is None
        # The step indicator is required (the live panel always shows the progress); nothing else is added.
        assert embed.footer.text == tenv.app.ui.progress(1, 3) and embed.author.name is None
        # Menu placeholders are required: an empty one would make Discord show its own default wording.
        assert component(message, views.pick_id(session.id, 0)).placeholder == tenv.text(k.BROWSE_SELECT_PLACEHOLDER)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)
        await go(tenv, customer, message, session.id, m.CART)
        edit = component(message, views.edit_id(session.id))
        assert edit.placeholder == tenv.text(k.CART_EDIT_PLACEHOLDER) and edit.options[0].description is None
        placed, _, submitted = await place_order(tenv, tenv.member(name="b"), [(p1.id, 1)])
        assert submitted.embeds[0].description is None
        assert submitted.embeds[1].title is None
        assert submitted.embeds[1].description == tenv.app.content.render(
            "checkout.payment.manual.instructions", order_number="1", total=tenv.app.ui.money(1000) or ""
        )
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        buyer = tenv.guild.get_member(placed.user_id)
        assert buyer is not None
        assert buyer.dms[-1].embeds[0].title is None  # type: ignore[attr-defined]
        assert submitted.embeds[0].description is None
    finally:
        await tenv.close()


async def test_missing_notification_text_at_runtime_sends_nothing(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, ch, _ = await place_order(tenv, customer, [(p1.id, 1)])
        values = dict(tenv.content)
        values[k.notify_description_key(m.PAID)] = ""
        tenv.app.content.replace(file_values=values)  # removed after readiness was computed
        sent_before = len(ch.messages)
        dms_before = len(customer.dms)  # the 'submission received' notification
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        assert (await order(tenv, placed.id)).status == m.PAID
        assert len(customer.dms) == dms_before
        assert len(ch.messages) == sent_before
    finally:
        await tenv.close()


async def test_reviews_hook_needs_a_ready_reviews_feature(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[int] = []

    async def schedule_review_prompt(app: Any, order_id: int) -> None:
        calls.append(order_id)

    fake = types.ModuleType("shopbot.features.reviews.service")
    fake.schedule_review_prompt = schedule_review_prompt  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shopbot.features.reviews.service", fake)
    tenv = await make_env(tmp_path, {"features": {"reviews": False}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        p1 = await add_product(tenv, "[p1]")
        placed, _, _ = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.PAID, placed.id))
        await press(tenv, staffer, staff_message(tenv, placed), views.staff_action_id(m.FULFILLED, placed.id))
        assert (await order(tenv, placed.id)).status == m.FULFILLED
        assert calls == []
    finally:
        await tenv.close()


async def test_orders_of_a_former_payment_provider_still_render(tmp_path: Path) -> None:
    from dataclasses import replace

    from shopbot.config.readiness import build_registry
    from shopbot.features.checkout import service

    tenv = await make_env(tmp_path)
    try:
        p1 = await add_product(tenv, "[p1]")
        placed, _, _ = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        # The operator switched the payment provider off: the manual provider's text is no longer registered.
        settings = tenv.app.settings
        tenv.app.settings = replace(settings, payments=replace(settings.payments, provider=""))
        tenv.app.content.replace(registry=build_registry(tenv.app.settings, tenv.app.modules))
        assert await service.payment_instructions(tenv.app, await order(tenv, placed.id)) is None
        spec = await service.customer_order_spec(tenv.app, placed.id)
        assert len(spec.embeds) == 1  # the order still renders, without instructions
        await tenv.app.db.run(lambda conn: conn.execute("UPDATE orders SET payment_provider = 'retired'"))
        assert await service.payment_instructions(tenv.app, await order(tenv, placed.id)) is None
    finally:
        await tenv.close()


def test_staff_channel_must_be_private() -> None:
    from shopbot.features.checkout.content_keys import settings_problems
    from tests.helpers import make_settings

    assert not [p for p in settings_problems(make_settings()) if "staff or admin access" in p]
    public = make_settings({"checkout": {"staff_channel": "shop"}})
    assert any("checkout.staff_channel" in p and "staff or admin access" in p for p in settings_problems(public))
