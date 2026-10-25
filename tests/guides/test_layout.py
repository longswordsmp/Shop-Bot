"""The guide layout: blocks are split into embeds and messages within Discord's limits, never cut."""

from __future__ import annotations

from shopbot.features import core_keys as ck
from shopbot.features.guides.common import guide_messages
from shopbot.ui import limits
from shopbot.ui.card import PARAGRAPH, Block, Card, Section, build_embeds, card_embeds, card_messages, pack_messages
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import ButtonSpec, EmbedField, EmbedSpec, MessageSpec, rows_of
from tests.helpers import TestEnv


def field(n: int, size: int = 50) -> EmbedField:
    return EmbedField(name=f"[q{n}]", value=(f"[a{n}]" + "x" * size)[:1024])


def all_fields(embeds: list[EmbedSpec]) -> list[EmbedField]:
    return [item for embed in embeds for item in embed.fields]


def assert_valid(specs: list[MessageSpec]) -> None:
    for spec in specs:
        assert validate_message(spec) == []
        assert sum(e.text_length() for e in spec.embeds) <= limits.EMBED_TOTAL_CHARS
        assert len(spec.embeds) <= limits.EMBEDS_PER_MESSAGE


def test_small_block_is_one_embed_with_branding(tenv: TestEnv) -> None:
    block = Block(tone="primary", title="[title]", description="[intro]", fields=(field(1), field(2)))
    embeds = build_embeds(tenv.app.ui, [block])
    assert len(embeds) == 1
    embed = embeds[0]
    assert (embed.title, embed.description, embed.fields) == ("[title]", "[intro]", (field(1), field(2)))
    assert embed.footer_text == tenv.text(ck.FOOTER_TEXT) and embed.author_name == tenv.text(ck.AUTHOR_NAME)
    assert embed.color == tenv.app.ui.color("primary")
    assert embed.timestamp is None  # panels carry no timestamp, so republishing unchanged text changes nothing


def test_only_the_first_embed_carries_the_branding(tenv: TestEnv) -> None:
    blocks = [Block(tone="primary", title="[a]"), Block(tone="info", title="[b]", fields=(field(1),))]
    first, second = build_embeds(tenv.app.ui, blocks)
    assert first.footer_text and first.author_name
    assert second.footer_text is None and second.author_name is None
    assert second.color == tenv.app.ui.color("info")


def test_more_than_25_fields_continue_in_the_next_embed_without_title(tenv: TestEnv) -> None:
    fields = tuple(field(n, 10) for n in range(1, 31))
    embeds = build_embeds(tenv.app.ui, [Block(tone="info", title="[section]", fields=fields)])
    assert [len(e.fields) for e in embeds] == [25, 5]
    assert embeds[0].title == "[section]" and embeds[1].title is None
    assert all_fields(embeds) == list(fields)


def test_a_block_over_6000_characters_continues_in_more_embeds_and_messages(tenv: TestEnv) -> None:
    fields = tuple(field(n, 1000) for n in range(1, 26))  # 25 fields of ~1010 characters
    embeds = build_embeds(tenv.app.ui, [Block(tone="info", title="[section]", fields=fields)])
    assert len(embeds) > 1
    assert all(e.text_length() <= limits.EMBED_TOTAL_CHARS for e in embeds)
    assert all_fields(embeds) == list(fields)  # every field present, in order, unchanged
    specs = guide_messages(tenv.app.ui, [Block(tone="info", title="[section]", fields=fields)], banner="faq")
    assert len(specs) > 1
    assert_valid(specs)


def test_more_than_10_embeds_continue_in_the_next_message(tenv: TestEnv) -> None:
    blocks = [Block(tone="info", title=f"[section {n}]", fields=(field(n),)) for n in range(1, 13)]
    specs = guide_messages(tenv.app.ui, blocks, banner="faq")
    assert [len(s.embeds) for s in specs] == [10, 2]
    assert [e.title for s in specs for e in s.embeds] == [f"[section {n}]" for n in range(1, 13)]
    assert_valid(specs)


