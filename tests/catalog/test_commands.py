"""/product commands and the staff product forms: permissions, flows, validation, idempotency, audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.features.catalog import repo, service
from shopbot.features.catalog import views as catalog_views
from shopbot.ui.discord_render import to_modal
from shopbot.ui.limits import validate_modal
from tests.catalog.util import (
    add_product,
    audit_actions,
    buttons,
    get,
    invoke,
    listing_channel,
    manager_env,
    product_command,
)
from tests.fakes import FakeInteraction, FakeMember, click, modal_input_ids, modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv

FORM = {
    "name": "[new product]",
    "price": "1,234.50",
    "stock": "7",
    "max_per_order": "2",
    "description": "[new description]",
}


def _title(interaction: FakeInteraction) -> str | None:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].title


def _all_text(interaction: FakeInteraction) -> str:
    return "\n".join(m.text() for m in interaction.sent)


async def _products(tenv: TestEnv) -> list[repo.Product]:
    return await tenv.app.db.run(repo.list_products, GUILD_ID, include_inactive=True, include_archived=True)


# --------------------------------------------------------------------------------------------------
# Command metadata and permissions
# --------------------------------------------------------------------------------------------------


async def test_group_metadata(tenv: TestEnv) -> None:
    assert tenv.app.bot is not None
    group = tenv.app.bot.tree.get_command("product", guild=tenv.app.guild_object)
    assert group is not None
    assert group.default_permissions == discord.Permissions(manage_guild=True)
    assert group.guild_only is True
    names = {c.name for c in group.commands}
    assert names == {
        "add",
        "edit",
        "price",
        "stock",
        "image",
        "toggle",
        "archive",
        "remove",
        "sort",
        "view",
        "list",
        "categories",
    }
    for command in group.commands:
        assert command.description and all(p.description for p in command.parameters)
        assert len(command.checks) == 2


@pytest.mark.parametrize("staff", [False, True])
async def test_customers_and_non_manager_staff_are_refused(tenv: TestEnv, staff: bool) -> None:
    product = await add_product(tenv, price=100)
    user = tenv.member(staff=staff)
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("add", {}),
        ("edit", {"product_id": product.id}),
        ("price", {"product_id": product.id, "amount": "5"}),
        ("stock", {"product_id": product.id, "mode": "set", "amount": 3}),
        ("toggle", {"product_id": product.id}),
        ("archive", {"product_id": product.id, "confirm": True}),
        ("list", {}),
    ]
    for name, kwargs in attempts:
        interaction = await invoke(tenv, user, name, **kwargs)
        assert interaction.response.modal is None
        assert interaction.last_sent is not None
        assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    assert await get(tenv, product.id) == product
    assert await audit_actions(tenv, "catalog.product_") == []


async def test_manager_role_without_staff_role_may_manage(tmp_path: Path) -> None:
    tenv, role = await manager_env(tmp_path)
    try:
        manager = tenv.guild.add_member(name="manager", roles=[role])
        interaction = await invoke(tenv, manager, "list")
        assert _title(interaction) == tenv.app.op("catalog.list.title", total=0)
        general_staff = tenv.member(staff=True)
        denied = await invoke(tenv, general_staff, "list")
        assert denied.last_sent is not None
        assert denied.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    finally:
        await tenv.close()


async def test_forged_form_submissions_are_refused(tenv: TestEnv) -> None:
    product = await add_product(tenv)
    customer, staff = tenv.member(), tenv.member(staff=True)
    for user in (customer, staff):
        added = await submit_modal(tenv.app, user=user, custom_id="sb:ct:addform:1", fields=FORM)
        edited = await submit_modal(
            tenv.app, user=user, custom_id=f"sb:ct:editform:{product.id}:{product.version}", fields=FORM
        )
        for interaction in (added, edited):
            assert interaction.last_sent is not None
            assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    assert [p.id for p in await _products(tenv)] == [product.id]
    assert (await get(tenv, product.id)).name == product.name


async def test_commands_refuse_when_catalog_not_ready(tenv: TestEnv) -> None:
    tenv.app.readiness.statuses["catalog"].ready = False
    admin = tenv.member(admin=True)
    for name, kwargs in (("add", {}), ("list", {}), ("toggle", {"product_id": 1})):
        interaction = await invoke(tenv, admin, name, **kwargs)
        assert _title(interaction) == tenv.app.op("command.feature_disabled", feature="catalog")
        assert interaction.response.modal is None


# --------------------------------------------------------------------------------------------------
# Add / edit forms
# --------------------------------------------------------------------------------------------------


async def _open_add_form(tenv: TestEnv, user: FakeMember) -> tuple[FakeInteraction, discord.ui.Modal]:
    interaction = await invoke(tenv, user, "add")
    modal = modal_of(interaction)
    assert modal is not None
    return interaction, modal


async def test_add_form_creates_product_audits_and_refreshes_listing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    admin = tenv.member(admin=True)
    interaction, modal = await _open_add_form(tenv, admin)
    assert modal.custom_id == f"sb:ct:addform:{interaction.id}"
    assert modal.title == tenv.app.op("catalog.form.add_title")
    assert modal_input_ids(modal) == ["name", "price", "stock", "max_per_order", "description"]

    submitted = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    assert submitted.response_kinds() == ["defer"]
    assert submitted.last_sent is not None and getattr(submitted.last_sent, "ephemeral", False)
    [product] = await _products(tenv)
    assert _title(submitted) == tenv.app.op("catalog.cmd.created", product_id=product.id)
    assert (product.name, product.price_minor, product.stock_on_hand, product.max_per_order) == (
        "[new product]",
        123450,
        7,
        2,
    )
    assert product.description == "[new description]"
    listing_field = submitted.last_sent.embeds[0].fields[0]
    assert listing_field.name == tenv.app.op("catalog.refresh.field")
    assert len(channel.messages) == 2 and channel.messages[1].embeds[0].title == "[new product]"
    [event] = await audit_actions(tenv, "catalog.product_")
    assert (event.action, event.actor_id, event.target_id) == ("catalog.product_add", admin.id, str(product.id))


async def test_duplicate_add_submission_creates_one_product(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    _, modal = await _open_add_form(tenv, admin)
    await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    again = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    products = await _products(tenv)
    assert len(products) == 1
    assert _title(again) == tenv.app.op("catalog.cmd.already_created", product_id=products[0].id)
    assert len(await audit_actions(tenv, "catalog.product_add")) == 1


@pytest.mark.parametrize(
    ("field", "value", "reason_key"),
    [
        ("name", "   ", "catalog.invalid.name"),
        ("price", "12.345", None),
        ("price", "abc", None),
        ("price", "-1", None),
        ("price", "1,23", None),
        ("stock", "-3", "catalog.invalid.stock"),
        ("stock", "2.5", "catalog.invalid.stock"),
        ("max_per_order", "0", "catalog.invalid.max_per_order"),
        ("description", "x" * 4001, "catalog.invalid.description"),
    ],
)
async def test_add_form_validation(tenv: TestEnv, field: str, value: str, reason_key: str | None) -> None:
    admin = tenv.member(admin=True)
    _, modal = await _open_add_form(tenv, admin)
    submitted = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields={**FORM, field: value})
    assert _title(submitted) == tenv.app.op("catalog.cmd.invalid")
    assert submitted.last_sent is not None
    description = submitted.last_sent.embeds[0].description or ""
    if reason_key is not None:
        assert tenv.app.op(reason_key, max=repo.STOCK_MAX if field == "stock" else repo.MAX_PER_ORDER_MAX) in (
            description
        )
    else:
        assert description.startswith("Price:")
    assert await _products(tenv) == []


async def test_blank_stock_and_limit_mean_unlimited_and_none(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    _, modal = await _open_add_form(tenv, admin)
    fields = {**FORM, "stock": "", "max_per_order": " ", "description": ""}
    await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=fields)
    [product] = await _products(tenv)
    assert (product.stock_on_hand, product.max_per_order, product.description) == (None, None, "")


async def test_edit_form_prefills_and_saves(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[old]", price=1050, stock=4, max_per_order=3, description="[d]")
    interaction = await invoke(tenv, admin, "edit", product_id=product.id)
    modal = modal_of(interaction)
    assert modal is not None
    assert modal.custom_id == f"sb:ct:editform:{product.id}:{product.version}"
    defaults = {
        item.custom_id: item.default for item in modal.walk_children() if isinstance(item, discord.ui.TextInput)
    }
    assert defaults == {"name": "[old]", "price": "10.50", "stock": "4", "max_per_order": "3", "description": "[d]"}

    fields = {**defaults, "name": "[renamed]", "price": "11"}
    saved = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=fields)  # type: ignore[arg-type]
    assert _title(saved) == tenv.app.op("catalog.cmd.updated", product_id=product.id)
    updated = await get(tenv, product.id)
    assert (updated.name, updated.price_minor, updated.version) == ("[renamed]", 1100, product.version + 1)
    [event] = await audit_actions(tenv, "catalog.product_edit")
    assert event.details == {"name": ["[old]", "[renamed]"], "price_minor": [1050, 1100]}

    # Submitting the same form again: the values are already saved, nothing changes.
    again = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=fields)  # type: ignore[arg-type]
    assert _title(again) == tenv.app.op("catalog.cmd.unchanged", product_id=product.id)
    assert (await get(tenv, product.id)).version == updated.version


async def test_stale_edit_is_rejected(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[p]", price=100, stock=5)
    stale_id = f"sb:ct:editform:{product.id}:{product.version}"
    await tenv.app.db.transaction(repo.reserve, product.id, 1, 9)  # an order changed the product meanwhile
    fields = {"name": "[changed]", "price": "1.00", "stock": "5", "max_per_order": "", "description": ""}
    stale = await submit_modal(tenv.app, user=admin, custom_id=stale_id, fields=fields)
    assert _title(stale) == tenv.app.op("catalog.cmd.stale", product_id=product.id)
    assert (await get(tenv, product.id)).name == "[p]"


async def test_edit_cannot_set_stock_below_reserved(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[p]", price=100, stock=5, reserved=4)
    fields = {"name": "[p]", "price": "1.00", "stock": "3", "max_per_order": "", "description": ""}
    result = await submit_modal(
        tenv.app, user=admin, custom_id=f"sb:ct:editform:{product.id}:{product.version}", fields=fields
    )
    assert _title(result) == tenv.app.op("catalog.cmd.invalid")
    assert (await get(tenv, product.id)).stock_on_hand == 5


async def test_edit_unknown_or_archived_product(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    missing = await invoke(tenv, admin, "edit", product_id=424242)
    assert modal_of(missing) is None and _title(missing) == tenv.app.op("catalog.cmd.not_found", product_id=424242)
    product = await add_product(tenv)
    await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=1, archived=True)
    archived = await invoke(tenv, admin, "edit", product_id=product.id)
    assert _title(archived) == tenv.app.op("catalog.cmd.archived_product", product_id=product.id)


async def test_edit_form_of_another_guilds_product_is_not_found(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    foreign = await tenv.app.db.transaction(repo.create_product, GUILD_ID + 1, name="[x]", price_minor=1, now=1)
    fields = {"name": "[hijack]", "price": "1", "stock": "", "max_per_order": "", "description": ""}
    result = await submit_modal(
        tenv.app, user=admin, custom_id=f"sb:ct:editform:{foreign.id}:{foreign.version}", fields=fields
    )
    assert _title(result) == tenv.app.op("catalog.cmd.not_found", product_id=foreign.id)


# --------------------------------------------------------------------------------------------------
# Single-field commands
# --------------------------------------------------------------------------------------------------


async def test_price_command(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, price=100)
    ok = await invoke(tenv, admin, "price", product_id=product.id, amount="1,250.5")
    assert ok.response_kinds() == ["defer"]
    assert ok.response.calls[0][1] == {"ephemeral": True, "thinking": True}
    money = tenv.text(ck.MONEY_FORMAT).replace("{amount}", "1,250.50").replace("{currency}", "USD")
    assert _title(ok) == tenv.app.op("catalog.cmd.price_set", product_id=product.id, price=money)
    assert (await get(tenv, product.id)).price_minor == 125050
    for bad in ("12.345", "1.2.3", "-5", "ten", "1,2345"):
        rejected = await invoke(tenv, admin, "price", product_id=product.id, amount=bad)
        assert _title(rejected) == tenv.app.op("catalog.cmd.invalid")
    assert (await get(tenv, product.id)).price_minor == 125050
    [event] = await audit_actions(tenv, "catalog.product_price")
    assert event.details == {"price_minor": [100, 125050]}


async def test_price_with_custom_separators_and_zero_exponent(tmp_path: Path) -> None:
    from tests.helpers import make_settings, make_test_env

    settings = make_settings({"checkout": {"currency": "JPY", "decimal_separator": ",", "thousands_separator": "."}})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        admin = tenv.member(admin=True)
        product = await add_product(tenv, price=1)
        await invoke(tenv, admin, "price", product_id=product.id, amount="1.500")
        assert (await get(tenv, product.id)).price_minor == 1500
        rejected = await invoke(tenv, admin, "price", product_id=product.id, amount="15,5")
        assert _title(rejected) == tenv.app.op("catalog.cmd.invalid")
    finally:
        await tenv.close()


async def test_stock_command_modes_and_reserved_guard(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, stock=10, reserved=4)
    op = tenv.app.op

    below = await invoke(tenv, admin, "stock", product_id=product.id, mode="set", amount=3)
    assert below.last_sent is not None
    assert below.last_sent.embeds[0].description == op("catalog.cmd.stock_below_reserved", reserved=4, requested=3)
    remove_too_many = await invoke(tenv, admin, "stock", product_id=product.id, mode="add", amount=-7)
    assert _title(remove_too_many) == op("catalog.cmd.invalid")
    assert (await get(tenv, product.id)).stock_on_hand == 10

    added = await invoke(tenv, admin, "stock", product_id=product.id, mode="add", amount=5)
    assert _title(added) == op("catalog.cmd.stock_added", product_id=product.id, delta="+5", stock=15)
    set_ = await invoke(tenv, admin, "stock", product_id=product.id, mode="set", amount=4)
    assert _title(set_) == op("catalog.cmd.stock_set", product_id=product.id, stock=4)
    unlimited = await invoke(tenv, admin, "stock", product_id=product.id, mode="unlimited")
    assert _title(unlimited) == op("catalog.cmd.stock_unlimited", product_id=product.id)
    cannot_add = await invoke(tenv, admin, "stock", product_id=product.id, mode="add", amount=1)
    assert _title(cannot_add) == op("catalog.cmd.stock_is_unlimited", product_id=product.id)
    missing_amount = await invoke(tenv, admin, "stock", product_id=product.id, mode="set")
    assert missing_amount.last_sent is not None
    assert missing_amount.last_sent.embeds[0].description == op("catalog.cmd.stock_amount_required")
    zero = await invoke(tenv, admin, "stock", product_id=product.id, mode="add", amount=0)
    assert zero.last_sent is not None and zero.last_sent.embeds[0].description == op("catalog.cmd.stock_add_zero")

    final = await get(tenv, product.id)
    assert final.stock_on_hand is None and final.reserved == 4
    modes = [e.details["mode"] for e in await audit_actions(tenv, "catalog.product_stock")]
    assert modes == ["add", "set", "unlimited"]


async def test_image_command_validation_and_clearing(tmp_path: Path) -> None:
    tenv, _ = await manager_env(
        tmp_path, {"catalog": {"manager_roles": ["catalog_manager"], "image_hosts": ["img.example.com"]}}
    )
    try:
        admin = tenv.member(admin=True)
        product = await add_product(tenv)
        op = tenv.app.op
        for bad in (
            "http://img.example.com/a.png",
            "https://img.example.com/a b.png",
            "https://user:pw@img.example.com/a",
        ):
            rejected = await invoke(tenv, admin, "image", product_id=product.id, url=bad)
            assert rejected.last_sent is not None
            assert rejected.last_sent.embeds[0].description == op("catalog.cmd.image_invalid")
        other_host = await invoke(tenv, admin, "image", product_id=product.id, url="https://evil.example.net/a.png")
        assert other_host.last_sent is not None
        assert other_host.last_sent.embeds[0].description == op(
            "catalog.cmd.image_host_not_allowed", hosts="img.example.com"
        )
        assert (await get(tenv, product.id)).image_url is None

        url = "https://IMG.example.com/products/a.png?v=2"
        ok = await invoke(tenv, admin, "image", product_id=product.id, url=url)
        assert _title(ok) == op("catalog.cmd.image_set", product_id=product.id)
        assert (await get(tenv, product.id)).image_url == url
        cleared = await invoke(tenv, admin, "image", product_id=product.id)
        assert _title(cleared) == op("catalog.cmd.image_cleared", product_id=product.id)
        assert (await get(tenv, product.id)).image_url is None
    finally:
        await tenv.close()


async def test_toggle_hides_and_shows_product_in_listing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[toggled]")
    off = await invoke(tenv, admin, "toggle", product_id=product.id)
    assert _title(off) == tenv.app.op("catalog.cmd.deactivated", product_id=product.id)
    assert "[toggled]" not in channel.messages[0].text()
    on = await invoke(tenv, admin, "toggle", product_id=product.id)
    assert _title(on) == tenv.app.op("catalog.cmd.activated", product_id=product.id)
    assert "[toggled]" in channel.messages[1].text()
    assert [e.details for e in await audit_actions(tenv, "catalog.product_toggle")] == [
        {"active": False},
        {"active": True},
    ]


async def test_archive_requires_confirmation_and_no_reservations(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, stock=5, reserved=2)
    op = tenv.app.op
    unconfirmed = await invoke(tenv, admin, "archive", product_id=product.id)
    assert _title(unconfirmed) == op("catalog.cmd.archive_confirm", product_id=product.id)
    blocked = await invoke(tenv, admin, "archive", product_id=product.id, confirm=True)
    assert _title(blocked) == op("catalog.cmd.archive_reserved", product_id=product.id, reserved=2)
    assert not (await get(tenv, product.id)).archived

    await tenv.app.db.transaction(repo.release, product.id, 2, 5)
    done = await invoke(tenv, admin, "archive", product_id=product.id, confirm=True)
    assert _title(done) == op("catalog.cmd.archived", product_id=product.id)
    archived = await get(tenv, product.id)
    assert archived.archived
    assert not await tenv.app.db.transaction(repo.reserve, product.id, 1, 6)
    twice = await invoke(tenv, admin, "archive", product_id=product.id, confirm=True)
    assert _title(twice) == op("catalog.cmd.archived", product_id=product.id)
    assert len(await audit_actions(tenv, "catalog.product_archive")) == 1
    frozen = await invoke(tenv, admin, "price", product_id=product.id, amount="3")
    assert _title(frozen) == op("catalog.cmd.archived_product", product_id=product.id)


async def test_sort_command_reorders_listing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    admin = tenv.member(admin=True)
    first = await add_product(tenv, "[first]")
    second = await add_product(tenv, "[second]")
    result = await invoke(tenv, admin, "sort", product_id=second.id, position=-5)
    assert _title(result) == tenv.app.op("catalog.cmd.sorted", product_id=second.id, position=-5)
    assert [e.title for e in channel.messages[1].embeds] == ["[second]", "[first]"]
    assert first.sort_order == 0


async def test_unknown_product_is_reported(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    for name, kwargs in (
        ("price", {"amount": "1"}),
        ("toggle", {}),
        ("sort", {"position": 1}),
        ("archive", {"confirm": True}),
        ("stock", {"mode": "set", "amount": 1}),
        ("image", {"url": ""}),
    ):
        interaction = await invoke(tenv, admin, name, product_id=777, **kwargs)
        assert _title(interaction) == tenv.app.op("catalog.cmd.not_found", product_id=777)


async def test_listing_failure_after_change_is_reported_not_raised(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, price=100)  # no listing channel provisioned
    interaction = await invoke(tenv, admin, "price", product_id=product.id, amount="2")
    assert interaction.last_sent is not None
    embed = interaction.last_sent.embeds[0]
    money = tenv.text(ck.MONEY_FORMAT).replace("{amount}", "2.00").replace("{currency}", "USD")
    assert embed.title == tenv.app.op("catalog.cmd.price_set", product_id=product.id, price=money)
    assert embed.fields[0].value == tenv.app.op("catalog.refresh.channel_missing", channel="catalog")
    assert (await get(tenv, product.id)).price_minor == 200


# --------------------------------------------------------------------------------------------------
# View / list / autocomplete
# --------------------------------------------------------------------------------------------------


async def test_view_shows_details_and_customer_card(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[viewed]", price=500, stock=3, reserved=1, description="[d]")
    interaction = await invoke(tenv, admin, "view", product_id=product.id)
    assert len(interaction.sent) == 2
    details, card = interaction.sent
    assert details.embeds[0].title == tenv.app.op("catalog.view.title", product_id=product.id)
    values = {f.name: f.value for f in details.embeds[0].fields}
    assert values[tenv.app.op("catalog.view.available")] == "2"
    assert card.embeds[0].title == "[viewed]"
    assert all(getattr(m, "ephemeral", False) for m in interaction.sent)


async def test_list_paginates_with_manager_buttons(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    for i in range(catalog_views.ADMIN_PAGE_SIZE + 2):
        await add_product(tenv, f"[p{i}]")
    archived = await add_product(tenv, "[gone]")
    await tenv.app.db.transaction(repo.update_product, archived.id, archived.version, now=1, archived=True)
    interaction = await invoke(tenv, admin, "list")
    message = interaction.last_sent
    assert message is not None
    assert message.embeds[0].title == tenv.app.op("catalog.list.title", total=12)
    assert message.embeds[0].footer.text == tenv.app.op("catalog.list.page", page=1, pages=2)
    assert "[gone]" not in message.text()
    assert buttons(message) == [
        ("sb:ct:apage:0:0", tenv.app.op("catalog.list.previous"), True),
        ("sb:ct:apage:1:0", tenv.app.op("catalog.list.next"), False),
    ]
    page_two = await click(tenv.app, user=admin, custom_id="sb:ct:apage:1:0", message=message)
    assert page_two.response_kinds() == ["edit_message"]
    assert "[p11]" in message.text() and "[p0]" not in message.text()

    staff = tenv.member(staff=True)
    denied = await click(tenv.app, user=staff, custom_id="sb:ct:apage:0:0", message=message)
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    customer = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:apage:0:0", message=message)
    assert customer.last_sent is not None
    assert customer.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)

    with_archived = await invoke(tenv, admin, "list", include_archived=True)
    assert with_archived.last_sent is not None
    assert with_archived.last_sent.embeds[0].title == tenv.app.op("catalog.list.title_archived", total=13)


async def test_autocomplete_suggests_products_to_managers_only(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    product = await add_product(tenv, "[findable]")
    command = product_command(tenv.app, "price")
    callback = command._params["product_id"].autocomplete
    admin_interaction = FakeInteraction(client=tenv.bot, user=admin, guild=tenv.guild)
    choices = await callback(admin_interaction, "find")
    assert [(c.name, c.value) for c in choices] == [(f"#{product.id} [findable]", product.id)]
    customer_interaction = FakeInteraction(client=tenv.bot, user=tenv.member(), guild=tenv.guild)
    assert await callback(customer_interaction, "find") == []


async def test_editor_forms_respect_limits(tenv: TestEnv) -> None:
    product = await add_product(tenv, "x" * 100, description="y" * 4000, stock=10**9, max_per_order=10**6)
    money = service.money_format(tenv.app)
    for spec in (
        catalog_views.add_form(tenv.app.op, money, 2**63),
        catalog_views.edit_form(tenv.app.op, money, product),
    ):
        assert validate_modal(spec) == []
        to_modal(spec)  # discord.py accepts it
