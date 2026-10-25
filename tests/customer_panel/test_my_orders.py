"""``/myorders``: a private reply with the member's own orders (newest first, paged with buttons), each with its
number, current stage, total and timeline; never another member's data; buttons keep working after a restart."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as ck
from shopbot.features.checkout import handling_repo
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views as checkout_views
from shopbot.features.my_orders import MODULE, views
from shopbot.features.my_orders import commands as my_commands
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders import repo as my_repo
from shopbot.ui.limits import validate_message
from tests.checkout.util import (
    add_product,
    error_text,
    money,
    order,
    place_order,
    press,
    render,
    restarted_app,
    staff_message,
)
from tests.customer_panel.util import env, run_my_orders, stage, timeline_entry
from tests.fakes import FakeMember, FakeMessage, click, http_error
from tests.helpers import GUILD_ID, TestEnv, make_settings


def _reply(interaction: Any) -> FakeMessage:
    reply = interaction.last_sent
    assert reply is not None, "no reply"
    assert reply.ephemeral  # private
    return reply


def _cards(reply: FakeMessage) -> list[discord.Embed]:
    return reply.embeds[1:]


def _buttons(reply: FakeMessage) -> list[Any]:
    return [getattr(child, "item", child) for child in reply.components()]


def _numbers(tenv: TestEnv, reply: FakeMessage) -> list[str]:
    name = tenv.text(k.ORDER_NUMBER_FIELD)
    return [f.value or "" for card in _cards(reply) for f in card.fields if f.name == name]


async def _orders(tenv: TestEnv, customer: FakeMember, count: int) -> list[m.Order]:
    p1 = await add_product(tenv, "[p1]", price=1000)
    placed: list[m.Order] = []
    for _ in range(count):
        tenv.clock.advance(60)
        found, _, message = await place_order(tenv, customer, [(p1.id, 1)])
        # Cancel it (unpaid) so the open-order limit allows the next one.
        await press(tenv, customer, message, checkout_views.order_cancel_yes_id(found.id))
        placed.append(await order(tenv, found.id))
    return placed


# -- registration ----------------------------------------------------------------------------------------------------


async def test_the_command_uses_the_configured_description(cp: TestEnv) -> None:
    assert cp.app.bot is not None
    command = cp.app.bot.tree.get_command(k.COMMAND_NAME, guild=cp.app.guild_object)
    assert isinstance(command, app_commands.Command)
    assert command.description == cp.text(k.COMMAND_DESCRIPTION)
    assert command.guild_only
    assert command.default_permissions is None  # every member can use it
    assert command.checks == []


async def test_the_command_is_registered_only_when_enabled_and_described(tmp_path: Path) -> None:
    off = await env(tmp_path / "off", {"features": {"my_orders": False}})
    try:
        assert off.app.bot is not None
        assert off.app.bot.tree.get_command(k.COMMAND_NAME, guild=off.app.guild_object) is None
    finally:
        await off.close()
    undescribed = await env(tmp_path / "undescribed", content={k.COMMAND_DESCRIPTION: ""})
    try:
        assert undescribed.app.bot is not None
        assert undescribed.app.bot.tree.get_command(k.COMMAND_NAME, guild=undescribed.app.guild_object) is None
        assert not undescribed.app.ready(k.FEATURE)
        problems = {p.key for p in undescribed.app.readiness.statuses[k.FEATURE].problems}
        assert k.COMMAND_DESCRIPTION in problems
    finally:
        await undescribed.close()
    too_long = await env(tmp_path / "long", content={k.COMMAND_DESCRIPTION: "x" * 101})
    try:
        assert my_commands.command_description(too_long.app) is None  # never sent to Discord (it would be refused)
    finally:
        await too_long.close()


def test_module_contract() -> None:
    assert MODULE.depends_on == ("checkout",)
    settings = make_settings({"features": {"my_orders": True}})
    assert MODULE.is_enabled(settings)
    assert MODULE.shared_keys(settings) == [core.MONEY_FORMAT, core.BUTTON_PREV, core.BUTTON_NEXT]
    keys = {key.key: key for key in MODULE.content_keys(settings)}
    assert keys[k.COMMAND_DESCRIPTION].required and keys[k.EMPTY].required
    assert not keys[k.ORDER_TRACKING_FIELD].required and not keys[k.LIST_DESCRIPTION].required
    assert all(key.feature == k.FEATURE for key in keys.values())
    assert make_settings().my_orders.orders_per_page == 5


# -- the list --------------------------------------------------------------------------------------------------------


async def test_empty_list(cp: TestEnv) -> None:
    reply = _reply(await run_my_orders(cp, cp.member()))
    assert reply.embeds[0].title == render(cp, k.LIST_TITLE, count=0)
    assert reply.embeds[0].description == cp.text(k.EMPTY)
    assert len(reply.embeds) == 1 and reply.components() == []


async def test_only_the_members_own_orders_newest_first(cp: TestEnv) -> None:
    alice, bob = cp.member(name="alice"), cp.member(name="bob")
    p1 = await add_product(cp, "[p1]", price=1000)
    first, _, message = await place_order(cp, alice, [(p1.id, 1)])
    await press(cp, alice, message, checkout_views.order_cancel_yes_id(first.id))
    cp.clock.advance(60)
    bobs, _, _ = await place_order(cp, bob, [(p1.id, 3)])
    cp.clock.advance(60)
    second, ch, _ = await place_order(cp, alice, [(p1.id, 2)])
    reply = _reply(await run_my_orders(cp, alice))
    assert reply.embeds[0].title == render(cp, k.LIST_TITLE, count=2)
    assert _numbers(cp, reply) == [str(second.number), str(first.number)]
    assert str(bobs.number) not in _numbers(cp, reply)
    assert money(cp, 3000) not in reply.text()  # nothing of Bob's order
    newest = _cards(reply)[0]
    fields = {f.name: f.value for f in newest.fields}
    assert newest.title == render(
        cp,
        k.ORDER_TITLE,
        order_number=str(second.number),
        status=stage(cp, m.PENDING),
        total=money(cp, 2000),
        date=f"<t:{second.created_at}:d>",
    )
    assert fields[cp.text(k.ORDER_STATUS_FIELD)] == stage(cp, m.PENDING)
    assert fields[cp.text(k.ORDER_TOTAL_FIELD)] == money(cp, 2000)
    assert fields[cp.text(k.ORDER_TIMELINE_FIELD)] == timeline_entry(cp, m.PENDING, second.created_at)
    assert fields[cp.text(k.ORDER_TICKET_FIELD)] == f"<#{ch.id}>"  # the open ticket is linked
    cancelled = {f.name: f.value for f in _cards(reply)[1].fields}
    assert cancelled[cp.text(k.ORDER_STATUS_FIELD)] == stage(cp, m.CANCELLED)
    assert cancelled[cp.text(k.ORDER_TIMELINE_FIELD)].split("\n") == [
        timeline_entry(cp, m.PENDING, first.created_at),
        timeline_entry(cp, m.CANCELLED, first.created_at),
    ]
    assert cp.text(k.ORDER_TICKET_FIELD) not in cancelled  # its ticket is closed
    bob_reply = _reply(await run_my_orders(cp, bob))
    assert _numbers(cp, bob_reply) == [str(bobs.number)]


async def test_notes_and_staff_data_are_never_shown(cp: TestEnv) -> None:
    from shopbot.features.checkout import service

    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, _, _ = await place_order(cp, customer, [(p1.id, 1)])
    await service.add_note(cp.app, placed.id, actor_id=1, note="[internal note]")
    reply = _reply(await run_my_orders(cp, customer))
    assert "[internal note]" not in reply.text()
    assert len(reply.embeds[1].fields[3].value.split("\n")) == 1  # the timeline holds the submission only


async def test_tracking_details_are_shown_when_staff_recorded_them(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, _, _ = await place_order(cp, customer, [(p1.id, 1)])
    staffer = cp.member(name="staffer", staff=True)
    await press(cp, staffer, staff_message(cp, placed), checkout_views.staff_action_id(m.PAID, placed.id))

    def add_tracking(conn: sqlite3.Connection) -> None:
        handling_repo.set_tracking(
            conn, guild_id=GUILD_ID, order_id=placed.id, actor_id=staffer.id, text="[track *123*]", now=1
        )

    await cp.app.db.transaction(add_tracking)
    reply = _reply(await run_my_orders(cp, customer))
    fields = {f.name: f.value for f in _cards(reply)[0].fields}
    assert fields[cp.text(k.ORDER_TRACKING_FIELD)] == checkout_views.escape("[track *123*]")


async def test_optional_fields_are_omitted_when_not_configured(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.ORDER_TICKET_FIELD: "", k.LIST_DESCRIPTION: "", k.ORDER_TRACKING_FIELD: ""})
    try:
        assert tenv.app.ready(k.FEATURE)
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        await place_order(tenv, customer, [(p1.id, 1)])
        reply = _reply(await run_my_orders(tenv, customer))
        assert reply.embeds[0].description is None
        assert [f.name for f in _cards(reply)[0].fields] == [
            tenv.text(k.ORDER_NUMBER_FIELD),
            tenv.text(k.ORDER_STATUS_FIELD),
            tenv.text(k.ORDER_TOTAL_FIELD),
            tenv.text(k.ORDER_TIMELINE_FIELD),
        ]
    finally:
        await tenv.close()


# -- paging ----------------------------------------------------------------------------------------------------------


async def test_paging_buttons_work_and_survive_a_restart(tmp_path: Path) -> None:
    tenv = await env(tmp_path, {"my_orders": {"orders_per_page": 2}})
    try:
        customer = tenv.member()
        placed = await _orders(tenv, customer, 5)
        newest_first = [str(o.number) for o in reversed(placed)]
        reply = _reply(await run_my_orders(tenv, customer))
        assert _numbers(tenv, reply) == newest_first[:2]
        assert render(tenv, core.PAGE_INDICATOR, page=1, pages=3) in (reply.embeds[0].footer.text or "")
        prev, nxt = _buttons(reply)
        assert prev.disabled and not nxt.disabled
        assert (prev.custom_id, nxt.custom_id) == (views.page_id(customer.id, 0), views.page_id(customer.id, 1))

        await click(tenv.app, user=customer, custom_id=nxt.custom_id, message=reply)
        assert _numbers(tenv, reply) == newest_first[2:4]
        app = restarted_app(tenv)  # the buttons carry only the member id and the page
        await click(app, user=customer, custom_id=views.page_id(customer.id, 2), message=reply)
        assert _numbers(tenv, reply) == newest_first[4:]
        prev, nxt = _buttons(reply)
        assert not prev.disabled and nxt.disabled
        await click(app, user=customer, custom_id=views.page_id(customer.id, 99), message=reply)
        assert _numbers(tenv, reply) == newest_first[4:]  # beyond the end: the last page
    finally:
        await tenv.close()


async def test_another_member_cannot_page_someone_elses_list(cp: TestEnv) -> None:
    customer, other = cp.member(), cp.member(name="other")
    p1 = await add_product(cp, "[p1]")
    await place_order(cp, customer, [(p1.id, 1)])
    refused = await click(cp.app, user=other, custom_id=views.page_id(customer.id, 0))
    assert error_text(refused) == cp.text(core.ERROR_PERMISSION)
    assert all(str(customer.id) not in message.text() for message in refused.sent)


async def test_a_failed_edit_is_answered_with_an_error(cp: TestEnv) -> None:
    customer = cp.member()
    reply = _reply(await run_my_orders(cp, customer))

    async def gone(**kwargs: Any) -> Any:
        raise http_error(discord.NotFound, 404, 10008, "Unknown Message")

    reply.edit = gone  # type: ignore[method-assign]
    clicked = await click(cp.app, user=customer, custom_id=views.page_id(customer.id, 0), message=reply)
    assert error_text(clicked) == cp.text(core.ERROR_NOT_FOUND)


# -- readiness and errors --------------------------------------------------------------------------------------------


async def test_not_ready_answers_unavailable(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.EMPTY: ""})
    try:
        assert not tenv.app.ready(k.FEATURE)
        reply = _reply(await run_my_orders(tenv, tenv.member()))
        assert reply.embeds[0].description == tenv.text(core.ERROR_UNAVAILABLE)
        clicked = await click(tenv.app, user=tenv.member(name="b"), custom_id=views.page_id(1, 0))
        assert error_text(clicked) == tenv.text(core.ERROR_UNAVAILABLE)
    finally:
        await tenv.close()


async def test_a_foreign_guild_gets_nothing(cp: TestEnv) -> None:
    from tests.fakes import FakeGuild, FakeInteraction

    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    await place_order(cp, customer, [(p1.id, 1)])
    foreign = FakeGuild(id=999)
    interaction = FakeInteraction(
        client=cp.app.bot, user=customer, guild=foreign, type=discord.InteractionType.application_command
    )
    await my_commands.my_orders(interaction)  # type: ignore[arg-type]
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == cp.text(core.ERROR_PERMISSION)


async def test_query_is_bound_to_the_member(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    await place_order(cp, customer, [(p1.id, 1)])
    assert await cp.app.db.run(my_repo.customer_orders, GUILD_ID, 0) == []
    assert await cp.app.db.run(my_repo.customer_orders, GUILD_ID + 1, customer.id) == []
    mine = await cp.app.db.run(my_repo.customer_orders, GUILD_ID, customer.id)
    assert [item.order.user_id for item in mine] == [customer.id]


# -- limits ----------------------------------------------------------------------------------------------------------


async def test_many_long_orders_are_paged_within_discords_limits(tmp_path: Path) -> None:
    long_title = "[" + "t" * 180 + "] {order_number} {status} {total} {date}"
    tenv = await env(
        tmp_path,
        {"my_orders": {"orders_per_page": 9}},
        content={k.ORDER_TITLE: long_title, k.LIST_DESCRIPTION: "[d] " * 900},
    )
    try:
        from shopbot.features.my_orders.preview import LONG_TRACKING, sample_orders

        items = sample_orders(tenv.app.settings, [m.REFUNDED] * 30, tracking=LONG_TRACKING)
        split = views.paginate(tenv.app.ui, tenv.app.settings, items)
        assert sum(len(page) for page in split.pages) == 30  # every order on exactly one page
        for page in range(len(split.pages)):
            spec = views.my_orders_message(tenv.app.ui, tenv.app.settings, items, page=page, user_id=1)
            assert validate_message(spec) == [], page
        assert len(split.pages) > 30 // 9  # fewer than 9 per page: the configured text needs the room
    finally:
        await tenv.close()


def test_every_stage_has_a_customer_name() -> None:
    settings = make_settings({"features": {"my_orders": True}})
    declared = {key.key for key in ck.content_keys(settings)}
    assert all(ck.stage_key(status) in declared for status in m.ORDER_STATUSES)