def test_lines_are_split_between_lines_and_never_cut(tenv: TestEnv) -> None:
    lines = tuple(f"[line {n}] " + "y" * 80 for n in range(1, 151))  # ~13 500 characters
    block = Block(tone="success", title="[status]", description="[explanation]", lines=lines)
    embeds = build_embeds(tenv.app.ui, [block])
    assert len(embeds) > 2
    assert all(len(e.description or "") <= limits.EMBED_DESCRIPTION for e in embeds)
    assert embeds[0].description is not None and embeds[0].description.startswith("[explanation]\n\n[line 1] ")
    rebuilt = "\n".join(e.description or "" for e in embeds).removeprefix("[explanation]\n\n")
    assert rebuilt.split("\n") == list(lines)
    assert all(e.color == tenv.app.ui.color("success") for e in embeds)
    assert_valid(guide_messages(tenv.app.ui, [block], banner="shipping"))


def test_message_packing_keeps_order_and_limits() -> None:
    embeds = [EmbedSpec(title=f"[{n}]", description="d" * 1500) for n in range(7)]
    groups = pack_messages(embeds)
    assert [len(g) for g in groups] == [3, 3, 1]
    assert [e for g in groups for e in g] == embeds


def test_link_buttons_are_attached_to_the_last_message(tenv: TestEnv) -> None:
    blocks = [Block(tone="info", title=f"[section {n}]", fields=(field(n),)) for n in range(1, 13)]
    rows = rows_of(*(ButtonSpec(label=f"[link {n}]", style="link", url="https://example.com") for n in range(7)))
    specs = guide_messages(tenv.app.ui, blocks, rows, banner="how-it-works")
    assert specs[0].rows == () and specs[-1].rows == rows
    assert [len(row) for row in specs[-1].rows] == [5, 2]


def test_empty_blocks_are_skipped(tenv: TestEnv) -> None:
    assert guide_messages(tenv.app.ui, [Block(tone="primary")], banner="faq") == []
    embeds = build_embeds(tenv.app.ui, [Block(tone="primary"), Block(tone="info", title="[only]")])
    assert [e.title for e in embeds] == ["[only]"]
    assert embeds[0].footer_text  # the first shown embed gets the branding


def test_a_card_is_one_embed_with_bold_section_headings_and_blank_lines_between_sections(tenv: TestEnv) -> None:
    card = Card(
        title="[title]",
        intro="[intro]",
        sections=(
            Section("[icon] [first]", ("[line 1]", "[line 2]")),
            Section(None, ("[plain]",)),
            Section("[empty]", ()),  # a heading alone is still shown
            Section(None, ()),  # nothing at all: skipped
        ),
        fields=(EmbedField("[order]", "[1042]", inline=True),),
        timestamp=True,
    )
    embeds = card_embeds(tenv.app.ui, card)
    assert len(embeds) == 1
    assert embeds[0].description == ("[intro]\n\n**[icon] [first]**\n[line 1]\n[line 2]\n\n[plain]\n\n**[empty]**")
    assert embeds[0].fields == (EmbedField("[order]", "[1042]", inline=True),)
    assert embeds[0].timestamp is not None


def test_a_long_card_continues_in_the_next_embed_without_its_title_and_nothing_is_cut(tenv: TestEnv) -> None:
    lines = tuple(f"[line {n}] " + "x" * 300 for n in range(40))
    card = Card(title="[title]", intro="[intro]", sections=(Section("[heading]", lines),), timestamp=True)
    specs = card_messages(tenv.app.ui, card, banner="faq")
    assert_valid(specs)
    embeds = [embed for spec in specs for embed in spec.embeds]
    assert len(embeds) > 1 and embeds[0].title == "[title]" and all(e.title is None for e in embeds[1:])
    assert embeds[0].timestamp is not None and all(e.timestamp is None for e in embeds[1:])
    text = "\n".join(e.description or "" for e in embeds)
    assert all(line in text for line in lines)
    assert not any((e.description or "").startswith("\n") for e in embeds)


def test_lines_come_before_the_fields_of_the_same_card(tenv: TestEnv) -> None:
    block = Block(tone="info", description="[intro]", lines=("[a]", PARAGRAPH, "[b]"), fields=(field(1),))
    (embed,) = build_embeds(tenv.app.ui, [block])
    assert embed.description == "[intro]\n\n[a]\n\n[b]" and embed.fields == (field(1),)
