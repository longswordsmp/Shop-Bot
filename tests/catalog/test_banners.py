"""Banners on catalog messages: only the first listing message shows one, product images keep working next to it,
private browsing and staff pages keep theirs while paging, and one-message replies swap theirs with the outcome."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog import images, repo, service, views
from shopbot.ui.limits import FILES_TOTAL_BYTES, validate_message
from shopbot.ui.spec import FileSpec
from tests.catalog.test_images import FakeUpload, png
from tests.catalog.util import add_product, invoke, listing_channel
from tests.fakes import FakeInteraction, FakeMessage, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, banner_names, make_banner_env


@pytest.fixture
async def bn(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Banners on (a tiny picture per banner id); two products per listing message."""
    tenv = await make_banner_env(tmp_path, {"catalog": {"products_per_listing_message": 2}})
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None
    return banner.filename


def uploaded_banners(edit: dict[str, Any]) -> list[str]:
    """Banner files an edit uploads again (a kept attachment is not an upload)."""
    return [
        a.filename
        for a in edit.get("attachments") or []
        if isinstance(a, discord.File) and a.filename.startswith("banner-")
    ]


def product_files(message: FakeMessage) -> list[str]:
    return [f.filename for f in message.files if not f.filename.startswith("banner-")]


def reply(interaction: FakeInteraction) -> FakeMessage:
    assert interaction.last_sent is not None
    return interaction.last_sent


async def upload(tenv: TestEnv, product_id: int, shade: int) -> None:
    await invoke(tenv, tenv.member(admin=True), "image", product_id=product_id, file=FakeUpload(png(shade)))


# -- live listing ---------------------------------------------------------------------------------------------------


async def test_only_the_first_listing_message_shows_the_catalog_banner(bn: TestEnv) -> None:
    channel = await listing_channel(bn)
    first = await add_product(bn, "[a]", stock=5)
    await add_product(bn, "[b]")
    await add_product(bn, "[c]")
    await upload(bn, first.id, 0x30)

    head, *cards = channel.messages
    assert len(cards) == 2  # 3 products, 2 per message
    assert banner_names(head) == [name(bn, "catalog")]
    assert all(banner_names(card) == [] for card in cards)
    assert head.embeds[0].image.url is None  # the banner is never shown inside an embed
    # The product image still shows on its card (attachment:// to the product image, never to the banner).
    [image] = product_files(cards[0])
    assert cards[0].embeds[0].image.url == f"attachment://{image}" and image.startswith(f"product-{first.id}-")


async def test_a_refresh_that_leaves_the_header_unchanged_never_uploads_the_banner_again(bn: TestEnv) -> None:
    channel = await listing_channel(bn)
    product = await add_product(bn, "[a]", stock=5)
    await upload(bn, product.id, 0x40)
    head, card = channel.messages

    report = await service.request_listing_refresh(bn.app)  # nothing changed
    assert report.ok and head.edits == [] and card.edits == []

    await bn.app.db.transaction(repo.reserve, product.id, 2, bn.clock.now())  # only the card changes
    await service.request_listing_refresh(bn.app)
    assert head.edits == [] and banner_names(head) == [name(bn, "catalog")]
    assert card.edits and banner_names(card) == [] and len(product_files(card)) == 1

    bn.app.content.set_override(keys.LISTING_TITLE, "[new title]")  # the header changes, its banner does not
    await service.request_listing_refresh(bn.app)
    assert head.embeds[0].title == "[new title]"
    assert uploaded_banners(head.edits[-1]) == [] and banner_names(head) == [name(bn, "catalog")]


async def test_the_empty_listing_keeps_the_banner_on_its_only_message(bn: TestEnv) -> None:
    channel = await listing_channel(bn)
    await service.request_listing_refresh(bn.app)
    [head] = channel.messages
    assert banner_names(head) == [name(bn, "catalog")] and len(head.embeds) == 2

    await add_product(bn, "[a]")  # the same message becomes the header of a listing with products
    await service.request_listing_refresh(bn.app)
    assert channel.messages[0] is head and len(head.embeds) == 1
    assert uploaded_banners(head.edits[-1]) == [] and banner_names(head) == [name(bn, "catalog")]


