"""``/orders`` (browse and filter, paginated, open a record) and ``/order view`` for staff."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from shopbot.commands import on_tree_error
from shopbot.features import core_keys as core
from shopbot.features.checkout import handling_repo, staff, views
from shopbot.features.checkout import models as m
from tests.checkout.util import add_product, error_text, op_text, place_order, restarted_app, run_command
from tests.fakes import FakeInteraction, FakeMember, click
from tests.helpers import TestEnv
from tests.operations.util import mark


async def _orders_command(tenv: TestEnv, *, user: FakeMember, app: Any = None, **kwargs: Any) -> FakeInteraction:
    app = app or tenv.app
    command = app.bot.tree.get_command("orders", guild=app.guild_object)
    assert isinstance(command, app_commands.Command)
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        if not await command._check_can_run(interaction):  # type: ignore[arg-type]
            raise app_commands.CheckFailure("check failed")
        await command.callback(interaction, **kwargs)  # type: ignore[arg-type]
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    return interaction


async def _orders(tenv: TestEnv, count: int) -> list[m.Order]:
    product = await add_product(tenv, "[p1]", price=1000, stock=500)
    placed: list[m.Order] = []
    for index in range(count):
        order, _, _ = await place_order(tenv, tenv.member(name=f"c{index}"), [(product.id, 1)])
        placed.append(order)
    return placed


def _list_lines(interaction: FakeInteraction) -> list[str]:
    message = interaction.last_sent
    assert message is not None
    return (message.embeds[0].description or "").split("\n")


async def test_orders_lists_filters_and_pages(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed = await _orders(ops, 12)
    await mark(ops, staffer, placed[0].id, m.PAID)
    listed = await _orders_command(ops, user=staffer)
    message = listed.last_sent
    assert message is not None and getattr(message, "ephemeral", False)
    assert message.embeds[0].title == ops.app.op("checkout.list.title", total=12)
    assert len(_list_lines(listed)) == 10
    # Amounts as the order records write them (common.money_format); the page as "Page 1 of 2".
    total = views.order_money(ops.app.ui, ops.app.settings, placed[0].currency)(placed[0].total_minor)
    assert all(f" · {total} · " in line for line in _list_lines(listed))
    assert message.embeds[0].footer.text == ops.app.op("checkout.list.page", page=1, pages=2) == "Page 1 of 2"
    ids = message.custom_ids()
    assert ids == [
        views.list_filter_id(0),
        views.list_view_id(),
        views.list_page_id(0, "", 0),
        views.list_page_id(1, "", 0),
    ]
    # Next page (a button; works after a restart because it only carries page/filter values).
    app = restarted_app(ops)
    await app.resources.refresh()
    paged = await click(app, user=staffer, custom_id=views.list_page_id(1, "", 0), message=message)
    assert len((message.embeds[0].description or "").split("\n")) == 2 and paged.response_kinds() == ["edit_message"]
    # Filter by status from the menu on the list.
    filtered = await click(ops.app, user=staffer, custom_id=views.list_filter_id(0), message=message, values=["paid"])
    assert filtered.response_kinds() == ["edit_message"]
    assert message.embeds[0].title == ops.app.op("checkout.list.title", total=1)
    # The same filter from the command option.
    paid_only = await _orders_command(ops, user=staffer, status="paid")
    assert paid_only.last_sent is not None and paid_only.last_sent.embeds[0].title == ops.app.op(
        "checkout.list.title", total=1
    )
    member_only = await _orders_command(ops, user=staffer, member=ops.guild.get_member(placed[3].user_id))
    assert len(_list_lines(member_only)) == 1


async def test_orders_menu_opens_a_record_privately(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed = await _orders(ops, 2)
    listed = await _orders_command(ops, user=staffer)
    message = listed.last_sent
    assert message is not None
    shown = await click(
        ops.app, user=staffer, custom_id=views.list_view_id(), message=message, values=[str(placed[1].id)]
    )
    assert shown.sent and all(getattr(msg, "ephemeral", False) for msg in shown.sent)
    assert shown.sent[0].embeds[0].title == ops.app.op("checkout.lookup.title", number=placed[1].number)
    missing = await click(ops.app, user=staffer, custom_id=views.list_view_id(), message=message, values=["999"])
    assert op_text(missing) == ops.app.op("checkout.op.order_missing")
    forged = await click(ops.app, user=staffer, custom_id=views.list_view_id(), message=message, values=["x"])
    assert error_text(forged) == ops.text(core.ERROR_INVALID_INPUT)
    bad_filter = await click(ops.app, user=staffer, custom_id=views.list_filter_id(0), message=message, values=["x"])
    assert error_text(bad_filter) == ops.text(core.ERROR_INVALID_INPUT)


async def test_orders_and_order_view_are_staff_only(ops: TestEnv) -> None:
    member = ops.member(name="member")
    placed = await _orders(ops, 1)
    denied = await _orders_command(ops, user=member)
    assert error_text(denied) == ops.text(core.ERROR_PERMISSION)
    view_denied = await run_command(ops, "view", user=member, number=placed[0].number)
    assert error_text(view_denied) == ops.text(core.ERROR_PERMISSION)
    for custom_id, values in ((views.list_view_id(), [str(placed[0].id)]), (views.list_filter_id(0), ["all"])):
        clicked = await click(ops.app, user=member, custom_id=custom_id, values=values)
        assert error_text(clicked) == ops.text(core.ERROR_PERMISSION)


async def test_order_view_is_the_full_record(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed = await _orders(ops, 1)
    shown = await run_command(ops, "view", user=staffer, number=placed[0].number)
    assert shown.sent and shown.sent[-1].embeds[0].title == ops.app.op("checkout.lookup.title", number=1)
    missing = await run_command(ops, "view", user=staffer, number=42)
    assert op_text(missing) == ops.app.op("checkout.op.not_found", number=42)


async def test_order_note_command_validates_and_records(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed = await _orders(ops, 1)
    blank = await run_command(ops, "note", user=staffer, number=placed[0].number, text="   ")
    assert op_text(blank) == ops.app.op("checkout.op.text_invalid", max=handling_repo.NOTE_MAX)
    noted = await run_command(ops, "note", user=staffer, number=placed[0].number, text="[note]")
    assert op_text(noted) == ops.app.op("checkout.op.note_added", number=placed[0].number)
    notes = await ops.app.db.run(handling_repo.activity, placed[0].id, (handling_repo.NOTE,))
    assert [n.text for n in notes] == ["[note]"]


async def test_order_list_amounts_fall_back_to_the_operator_wording(ops: TestEnv) -> None:
    """Without common.money_format the staff list still works with operator text alone ("12.50 USD")."""
    values = {key: value for key, value in ops.content.items() if key != core.MONEY_FORMAT}
    ops.app.content.replace(file_values=values)
    assert staff.record_amount(ops.app, 1250, "USD") == staff.amount(ops.app, 1250, "USD") == "12.50 USD"
