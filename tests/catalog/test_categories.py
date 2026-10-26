"""Categories: /category and /product categories (permissions, validation, names unique ignoring case, at most 24
plus "All", audit), the menu on the #catalog header (re-rendered on every change and after every pick), the private
view a pick opens (All, only the category's listed items, private, paging within Discord's limits, buy buttons),
``catalog.product_cards = false``, the migration and the /reset wipe."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.features.catalog import categories, images, repo, service, views
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog.preview import sample_png
from shopbot.features.setup import reset_store
from shopbot.ui.limits import BUTTON_LABEL, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE, validate_message
from shopbot.ui.spec import ButtonSpec
from tests.catalog.util import add_product, audit_actions, buttons, invoke, listing_channel, manager_env, menu
from tests.fakes import FakeInteraction, FakeMember, FakeMessage, click
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env

RELAXED = {"bot": {"rate_limit_per_10s": 100}}


async def category(tenv: TestEnv, user: FakeMember, command: str, /, **kwargs: Any) -> FakeInteraction:
    return await invoke(tenv, user, command, group_name="category", **kwargs)


def answer(interaction: FakeInteraction) -> discord.Embed:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0]


async def listed_categories(tenv: TestEnv) -> list[categories.Category]:
    return await tenv.app.db.run(categories.list_categories, GUILD_ID)


async def make_category(tenv: TestEnv, name: str, **kwargs: Any) -> categories.Category:
    return await tenv.app.db.transaction(
        categories.create_category, GUILD_ID, name=name, actor_id=None, now=tenv.clock.now(), **kwargs
    )


async def tag(tenv: TestEnv, product: repo.Product, *tags: categories.Category) -> None:
    for each in tags:
        await tenv.app.db.transaction(categories.add_product, product.id, each.id)


def ephemeral(message: FakeMessage) -> FakeMessage:
    message.flags = discord.MessageFlags(ephemeral=True)
    return message


def card_titles(message: FakeMessage) -> list[str | None]:
    """The product card titles of a category page (the first embed is its title card)."""
    return [e.title for e in message.embeds[1:]]


def all_name(tenv: TestEnv) -> str:
    return views.all_name(tenv.app.ui)


async def header(tenv: TestEnv) -> FakeMessage:
    channel = await listing_channel(tenv)
    await service.request_listing_refresh(tenv.app)
    return channel.messages[0]


# --------------------------------------------------------------------------------------------------
# Commands: metadata, permissions, validation, audit
# --------------------------------------------------------------------------------------------------


async def test_category_group_metadata(tenv: TestEnv) -> None:
    assert tenv.app.bot is not None
    group = tenv.app.bot.tree.get_command("category", guild=tenv.app.guild_object)
    assert group is not None
    assert group.default_permissions == discord.Permissions(manage_guild=True)
    assert group.guild_only is True
    assert {c.name for c in group.commands} == {"create", "edit", "delete", "list"}
    for command in group.commands:
        assert command.description and all(p.description for p in command.parameters)
        assert len(command.checks) == 2


@pytest.mark.parametrize("staff", [False, True])
async def test_customers_and_non_manager_staff_are_refused(tenv: TestEnv, staff: bool) -> None:
    existing = await make_category(tenv, "[kept]")
    product = await add_product(tenv)
    user = tenv.member(staff=staff)
    attempts: list[tuple[str, str, dict[str, Any]]] = [
        ("category", "create", {"name": "[new]"}),
        ("category", "edit", {"category": existing.id, "name": "[renamed]"}),
        ("category", "delete", {"category": existing.id, "confirm": True}),
        ("category", "list", {}),
        ("product", "categories", {"product_id": product.id, "add": existing.id}),
    ]
    for group_name, name, kwargs in attempts:
        interaction = await invoke(tenv, user, name, group_name=group_name, **kwargs)
        assert answer(interaction).description == tenv.text(ck.ERROR_PERMISSION)
    assert await listed_categories(tenv) == [existing]
    assert await tenv.app.db.run(categories.categories_of, GUILD_ID, product.id) == []
    assert await audit_actions(tenv, "catalog.") == []


async def test_a_catalog_manager_may_manage_categories(tmp_path: Path) -> None:
    tenv, role = await manager_env(tmp_path)
    try:
        manager = tenv.guild.add_member(name="manager", roles=[role])
        created = await category(tenv, manager, "create", name="[by manager]")
        assert answer(created).title == tenv.app.op("catalog.category.created", category_id=1, name="[by manager]")
    finally:
        await tenv.close()


async def test_create_puts_the_category_in_the_menu_at_once_and_audits(tenv: TestEnv) -> None:
    await add_product(tenv, "[p]")
    head = await header(tenv)
    # No category yet: no menu (its only choice, All, would repeat the public cards) and no line about it.
    assert menu(head) == []
    assert tenv.text(keys.PICKER_DESCRIPTION) not in (head.embeds[0].description or "")
    admin = tenv.member(admin=True)
    created = await category(tenv, admin, "create", name="Old  stock ", emoji="🔥", description="Last pieces")
    reply = answer(created)
    assert reply.title == tenv.app.op("catalog.category.created", category_id=1, name="Old stock")
    assert reply.description == tenv.app.op("catalog.category.created_hint", name="Old stock")
    assert reply.fields[0].name == tenv.app.op("catalog.refresh.field")  # the listing was refreshed right away
    assert created.last_sent is not None and created.last_sent.flags.ephemeral
    (stored,) = await listed_categories(tenv)
    assert (stored.name, stored.emoji, stored.description, stored.position) == ("Old stock", "🔥", "Last pieces", 1)
    assert menu(head) == [("0", tenv.text(keys.PICKER_ALL)), ("1", "Old stock")]
    picker = next(
        getattr(c, "item", c) for c in head.components() if isinstance(getattr(c, "item", c), discord.ui.Select)
    )
    option = picker.options[1]
    assert str(option.emoji) == "🔥" and option.description == "Last pieces"
    assert tenv.text(keys.PICKER_DESCRIPTION) in (head.embeds[0].description or "")

    first = await category(tenv, admin, "create", name="New in", position=0)
    assert "New in" in (answer(first).title or "")
    assert [name for _, name in menu(head)] == [tenv.text(keys.PICKER_ALL), "New in", "Old stock"]
    events = await audit_actions(tenv, "catalog.category_")
    assert [e.action for e in events] == ["catalog.category_create", "catalog.category_create"]
    assert events[0].details is not None and events[0].details["name"] == "Old stock"


async def test_names_are_unique_ignoring_case_and_all_is_built_in(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    await category(tenv, admin, "create", name="Old stock")
    await category(tenv, admin, "create", name="Ärmel")
    for taken in ("OLD STOCK", "ärmel"):
        refused = await category(tenv, admin, "create", name=taken)
        existing = "Old stock" if taken == "OLD STOCK" else "Ärmel"
        category_id = 1 if taken == "OLD STOCK" else 2
        assert answer(refused).title == tenv.app.op("catalog.cmd.invalid")
        expected = tenv.app.op("catalog.category.invalid.duplicate", category_id=category_id, name=existing)
        assert answer(refused).description == expected
    for reserved in ("all", all_name(tenv).upper()):
        refused = await category(tenv, admin, "create", name=reserved)
        assert answer(refused).description == tenv.app.op(
            "catalog.category.invalid.name_all", name=discord.utils.escape_markdown(reserved)
        )
    assert [c.name for c in await listed_categories(tenv)] == ["Old stock", "Ärmel"]
    # Renaming: to another category's name is refused; changing only the case of its own name is fine.
    clash = await category(tenv, admin, "edit", category=2, name="old Stock")
    assert answer(clash).description == tenv.app.op(
        "catalog.category.invalid.duplicate", category_id=1, name="Old stock"
    )
    recased = await category(tenv, admin, "edit", category=1, name="OLD stock")
    assert answer(recased).title == tenv.app.op("catalog.category.updated", category_id=1, name="OLD stock")


def test_emoji_forms_and_single_emoji() -> None:
    icons = {"tag", "fire_icon"}
    for written in ("tag", ":tag:", "icon_tag", "{icon_tag}", " TAG "):
        assert categories.normalize_emoji(written, icons) == "{icon_tag}"
    assert categories.normalize_emoji("", icons) == ""
    for refused, names in (("nope", icons), ("tag", None)):  # unknown icon; icons switched off
        with pytest.raises(categories.InvalidCategory):
            categories.normalize_emoji(refused, names)
    for single in ("🔥", "🍕", "🇺🇸", "🇽🇰", "❤️", "❤", "⭐", "✅"):
        assert categories.is_single_emoji(single), single
        assert categories.normalize_emoji(single, icons) == single
    pizza, burger, zwj, light = "\U0001f355", "\U0001f354", "‍", "\U0001f3fb"
    combined = (
        "👍🏽",  # a skin tone
        pizza + light,  # a skin tone on an emoji that takes none
        "👨‍👩‍👧",  # joined from several (U+200D)
        pizza + zwj + burger,  # a join no one defined
        pizza + zwj,  # a join that ends half-way
        pizza + "️️",  # the emoji presentation twice
        "\U0001f1e6\U0001f1e6",  # two flag letters that are no country
        "\U0001f1fa",  # one flag letter
        light,  # a skin tone alone
        "\U0001f6d8",  # in the emoji block, but no emoji (unassigned)
        "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # a tag sequence
    )
    for not_one in ("🔥🔥", "→", "★", "a", "🇺🇸🇨🇦", "️", "🔥 x", "x" * 20, "1️⃣", *combined):
        assert not categories.is_single_emoji(not_one), not_one
        with pytest.raises(categories.InvalidCategory):
            categories.normalize_emoji(not_one, icons)


async def test_icons_are_stored_as_placeholders_and_shown_as_the_option_emoji(tmp_path: Path) -> None:
    folder = tmp_path / "config" / "emojis"
    folder.mkdir(parents=True)
    (folder / "tag.png").write_bytes(sample_png())
    tenv = await make_test_env(tmp_path, settings=make_settings({"icons": {"enabled": True}}))
    try:
        tenv.app.icons.ids["tag"] = 123456789012345678
        await add_product(tenv)
        head = await header(tenv)
        await category(tenv, tenv.member(admin=True), "create", name="[tagged]", emoji=":tag:")
        assert (await listed_categories(tenv))[0].emoji == "{icon_tag}"
        picker = next(
            getattr(c, "item", c) for c in head.components() if isinstance(getattr(c, "item", c), discord.ui.Select)
        )
        assert str(picker.options[1].emoji) == "<:tag:123456789012345678>"
    finally:
        await tenv.close()


async def test_a_category_without_emoji_shows_the_all_options_icon(tmp_path: Path) -> None:
    folder = tmp_path / "config" / "emojis"
    folder.mkdir(parents=True)
    (folder / "tag.png").write_bytes(sample_png())
    settings = make_settings({"icons": {"enabled": True}})
    tenv = await make_test_env(tmp_path, settings=settings, content={keys.PICKER_ALL: "{icon_tag} [all]"})
    try:
        await add_product(tenv)
        await make_category(tenv, "[plain]")
        head = await header(tenv)
        picker = next(
            getattr(c, "item", c) for c in head.components() if isinstance(getattr(c, "item", c), discord.ui.Select)
        )
        assert [o.emoji for o in picker.options] == [None, None]  # the icon is not uploaded yet: no emoji at all
        tenv.app.icons.ids["tag"] = 123456789012345678
        await service.request_listing_refresh(tenv.app)
        picker = next(
            getattr(c, "item", c) for c in head.components() if isinstance(getattr(c, "item", c), discord.ui.Select)
        )
        assert [str(o.emoji) for o in picker.options] == ["<:tag:123456789012345678>"] * 2  # the options line up
    finally:
        await tenv.close()


async def test_invalid_values_are_refused_and_nothing_is_saved(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    op = tenv.app.op
    bad_emoji = await category(tenv, admin, "create", name="[x]", emoji="nope")
    assert answer(bad_emoji).description == op("catalog.category.invalid.emoji")
    bad_name = await category(tenv, admin, "create", name="bad\u0007name")
    assert answer(bad_name).description == op("catalog.category.invalid.name", max=categories.NAME_MAX)
    reply = await category_admin_create(tenv, description="d" * (categories.DESCRIPTION_MAX + 1))
    assert reply.description == op("catalog.category.invalid.description", max=categories.DESCRIPTION_MAX)
    assert await listed_categories(tenv) == []
    assert await audit_actions(tenv, "catalog.category_") == []


async def category_admin_create(tenv: TestEnv, **kwargs: Any) -> service.OpReply:
    from shopbot.features.catalog import category_admin

    return await category_admin.create(tenv.app, actor_id=1, name="[x]", **kwargs)


async def test_at_most_24_categories_besides_all(tenv: TestEnv) -> None:
    for i in range(categories.MAX_CATEGORIES):
        await make_category(tenv, f"[c{i}]")
    await add_product(tenv)
    refused = await category(tenv, tenv.member(admin=True), "create", name="[one too many]")
    assert answer(refused).description == tenv.app.op("catalog.category.too_many", max=categories.MAX_CATEGORIES)
    head = await header(tenv)
    assert len(menu(head)) == 25  # All + 24: one full Discord menu
    assert len(await listed_categories(tenv)) == categories.MAX_CATEGORIES


async def test_edit_changes_values_and_clears_emoji_and_description(tenv: TestEnv) -> None:
    await add_product(tenv)
    head = await header(tenv)
    admin = tenv.member(admin=True)
    await category(tenv, admin, "create", name="[old]", emoji="🔥", description="[about]")
    edited = await category(tenv, admin, "edit", category=1, name="[new]", emoji="-", description=" - ", position=7)
    assert answer(edited).title == tenv.app.op("catalog.category.updated", category_id=1, name="[new]")
    (stored,) = await listed_categories(tenv)
    assert (stored.name, stored.emoji, stored.description, stored.position) == ("[new]", "", "", 7)
    assert menu(head)[1] == ("1", "[new]")
    (event,) = [e for e in await audit_actions(tenv, "catalog.category_") if e.action == "catalog.category_edit"]
    assert event.details == {
        "description": ["[about]", ""],
        "emoji": ["🔥", ""],
        "name": ["[old]", "[new]"],
        "position": [1, 7],
    }

    nothing = await category(tenv, admin, "edit", category=1)
    assert answer(nothing).description == tenv.app.op("catalog.category.edit_nothing")
    same = await category(tenv, admin, "edit", category=1, name="[new]")
    assert answer(same).title == tenv.app.op("catalog.category.unchanged", category_id=1, name="[new]")
    missing = await category(tenv, admin, "edit", category=99, name="[x]")
    assert answer(missing).title == tenv.app.op("catalog.category.not_found", category_id=99)
    assert len(await audit_actions(tenv, "catalog.category_edit")) == 1


async def test_delete_asks_first_and_keeps_the_products(tenv: TestEnv) -> None:
    first, second = await add_product(tenv, "[a]"), await add_product(tenv, "[b]")
    head = await header(tenv)
    admin = tenv.member(admin=True)
    await category(tenv, admin, "create", name="[gone]")
    (gone,) = await listed_categories(tenv)
    await tag(tenv, first, gone)
    await tag(tenv, second, gone)
    old = await add_product(tenv, "[old]")  # archived after tagging: not counted as an item, its link still goes
    await tag(tenv, old, gone)
    await tenv.app.db.transaction(repo.update_product, old.id, old.version, now=1, archived=True)

    asked = await category(tenv, admin, "delete", category=gone.id)
    assert answer(asked).title == tenv.app.op("catalog.category.delete_title", category_id=gone.id)
    assert answer(asked).description == tenv.app.op("catalog.category.delete_confirm", name="[gone]", items=2)
    assert await listed_categories(tenv) == [gone]

    deleted = await category(tenv, admin, "delete", category=gone.id, confirm=True)
    assert answer(deleted).title == tenv.app.op("catalog.category.deleted", name="[gone]", items=2)
    assert await listed_categories(tenv) == []
    assert [p.name for p in await tenv.app.db.run(repo.list_products, GUILD_ID)] == ["[a]", "[b]"]
    links = await tenv.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM product_categories").fetchone()[0])
    assert links == 0
    assert menu(head) == []  # the last category went: the header has no menu again
    (event,) = await audit_actions(tenv, "catalog.category_delete")
    assert event.details == {"name": "[gone]", "products_untagged": 3}
    again = await category(tenv, admin, "delete", category=gone.id, confirm=True)
    assert answer(again).title == tenv.app.op("catalog.category.not_found", category_id=gone.id)


async def test_list_shows_all_and_each_category_with_its_items(tenv: TestEnv) -> None:
    shown, hidden = await add_product(tenv, "[shown]"), await add_product(tenv, "[hidden]", active=False)
    full = await make_category(tenv, "[full]", description="[about]")
    await make_category(tenv, "[empty]")
    await tag(tenv, shown, full)
    await tag(tenv, hidden, full)
    listed = await category(tenv, tenv.member(admin=True), "list")
    text = listed.last_sent.text() if listed.last_sent else ""
    op = tenv.app.op
    assert answer(listed).title == op("catalog.category.list.title", count=2, max=categories.MAX_CATEGORIES)
    assert op("catalog.category.list.all", items=1) in text
    line = op("catalog.category.list.line", category_id=full.id, emoji="·", name="[full]", items=1, position=1)
    assert line + op("catalog.category.list.hidden", hidden=1) in text
    assert op("catalog.category.list.description", description="[about]") in text
    assert "[empty]" in text.replace("\\", "")


def test_a_long_category_list_continues_with_one_card_per_message(tenv: TestEnv) -> None:
    menu_items = [
        categories.Category(i, GUILD_ID, f"[{i:02d}]".ljust(categories.NAME_MAX, "n"), "", "d" * 100, i, 0, 0)
        for i in range(1, categories.MAX_CATEGORIES + 1)
    ]
    messages = views.category_list_messages(tenv.app.ui, tenv.app.op, menu_items, {}, 0)
    assert len(messages) == 2 and all(len(m.embeds) == 1 for m in messages)  # never two cards in one message
    assert all(validate_message(m) == [] for m in messages)
    assert messages[0].banner == tenv.app.ui.banner("staff-dashboard") and messages[1].banner is None
    assert messages[0].embeds[0].title and messages[1].embeds[0].title is None  # a continuation of the same list
    text = "".join(m.embeds[0].description or "" for m in messages)
    assert all(c.name in text for c in menu_items)


# --------------------------------------------------------------------------------------------------
# /product categories
# --------------------------------------------------------------------------------------------------


async def test_a_product_can_be_in_several_categories(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    old, sale = await make_category(tenv, "Old stock"), await make_category(tenv, "Sale")
    admin = tenv.member(admin=True)
    op = tenv.app.op

    def run(**kwargs: Any) -> Any:
        return invoke(tenv, admin, "categories", product_id=product.id, **kwargs)

    added = answer(await run(add=old.id))
    assert added.title == op("catalog.cmd.categories_title", product_id=product.id)
    assert added.description == "\n".join(
        (op("catalog.cmd.category_added", name="Old stock"), op("catalog.cmd.categories_now", names="Old stock"))
    )
    both = answer(await run(add=sale.id))
    assert op("catalog.cmd.categories_now", names="Old stock, Sale") in (both.description or "")
    again = answer(await run(add=old.id))
    assert op("catalog.cmd.category_already_in", name="Old stock") in (again.description or "")
    moved = answer(await run(add=old.id, remove=sale.id))  # add and remove in one go
    assert op("catalog.cmd.category_removed", name="Sale") in (moved.description or "")
    shown = answer(await run())
    assert shown.description == op("catalog.cmd.categories_now", names="Old stock")
    out = answer(await run(remove=old.id))
    assert op("catalog.cmd.categories_none") in (out.description or "")
    not_in = answer(await run(remove=old.id))
    assert op("catalog.cmd.category_not_in", name="Old stock") in (not_in.description or "")

    events = await audit_actions(tenv, "catalog.product_categories")
    assert [e.details for e in events] == [
        {"added": "Old stock"},
        {"added": "Sale"},
        {"removed": "Sale"},
        {"removed": "Old stock"},
    ]
    unknown = answer(await run(add=99))
    assert unknown.title == op("catalog.category.not_found", category_id=99)
    nothing = answer(await invoke(tenv, admin, "categories", product_id=999, add=old.id))
    assert nothing.title == op("catalog.cmd.not_found", product_id=999)
    await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=1, archived=True)
    archived = answer(await run(add=old.id))
    assert archived.title == op("catalog.cmd.archived_product", product_id=product.id)


async def test_product_view_names_its_categories(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    await tag(tenv, product, await make_category(tenv, "[one]"), await make_category(tenv, "[two]"))
    viewed = await invoke(tenv, tenv.member(admin=True), "view", product_id=product.id)
    details = viewed.sent[0].embeds[0]
    field = next(f for f in details.fields if f.name == tenv.app.op("catalog.view.categories"))
    assert field.value == "[one], [two]"


# --------------------------------------------------------------------------------------------------
# The private view a pick opens
# --------------------------------------------------------------------------------------------------


async def test_all_shows_every_orderable_product_and_a_category_only_its_own(tenv: TestEnv) -> None:
    old, sale = await make_category(tenv, "Old stock"), await make_category(tenv, "Sale")
    a = await add_product(tenv, "a")
    inactive = await add_product(tenv, "inactive", active=False)
    archived = await add_product(tenv, "archived")
    await tenv.app.db.transaction(repo.update_product, archived.id, archived.version, now=1, archived=True)
    untagged = await add_product(tenv, "untagged", stock=0)  # sold out: still shown, its buy button disabled
    e = await add_product(tenv, "e")
    await tag(tenv, a, old)
    await tag(tenv, inactive, old)
    await tag(tenv, archived, old)
    await tag(tenv, e, sale, old)

    everything = await service.category_view(tenv.app, categories.ALL, 0)
    assert [x.title for x in everything.embeds[1:]] == ["a", "untagged", "e"]
    assert everything.embeds[0].title == tenv.text(keys.CATEGORY_TITLE).replace("{category}", all_name(tenv))
    old_page = await service.category_view(tenv.app, old.id, 0)
    assert [x.title for x in old_page.embeds[1:]] == ["a", "e"]
    assert old_page.embeds[0].title == tenv.text(keys.CATEGORY_TITLE).replace("{category}", "Old stock")
    sale_page = await service.category_view(tenv.app, sale.id, 0)
    assert [x.title for x in sale_page.embeds[1:]] == ["e"]
    buy = {b.custom_id: b.disabled for row in everything.rows for b in row}
    assert buy[views.buy_custom_id(untagged.id)] is True and buy[views.buy_custom_id(a.id)] is False
    with pytest.raises(categories.CategoryNotFound):
        await service.category_view(tenv.app, 99, 0)


async def test_a_pick_answers_privately_and_resets_the_menu(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]", description="[about]")
    old = await make_category(tenv, "Old stock")
    await tag(tenv, product, old)
    head = await header(tenv)
    channel_messages = len(head.channel.messages)
    customer = tenv.member()
    for _ in range(2):  # the same category can be picked again
        picked = await click(tenv.app, user=customer, custom_id="sb:ct:cat", values=[str(old.id)], message=head)
        # acknowledged by re-rendering only the header's components; the card and banner stay
        assert picked.response_kinds() == ["edit_message"]
        assert set(picked.response.calls[0][1]) == {"view"}
        assert menu(head) == [("0", tenv.text(keys.PICKER_ALL)), (str(old.id), "Old stock")]
        private = picked.last_sent
        assert private is not None and private.flags.ephemeral
        title = tenv.text(keys.CATEGORY_TITLE).replace("{category}", "Old stock")
        assert private.embeds[0].title == title
        assert private.embeds[0].description == tenv.text(keys.CATEGORY_INTRO).replace("{count}", "1").replace(
            "{category}", "Old stock"
        )
        assert card_titles(private) == ["[p]"] and private.embeds[1].description == "[about]"
        label = tenv.text(keys.CATEGORY_BUTTON_BUY).replace("{name}", "[p]")
        assert buttons(private) == [(views.buy_custom_id(product.id), label, False)]  # one page: no paging
    assert len(head.channel.messages) == channel_messages  # nothing new in the public channel


async def test_an_empty_category_says_so(tenv: TestEnv) -> None:
    await add_product(tenv)
    empty = await make_category(tenv, "[nothing yet]")
    head = await header(tenv)
    picked = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:cat", values=[str(empty.id)], message=head)
    assert picked.last_sent is not None and picked.last_sent.flags.ephemeral
    (card,) = picked.last_sent.embeds
    assert card.description == tenv.text(keys.CATEGORY_EMPTY).replace("{category}", "[nothing yet]")
    assert buttons(picked.last_sent) == []


async def test_a_deleted_category_is_not_found_and_the_menu_is_still_reset(tenv: TestEnv) -> None:
    await add_product(tenv)
    head = await header(tenv)
    stale = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:cat", values=["42"], message=head)
    assert stale.response_kinds() == ["edit_message"]
    assert stale.last_sent is not None and stale.last_sent.embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)
    paging = await click(tenv.app, user=tenv.member(name="other"), custom_id="sb:ct:catpage:42:1")
    assert paging.last_sent is not None and paging.last_sent.embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_picking_another_category_right_away_is_answered_and_resets_the_menu(tmp_path: Path) -> None:
    # The anti-double-click spacing (700 ms shipped) refuses the same click; another category is another click.
    settings = make_settings({"bot": {"interaction_cooldown_ms": 10_000, "rate_limit_per_10s": 100}})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        await add_product(tenv, "[p]")
        first, second = await make_category(tenv, "[a]"), await make_category(tenv, "[b]")
        head = await header(tenv)
        customer = tenv.member()
        for picked_category in (first, second):
            value = str(picked_category.id)
            picked = await click(tenv.app, user=customer, custom_id="sb:ct:cat", values=[value], message=head)
            assert picked.response_kinds() == ["edit_message"]  # the menu is reset
            assert picked.last_sent is not None and picked.last_sent.flags.ephemeral
            assert picked.last_sent.embeds[0].title == views.category_title(tenv.app.ui, picked_category.name)
        again = await click(tenv.app, user=customer, custom_id="sb:ct:cat", values=[str(second.id)], message=head)
        assert again.last_sent is not None and again.last_sent.embeds[0].description == tenv.text(ck.ERROR_BUSY)
    finally:
        await tenv.close()


async def test_a_category_emoji_discord_refuses_does_not_stop_the_listing(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes import http_error

    pizza = "\U0001f355"
    await add_product(tenv, "[p]")
    await make_category(tenv, "[food]", emoji=pizza)  # valid here; say Discord does not know it
    channel = await listing_channel(tenv)
    edit = FakeMessage.edit

    async def refusing(self: FakeMessage, **kwargs: Any) -> FakeMessage:
        view = kwargs.get("view")
        items = [getattr(c, "item", c) for c in (view.children if view is not None else [])]
        if any(getattr(o.emoji, "name", None) == pizza for i in items for o in getattr(i, "options", [])):
            raise http_error(discord.HTTPException, 400, 50035, "Invalid Form Body (Invalid emoji)")
        return await edit(self, **kwargs)

    async def refusing_send(content: str | None = None, **kwargs: Any) -> FakeMessage:
        await refusing(FakeMessage(channel), **kwargs)  # the same check for a new message
        return await type(channel).send(channel, content, **kwargs)

    monkeypatch.setattr(FakeMessage, "edit", refusing)
    monkeypatch.setattr(channel, "send", refusing_send)
    report = await service.request_listing_refresh(tenv.app)
    assert report.ok and report.lines[-1] == tenv.app.op("catalog.refresh.emoji_refused")
    head = channel.messages[0]
    assert menu(head) == [("0", tenv.text(keys.PICKER_ALL)), ("1", "[food]")]  # the menu, without that emoji
    assert len(channel.messages) == 2  # the product card was posted too
    # A pick resets the menu the same way; later refreshes keep updating the product cards.
    picked = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:cat", values=["1"], message=head)
    assert picked.response_kinds() == ["edit_message"]
    await add_product(tenv, "[new]")
    report = await service.request_listing_refresh(tenv.app)
    assert report.ok and "[new]" in channel.messages[-1].text()


async def test_long_names_are_cut_on_the_buy_buttons_of_a_category_page(tenv: TestEnv) -> None:
    long_name = "[" + "n" * 97 + "]"
    product = await add_product(tenv, long_name)
    spec = await service.category_view(tenv.app, categories.ALL, 0)
    (buy,) = [c for row in spec.rows for c in row]
    assert isinstance(buy, ButtonSpec) and buy.custom_id == views.buy_custom_id(product.id)
    assert buy.label is not None and len(buy.label) == BUTTON_LABEL and buy.label.endswith("…")
    prefix = tenv.text(keys.CATEGORY_BUTTON_BUY).split("{name}")[0]
    assert buy.label.startswith(prefix + "[nnn")  # still names its item, unlike the plain short label


async def test_pages_edit_the_private_message_in_place(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings(RELAXED))
    try:
        products = [await add_product(tenv, f"p{i}", price=100 * (i + 1)) for i in range(7)]
        head = await header(tenv)
        customer = tenv.member()
        picked = await click(tenv.app, user=customer, custom_id="sb:ct:cat", values=["0"], message=head)
        private = picked.last_sent
        assert private is not None and private.flags.ephemeral
        assert card_titles(private) == [p.name for p in products[:5]]  # catalog.products_per_category_page = 5
        previous, following = tenv.text(ck.BUTTON_PREV), tenv.text(ck.BUTTON_NEXT)
        assert buttons(private)[5:] == [("sb:ct:catpage:0:-1", previous, True), ("sb:ct:catpage:0:1", following, False)]
        indicator = tenv.text(ck.PAGE_INDICATOR).replace("{page}", "1").replace("{pages}", "2")
        assert private.embeds[0].footer.text == f"{tenv.text(ck.FOOTER_TEXT)} · {indicator}"
        assert all(e.footer.text is None for e in private.embeds[1:])  # the title card carries the footer

        nxt = await click(tenv.app, user=customer, custom_id="sb:ct:catpage:0:1", message=ephemeral(private))
        assert nxt.response_kinds() == ["edit_message"]
        assert card_titles(private) == [p.name for p in products[5:]]
        assert buttons(private)[2:] == [("sb:ct:catpage:0:0", previous, False), ("sb:ct:catpage:0:2", following, True)]
        # products changed since: the page is read again (and clamped to the pages that exist)
        await tenv.app.db.transaction(repo.update_product, products[6].id, products[6].version, now=1, active=False)
        await click(tenv.app, user=customer, custom_id="sb:ct:catpage:0:1", message=private)
        assert card_titles(private) == ["p5"]
    finally:
        await tenv.close()


async def test_pages_stay_within_discord_limits(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_category_page": 9}}))
    try:
        long_text = "x" * 3500
        names = [f"[long{i}]" for i in range(3)] + [f"[short{i}]" for i in range(14)]
        for name in names:
            await add_product(tenv, name, description=long_text if name.startswith("[long") else "")
        seen: list[str | None] = []
        page = 0
        while True:
            spec = await service.category_view(tenv.app, categories.ALL, page)
            assert validate_message(spec) == []
            assert len(spec.embeds) <= EMBEDS_PER_MESSAGE
            assert sum(e.text_length() for e in spec.embeds) <= EMBED_TOTAL_CHARS
            assert len(spec.rows) <= 5
            seen += [e.title for e in spec.embeds[1:]]
            following = views.category_page_custom_id(categories.ALL, page + 1)
            if not any(b.custom_id == following and not b.disabled for row in spec.rows for b in row):  # type: ignore[union-attr]
                break
            page += 1
        assert seen == [discord.utils.escape_markdown(n) for n in names]  # every item once, in order, nothing cut
        assert page >= 3  # the long descriptions start new pages before 9 cards
    finally:
        await tenv.close()


async def test_uploaded_pictures_are_attached_to_the_page(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[pictured]")
    data = sample_png()
    await tenv.app.db.transaction(
        images.store_image,
        GUILD_ID,
        product.id,
        images.validate_bytes("p.png", data),
        actor_id=None,
        now=tenv.clock.now(),
    )
    spec = await service.category_view(tenv.app, categories.ALL, 0)
    assert spec.files is not None and len(spec.files) == 1 and spec.files[0].data == data
    assert spec.embeds[1].image_url == f"attachment://{spec.files[0].filename}"
    assert spec.banner_in_card is False  # the banner never takes a product card's picture slot


async def test_the_buy_button_of_a_page_opens_the_order(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]", stock=3)
    head = await header(tenv)
    customer = tenv.member()
    picked = await click(tenv.app, user=customer, custom_id="sb:ct:cat", values=["0"], message=head)
    assert picked.last_sent is not None
    (buy,) = buttons(picked.last_sent)
    assert buy[0] == f"sb:co:buy:{product.id}"
    bought = await click(tenv.app, user=customer, custom_id=buy[0] or "", message=ephemeral(picked.last_sent))
    assert bought.response_kinds() == ["defer"]  # checkout's buy flow answers privately
    assert bought.last_sent is not None and bought.last_sent.flags.ephemeral
    sessions = await tenv.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM checkout_sessions").fetchone()[0])
    assert sessions == 1


# --------------------------------------------------------------------------------------------------
# The listing: the menu on the header, product cards on or off
# --------------------------------------------------------------------------------------------------


async def test_the_menu_shows_only_while_products_are_listed(tenv: TestEnv) -> None:
    await make_category(tenv, "[c]")
    head = await header(tenv)
    assert menu(head) == []  # nothing to browse: the header with the 'empty' text, no menu
    assert tenv.text(keys.PICKER_DESCRIPTION) not in (head.embeds[0].description or "")
    await add_product(tenv)
    await service.request_listing_refresh(tenv.app)
    assert [value for value, _ in menu(head)] == ["0", "1"]


async def test_product_cards_off_leaves_only_the_header_with_the_menu(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"product_cards": False}}))
    try:
        await add_product(tenv, "[a]")
        await add_product(tenv, "[b]")
        channel = await listing_channel(tenv)
        report = await service.request_listing_refresh(tenv.app)
        assert report.ok and len(channel.messages) == 1
        (head,) = channel.messages
        assert [value for value, _ in menu(head)] == ["0"]
        assert "[a]" not in head.text()
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Database and /reset
# --------------------------------------------------------------------------------------------------


async def test_schema_backs_the_rules(tenv: TestEnv) -> None:
    product = await add_product(tenv)
    first = await make_category(tenv, "Old stock")
    await tag(tenv, product, first)

    def duplicate(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO catalog_categories (guild_id, name, created_at, updated_at) VALUES (?, 'OLD STOCK', 0, 0)",
            (GUILD_ID,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        await tenv.app.db.transaction(duplicate)

    def delete_row(conn: sqlite3.Connection) -> int:
        conn.execute("DELETE FROM catalog_categories WHERE id = ?", (first.id,))
        return int(conn.execute("SELECT COUNT(*) FROM product_categories").fetchone()[0])

    assert await tenv.app.db.transaction(delete_row) == 0  # links go with the category
    assert await tenv.app.db.run(repo.get_product, GUILD_ID, product.id) is not None


async def test_reset_wipes_categories_with_the_products(tenv: TestEnv) -> None:
    product = await add_product(tenv)
    await tag(tenv, product, await make_category(tenv, "[c]"))
    counts = await tenv.app.db.transaction(reset_store.wipe_shop_data, tenv.clock.now())
    assert counts["catalog_categories"] == 1 and counts["product_categories"] == 1 and counts["products"] == 1
    assert await listed_categories(tenv) == []
