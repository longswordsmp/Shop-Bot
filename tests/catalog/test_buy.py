"""The listing's buy buttons (route ``co:buy``, handled by checkout): open or continue the customer's private order
channel with the product in the cart, stock and cart limits, disabled buttons, concurrency, readiness, restarts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog import service as catalog_service
from shopbot.features.catalog import views as catalog_views
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, views
from shopbot.features.checkout import service as checkout_service
from tests.catalog.util import buttons
from tests.checkout.util import (
    add_product,
    channel,
    error_text,
    make_env,
    place_order,
    press,
    reload_session,
    render,
    session_of,
    start,
    ticket_channel_of,
    workflow_message,
)
from tests.fakes import FakeMember, FakeMessage, FakeTextChannel, click, http_error
from tests.helpers import GUILD_ID, TestEnv


@pytest.fixture
async def shop(tmp_path: Path) -> Any:
    env = await make_env(tmp_path, {"catalog": {"products_per_listing_message": 1}})
    try:
        yield env
    finally:
        await env.close()


async def _listing(tenv: TestEnv) -> list[FakeMessage]:
    await catalog_service.request_listing_refresh(tenv.app)
    return channel(tenv, "catalog").messages


async def _buy(tenv: TestEnv, user: FakeMember, product_id: int, message: FakeMessage | None = None) -> Any:
    return await click(tenv.app, user=user, custom_id=catalog_views.buy_custom_id(product_id), message=message)


async def _cart(tenv: TestEnv, session_id: int) -> dict[int, int]:
    return await tenv.app.db.run(repo.cart_quantities, session_id)


async def _order_channels(tenv: TestEnv) -> list[FakeTextChannel]:
    prefix = tenv.text(k.CHANNEL_PREFIX)
    return [c for c in tenv.guild.text_channels if c.name.startswith(prefix) and not c.deleted]


async def _session_count(tenv: TestEnv) -> int:
    return await tenv.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM checkout_sessions").fetchone()[0])


def test_catalog_builds_the_custom_id_checkout_routes() -> None:
    assert catalog_views.BUY_ROUTE == k.ROUTE
    assert catalog_views.buy_custom_id(42) == "sb:co:buy:42"


async def test_buy_opens_a_new_order_channel_with_the_product_in_the_cart(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]", price=1250, stock=3)
    head, card = await _listing(shop)
    customer = shop.member()
    assert buttons(card) == [(f"sb:co:buy:{product.id}", render(shop, catalog_keys.BUTTON_BUY, name="[p1]"), False)]

    clicked = await _buy(shop, customer, product.id, card)
    assert clicked.response_kinds() == ["defer"]
    session = await session_of(shop, customer)
    ticket_channel = await ticket_channel_of(shop, session.ticket_id)
    assert error_text(clicked) == render(shop, k.STARTED, channel_mention=f"<#{ticket_channel.id}>")
    assert clicked.last_sent is not None and clicked.last_sent.flags.ephemeral
    assert session.status == m.ACTIVE and session.step == m.CART
    assert await _cart(shop, session.id) == {product.id: 1}
    # the first workflow message already shows the cart with the product
    message = workflow_message(ticket_channel)
    assert message.id == session.message_id
    assert message.embeds[0].title == shop.text(k.CART_TITLE)
    assert "[p1]" in message.text()
    assert ticket_channel.permissions_for(customer).view_channel
    assert not ticket_channel.permissions_for(shop.member(name="other")).view_channel
    # nothing is reserved before the order is submitted; the public listing is untouched
    assert (await shop.app.db.run(catalog_repo.get_product, GUILD_ID, product.id)).reserved == 0  # type: ignore[union-attr]
    assert card.edits == [] and head.edits == []


async def test_buy_continues_the_existing_channel_and_updates_its_live_panel(shop: TestEnv) -> None:
    first = await add_product(shop, "[p1]")
    second = await add_product(shop, "[p2]", stock=5)
    customer = shop.member()
    _, session, ticket_channel, message = await start(shop, customer)
    assert message.embeds[0].title == shop.text(k.BROWSE_TITLE)

    clicked = await _buy(shop, customer, second.id)
    assert error_text(clicked) == render(shop, k.BUY_ADDED, name="[p2]", channel_mention=f"<#{ticket_channel.id}>")
    assert await _cart(shop, session.id) == {second.id: 1}
    assert (await reload_session(shop, session.id)).step == m.CART
    assert message.embeds[0].title == shop.text(k.CART_TITLE) and "[p2]" in message.text()
    assert len(await _order_channels(shop)) == 1 and await _session_count(shop) == 1

    again = await _buy(shop, customer, first.id)
    assert error_text(again) == render(shop, k.BUY_ADDED, name="[p1]", channel_mention=f"<#{ticket_channel.id}>")
    assert await _cart(shop, session.id) == {second.id: 1, first.id: 1}
    assert "[p1]" in message.text()


async def test_repeated_buy_never_adds_a_second_unit(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]", stock=10)
    customer = shop.member()
    await _buy(shop, customer, product.id)
    session = await session_of(shop, customer)
    shop.clock.advance(60)
    again = await _buy(shop, customer, product.id)
    assert await _cart(shop, session.id) == {product.id: 1}
    ticket_channel = await ticket_channel_of(shop, session.ticket_id)
    assert error_text(again) == render(shop, k.BUY_ADDED, name="[p1]", channel_mention=f"<#{ticket_channel.id}>")
    assert len(await _order_channels(shop)) == 1


async def test_buy_reply_falls_back_to_the_session_exists_text(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.BUY_ADDED: ""})
    try:
        product = await add_product(tenv, "[p1]")
        customer = tenv.member()
        _, _, ticket_channel, _ = await start(tenv, customer)
        clicked = await _buy(tenv, customer, product.id)
        assert error_text(clicked) == render(tenv, k.SESSION_EXISTS, channel_mention=f"<#{ticket_channel.id}>")
    finally:
        await tenv.close()


async def test_buy_from_a_later_step_returns_to_the_cart_keeping_answers(shop: TestEnv) -> None:
    first = await add_product(shop, "[p1]")
    second = await add_product(shop, "[p2]")
    customer = shop.member()
    _, session, _, message = await start(shop, customer)
    await _buy(shop, customer, first.id)
    await press(shop, customer, message, views.go_id(session.id, m.REVIEW))
    assert (await reload_session(shop, session.id)).step == m.REVIEW
    await _buy(shop, customer, second.id)
    current = await reload_session(shop, session.id)
    assert current.step == m.CART and await _cart(shop, session.id) == {first.id: 1, second.id: 1}
    assert message.embeds[0].title == shop.text(k.CART_TITLE)


async def test_concurrent_buys_open_one_channel_with_every_product(shop: TestEnv) -> None:
    products = [await add_product(shop, f"[p{i}]") for i in range(3)]
    customer = shop.member()
    results = await asyncio.gather(*(_buy(shop, customer, p.id) for p in products))
    assert len(await _order_channels(shop)) == 1 and await _session_count(shop) == 1
    session = await session_of(shop, customer)
    assert await _cart(shop, session.id) == {p.id: 1 for p in products}
    assert all(error_text(r) for r in results)


async def test_start_and_buy_at_once_open_one_channel(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]")
    customer = shop.member()
    await asyncio.gather(click(shop.app, user=customer, custom_id=views.start_id()), _buy(shop, customer, product.id))
    assert len(await _order_channels(shop)) == 1 and await _session_count(shop) == 1
    session = await session_of(shop, customer)
    assert await _cart(shop, session.id) == {product.id: 1}


async def test_out_of_stock_inactive_archived_and_unknown_products_create_nothing(shop: TestEnv) -> None:
    customer = shop.member()
    sold_out = await add_product(shop, "[sold]", stock=2)
    assert await shop.app.db.transaction(catalog_repo.reserve, sold_out.id, 2, 1)
    inactive = await add_product(shop, "[off]", active=False)
    archived = await add_product(shop, "[gone]")
    await shop.app.db.transaction(catalog_repo.update_product, archived.id, archived.version, now=2, archived=True)

    refused = await _buy(shop, customer, sold_out.id)
    assert error_text(refused) == render(shop, k.ERROR_OUT_OF_STOCK, name="[sold]", available=0)
    for product, name in ((inactive, "[off]"), (archived, "[gone]")):
        refused = await _buy(shop, customer, product.id)
        assert error_text(refused) == render(shop, k.ERROR_PRODUCT_UNAVAILABLE, name=name)
    unknown = await _buy(shop, customer, 99_999)
    assert error_text(unknown) == shop.text(core.ERROR_NOT_FOUND)
    assert await _session_count(shop) == 0 and await _order_channels(shop) == []
    assert [c for c in shop.guild.audit if c[0] == "create_text_channel"] == []


async def test_sold_out_listing_button_is_disabled_and_reenabled(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]", stock=1)
    _, card = await _listing(shop)
    assert buttons(card)[0][2] is False
    customer = shop.member()
    await _buy(shop, customer, product.id)
    other = shop.member(name="other")
    order, _, _ = await place_order(shop, other, [(product.id, 1)])  # reserves the last unit
    await shop.app.scheduler.run_due()
    assert buttons(card)[0][2] is True
    # The first customer's cart still holds the product; a click on a stale enabled button is refused.
    shop.clock.advance(60)
    refused = await _buy(shop, shop.member(name="third"), product.id)
    assert error_text(refused) == render(shop, k.ERROR_OUT_OF_STOCK, name="[p1]", available=0)
    await checkout_service.change_status(shop.app, order.id, m.CANCELLED, actor_id=None)
    await shop.app.scheduler.run_due()
    assert buttons(card)[0][2] is False


async def test_cart_limit_is_respected(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"max_cart_lines": 1}})
    try:
        first = await add_product(tenv, "[p1]")
        second = await add_product(tenv, "[p2]")
        customer = tenv.member()
        await _buy(tenv, customer, first.id)
        session = await session_of(tenv, customer)
        full = await _buy(tenv, customer, second.id)
        assert error_text(full) == render(tenv, k.ERROR_CART_FULL, max=1)
        assert await _cart(tenv, session.id) == {first.id: 1}
    finally:
        await tenv.close()


async def test_open_order_limit_is_respected(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]")
    customer = shop.member()
    await place_order(shop, customer, [(product.id, 1)])
    shop.clock.advance(60)
    blocked = await _buy(shop, customer, product.id)
    assert error_text(blocked) == render(shop, k.LIMIT_REACHED, limit=1)
    assert len(await _order_channels(shop)) == 1


async def test_product_that_sold_out_while_opening_leaves_an_empty_cart_and_explains(
    shop: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = await add_product(shop, "[p1]", stock=1)
    customer = shop.member()

    def sold_out(conn: Any, **kwargs: Any) -> Any:
        raise repo.OutOfStock("[p1]", 0)

    monkeypatch.setattr(repo, "add_to_cart", sold_out)
    clicked = await _buy(shop, customer, product.id)
    session = await session_of(shop, customer)
    assert session.status == m.ACTIVE and await _cart(shop, session.id) == {}
    reply = clicked.last_sent
    assert reply is not None and len(reply.embeds) == 2
    ticket_channel = await ticket_channel_of(shop, session.ticket_id)
    assert reply.embeds[0].description == render(shop, k.STARTED, channel_mention=f"<#{ticket_channel.id}>")
    assert reply.embeds[1].description == render(shop, k.ERROR_OUT_OF_STOCK, name="[p1]", available=0)


async def test_checkout_not_ready_answers_unavailable(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.BROWSE_TITLE: ""})
    try:
        assert tenv.app.ready("catalog") and not tenv.app.ready("checkout")
        product = await add_product(tenv, "[p1]")
        _, card = await _listing(tenv)
        assert buttons(card)[0][0] == f"sb:co:buy:{product.id}"  # still shown; checkout answers
        denied = await _buy(tenv, tenv.member(), product.id, card)
        assert error_text(denied) == tenv.text(core.ERROR_UNAVAILABLE)
        assert await _session_count(tenv) == 0
    finally:
        await tenv.close()


async def test_listing_without_checkout_has_no_buy_buttons(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"checkout": False}, "catalog": {"products_per_listing_message": 1}})
    try:
        await add_product(tenv, "[p1]")
        head, card = await _listing(tenv)
        assert buttons(card) == [] and [b[0] for b in buttons(head)] == ["sb:ct:browse:0"]
    finally:
        await tenv.close()


async def test_buy_from_dm_or_by_a_bot_does_nothing(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]")
    dm = await click(shop.app, user=shop.member(), custom_id=catalog_views.buy_custom_id(product.id), dm=True)
    assert dm.sent == []
    robot = shop.guild.add_member(name="robot", bot=True)
    denied = await _buy(shop, robot, product.id)
    assert error_text(denied) == shop.text(core.ERROR_PERMISSION)
    assert await _session_count(shop) == 0


async def test_forged_custom_ids_are_refused(shop: TestEnv) -> None:
    customer = shop.member()
    for custom_id in ("sb:co:buy", "sb:co:buy:abc", "sb:co:buy:-5"):
        refused = await click(shop.app, user=customer, custom_id=custom_id)
        assert error_text(refused) == shop.text(core.ERROR_NOT_FOUND)
    assert await _session_count(shop) == 0


async def test_buy_button_works_after_a_restart(shop: TestEnv) -> None:
    from tests.checkout.util import restarted_app

    product = await add_product(shop, "[p1]")
    _, card = await _listing(shop)
    custom_id = buttons(card)[0][0]
    assert custom_id is not None
    app = restarted_app(shop)
    await app.resources.refresh()
    customer = shop.member()
    clicked = await click(app, user=customer, custom_id=custom_id, message=card)
    session = await session_of(shop, customer)
    assert await _cart(shop, session.id) == {product.id: 1}
    assert error_text(clicked) is not None and "<#" in (error_text(clicked) or "")


async def test_channel_creation_failure_leaves_nothing(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]")
    shop.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    failed = await _buy(shop, shop.member(), product.id)
    assert error_text(failed) == shop.text(core.ERROR_GENERIC)
    assert await _session_count(shop) == 0


async def test_live_panel_edit_failure_still_adds_and_answers(shop: TestEnv) -> None:
    product = await add_product(shop, "[p1]")
    customer = shop.member()
    _, session, ticket_channel, message = await start(shop, customer)
    await message.delete()  # someone deleted the workflow message
    clicked = await _buy(shop, customer, product.id)
    assert await _cart(shop, session.id) == {product.id: 1}
    assert error_text(clicked) == render(shop, k.BUY_ADDED, name="[p1]", channel_mention=f"<#{ticket_channel.id}>")
