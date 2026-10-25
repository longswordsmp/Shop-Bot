"""/product remove (confirmation buttons) and removing/archiving with one product per listing message."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.core.events import PRODUCT_CHANGED
from shopbot.features import core_keys as ck
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog import repo, service, views
from tests.catalog.util import (
    add_product,
    audit_actions,
    buttons,
    get,
    invoke,
    listing_channel,
    manager_env,
    restarted_app,
)
from tests.fakes import FakeInteraction, FakeMessage, click
from tests.helpers import TestEnv, make_settings, make_test_env


@pytest.fixture
async def one_per_message(tmp_path: Path) -> Any:
    env = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 1}}))
    try:
        yield env
    finally:
        await env.close()


def _title(interaction: FakeInteraction) -> str | None:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].title


def _confirmation(interaction: FakeInteraction) -> FakeMessage:
    message = interaction.last_sent
    assert message is not None
    return message


def _product_titles(messages: list[FakeMessage]) -> list[str | None]:
    return [e.title for m in messages[1:] for e in m.embeds]


async def test_remove_asks_then_removes_and_updates_the_listing(one_per_message: TestEnv) -> None:
    tenv = one_per_message
    channel = await listing_channel(tenv)
    first, second, third = [await add_product(tenv, f"[p{i}]") for i in (1, 2, 3)]
    await service.request_listing_refresh(tenv.app)
    ids = [m.id for m in channel.messages]
    assert _product_titles(channel.messages) == ["[p1]", "[p2]", "[p3]"]
    events: list[dict[str, Any]] = []

    async def on_change(**payload: Any) -> None:
        events.append(payload)

    tenv.app.events.subscribe(PRODUCT_CHANGED, on_change)
    admin = tenv.member(admin=True)
    asked = await invoke(tenv, admin, "remove", product_id=second.id)
    prompt = _confirmation(asked)
    assert prompt.flags.ephemeral
    assert prompt.embeds[0].title == tenv.app.op("catalog.remove.title", product_id=second.id)
    assert buttons(prompt) == [
        (f"sb:ct:remove:{second.id}", tenv.app.op("catalog.remove.button_remove"), False),
        (f"sb:ct:remove_no:{second.id}", tenv.app.op("catalog.remove.button_keep"), False),
    ]
    assert not (await get(tenv, second.id)).archived  # nothing happens before the confirmation

    done = await click(tenv.app, user=admin, custom_id=views.remove_custom_id(second.id), message=prompt)
    assert done.response_kinds() == ["defer"]
    assert prompt.embeds[0].title == tenv.app.op("catalog.cmd.removed", product_id=second.id)
    assert buttons(prompt) == []  # the confirmation is replaced by the result
    assert (await get(tenv, second.id)).archived
    # No stale message: the product messages keep their order and the surplus message is deleted.
    assert _product_titles(channel.messages) == ["[p1]", "[p3]"]
    assert [m.id for m in channel.messages] == ids[:3]
    assert [b[0] for m in channel.messages[1:] for b in buttons(m)] == [f"sb:co:buy:{p.id}" for p in (first, third)]
    audits = await audit_actions(tenv, "catalog.product_archive")
    assert [e.details for e in audits] == [{"name": "[p2]", "via": "remove"}]
    assert [e["action"] for e in events] == ["archive"]

    # A second click (another copy of the confirmation, or a double click) changes nothing more.
    again = await click(tenv.app, user=admin, custom_id=views.remove_custom_id(second.id), message=prompt)
    assert again.response_kinds() == ["defer"]
    assert len(await audit_actions(tenv, "catalog.product_archive")) == 1
    assert _product_titles(channel.messages) == ["[p1]", "[p3]"]


async def test_keep_changes_nothing(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    admin = tenv.member(admin=True)
    prompt = _confirmation(await invoke(tenv, admin, "remove", product_id=product.id))
    kept = await click(tenv.app, user=admin, custom_id=views.remove_cancel_custom_id(product.id), message=prompt)
    assert kept.response_kinds() == ["edit_message"]
    assert prompt.embeds[0].title == tenv.app.op("catalog.cmd.remove_kept", product_id=product.id)
    assert not (await get(tenv, product.id)).archived
    assert await audit_actions(tenv, "catalog.product_archive") == []


async def test_only_managers_can_confirm(tmp_path: Path) -> None:
    tenv, manager_role = await manager_env(tmp_path)
    try:
        product = await add_product(tenv, "[p]")
        staff = tenv.member(name="staffer", staff=True)  # staff without a catalog manager role
        denied = await invoke(tenv, staff, "remove", product_id=product.id)
        assert denied.last_sent is not None
        assert buttons(denied.last_sent) == []
        forged = await click(tenv.app, user=staff, custom_id=views.remove_custom_id(product.id))
        assert forged.last_sent is not None and forged.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
        customer = tenv.member(name="customer")
        forged = await click(tenv.app, user=customer, custom_id=views.remove_custom_id(product.id))
        assert forged.last_sent is not None and forged.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
        assert not (await get(tenv, product.id)).archived

        manager = tenv.guild.add_member(name="manager", roles=[tenv.staff_role, manager_role])
        prompt = _confirmation(await invoke(tenv, manager, "remove", product_id=product.id))
        await click(tenv.app, user=manager, custom_id=views.remove_custom_id(product.id), message=prompt)
        assert (await get(tenv, product.id)).archived
    finally:
        await tenv.close()


async def test_units_held_by_pending_orders_block_removal(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]", stock=5)
    admin = tenv.member(admin=True)
    prompt = _confirmation(await invoke(tenv, admin, "remove", product_id=product.id))
    assert buttons(prompt)  # nothing reserved yet
    await tenv.app.db.transaction(repo.reserve, product.id, 2, 3)  # an order comes in meanwhile
    refused = await click(tenv.app, user=admin, custom_id=views.remove_custom_id(product.id), message=prompt)
    assert refused.response_kinds() == ["defer"]
    assert prompt.embeds[0].title == tenv.app.op("catalog.cmd.remove_reserved", product_id=product.id, reserved=2)
    assert not (await get(tenv, product.id)).archived
    asked = await invoke(tenv, admin, "remove", product_id=product.id)
    assert _title(asked) == tenv.app.op("catalog.cmd.remove_reserved", product_id=product.id, reserved=2)
    assert buttons(_confirmation(asked)) == []


async def test_unknown_and_already_removed_products(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    unknown = await invoke(tenv, admin, "remove", product_id=424242)
    assert _title(unknown) == tenv.app.op("catalog.cmd.not_found", product_id=424242)
    product = await add_product(tenv, "[p]")
    await invoke(tenv, admin, "archive", product_id=product.id, confirm=True)
    again = await invoke(tenv, admin, "remove", product_id=product.id)
    assert _title(again) == tenv.app.op("catalog.cmd.removed", product_id=product.id)
    assert buttons(_confirmation(again)) == []


async def test_confirmation_survives_a_restart(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    admin = tenv.member(admin=True)
    prompt = _confirmation(await invoke(tenv, admin, "remove", product_id=product.id))
    app = restarted_app(tenv)
    await app.resources.refresh()
    await click(app, user=admin, custom_id=views.remove_custom_id(product.id), message=prompt)
    assert (await get(tenv, product.id)).archived


async def test_archive_command_with_one_product_per_message_leaves_no_stale_message(one_per_message: TestEnv) -> None:
    tenv = one_per_message
    channel = await listing_channel(tenv)
    products = [await add_product(tenv, f"[p{i}]") for i in (1, 2, 3)]
    await service.request_listing_refresh(tenv.app)
    await invoke(tenv, tenv.member(admin=True), "archive", product_id=products[1].id, confirm=True)
    assert _product_titles(channel.messages) == ["[p1]", "[p3]"]
    assert len(channel.messages) == 3
    # Editing a product updates its own message in place.
    third = await get(tenv, products[2].id)
    await tenv.app.db.transaction(repo.update_product, third.id, third.version, now=5, description="[changed]")
    await service.request_listing_refresh(tenv.app)
    assert channel.messages[2].embeds[0].description == "[changed]"
    assert channel.messages[1].embeds[0].description is None


async def test_remove_requires_a_ready_catalog(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={keys.LISTING_TITLE: ""})
    try:
        assert not tenv.app.ready("catalog")
        product = await add_product(tenv, "[p]")
        asked = await invoke(tenv, tenv.member(admin=True), "remove", product_id=product.id)
        assert _title(asked) == tenv.app.op("command.feature_disabled", feature="catalog")
        clicked = await click(tenv.app, user=tenv.member(admin=True), custom_id=views.remove_custom_id(product.id))
        assert clicked.last_sent is not None
        assert clicked.last_sent.embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)
        assert not (await get(tenv, product.id)).archived
    finally:
        await tenv.close()
