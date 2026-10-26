"""The "Edit text" message command: find the texts a bot message shows, edit one, posted panels follow."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import discord
import pytest
from discord import app_commands

from shopbot.commands import on_tree_error
from shopbot.config.content_keys import Kind, ck
from shopbot.db.repos import state as state_repo
from shopbot.features.admin import edit_text, service
from shopbot.features.admin.commands import EDIT_TEXT_COMMAND
from shopbot.features.core_keys import AUTHOR_NAME
from shopbot.features.guides import how_to_buy
from shopbot.ui.discord_render import to_embed, to_view
from shopbot.ui.spec import ButtonSpec, EmbedSpec, MessageSpec, SelectOptionSpec, SelectSpec
from tests.admin.util import admin, customer, sent
from tests.fakes import FakeInteraction, FakeMessage, click, submit_modal
from tests.guides.util import channel, guide_env, messages
from tests.helpers import TestEnv


@pytest.fixture
async def genv(tmp_path: Path) -> Any:
    tenv = await guide_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def as_discord_message(message: FakeMessage) -> Any:
    """What discord.py hands the message command: embeds plus real component objects (from the sent view)."""
    rows = [discord.ActionRow(row) for row in message.view.to_components()] if message.view is not None else []
    return types.SimpleNamespace(id=message.id, author=message.author, embeds=message.embeds, components=rows)


async def run_edit_text(tenv: TestEnv, user: Any, message: Any) -> FakeInteraction:
    assert tenv.app.bot is not None
    menu = tenv.app.bot.tree.get_command(
        EDIT_TEXT_COMMAND, guild=tenv.app.guild_object, type=discord.AppCommandType.message
    )
    assert isinstance(menu, app_commands.ContextMenu)
    interaction = FakeInteraction(
        client=tenv.app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        if not await menu._check_can_run(interaction):  # type: ignore[arg-type]
            raise app_commands.CheckFailure("check failed")
        await menu.callback(interaction, message)  # type: ignore[arg-type,call-arg]
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    return interaction


def select_options(message: FakeMessage) -> list[discord.SelectOption]:
    items = [getattr(item, "item", item) for item in message.components()]  # routed items wrap the select
    selects = [item for item in items if isinstance(item, discord.ui.Select)]
    assert len(selects) == 1
    return list(selects[0].options)


async def test_lists_the_texts_of_a_panel_in_display_order_and_edits_one(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    panel = messages(genv, "how_to_buy")[0]
    owner = admin(genv)
    interaction = await run_edit_text(genv, owner, as_discord_message(panel))
    listing = sent(interaction)
    keys = [option.value for option in select_options(listing)]
    # In display order: author line, title, steps, then the link buttons under the embed.
    order = [AUTHOR_NAME, how_to_buy.TITLE, how_to_buy.step_name_key(1), how_to_buy.link_label_key("catalog")]
    assert [key for key in keys if key in order] == order
    labels = {option.value: option.label for option in select_options(listing)}
    assert labels[how_to_buy.TITLE] == genv.text(how_to_buy.TITLE)

    picked = await click(genv.app, user=owner, custom_id="sb:ad:etext", values=[how_to_buy.TITLE])
    modal = picked.response.modal
    assert modal is not None
    inputs = [item for item in modal.walk_children() if isinstance(item, discord.ui.TextInput)]
    assert inputs[0].default == genv.text(how_to_buy.TITLE)

    saved = await submit_modal(genv.app, user=owner, custom_id=modal.custom_id, fields={"value": "New title"})
    assert await genv.app.db.run(state_repo.overrides_all) == {how_to_buy.TITLE: "New title"}
    # The reply shows what really happened to each posted panel (here: the guide was updated in place).
    assert f"`how_to_buy`: 1 message(s) in {channel(genv, 'how_to_buy').mention}" in sent(saved).text()
    assert "1 updated" in sent(saved).text()
    assert messages(genv, "how_to_buy")[0].embeds[0].title == "New title"  # the posted panel follows at once


async def test_clearing_an_override_puts_the_posted_panel_back_at_once(genv: TestEnv) -> None:
    from shopbot.features.admin import commands

    await how_to_buy.publish(genv.app)
    genv.app.content.set_override(how_to_buy.TITLE, "[override]")
    await genv.app.db.transaction(state_repo.override_set, how_to_buy.TITLE, "[override]", 1, 1)
    await how_to_buy.publish(genv.app)
    assert messages(genv, "how_to_buy")[0].embeds[0].title == "[override]"
    interaction = FakeInteraction(client=genv.app.bot, user=admin(genv), guild=genv.guild)
    await commands.handle_content_clear(interaction, how_to_buy.TITLE)  # type: ignore[arg-type]
    assert messages(genv, "how_to_buy")[0].embeds[0].title == genv.text(how_to_buy.TITLE)
    assert "1 updated" in sent(interaction).text()


def test_the_messages_own_feature_comes_first_when_texts_are_shared() -> None:
    shared = "Choose an option from the menu below"
    own = ck("own.text", Kind.DESCRIPTION, "faq", "g", "doc")
    other = ck("other.text", Kind.DESCRIPTION, "shipping", "g", "doc")
    later = ck("own.later", Kind.DESCRIPTION, "faq", "g", "doc")
    values = {"own.text": shared, "other.text": shared, "own.later": "A later text of this message"}
    texts = [shared, "A later text of this message"]
    keys = edit_text.find_keys(texts, values, [other, own, later], features=frozenset({"faq"}))
    assert keys == ["own.text", "own.later", "other.text"]  # the other feature's twin goes last
    # Without components the feature with the most unambiguous matches is taken.
    assert edit_text.find_keys(texts, values, [other, own, later]) == keys


def test_server_names_and_form_titles_are_never_offered() -> None:
    role = ck("server.role.x.name", Kind.ROLE_NAME, "server", "g", "doc")
    title = ck("x.modal_title", Kind.MODAL_TITLE, "faq", "g", "doc")
    values = {"server.role.x.name": "Customer", "x.modal_title": "Customer"}
    assert edit_text.find_keys(["Customer"], values, [role, title]) == []


async def test_only_the_bots_own_messages(genv: TestEnv) -> None:
    other = types.SimpleNamespace(id=1, author=customer(genv), embeds=[], components=[])
    interaction = await run_edit_text(genv, admin(genv), other)
    assert sent(interaction).embeds[0].title == "Only messages sent by this bot can be edited here."


async def test_a_message_without_configured_text(genv: TestEnv) -> None:
    empty = types.SimpleNamespace(
        id=1, author=genv.guild.me, embeds=[discord.Embed(title="Typed by staff")], components=[]
    )
    interaction = await run_edit_text(genv, admin(genv), empty)
    assert sent(interaction).embeds[0].title == "No editable text found"


async def test_non_admins_cannot_use_it(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    panel = messages(genv, "how_to_buy")[0]
    interaction = await run_edit_text(genv, customer(genv), as_discord_message(panel))
    assert interaction.response.modal is None
    assert all("Edit text in this message" not in m.text() for m in interaction.sent)


async def test_the_picked_text_is_checked_again(genv: TestEnv) -> None:
    picked = await click(genv.app, user=customer(genv), custom_id="sb:ad:etext", values=[how_to_buy.TITLE])
    assert picked.response.modal is None
    unknown = await click(genv.app, user=admin(genv), custom_id="sb:ad:etext", values=["no.such.key"])
    assert unknown.response.modal is None


def _key(key: str, kind: Kind = Kind.DESCRIPTION, placeholders: tuple[str, ...] = ()) -> Any:
    return ck(key, kind, "core", "group", "doc", placeholders=placeholders)


def test_find_keys_matches_whole_texts_parts_and_placeholders() -> None:
    definitions = [
        _key("a.title", Kind.TITLE, ("number",)),
        _key("a.intro"),
        _key("a.short"),
        _key("a.button", Kind.BUTTON_LABEL),
        _key("a.emoji", Kind.EMOJI),
        _key("a.only_data", Kind.FIELD_VALUE, ("amount",)),
        _key("a.other"),
    ]
    values = {
        "a.title": "Order #{number}",
        "a.intro": "Your order is waiting for payment.",
        "a.short": "Yes",
        "a.button": "Back",
        "a.emoji": "⭐",
        "a.only_data": "{amount}",
        "a.other": "Not shown anywhere in this message",
    }
    texts = ["Order #42", "Hello <@1>. Your order is waiting for payment. Yes, really.", "Back"]
    assert edit_text.find_keys(texts, values, definitions) == ["a.title", "a.intro", "a.button"]


def test_braces_that_are_not_placeholders_are_literal() -> None:
    definitions = [_key("a.braces", Kind.DESCRIPTION, ("x",))]
    values = {"a.braces": "Use {code} here"}
    assert edit_text.find_keys(["Use {code} here"], values, definitions) == ["a.braces"]
    assert edit_text.find_keys(["Use anything here"], values, definitions) == []


async def test_content_set_autocomplete_finds_keys_by_their_text(genv: TestEnv) -> None:
    await service.save_override(genv.app, how_to_buy.TITLE, "How to shop with us", actor_id=1)
    assert service.suggest_keys(genv.app, "shop with")[0] == how_to_buy.TITLE
    assert service.suggest_keys(genv.app, "HOW TO SHOP")[0] == how_to_buy.TITLE


async def test_text_search_ignores_markdown_and_icons(genv: TestEnv) -> None:
    text = "{icon_cart} Press **Buy** on an [item](https://example.com) you like"
    await service.save_override(genv.app, how_to_buy.INTRODUCTION, text, actor_id=1)
    # Words copied from the message as Discord shows it (no stars, the link's label only).
    assert service.suggest_keys(genv.app, "Press Buy on an item")[0] == how_to_buy.INTRODUCTION
    assert service.suggest_keys(genv.app, "**Buy** on")[0] == how_to_buy.INTRODUCTION


def test_a_button_or_option_text_that_starts_with_an_icon_is_found() -> None:
    definitions = [
        _key("a.button", Kind.BUTTON_LABEL),
        _key("a.option", Kind.OPTION_LABEL),
        _key("a.heading", Kind.FIELD_NAME),
    ]
    values = {"a.button": "{icon_cart} Start order", "a.option": "{icon_tag} Deals", "a.heading": "{icon_truck} Ship"}
    # As sent: the icon is the button's or option's emoji (not part of its label).
    cart, tag = "<:cart:111111111111111111>", "<:tag:222222222222222222>"
    spec = MessageSpec(
        embeds=(EmbedSpec(title="Ship"),),  # an icon that is not uploaded yet shows nothing in a text
        rows=(
            (ButtonSpec(label=f"{cart} Start order", custom_id="sb:x:go"),),
            (SelectSpec(custom_id="sb:x:pick", options=(SelectOptionSpec(label=f"{tag} Deals", value="d"),)),),
        ),
    )
    view = to_view(spec)
    assert view is not None
    sent_message: Any = types.SimpleNamespace(
        embeds=[to_embed(e) for e in spec.embeds], components=[discord.ActionRow(r) for r in view.to_components()]
    )
    texts = edit_text.message_texts(sent_message)
    assert texts == ["Ship", "Start order", "Deals"]
    assert edit_text.find_keys(texts, values, definitions) == ["a.heading", "a.button", "a.option"]
    # The icon as text (an emoji written in the label) still matches too; a label without the icon's words does not.
    assert edit_text.find_keys([f"{cart} Start order"], values, definitions) == ["a.button"]
    assert edit_text.find_keys(["Start"], values, definitions) == []
