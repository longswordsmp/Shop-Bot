"""``/discount`` (catalog managers and admins): create with every validation, list, end (audited; a code can be made
again once it ended), permissions, and the switched-off setting."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import discord
import pytest
from discord import app_commands

from shopbot.features.checkout import discount_repo
from shopbot.features.checkout import discounts as d
from tests.checkout.util import add_product, audit_actions, money
from tests.fakes import FakeInteraction, FakeMember
from tests.helpers import GUILD_ID, TestEnv


def _command(tenv: TestEnv, name: str) -> app_commands.Command[Any, ..., Any]:
    assert tenv.app.bot is not None
    group = tenv.app.bot.tree.get_command("discount", guild=tenv.app.guild_object)
    assert isinstance(group, app_commands.Group)
    command = group.get_command(name)
    assert isinstance(command, app_commands.Command)
    return command


async def run_discount(tenv: TestEnv, name: str, *, user: FakeMember, **kwargs: Any) -> FakeInteraction:
    """Invoke a /discount subcommand like discord.py does: checks first, then the callback."""
    from shopbot.commands import on_tree_error

    command = _command(tenv, name)
    interaction = FakeInteraction(
        client=tenv.app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        if not await command._check_can_run(interaction):  # type: ignore[arg-type]
            raise app_commands.CheckFailure("check failed")
        await command.callback(interaction, **kwargs)  # type: ignore[arg-type]
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    return interaction


def choice(kind: str) -> app_commands.Choice[str]:
    from shopbot.features.checkout.commands import DISCOUNT_TYPES

    return next(c for c in DISCOUNT_TYPES if c.value == kind)


async def create(tenv: TestEnv, user: FakeMember, code: str, kind: str = d.PERCENT, **kwargs: Any) -> FakeInteraction:
    defaults: dict[str, Any] = {
        "value": None,
        "products": None,
        "min_subtotal": None,
        "max_uses": None,
        "expires_in_days": None,
        "once_per_customer": True,
    }
    return await run_discount(tenv, "create", user=user, code=code, kind=choice(kind), **{**defaults, **kwargs})


def title(interaction: FakeInteraction) -> str:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].title or ""


def body(interaction: FakeInteraction) -> str:
    return "\n".join(message.text() for message in interaction.sent)


async def codes(tenv: TestEnv) -> list[d.Code]:
    usable, ended = await tenv.app.db.run(discount_repo.list_codes, GUILD_ID, tenv.clock.now())
    return [s.code for s in (*usable, *ended)]


def test_the_command_is_for_catalog_managers() -> None:
    from shopbot.features.checkout.commands import build_discount_group

    group = build_discount_group()
    assert group.default_permissions is not None and group.default_permissions.manage_guild
    assert {c.name for c in group.commands} == {"create", "list", "end"}
    create_command = group.get_command("create")
    assert isinstance(create_command, app_commands.Command)
    kind = next(p for p in create_command.parameters if p.name == "kind")
    assert kind.display_name == "type"
    assert [c.name for c in kind.choices] == ["Percent off", "Amount off", "Buy one, get one free"]


async def test_customers_and_staff_without_the_manager_role_are_refused(co: TestEnv) -> None:
    for user in (co.member(), co.member(name="staff", staff=True)):
        await create(co, user, "SAVE10", value="10")
        assert await codes(co) == []
    admin = co.member(name="admin", admin=True)
    await create(co, admin, "SAVE10", value="10")
    assert [c.code for c in await codes(co)] == ["SAVE10"]


async def test_create_percent_amount_and_buy_one_get_one_free(co: TestEnv) -> None:
    admin = co.member(name="admin", admin=True)
    shirt = await add_product(co, "[shirt]", price=1000)
    hat = await add_product(co, "[hat]", price=500)
    reply = await create(co, admin, " save10 ", value="10%", min_subtotal="20.00", max_uses=100, expires_in_days=7)
    assert title(reply) == co.app.op("checkout.discounts.created", code="SAVE10")
    terms = body(reply)
    for part in (
        co.app.op("checkout.discounts.terms.percent", percent="10"),
        co.app.op("checkout.discounts.terms.all_items"),
        co.app.op("checkout.discounts.terms.minimum", minimum=money(co, 2000)),
        co.app.op("checkout.discounts.terms.max_uses", max=100),
        co.app.op("checkout.discounts.terms.once"),
    ):
        assert part in terms
    await create(co, admin, "FIVE", d.AMOUNT, value="5.00", once_per_customer=False)
    await create(co, admin, "BOGO", d.BOGO, products=f"{shirt.id}, {hat.id}")
    made = {c.code: c for c in await codes(co)}
    assert (made["SAVE10"].kind, made["SAVE10"].value, made["SAVE10"].min_subtotal_minor) == (d.PERCENT, 1000, 2000)
    assert made["SAVE10"].expires_at == co.clock.now() + 7 * 86_400 and made["SAVE10"].max_uses == 100
    assert (made["FIVE"].kind, made["FIVE"].value, made["FIVE"].once_per_customer) == (d.AMOUNT, 500, False)
    assert (made["BOGO"].kind, made["BOGO"].value) == (d.BOGO, 0)
    assert made["BOGO"].product_ids == frozenset({shirt.id, hat.id})
    assert (await audit_actions(co)).count("checkout.discount_created") == 3


@pytest.mark.parametrize(
    ("code", "kind", "options", "key"),
    [
        ("AB", d.PERCENT, {"value": "10"}, "checkout.discounts.code_invalid"),
        ("SAVE 10", d.PERCENT, {"value": "10"}, "checkout.discounts.code_invalid"),
        ("SAVE10", d.PERCENT, {}, "checkout.discounts.value_missing"),
        ("SAVE10", d.PERCENT, {"value": "0"}, "checkout.discounts.percent_invalid"),
        ("SAVE10", d.PERCENT, {"value": "101"}, "checkout.discounts.percent_invalid"),
        ("SAVE10", d.PERCENT, {"value": "12.5"}, "checkout.discounts.percent_invalid"),
        ("SAVE10", d.AMOUNT, {"value": "abc"}, "checkout.discounts.amount_invalid"),
        ("SAVE10", d.AMOUNT, {"value": "0"}, "checkout.discounts.amount_zero"),
        ("SAVE10", d.BOGO, {"value": "1"}, "checkout.discounts.bogo_value"),
        ("SAVE10", d.PERCENT, {"value": "10", "products": "shirt"}, "checkout.discounts.products_invalid"),
        ("SAVE10", d.PERCENT, {"value": "10", "products": "999"}, "checkout.discounts.products_unknown"),
        ("SAVE10", d.PERCENT, {"value": "10", "min_subtotal": "x"}, "checkout.discounts.minimum_invalid"),
    ],
)
async def test_create_refuses_what_is_not_valid(
    co: TestEnv, code: str, kind: str, options: dict[str, Any], key: str
) -> None:
    admin = co.member(name="admin", admin=True)
    reply = await create(co, admin, code, kind, **options)
    expected = co.app.op(key).split("{")[0]
    assert title(reply).startswith(expected), (title(reply), expected)
    assert await codes(co) == []


async def test_a_code_exists_once_until_it_ended(co: TestEnv) -> None:
    admin = co.member(name="admin", admin=True)
    await create(co, admin, "SAVE10", value="10")
    taken = await create(co, admin, "save10", value="20")
    assert title(taken) == co.app.op("checkout.discounts.code_taken", code="SAVE10")
    ended = await run_discount(co, "end", user=admin, code="save10")
    assert title(ended) == co.app.op("checkout.discounts.ended", code="SAVE10")
    again = await create(co, admin, "SAVE10", value="20")
    assert title(again) == co.app.op("checkout.discounts.created", code="SAVE10")
    found = await co.app.db.run(discount_repo.find_code, GUILD_ID, "SAVE10")
    assert found is not None and found.value == 2000 and found.ended_at is None
    missing = await run_discount(co, "end", user=admin, code="NOPE")
    assert title(missing) == co.app.op("checkout.discounts.not_found", code="NOPE")
    assert (await audit_actions(co)).count("checkout.discount_ended") == 1


async def test_list_shows_active_codes_then_the_latest_ended(co: TestEnv) -> None:
    admin = co.member(name="admin", admin=True)
    shirt = await add_product(co, "[shirt]", price=1000)
    empty = await run_discount(co, "list", user=admin)
    assert co.app.op("checkout.discounts.list.none") in body(empty)
    await create(co, admin, "BOGO", d.BOGO, products=str(shirt.id))
    await create(co, admin, "OLD", value="5")
    await run_discount(co, "end", user=admin, code="OLD")
    reply = await run_discount(co, "list", user=admin)
    text = body(reply)
    assert title(reply) == co.app.op("checkout.discounts.list.title")
    assert (
        text.index("**BOGO**") < text.index(co.app.op("checkout.discounts.list.ended", count=1)) < text.index("**OLD**")
    )
    assert co.app.op("checkout.discounts.terms.bogo") in text
    assert co.app.op("checkout.discounts.terms.products", products="[shirt]") in text
    assert co.app.op("checkout.discounts.list.uses", uses=0) in text


async def test_switched_off(co: TestEnv) -> None:
    admin = co.member(name="admin", admin=True)
    co.app.settings = replace(co.app.settings, checkout=replace(co.app.settings.checkout, discount_codes=False))
    off = co.app.op("checkout.discounts.off")
    assert title(await create(co, admin, "SAVE10", value="10")) == off
    assert title(await run_discount(co, "list", user=admin)) == off
    assert title(await run_discount(co, "end", user=admin, code="SAVE10")) == off
    assert await codes(co) == []
