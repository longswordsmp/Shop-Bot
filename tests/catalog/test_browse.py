"""Private browsing: first click, paging, bounds, empty catalog, missing content, and the embedding API."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features import core_keys as ck
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog import repo, service
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec
from tests.catalog.util import add_product, buttons, listing_channel
from tests.fakes import FakeMessage, click
from tests.helpers import TestEnv, make_settings, make_test_env


def _ephemeral(message: FakeMessage) -> FakeMessage:
    message.flags = discord.MessageFlags(ephemeral=True)
    return message


def _footer(tenv: TestEnv, page: int, pages: int) -> str:
    indicator = tenv.text(ck.PAGE_INDICATOR).replace("{page}", str(page)).replace("{pages}", str(pages))
    return f"{tenv.text(ck.FOOTER_TEXT)}\n{indicator}"


async def test_browse_from_listing_opens_private_view_and_pages_in_place(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    first = await add_product(tenv, "[a]", price=100, stock=3, description="[desc a]")
    second = await add_product(tenv, "[b]", price=200)
    third = await add_product(tenv, "[c]", price=300, stock=0)
    await service.request_listing_refresh(tenv.app)
    listing = channel.messages[0]  # the header message carries the browse button
    customer = tenv.member()

    opened = await click(tenv.app, user=customer, custom_id="sb:ct:browse:0", message=listing)
    assert opened.response_kinds() == ["send_message"]
    private = opened.last_sent
    assert private is not None and getattr(private, "ephemeral", False) is True
    assert len(channel.messages) == 2 and listing.edits == []  # the public listing is untouched
    card = private.embeds[0]
    assert card.title == first.name and card.description == "[desc a]"
    assert card.footer.text == _footer(tenv, 1, 3)
    assert card.author.name == tenv.text(ck.AUTHOR_NAME)
    previous_label, next_label = tenv.text(ck.BUTTON_PREV), tenv.text(ck.BUTTON_NEXT)

    def buy(product: repo.Product) -> str:
        return tenv.text(keys.BUTTON_BUY).replace("{name}", product.name)

    assert buttons(private) == [
        ("sb:ct:browse:-1", previous_label, True),
        ("sb:ct:browse:1", next_label, False),
        (f"sb:co:buy:{first.id}", buy(first), False),
    ]

    _ephemeral(private)
    middle = await click(tenv.app, user=customer, custom_id="sb:ct:browse:1", message=private)
    assert middle.response_kinds() == ["edit_message"]
    assert private.embeds[0].title == second.name and private.embeds[0].description is None
    assert private.embeds[0].fields[1].value == tenv.text(keys.AVAILABILITY_UNLIMITED)
    assert buttons(private) == [
        ("sb:ct:browse:0", previous_label, False),
        ("sb:ct:browse:2", next_label, False),
        (f"sb:co:buy:{second.id}", buy(second), False),
    ]

    last = await click(tenv.app, user=customer, custom_id="sb:ct:browse:2", message=private)
    assert last.response_kinds() == ["edit_message"]
    assert private.embeds[0].title == third.name
    assert private.embeds[0].fields[1].value == tenv.text(keys.AVAILABILITY_OUT_OF_STOCK)
    assert private.embeds[0].footer.text == _footer(tenv, 3, 3)
    assert buttons(private)[1] == ("sb:ct:browse:3", next_label, True)
    assert buttons(private)[2] == (f"sb:co:buy:{third.id}", buy(third), True)  # sold out: disabled


async def test_out_of_range_page_is_clamped_and_negative_is_not_found(tenv: TestEnv) -> None:
    await add_product(tenv, "[a]")
    last = await add_product(tenv, "[b]")
    customer = tenv.member()
    beyond = await click(tenv.app, user=customer, custom_id="sb:ct:browse:99")
    assert beyond.last_sent is not None and beyond.last_sent.embeds[0].title == last.name
    negative = await click(tenv.app, user=customer, custom_id="sb:ct:browse:-1")
    assert negative.last_sent is not None
    assert negative.last_sent.embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_products_removed_while_browsing(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[only]")
    customer = tenv.member()
    opened = await click(tenv.app, user=customer, custom_id="sb:ct:browse:0")
    private = opened.last_sent
    assert private is not None and private.embeds[0].title == product.name
    # a single page needs no navigation; only the product's buy button
    assert buttons(private) == [
        (f"sb:co:buy:{product.id}", tenv.text(keys.BUTTON_BUY).replace("{name}", "[only]"), False)
    ]
    await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=5, active=False)
    _ephemeral(private)
    await click(tenv.app, user=customer, custom_id="sb:ct:browse:0", message=private)
    assert [e.description for e in private.embeds] == [tenv.text(keys.LISTING_EMPTY)]
    assert buttons(private) == []


async def test_empty_catalog_shows_configured_empty_text(tenv: TestEnv) -> None:
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    assert opened.last_sent is not None
    assert opened.last_sent.embeds[0].description == tenv.text(keys.LISTING_EMPTY)
    assert opened.last_sent.embeds[0].title is None


async def test_inactive_and_archived_products_are_not_browsable(tenv: TestEnv) -> None:
    await add_product(tenv, "[inactive]", active=False)
    archived = await add_product(tenv, "[archived]")
    await tenv.app.db.transaction(repo.update_product, archived.id, archived.version, now=1, archived=True)
    shown = await add_product(tenv, "[shown]")
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    assert opened.last_sent is not None and opened.last_sent.embeds[0].title == shown.name


async def test_browse_disabled_answers_unavailable(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"browse_enabled": False}}))
    try:
        await add_product(tenv)
        opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
        assert opened.last_sent is not None
        assert opened.last_sent.embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)
    finally:
        await tenv.close()


async def test_not_ready_feature_answers_unavailable(tenv: TestEnv) -> None:
    await add_product(tenv)
    values = dict(tenv.content)
    del values[keys.FIELD_PRICE]
    tenv.app.content.replace(file_values=values)
    tenv.app.recompute_readiness()
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    assert opened.last_sent is not None
    assert [e.description for e in opened.last_sent.embeds] == [tenv.text(ck.ERROR_UNAVAILABLE)]


async def test_runtime_missing_content_never_sends_partial_card(tenv: TestEnv) -> None:
    await add_product(tenv, stock=None)
    values = dict(tenv.content)
    del values[keys.AVAILABILITY_UNLIMITED]  # readiness left stale on purpose
    tenv.app.content.replace(file_values=values)
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    assert opened.last_sent is not None
    assert [e.description for e in opened.last_sent.embeds] == [tenv.text(ck.ERROR_UNAVAILABLE)]
    assert opened.last_sent.embeds[0].fields == []


async def test_nothing_configured_means_silent_acknowledgement(tenv: TestEnv) -> None:
    await add_product(tenv)
    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    assert opened.sent == [] and opened.response_kinds() == ["defer"]


async def test_product_names_are_escaped_and_never_ping(tenv: TestEnv) -> None:
    await add_product(tenv, "**bold** @everyone <@1>", description="**intended formatting**")
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    message = opened.last_sent
    assert message is not None
    assert message.embeds[0].title == discord.utils.escape_markdown("**bold** @everyone <@1>")
    assert message.embeds[0].description == "**intended formatting**"
    assert message.content == ""
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False and mentions.users is False


async def test_browse_view_api_supports_custom_paging_and_extra_buttons(tenv: TestEnv) -> None:
    first = await add_product(tenv, "[a]")
    await add_product(tenv, "[b]")

    def add_to_cart(product: repo.Product) -> list[ButtonSpec]:
        return [ButtonSpec(label="[cart]", custom_id=cid("zz", "cart", product.id), style="success")]

    spec = await service.browse_view(
        tenv.app, 0, page_custom_id=lambda page: cid("zz", "page", 7, page), extra_buttons=add_to_cart
    )
    ids = [component.custom_id for row in spec.rows for component in row]
    assert ids == ["sb:zz:page:7:-1", "sb:zz:page:7:1", f"sb:co:buy:{first.id}", f"sb:zz:cart:{first.id}"]
    assert len(spec.rows) == 2
