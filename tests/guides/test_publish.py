"""Publishing the guides: content, channel links, idempotence, multi-message order, failures, restart, /setup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.panels import withdraw_unready_panels
from shopbot.db.repos import state
from shopbot.features import core_keys as ck
from shopbot.features.guides import faq, how_to_buy, shipping
from shopbot.features.guides.common import record_key
from shopbot.features.server_keys import channel_url
from shopbot.ui import limits
from tests.fakes import FakeMessage, http_error
from tests.guides.util import apply_settings, channel, guide_env, guide_settings, messages
from tests.helpers import TestEnv
from tests.setup.util import all_lines, restarted_app, setup


@pytest.fixture
async def genv(tmp_path: Path) -> Any:
    tenv = await guide_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


async def records(tenv: TestEnv, prefix: str) -> list[state.PublishedMessage]:
    return await tenv.app.db.run(state.published_with_prefix, tenv.app.env.guild_id, prefix)


def mention(tenv: TestEnv, key: str) -> str:
    return f"<#{tenv.app.resources.channel_id(key)}>"


def long_answers(section: str, count: int, size: int = 1000) -> dict[str, str]:
    return {faq.answer_key(section, n): (f"[answer {section} {n}] " + "z" * size)[:1024] for n in range(1, count + 1)}


def assert_within_limits(found: list[FakeMessage]) -> None:
    for message in found:
        assert 1 <= len(message.embeds) <= limits.EMBEDS_PER_MESSAGE
        total = 0
        for embed in message.embeds:
            assert len(embed.fields) <= limits.EMBED_FIELDS
            assert len(embed.description or "") <= limits.EMBED_DESCRIPTION
            total += len(embed.title or "") + len(embed.description or "") + len(embed.footer.text or "")
            total += len(embed.author.name or "") + sum(len(f.name or "") + len(f.value or "") for f in embed.fields)
        assert total <= limits.EMBED_TOTAL_CHARS


# --- #how-to-buy --------------------------------------------------------------------------------------------


async def test_how_to_buy_posts_the_guide_with_channel_links(genv: TestEnv) -> None:
    lines = await how_to_buy.publish(genv.app)
    found = messages(genv, "how_to_buy")
    assert len(found) == 1
    embed = found[0].embeds[0]
    assert embed.title == genv.text(how_to_buy.TITLE)
    assert embed.description is not None and embed.description.startswith(genv.text(how_to_buy.INTRODUCTION)[:6])
    assert mention(genv, "catalog") in embed.description and "{channel_catalog}" not in embed.description
    headings = [f"**{genv.text(how_to_buy.step_name_key(n))}**" for n in (1, 2, 3)]
    positions = [embed.description.index(h) for h in headings]
    assert positions == sorted(positions) and embed.fields == []  # one card: the steps in order, no fields
    assert embed.description.count(mention(genv, "purchase")) >= 3
    assert embed.footer.text == genv.text(ck.FOOTER_TEXT)
    assert embed.color is not None and embed.color.value == genv.app.ui.color("primary")
    buttons = found[0].components()
    assert [b.style for b in buttons] == [discord.ButtonStyle.link] * 3
    assert [b.label for b in buttons] == [
        genv.text(how_to_buy.link_label_key(k)) for k in ("catalog", "purchase", "faq")
    ]
    assert [b.url for b in buttons] == [channel_url(genv.app, k) for k in ("catalog", "purchase", "faq")]
    assert all(str(b.emoji) == genv.text(how_to_buy.link_emoji_key("faq")) for b in buttons)
    assert found[0].custom_ids() == []
    assert found[0].allowed_mentions is not None and found[0].allowed_mentions.everyone is False
    assert lines == [
        genv.app.op(
            "guides.publish.done",
            feature="how_to_buy",
            channel=channel(genv, "how_to_buy").mention,
            messages=1,
            created=1,
            updated=0,
            unchanged=0,
            removed=0,
        )
    ]


async def test_optional_link_emoji_is_omitted(tmp_path: Path) -> None:
    empty = {how_to_buy.link_emoji_key(k): "" for k in ("catalog", "purchase", "faq")}
    tenv = await guide_env(tmp_path, content=empty)
    try:
        await how_to_buy.publish(tenv.app)
        assert all(b.emoji is None for b in messages(tenv, "how_to_buy")[0].components())
    finally:
        await tenv.close()


async def test_publishing_twice_changes_nothing_and_edits_in_place(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    first = messages(genv, "how_to_buy")[0]
    lines = await how_to_buy.publish(genv.app)
    assert messages(genv, "how_to_buy") == [first] and first.edits == []
    assert "1 up to date" in lines[0]
    genv.app.content.set_override(how_to_buy.TITLE, "[changed title]")
    lines = await how_to_buy.publish(genv.app)
    assert messages(genv, "how_to_buy") == [first] and len(first.edits) == 1
    assert first.embeds[0].title == "[changed title]"
    assert "1 updated" in lines[0]


async def test_deleted_message_is_reposted(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    await messages(genv, "how_to_buy")[0].delete()
    lines = await how_to_buy.publish(genv.app)
    assert len(messages(genv, "how_to_buy")) == 1
    assert "1 posted" in lines[0]


async def test_link_button_to_a_missing_channel_is_not_posted(tmp_path: Path) -> None:
    plain = {
        how_to_buy.INTRODUCTION: "[intro]",
        how_to_buy.CLOSING: "",
        **{how_to_buy.step_value_key(n): f"[step {n}]" for n in (1, 2, 3)},
    }
    tenv = await guide_env(tmp_path, content=plain)
    try:
        await channel(tenv, "purchase").delete()  # deleted in Discord; /setup would recreate it
        lines = await how_to_buy.publish(tenv.app)
        assert messages(tenv, "how_to_buy") == []
        assert lines == [tenv.app.op("guides.publish.link_channel_missing", feature="how_to_buy", channel="purchase")]
        await tenv.app.resources.forget("channel", "purchase")  # never created at all
        assert "would be broken" in (await how_to_buy.publish(tenv.app))[0]
        assert await records(tenv, how_to_buy.PREFIX) == []
    finally:
        await tenv.close()


async def test_text_linking_a_missing_channel_is_not_posted(genv: TestEnv) -> None:
    await genv.app.resources.forget("channel", "shop")
    lines = await how_to_buy.publish(genv.app)
    assert messages(genv, "how_to_buy") == []
    assert "links to channel `shop`" in lines[0] and "how_to_buy." in lines[0]
    lines = await faq.publish(genv.app)
    assert messages(genv, "faq") == [] and "links to channel `shop`" in lines[0]


async def test_missing_guide_channel_is_reported(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, provision=False)
    try:
        for module, key in ((how_to_buy, "how_to_buy"), (faq, "faq"), (shipping, "shipping")):
            lines = await module.publish(tenv.app)
            assert lines == [tenv.app.op("guides.publish.channel_missing", feature=key, channel=key)]
    finally:
        await tenv.close()


async def test_missing_text_is_never_posted(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content={how_to_buy.step_name_key(2): "", faq.section_title_key("orders"): ""})
    try:
        report = await setup(tenv)
        assert messages(tenv, "how_to_buy") == [] and messages(tenv, "faq") == []
        assert len(messages(tenv, "shipping")) >= 1  # the complete guide is unaffected
        assert any("`how_to_buy`" in line for line in report.lines["problems"])
        # Called directly anyway: the missing key is named, nothing is posted, nothing is invented.
        lines = await how_to_buy.publish(tenv.app)
        assert how_to_buy.step_name_key(2) in lines[0] and messages(tenv, "how_to_buy") == []
    finally:
        await tenv.close()


async def test_not_ready_guide_is_not_posted_when_called_directly(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, {"how_to_buy": {"link_channels": ["order_queue"]}})
    try:
        lines = await how_to_buy.publish(tenv.app)
        assert lines == [tenv.app.op("guides.publish.not_ready", feature="how_to_buy")]
        assert messages(tenv, "how_to_buy") == []
    finally:
        await tenv.close()


async def test_discord_failure_is_reported_and_retried(genv: TestEnv) -> None:
    target = channel(genv, "how_to_buy")
    target.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    lines = await how_to_buy.publish(genv.app)
    assert lines == [genv.app.op("guides.publish.failed", feature="how_to_buy", http_status=403, code=50013)]
    assert await records(genv, how_to_buy.PREFIX) == []
    target.fail_send = None
    await how_to_buy.publish(genv.app)
    assert len(target.messages) == 1


async def test_missing_bot_permissions_are_reported(genv: TestEnv) -> None:
    target = channel(genv, "faq")
    await target.set_permissions(
        discord.Object(id=genv.guild.me.id), overwrite=discord.PermissionOverwrite(embed_links=False)
    )
    lines = await faq.publish(genv.app)
    assert target.messages == [] and "embed_links" in lines[0]


async def test_text_too_long_for_one_embed_is_reported_not_cut(tmp_path: Path) -> None:
    content = {
        "branding.author_name": "a" * 256,
        "branding.footer_text": "f" * 2048,
        how_to_buy.TITLE: "t" * 256,
        how_to_buy.INTRODUCTION: "i" * 4000,
    }
    tenv = await guide_env(tmp_path, content=content)
    try:
        lines = await how_to_buy.publish(tenv.app)
        assert messages(tenv, "how_to_buy") == []
        assert "exceeds Discord limits" in lines[0] and "message 1" in lines[0]
    finally:
        await tenv.close()


async def test_restart_keeps_the_same_messages(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    await faq.publish(genv.app)
    before = messages(genv, "how_to_buy") + messages(genv, "faq")
    app = restarted_app(genv)
    await app.resources.refresh()
    assert "up to date" in (await how_to_buy.publish(app))[0]
    assert "up to date" in (await faq.publish(app))[0]
    assert messages(genv, "how_to_buy") + messages(genv, "faq") == before
    assert all(m.edits == [] for m in before)


async def test_concurrent_publishing_never_duplicates(genv: TestEnv) -> None:
    await asyncio.gather(how_to_buy.publish(genv.app), how_to_buy.publish(genv.app), how_to_buy.publish(genv.app))
    assert len(messages(genv, "how_to_buy")) == 1
    assert len(await records(genv, how_to_buy.PREFIX)) == 1


async def test_changed_channel_moves_the_guide(genv: TestEnv) -> None:
    await how_to_buy.publish(genv.app)
    apply_settings(genv, guide_settings({"how_to_buy": {"channel": "purchase"}}))
    await genv.app.resources.refresh()
    lines = await how_to_buy.publish(genv.app)
    assert messages(genv, "how_to_buy") == []
    assert len(messages(genv, "purchase")) == 1
    assert "1 posted" in lines[0]


# --- #faq -------------------------------------------------------------------------------------------------------


async def test_faq_is_one_card_with_a_section_per_topic(genv: TestEnv) -> None:
    await faq.publish(genv.app)
    found = messages(genv, "faq")
    assert len(found) == 1
    (card,) = found[0].embeds
    assert card.title == genv.text(faq.TITLE) and card.fields == []
    text = card.description or ""
    assert mention(genv, "faq") in text and mention(genv, "catalog") in text
    order = [
        f"**{genv.text(faq.section_title_key('general'))}**",
        f"**{genv.text(faq.question_key('general', 1))}**",
        f"**{genv.text(faq.question_key('general', 2))}**",
        f"**{genv.text(faq.section_title_key('orders'))}**",
        f"**{genv.text(faq.question_key('orders', 1))}**",
    ]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
    assert card.color is not None and card.color.value == genv.app.ui.color("info")
    assert card.footer.text == genv.text(ck.FOOTER_TEXT)


async def test_faq_without_title_and_introduction_starts_with_the_first_section(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content={faq.TITLE: "", faq.INTRODUCTION: ""})
    try:
        await faq.publish(tenv.app)
        (card,) = messages(tenv, "faq")[0].embeds
        assert card.title is None
        assert (card.description or "").startswith(f"**{tenv.text(faq.section_title_key('general'))}**")
        assert card.footer.text == tenv.text(ck.FOOTER_TEXT)
    finally:
        await tenv.close()


LONG_FAQ = {
    "faq": {
        "sections": [
            {"id": "general", "questions": 25},
            {"id": "orders", "questions": 3},
            *({"id": f"topic_{n}", "questions": 1} for n in range(1, 11)),
        ]
    }
}


async def test_long_faq_is_split_into_ordered_messages_within_limits(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, LONG_FAQ, content=long_answers("general", 25))
    try:
        lines = await faq.publish(tenv.app)
        found = messages(tenv, "faq")
        assert len(found) > 3
        assert_within_limits(found)
        # Every answer is present exactly once, complete, in order; only the first embed has the title.
        text = "\n".join(e.description or "" for m in found for e in m.embeds)
        answers = [tenv.text(faq.answer_key("general", n)) for n in range(1, 26)]
        assert all(text.count(answer) == 1 for answer in answers)
        assert [text.index(a) for a in answers] == sorted(text.index(a) for a in answers)
        sections = ["general", "orders", *(f"topic_{n}" for n in range(1, 11))]
        headings = [text.index(f"**{tenv.text(faq.section_title_key(s))}**") for s in sections]
        assert headings == sorted(headings)
        assert [e.title for m in found for e in m.embeds if e.title] == [tenv.text(faq.TITLE)]
        assert f"{len(found)} message(s)" in lines[0]
        keys = [r.key for r in await records(tenv, faq.PREFIX)]
        assert sorted(keys) == sorted(record_key(faq.PREFIX, i) for i in range(len(found)))
    finally:
        await tenv.close()


async def test_shorter_faq_removes_the_surplus_messages(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, LONG_FAQ, content=long_answers("general", 25))
    try:
        await faq.publish(tenv.app)
        long_count = len(messages(tenv, "faq"))
        first = messages(tenv, "faq")[0]
        apply_settings(tenv, guide_settings())
        lines = await faq.publish(tenv.app)
        found = messages(tenv, "faq")
        assert found == [first]  # the first message is edited in place, the rest are removed
        assert f"{long_count - 1} old message(s) removed" in lines[0]
        assert len(await records(tenv, faq.PREFIX)) == 1
    finally:
        await tenv.close()


async def test_longer_faq_appends_messages_in_order(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path)
    try:
        await faq.publish(tenv.app)
        first = messages(tenv, "faq")[0]
        apply_settings(tenv, guide_settings(LONG_FAQ), long_answers("general", 25))
        await faq.publish(tenv.app)
        found = messages(tenv, "faq")
        assert found[0] is first and len(found) > 3
        assert_within_limits(found)
    finally:
        await tenv.close()


async def test_deleted_first_message_reposts_the_following_ones_in_order(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, LONG_FAQ, content=long_answers("general", 25))
    try:
        await faq.publish(tenv.app)
        titles_before = [[e.title for e in m.embeds] for m in messages(tenv, "faq")]
        await messages(tenv, "faq")[0].delete()
        await faq.publish(tenv.app)
        found = messages(tenv, "faq")
        assert [[e.title for e in m.embeds] for m in found] == titles_before  # same order in the channel
        recorded = {r.key: r.message_id for r in await records(tenv, faq.PREFIX)}
        assert [recorded[record_key(faq.PREFIX, i)] for i in range(len(found))] == [m.id for m in found]
    finally:
        await tenv.close()


# --- #shipping ------------------------------------------------------------------------------------------------

SHIPPING_TEXT = {
    shipping.status_indicator_key("full"): "🟢",
    shipping.status_indicator_key("partial"): "🟡",
    shipping.status_indicator_key("none"): "🔴",
    shipping.group_name_key("north"): "[north]",
    shipping.group_name_key("europe"): "[europe]",
    shipping.FORMAT_EXCLUDED: "{country} [except] {areas}",
    shipping.FORMAT_LAST_AREA: "{areas} [and] {last}",
    shipping.NOT_AVAILABLE_HEADING: "[not available]",
    shipping.NOT_AVAILABLE_OTHERS: "[others]",
    shipping.LEGEND: "[legend]",
}


async def test_shipping_is_one_card_listing_countries_by_group_with_status_dots(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content=SHIPPING_TEXT)
    try:
        await shipping.publish(tenv.app)
        found = messages(tenv, "shipping")
        assert len(found) == 1
        (card,) = found[0].embeds
        assert card.title == tenv.text(shipping.TITLE) and card.fields == []
        text = card.description or ""
        assert "[legend]" in text
        assert "**[north]**\n🟢 Canada · Mexico\n🟡 United States [except] Alaska [and] North Dakota" in text
        assert "**[europe]**\n🟢 Germany · France" in text
        assert "**[not available]**\n🔴 Cuba · Russia · [others]" in text
        info = [f"**{tenv.text(shipping.info_name_key(n))}**" for n in (1, 2)]
        positions = [text.index(h) for h in ("**[north]**", "**[europe]**", "**[not available]**", *info)]
        assert positions == sorted(positions)
        assert card.color is not None and card.color.value == tenv.app.ui.color("info")
    finally:
        await tenv.close()


async def test_delivery_options_are_listed_with_their_prices(tmp_path: Path) -> None:
    options = [{"id": "standard", "fee_minor": 500}, {"id": "expedited", "fee_minor": 1500}]
    content = SHIPPING_TEXT | {
        shipping.DELIVERY_HEADING: "[delivery]",
        shipping.FORMAT_DELIVERY: "{label}: {price}",
        "checkout.fulfillment.standard.label": "[standard]",
        "checkout.fulfillment.expedited.label": "[expedited]",
    }
    tenv = await guide_env(tmp_path, {"checkout": {"fulfillment_options": options}}, content=content)
    try:
        await shipping.publish(tenv.app)
        text = messages(tenv, "shipping")[0].embeds[0].description or ""
        five, fifteen = tenv.app.ui.money(500), tenv.app.ui.money(1500)
        assert f"**[delivery]**\n[standard]: {five}\n[expedited]: {fifteen}" in text
    finally:
        await tenv.close()


async def test_a_country_with_one_excluded_area_and_no_not_available_list(tmp_path: Path) -> None:
    overrides = {"shipping": {"excluded_areas": {"US": ["AK"]}, "not_available": [], "info_sections": 0}}
    content = SHIPPING_TEXT | {shipping.NOT_AVAILABLE_OTHERS: ""}
    tenv = await guide_env(tmp_path, overrides, content=content)
    try:
        await shipping.publish(tenv.app)
        text = messages(tenv, "shipping")[0].embeds[0].description or ""
        assert "🟡 United States [except] Alaska" in text and "[and]" not in text
        assert "[not available]" not in text  # nothing listed there, so no heading
    finally:
        await tenv.close()


async def test_large_country_list_is_split_within_limits(tmp_path: Path) -> None:
    from shopbot.core.countries import COUNTRIES

    codes = sorted(COUNTRIES)
    groups = [{"id": f"g{n}", "countries": codes[n * 12 : (n + 1) * 12]} for n in range(20)]
    names = {shipping.group_name_key(f"g{n}"): f"[group {n:02d}] " + "w" * 40 for n in range(20)}
    infos = {shipping.info_value_key(n): f"[info {n}] " + "v" * 900 for n in range(1, 11)}
    overrides = {"shipping": {"groups": groups, "excluded_areas": {}, "not_available": [], "info_sections": 10}}
    tenv = await guide_env(tmp_path, overrides, content=SHIPPING_TEXT | names | infos)
    try:
        lines = await shipping.publish(tenv.app)
        found = messages(tenv, "shipping")
        assert len(found) > 1 and "message(s)" in lines[0]
        assert_within_limits(found)
        text = "\n".join(e.description or "" for m in found for e in m.embeds)
        for n in range(20):
            assert text.count(f"[group {n:02d}] ") == 1  # every group exactly once, never cut
        for n in range(1, 11):
            assert text.count(f"[info {n}] ") == 1
    finally:
        await tenv.close()


# --- /setup and withdrawal --------------------------------------------------------------------------------------


async def test_setup_creates_channels_and_publishes_each_guide_once(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, provision=False)
    try:
        report = await setup(tenv)
        for key in ("how_to_buy", "faq", "shipping"):
            assert len(messages(tenv, key)) == 1, key
        text = all_lines(report)
        assert all(f"`{key}`: 1 message(s)" in text for key in ("how_to_buy", "faq", "shipping"))
        report = await setup(tenv)
        for key in ("how_to_buy", "faq", "shipping"):
            assert len(messages(tenv, key)) == 1, key
            assert messages(tenv, key)[0].edits == []
        assert "1 up to date" in all_lines(report)
    finally:
        await tenv.close()


async def test_guide_that_stops_being_ready_is_withdrawn(genv: TestEnv) -> None:
    await setup(genv)
    assert len(messages(genv, "faq")) == 1
    genv.app.content.replace(file_values={**genv.content, faq.section_title_key("general"): ""})
    genv.app.recompute_readiness()
    results = await withdraw_unready_panels(genv.app)
    assert [r.key for r in results] == [record_key(faq.PREFIX, 0)]
    assert messages(genv, "faq") == [] and len(messages(genv, "how_to_buy")) == 1


async def test_disabled_guides_are_never_published(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, {"features": {"how_to_buy": False, "faq": False, "shipping": False}})
    try:
        await setup(tenv)
        assert all(messages(tenv, key) == [] for key in ("how_to_buy", "faq", "shipping"))
    finally:
        await tenv.close()
