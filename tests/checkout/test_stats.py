"""/stats: sales of a period (only orders whose payment was confirmed; refunds taken off), the open orders that need
staff now (any age), best sellers, orders in another currency noted; staff only."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from shopbot.commands import on_tree_error
from shopbot.features import core_keys as ck
from shopbot.features.checkout import models as m
from shopbot.features.checkout import stats
from shopbot.features.checkout.order_money import order_money
from tests.checkout.util import add_product, place_order
from tests.fakes import FakeInteraction, FakeMember
from tests.helpers import GUILD_ID, TestEnv

DAY = 86_400


async def _set(tenv: TestEnv, order_id: int, status: str, *, refunded: int = 0, currency: str | None = None) -> None:
    def write(conn: Any) -> None:
        conn.execute("UPDATE orders SET status = ?, refunded_minor = ? WHERE id = ?", (status, refunded, order_id))
        if currency is not None:
            conn.execute("UPDATE orders SET currency = ? WHERE id = ?", (currency, order_id))

    await tenv.app.db.run(write)


async def _collect(tenv: TestEnv, since: int | None = None) -> stats.Stats:
    return await tenv.app.db.run(stats.collect, GUILD_ID, tenv.app.settings.checkout.currency, since)


async def test_a_new_shop_has_nothing_to_count(co: TestEnv) -> None:
    empty = await _collect(co)
    assert (empty.placed, empty.sold, empty.revenue_minor, empty.best, empty.other_currency) == (0, 0, 0, (), 0)
    assert empty.average_minor is None
    spec = await stats.stats_reply(co.app, "7d")
    text = spec.embeds[0].description or ""
    assert co.app.op("checkout.stats.line.none") in text
    assert spec.embeds[0].title == co.app.op("checkout.stats.title", period=co.app.op("checkout.stats.period.week"))


async def test_sales_count_only_confirmed_payments_and_take_refunds_off(co: TestEnv) -> None:
    cheap = await add_product(co, "[cheap]", price=500)
    dear = await add_product(co, "[dear]", price=1000)
    a, _, _ = await place_order(co, co.member(name="a"), [(dear.id, 2)])
    b, _, _ = await place_order(co, co.member(name="b"), [(dear.id, 1), (cheap.id, 3)])
    c, _, _ = await place_order(co, co.member(name="c"), [(cheap.id, 1)])
    d, _, _ = await place_order(co, co.member(name="d"), [(cheap.id, 2)])
    await _set(co, a.id, m.PAID)
    await _set(co, b.id, m.FULFILLED, refunded=200)
    await _set(co, c.id, m.CANCELLED)  # d stays waiting for payment
    counted = await _collect(co)
    assert counted.placed == 4 and counted.sold == 2 and counted.ended_unsold == 1
    assert counted.revenue_minor == a.total_minor + b.total_minor - 200 and counted.refunded_minor == 200
    assert counted.average_minor == counted.revenue_minor // 2
    assert counted.open_counts == {"pending": 1, "submitted": 0, "to_ship": 1, "shipped": 0}
    # Same quantity (3 each): the larger amount first; the cancelled and unpaid orders do not count.
    assert [(s.name, s.quantity, s.total_minor) for s in counted.best] == [("[dear]", 3, 3000), ("[cheap]", 3, 1500)]
    money = order_money(co.app.ui, co.app.settings, counted.currency)
    text = stats.stats_message(co.app.ui, co.app.op, money, counted, "all").embeds[0].description or ""
    assert (
        co.app.op(
            "checkout.stats.line.revenue",
            revenue=money(counted.revenue_minor),
            average=money(counted.revenue_minor // 2),
        )
        in text
    )
    assert co.app.op("checkout.stats.line.refunded", amount=money(200)) in text
    assert co.app.op("checkout.stats.line.best", rank=1, name="[dear]", quantity=3, total=money(3000)) in text
    assert d.status == m.PENDING


async def test_the_period_limits_sales_but_open_orders_count_at_any_age(co: TestEnv) -> None:
    item = await add_product(co, "[item]", price=700)
    old, _, _ = await place_order(co, co.member(name="old"), [(item.id, 1)])
    await _set(co, old.id, m.PAID)  # still to ship, placed long ago
    co.clock.advance(10 * DAY)
    new, _, _ = await place_order(co, co.member(name="new"), [(item.id, 2)])
    await _set(co, new.id, m.SHIPPED)
    week = await _collect(co, since=co.clock.now() - 7 * DAY)
    assert week.placed == 1 and week.sold == 1 and week.revenue_minor == new.total_minor
    assert [(s.name, s.quantity) for s in week.best] == [("[item]", 2)]
    assert week.open_counts == {"pending": 0, "submitted": 0, "to_ship": 1, "shipped": 1}
    everything = await _collect(co)
    assert everything.placed == 2 and everything.revenue_minor == old.total_minor + new.total_minor


async def test_orders_in_another_currency_are_noted_not_added(co: TestEnv) -> None:
    item = await add_product(co, "[item]", price=700)
    placed, _, _ = await place_order(co, co.member(name="x"), [(item.id, 1)])
    await _set(co, placed.id, m.PAID, currency="EUR")
    counted = await _collect(co)
    assert counted.placed == 0 and counted.sold == 0 and counted.other_currency == 1
    money = order_money(co.app.ui, co.app.settings, counted.currency)
    text = stats.stats_message(co.app.ui, co.app.op, money, counted, "all").embeds[0].description or ""
    assert co.app.op("checkout.stats.other_currency", count=1, currency=counted.currency) in text


async def _invoke(tenv: TestEnv, user: FakeMember, **kwargs: Any) -> FakeInteraction:
    assert tenv.app.bot is not None
    command = tenv.app.bot.tree.get_command("stats", guild=tenv.app.guild_object)
    assert isinstance(command, app_commands.Command)
    interaction = FakeInteraction(
        client=tenv.app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        allowed = await command._check_can_run(interaction)  # type: ignore[arg-type]
    except app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    assert allowed
    await command.callback(interaction, **kwargs)
    return interaction


async def test_the_command_answers_staff_privately_and_refuses_customers(co: TestEnv) -> None:
    staffer = co.member(name="staffer", staff=True)
    answered = await _invoke(co, staffer, period="30d")
    sent = answered.last_sent
    assert sent is not None and getattr(sent, "ephemeral", False)
    assert sent.embeds[0].title == co.app.op("checkout.stats.title", period=co.app.op("checkout.stats.period.month"))
    customer = co.member(name="customer")
    refused = await _invoke(co, customer, period="30d")
    assert refused.last_sent is not None
    assert refused.last_sent.embeds[0].description == co.text(ck.ERROR_PERMISSION)