async def test_listing_packing_counts_the_banner_toward_the_upload_limits(bn: TestEnv) -> None:
    products = [await add_product(bn, f"[p{i}]") for i in range(4)]
    refs = {
        p.id: images.ProductImage(
            product_id=p.id,
            guild_id=GUILD_ID,
            filename="x.png",
            content_type="image/png",
            size=4 * 1024 * 1024,
            sha256=f"{p.id:064x}",
            uploaded_by=None,
            created_at=0,
        )
        for p in products
    }
    specs = views.listing_messages(bn.app.ui, bn.app.settings, products, refs)
    assert [s.banner is not None for s in specs] == [True, False, False]
    assert all(validate_message(spec) == [] for spec in specs)

    card = views._Card(embed=specs[1].embeds[0], button=None, file=specs[1].files[0] if specs[1].files else None)
    assert card.file is not None
    large = FileSpec(filename="banner-x-0.png", sha256="0" * 64, size=FILES_TOTAL_BYTES - 2 * card.file.size + 1)
    assert views._fits([card], card, 10)  # two 4 MiB images fit one message
    assert not views._fits([card], card, 10, banner=large)  # ... but not next to a banner that fills the rest


# -- private browsing -----------------------------------------------------------------------------------------------


async def test_private_browsing_keeps_the_catalog_banner_while_paging(bn: TestEnv) -> None:
    with_image = await add_product(bn, "[a]")
    await add_product(bn, "[b]")
    await upload(bn, with_image.id, 0x50)
    customer = bn.member()
    private = reply(await click(bn.app, user=customer, custom_id=views.browse_custom_id(0)))
    assert banner_names(private) == [name(bn, "catalog")]
    [image] = product_files(private)
    assert private.embeds[0].image.url == f"attachment://{image}"

    private.flags = discord.MessageFlags(ephemeral=True)
    await click(bn.app, user=customer, custom_id=views.browse_custom_id(1), message=private)
    assert uploaded_banners(private.edits[-1]) == []  # the banner is kept, not uploaded again
    assert banner_names(private) == [name(bn, "catalog")] and product_files(private) == []  # the image is gone
    await click(bn.app, user=customer, custom_id=views.browse_custom_id(0), message=private)
    assert banner_names(private) == [name(bn, "catalog")] and len(product_files(private)) == 1

    # Another feature embedding the browser in its own flow shows its own banner.
    embedded = await service.browse_view(bn.app, 0, banner_id="selection-and-cart")
    assert embedded.banner is not None and embedded.banner.filename == name(bn, "selection-and-cart")


# -- staff ----------------------------------------------------------------------------------------------------------


async def test_product_view_shows_the_banner_on_the_details_only(bn: TestEnv) -> None:
    product = await add_product(bn, "[p]")
    await upload(bn, product.id, 0x60)
    details, card = (await invoke(bn, bn.member(admin=True), "view", product_id=product.id)).sent[-2:]
    assert banner_names(details) == [name(bn, "staff-dashboard")]
    assert banner_names(card) == []  # the customer card preview, like the listing cards
    assert details.embeds[0].thumbnail.url == f"attachment://{product_files(details)[0]}"
    assert card.embeds[0].image.url == f"attachment://{product_files(card)[0]}"


async def test_product_list_keeps_the_staff_dashboard_banner_while_paging(bn: TestEnv) -> None:
    for i in range(views.ADMIN_PAGE_SIZE + 1):
        await add_product(bn, f"[p{i}]")
    admin = bn.member(admin=True)
    listing = reply(await invoke(bn, admin, "list"))
    assert banner_names(listing) == [name(bn, "staff-dashboard")]
    await click(bn.app, user=admin, custom_id=views.admin_page_custom_id(1, False), message=listing)
    assert listing.edits and "attachments" not in listing.edits[-1]  # same banner: attachments left alone
    assert banner_names(listing) == [name(bn, "staff-dashboard")]


