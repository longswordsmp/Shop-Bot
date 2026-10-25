"""Discord-facing fixes: large orders stay within message limits, menus are reset after a pick, and ``/order list``
accepts a customer who left the server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec
from tests.checkout.util import (
    add_product,
    channel,
    component,
    control_ids,
    error_text,
    go,
    make_env,
    money,
    order,
    orders,
    place_order,
    press,
    render,
    run_command,
    set_qty,
    staff_message,
    start,
    submit,
    workflow_message,
)
from tests.fakes import FakeInteraction, FakeMessage, FakeUser, modal_of
from tests.helpers import TestEnv

# -- [content-ui #0] the largest order still shows its payment instructions and cancel button -----------------------

DESCRIPTION = ("[sample description] " * 45)[:900]
INSTRUCTIONS = ("[sample payment instructions] " * 90)[:2600]
STAFF_DESCRIPTION = ("[sample staff text] " * 175)[:3500] + " {user_mention} {channel_mention}"
LARGE_CONTENT = {
    k.SUBMITTED_DESCRIPTION: DESCRIPTION,
    "checkout.payment.manual.instructions": INSTRUCTIONS,
    k.CART_LINE: "{quantity} x {name} ({unit_price} each): {line_total}",
    k.STAFF_DESCRIPTION: STAFF_DESCRIPTION,
}


def _texts(message: FakeMessage) -> str:
    return "\n".join(f.value or "" for embed in message.embeds for f in embed.fields)


def _bot_messages(ch: Any) -> list[FakeMessage]:
    return [msg for msg in ch.messages if msg.author is ch.guild.me]


async def test_a_full_cart_with_long_names_never_hides_the_payment_instructions(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"max_cart_lines": 20}}, content=LARGE_CONTENT)
    try:
        assert tenv.app.ready("checkout"), tenv.app.readiness.statuses["checkout"].problems
        customer = tenv.member()
        staffer = tenv.member(name="staffer", staff=True)
        names = [f"[sample product {i}]".ljust(100, "_") for i in range(1, 21)]
        products = [await add_product(tenv, name, price=1000) for name in names]
        _, session, ch, message = await start(tenv, customer)
        for item in products:
            await set_qty(tenv, customer, message, session.id, item.id, 1)
        await go(tenv, customer, message, session.id, m.CART)
        await go(tenv, customer, message, session.id, m.REVIEW)
        result = await submit(tenv, customer, message, session.id)
        assert error_text(result) is None
        placed = (await orders(tenv))[0]
        values = {"order_number": "1", "total": money(tenv, 20_000)}
        # The workflow message: status text, totals, the payment instructions and the cancel button.
        assert message.embeds[0].title == render(tenv, k.SUBMITTED_TITLE, **values)
        assert message.embeds[0].description == DESCRIPTION
        assert message.embeds[-1].description == INSTRUCTIONS
        assert message.custom_ids() == [views.order_cancel_id(placed.id)]
        fields = {f.name: f.value for f in message.embeds[0].fields}
        assert fields[tenv.text(k.REVIEW_TOTAL_FIELD)] == money(tenv, 20_000)
        # Every order line follows in the order channel (nothing is cut).
        extras = [msg for msg in _bot_messages(ch) if msg is not workflow_message(ch)]
        assert extras and all(len(msg.embeds) <= 10 for msg in extras)
        listed = "\n".join(_texts(msg) for msg in extras)
        assert all(views.escape(name) in listed for name in names)
        assert all(f.name == tenv.text(k.CART_FIELD) for msg in extras for e in msg.embeds for f in e.fields)
        # Staff get their controls (and the lines separately), too.
        assert placed.staff_message_id is not None
        control = staff_message(tenv, placed)
        assert control_ids(control) == [
            views.staff_action_id(m.PAID, placed.id, m.PENDING),
            views.staff_action_id(m.REJECTED, placed.id, m.PENDING),
            views.staff_action_id(m.CANCELLED, placed.id, m.PENDING),
        ]
        staff_listed = "\n".join(_texts(msg) for msg in _bot_messages(channel(tenv, "order_queue")))
        assert all(views.escape(name) in staff_listed for name in names)
        # Going back from the cancel question and later status changes render without errors and post nothing twice.
        await press(tenv, customer, message, views.order_cancel_id(placed.id))
        back = await press(tenv, customer, message, views.order_cancel_no_id(placed.id))
        assert error_text(back) is None and message.embeds[-1].description == INSTRUCTIONS
        posted = len(ch.messages)
        await press(tenv, staffer, control, views.staff_action_id(m.PAID, placed.id, m.PENDING))
        paid = await order(tenv, placed.id)
        assert paid.status == m.PAID
        assert message.embeds[0].title == render(tenv, k.status_title_key(m.PAID), **values)
        assert len([msg for msg in ch.messages if msg.author is ch.guild.me]) <= posted + 1  # at most the notification
    finally:
        await tenv.close()


async def test_order_messages_fall_back_to_a_separate_instructions_message(tmp_path: Path) -> None:
    """Configured text that alone fills a message: the instructions get their own message; nothing is cut."""
    long_description = ("[d] " * 1100)[:4096]
    long_instructions = ("[i] " * 1100)[:4096]
    content = {k.SUBMITTED_DESCRIPTION: long_description, "checkout.payment.manual.instructions": long_instructions}
    tenv = await make_env(tmp_path, content=content)
    try:
        p1 = await add_product(tenv, "[p1]")
        placed, ch, message = await place_order(tenv, tenv.member(), [(p1.id, 1)])
        assert message.embeds[0].description == long_description
        assert message.custom_ids() == [views.order_cancel_id(placed.id)]
        separate = [msg for msg in _bot_messages(ch) if msg is not message]
        assert [msg.embeds[0].description for msg in separate] == [long_instructions]
    finally:
        await tenv.close()


async def test_order_message_budget_keeps_everything_within_limits(co: TestEnv) -> None:
    from shopbot.features.checkout import preview

    view = preview._order(co.app.settings, m.PENDING, preview._largest_cart(co.app.settings))
    instructions = None
    messages = views.order_messages(co.app.ui, co.app.settings, view, instructions=instructions)
    specs: list[MessageSpec] = [messages.main, *messages.instructions, *messages.items]
    assert all(validate_message(spec) == [] for spec in specs)


# -- [discord-api #1] the product and cart menus are reset after a pick ----------------------------------------------


async def test_the_product_menu_is_reset_after_every_pick(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", stock=2)
    empty = await add_product(co, "[empty]", stock=0)
    _, session, _, message = await start(co, customer)
    edits = len(message.edits)
    picked = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    assert modal_of(picked) is not None
    assert len(message.edits) == edits + 1 and message.edits[-1]["view"] is not None
    assert views.pick_id(session.id, 0) in message.custom_ids()
    # A refused quantity: the answer re-renders the message (resetting the menu); the error follows privately.
    refused = await set_qty(co, customer, message, session.id, p1.id, 5)
    assert refused.response_kinds() == ["edit_message"]
    assert error_text(refused) == render(co, k.ERROR_OUT_OF_STOCK, name=views.escape("[p1]"), available=2)
    # A refused pick: the same.
    sold_out = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(empty.id)])
    assert sold_out.response_kinds() == ["edit_message"] and modal_of(sold_out) is None
    assert error_text(sold_out) == render(co, k.ERROR_OUT_OF_STOCK, name=views.escape("[empty]"), available=0)
    # The same product can be picked again (the menu shows no selection).
    again = await press(co, customer, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    assert modal_of(again) is not None


async def test_the_cart_menu_is_reset_after_every_pick(co: TestEnv) -> None:
    customer = co.member()
    p1 = await add_product(co, "[p1]", price=500)
    _, session, _, message = await start(co, customer)
    await set_qty(co, customer, message, session.id, p1.id, 1)
    await go(co, customer, message, session.id, m.CART)
    edits = len(message.edits)
    opened = await press(co, customer, message, views.edit_id(session.id), values=[str(p1.id)])
    assert modal_of(opened) is not None
    assert len(message.edits) == edits + 1
    assert component(message, views.edit_id(session.id)).options[0].label == "[p1]"
    missing = await press(co, customer, message, views.edit_id(session.id), values=["12345"])
    assert missing.response_kinds() == ["edit_message"] and error_text(missing) is not None


async def test_other_members_get_no_reset_of_the_workflow(co: TestEnv) -> None:
    customer, other = co.member(), co.member(name="other")
    p1 = await add_product(co, "[p1]")
    _, session, _, message = await start(co, customer)
    edits = len(message.edits)
    denied = await press(co, other, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    assert denied.response_kinds() == ["send_message"] and len(message.edits) == edits


# -- [discord-api #3] /order list accepts a customer who left --------------------------------------------------------


async def test_order_list_accepts_a_customer_who_left(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    buyer = co.member(name="buyer")
    p1 = await add_product(co, "[p1]")
    await place_order(co, buyer, [(p1.id, 1)])
    departed = FakeUser(id=buyer.id, name="departed")  # resolved as a user without member data
    assert co.app.bot is not None
    group = co.app.bot.tree.get_command("order", guild=co.app.guild_object)
    assert isinstance(group, app_commands.Group)
    command = group.get_command("list")
    assert isinstance(command, app_commands.Command)
    parameter = command._params["member"]
    assert parameter.type is discord.AppCommandOptionType.user
    interaction = FakeInteraction(
        client=co.bot, user=staffer, guild=co.guild, type=discord.InteractionType.application_command
    )
    assert await parameter.transform(interaction, departed) is departed  # type: ignore[arg-type]
    listed = await run_command(co, "list", user=staffer, member=departed)
    assert listed.sent[-1].embeds[0].title == co.app.op("checkout.list.title", total=1)


# -- more than 25 fields (unlimited checkout.fees) ------------------------------------------------------------------


async def test_many_fees_spread_over_several_cards(tmp_path: Path) -> None:
    fees = [{"id": f"fee{i}", "kind": "flat", "amount_minor": 10} for i in range(30)]
    tenv = await make_env(tmp_path, {"checkout": {"fees": fees}})
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=1000)
        placed, _, message = await place_order(tenv, customer, [(p1.id, 1)])
        assert placed.total_minor == 1000 + 30 * 10
        assert all(len(embed.fields) <= 25 for embed in message.embeds)
        names = [f.name for embed in message.embeds for f in embed.fields]
        assert all(tenv.text(k.fee_label_key(f"fee{i}")) in names for i in range(30))
        control = staff_message(tenv, placed)
        assert len(control.embeds) > 1 and all(len(embed.fields) <= 25 for embed in control.embeds)
    finally:
        await tenv.close()
