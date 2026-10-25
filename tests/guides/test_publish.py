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
    assert [f.name for f in embed.fields] == [genv.text(how_to_buy.step_name_key(n)) for n in (1, 2, 3)]
    assert all(mention(genv, "purchase") in (f.value or "") for f in embed.fields)
    assert all(not f.inline for f in embed.fields)
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
    plain = {how_to_buy.INTRODUCTION: "[intro]", **{how_to_buy.step_value_key(n): f"[step {n}]" for n in (1, 2, 3)}}
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


async def test_faq_has_one_embed_per_section(genv: TestEnv) -> None:
    await faq.publish(genv.app)
    found = messages(genv, "faq")
    assert len(found) == 1
    head, general, orders = found[0].embeds
    assert (head.title, head.description) == (genv.text(faq.TITLE), head.description)
    assert head.description is not None and mention(genv, "faq") in head.description
    assert head.fields == []
    assert general.title == genv.text(faq.section_title_key("general"))
    assert [f.name for f in general.fields] == [genv.text(faq.question_key("general", n)) for n in (1, 2)]
    assert all(mention(genv, "catalog") in (f.value or "") for f in general.fields)
    assert orders.title == genv.text(faq.section_title_key("orders")) and len(orders.fields) == 1
    assert general.color is not None and general.color.value == genv.app.ui.color("info")
    assert head.footer.text and general.footer.text is None  # the branding appears once


async def test_faq_without_title_and_introduction_starts_with_the_first_section(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content={faq.TITLE: "", faq.INTRODUCTION: ""})
    try:
        await faq.publish(tenv.app)
        embeds = messages(tenv, "faq")[0].embeds
        assert [e.title for e in embeds] == [tenv.text(faq.section_title_key(s)) for s in ("general", "orders")]
        assert embeds[0].footer.text == tenv.text(ck.FOOTER_TEXT)
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
        # Every answer is present exactly once, complete, in order.
        values = [f.value for m in found for e in m.embeds for f in e.fields]
        expected = [tenv.text(faq.answer_key("general", n)) for n in range(1, 26)]
        assert [v for v in values if v is not None and v.startswith("[answer general")] == expected
        titles = [e.title for m in found for e in m.embeds if e.title]
        sections = ["general", "orders", *(f"topic_{n}" for n in range(1, 11))]
        assert titles == [tenv.text(faq.TITLE), *(tenv.text(faq.section_title_key(s)) for s in sections)]
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
    shipping.FORMAT_REGION: "{indicator} {name}",
    shipping.FORMAT_REGION_WITH_NOTE: "{indicator} {name} ({note})",
    shipping.status_indicator_key("full"): "🟢",
    shipping.status_indicator_key("partial"): "🟡",
    shipping.status_indicator_key("none"): "🔴",
    shipping.status_label_key("full"): "{indicator} [full label]",
    shipping.status_label_key("partial"): "[partial label]",
    shipping.status_label_key("none"): "[none label] {indicator}",
    shipping.status_description_key("full"): "",
    shipping.status_description_key("partial"): "[partial explanation]",
    shipping.status_description_key("none"): "",
    shipping.region_name_key("north"): "[north]",
    shipping.region_name_key("south"): "[south]",
    shipping.region_name_key("east"): "[east]",
    shipping.region_name_key("west"): "[west]",
    shipping.region_note_key("north"): "",
    shipping.region_note_key("south"): "",
    shipping.region_note_key("east"): "",
    shipping.region_note_key("west"): "[west note]",
}


async def test_shipping_lists_regions_by_status_with_indicators(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content=SHIPPING_TEXT)
    try:
        await shipping.publish(tenv.app)
        found = messages(tenv, "shipping")
        assert len(found) == 1
        head, full, partial, none, info = found[0].embeds
        assert head.title == tenv.text(shipping.TITLE) and head.fields == []
        assert full.title == "🟢 [full label]" and full.description == "🟢 [north]\n🟢 [west] ([west note])"
        assert partial.title == "[partial label]" and partial.description == "[partial explanation]\n\n🟡 [south]"
        assert none.title == "[none label] 🔴" and none.description == "🔴 [east]"
        colors = [e.color.value if e.color else None for e in (full, partial, none)]
        assert colors == [tenv.app.ui.color(t) for t in ("success", "warning", "danger")]
        assert len({*colors}) == 3  # the statuses are visually distinct
        assert [f.name for f in info.fields] == [tenv.text(shipping.info_name_key(n)) for n in (1, 2)]
        assert info.title is None
    finally:
        await tenv.close()


async def test_information_can_come_before_the_regions(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, {"shipping": {"info_position": "before"}}, content=SHIPPING_TEXT)
    try:
        await shipping.publish(tenv.app)
        embeds = messages(tenv, "shipping")[0].embeds
        assert len(embeds) == 4
        assert [f.name for f in embeds[0].fields] == [tenv.text(shipping.info_name_key(n)) for n in (1, 2)]
        assert embeds[-1].title == "[none label] 🔴"
    finally:
        await tenv.close()


async def test_status_without_regions_or_explanation_is_omitted(tmp_path: Path) -> None:
    overrides = {"shipping": {"regions": [{"id": "north", "status": "full"}], "info_sections": 0}}
    tenv = await guide_env(tmp_path, overrides, content=SHIPPING_TEXT | {shipping.region_name_key("north"): "[n]"})
    try:
        await shipping.publish(tenv.app)
        titles = [e.title for e in messages(tenv, "shipping")[0].embeds]
        # "partial" has an explanation, so it is shown (without regions); "none" has neither.
        assert titles == [tenv.text(shipping.TITLE), "🟢 [full label]", "[partial label]"]
    finally:
        await tenv.close()


async def test_shipping_without_regions_shows_header_and_information(tmp_path: Path) -> None:
    content = {shipping.status_description_key(s): "" for s in ("full", "partial", "none")}
    tenv = await guide_env(tmp_path, {"shipping": {"regions": []}}, content=content)
    try:
        assert tenv.app.ready("shipping")
        await shipping.publish(tenv.app)
        embeds = messages(tenv, "shipping")[0].embeds
        assert [e.title for e in embeds] == [tenv.text(shipping.TITLE), None]
        assert len(embeds[1].fields) == 2
    finally:
        await tenv.close()


async def test_large_region_list_is_split_within_limits(tmp_path: Path) -> None:
    regions = [{"id": f"r{n}", "status": ("full", "partial", "none")[n % 3]} for n in range(300)]
    names = {shipping.region_name_key(f"r{n}"): f"[region {n:03d}] " + "w" * 40 for n in range(300)}
    tenv = await guide_env(tmp_path, {"shipping": {"regions": regions, "info_sections": 25}}, content=names)
    try:
        lines = await shipping.publish(tenv.app)
        found = messages(tenv, "shipping")
        assert len(found) > 1 and "message(s)" in lines[0]
        assert_within_limits(found)
        text = "\n".join(e.description or "" for m in found for e in m.embeds)
        for n in range(300):
            assert text.count(f"[region {n:03d}] ") == 1  # every region exactly once, never cut
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