async def test_remove_confirmation_swaps_its_banner_with_the_outcome(bn: TestEnv) -> None:
    admin = bn.member(admin=True)
    kept = await add_product(bn, "[kept]")
    prompt = reply(await invoke(bn, admin, "remove", product_id=kept.id))
    assert banner_names(prompt) == [name(bn, "general-response")]
    await click(bn.app, user=admin, custom_id=views.remove_cancel_custom_id(kept.id), message=prompt)
    assert "attachments" not in prompt.edits[-1]  # kept: the same banner stays
    assert banner_names(prompt) == [name(bn, "general-response")]

    removed = await add_product(bn, "[removed]")
    prompt = reply(await invoke(bn, admin, "remove", product_id=removed.id))
    await click(bn.app, user=admin, custom_id=views.remove_custom_id(removed.id), message=prompt)
    assert prompt.embeds[0].title == bn.app.op("catalog.cmd.removed", product_id=removed.id)
    assert banner_names(prompt) == [name(bn, "successful-confirmation")] and len(prompt.attachments) == 1

    blocked = await add_product(bn, "[blocked]", stock=5)
    prompt = reply(await invoke(bn, admin, "remove", product_id=blocked.id))
    await bn.app.db.transaction(repo.reserve, blocked.id, 2, bn.clock.now())  # a pending order holds units now
    await click(bn.app, user=admin, custom_id=views.remove_custom_id(blocked.id), message=prompt)
    assert banner_names(prompt) == [name(bn, "error")] and len(prompt.attachments) == 1


async def test_operator_replies_follow_the_outcome_not_the_tone(bn: TestEnv) -> None:
    admin = bn.member(admin=True)
    product = await add_product(bn, "[p]")
    # The listing channel does not exist here: the refresh fails, so the saved change has the warning tone.
    saved = await invoke(bn, admin, "price", product_id=product.id, amount="3.00")
    warning = bn.app.ui.operator_embed("[t]", tone="warning").color
    assert warning is not None and reply(saved).embeds[0].colour == discord.Colour(warning)
    assert banner_names(reply(saved)) == [name(bn, "successful-confirmation")]
    refused = await invoke(bn, admin, "price", product_id=product.id, amount="abc")
    assert banner_names(reply(refused)) == [name(bn, "error")]
    unknown = await invoke(bn, admin, "view", product_id=424242)
    assert banner_names(reply(unknown)) == [name(bn, "error")]
    confirm = await invoke(bn, admin, "archive", product_id=product.id)  # a prompt (confirm: false)
    assert banner_names(reply(confirm)) == [name(bn, "general-response")]

    fields = {"name": "[new]", "price": "1.00", "stock": "", "max_per_order": "", "description": ""}
    form = views.add_form_custom_id(7)
    created = await submit_modal(bn.app, user=admin, custom_id=form, fields=fields)
    assert banner_names(reply(created)) == [name(bn, "successful-confirmation")]
    again = await submit_modal(bn.app, user=admin, custom_id=form, fields=fields)  # the same form twice
    assert banner_names(reply(again)) == [name(bn, "general-response")]


async def test_commands_of_an_unready_catalog_answer_with_the_error_banner(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, content={keys.LISTING_TITLE: ""})
    try:
        assert not tenv.app.ready("catalog")
        answered = await invoke(tenv, tenv.member(admin=True), "list")
        assert reply(answered).embeds[0].title == tenv.app.op("command.feature_disabled", feature="catalog")
        assert banner_names(reply(answered)) == [name(tenv, "error")]
    finally:
        await tenv.close()
