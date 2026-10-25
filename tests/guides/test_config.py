"""Content keys, readiness, settings problems and content problems of the three guides."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shopbot.config.content_keys import Kind
from shopbot.features.guides import faq, how_to_buy, shipping
from shopbot.features.server_keys import channel_placeholders
from tests.guides.util import apply_settings, guide_env, problem_keys, problems
from tests.helpers import TestEnv, make_settings


@pytest.fixture
async def genv(tmp_path: Path) -> TestEnv:
    tenv = await guide_env(tmp_path)
    try:
        yield tenv  # type: ignore[misc]
    finally:
        await tenv.close()


def definition(tenv: TestEnv, key: str) -> tuple[Kind, bool, str, tuple[str, ...]]:
    found = tenv.app.content.registry.get(key)
    assert found is not None, key
    assert found.doc and found.group
    return found.kind, found.required, found.feature, found.placeholders


async def test_how_to_buy_keys(genv: TestEnv) -> None:
    links = channel_placeholders(genv.app.settings)
    assert "channel_catalog" in links and "channel_purchase" in links
    assert definition(genv, how_to_buy.TITLE) == (Kind.TITLE, True, "how_to_buy", ())
    assert definition(genv, how_to_buy.INTRODUCTION) == (Kind.DESCRIPTION, True, "how_to_buy", links)
    for n in (1, 2, 3):
        # Discord renders #channel links in field values but not in field names, so only values accept them.
        assert definition(genv, how_to_buy.step_name_key(n)) == (Kind.FIELD_NAME, True, "how_to_buy", ())
        assert definition(genv, how_to_buy.step_value_key(n)) == (Kind.FIELD_VALUE, True, "how_to_buy", links)
    assert genv.app.content.registry.get(how_to_buy.step_name_key(4)) is None
    for key in ("catalog", "purchase", "faq"):
        assert definition(genv, how_to_buy.link_label_key(key)) == (Kind.BUTTON_LABEL, True, "how_to_buy", ())
        assert definition(genv, how_to_buy.link_emoji_key(key)) == (Kind.EMOJI, False, "how_to_buy", ())
    assert genv.app.ready("how_to_buy")


async def test_faq_keys(genv: TestEnv) -> None:
    links = channel_placeholders(genv.app.settings)
    assert definition(genv, faq.TITLE) == (Kind.TITLE, False, "faq", ())
    assert definition(genv, faq.INTRODUCTION) == (Kind.DESCRIPTION, False, "faq", links)
    assert definition(genv, faq.section_title_key("general")) == (Kind.TITLE, True, "faq", ())
    for n in (1, 2):
        assert definition(genv, faq.question_key("general", n)) == (Kind.FIELD_NAME, True, "faq", ())
        assert definition(genv, faq.answer_key("general", n)) == (Kind.FIELD_VALUE, True, "faq", links)
    assert genv.app.content.registry.get(faq.question_key("orders", 2)) is None
    assert genv.app.ready("faq")


async def test_shipping_keys(genv: TestEnv) -> None:
    links = channel_placeholders(genv.app.settings)
    assert definition(genv, shipping.TITLE) == (Kind.TITLE, True, "shipping", ())
    assert definition(genv, shipping.INTRODUCTION) == (Kind.DESCRIPTION, True, "shipping", links)
    assert definition(genv, shipping.LEGEND)[:2] == (Kind.DESCRIPTION, False)
    for status in ("full", "partial", "none"):
        assert definition(genv, shipping.status_indicator_key(status))[:2] == (Kind.FIELD_NAME, True)
    for group in ("north", "europe"):
        assert definition(genv, shipping.group_name_key(group))[:2] == (Kind.FIELD_NAME, True)
    assert definition(genv, shipping.FORMAT_EXCLUDED) == (Kind.FORMAT, True, "shipping", ("country", "areas"))
    assert definition(genv, shipping.FORMAT_LAST_AREA) == (Kind.FORMAT, True, "shipping", ("areas", "last"))
    assert definition(genv, shipping.NOT_AVAILABLE_HEADING)[:2] == (Kind.FIELD_NAME, True)
    assert definition(genv, shipping.NOT_AVAILABLE_OTHERS)[:2] == (Kind.FIELD_VALUE, False)
    assert not definition(genv, shipping.DELIVERY_HEADING)[1]  # no delivery options configured here
    assert definition(genv, shipping.info_name_key(2))[:2] == (Kind.FIELD_NAME, True)
    assert definition(genv, shipping.info_value_key(2)) == (Kind.FIELD_VALUE, True, "shipping", links)
    assert genv.app.content.registry.get(shipping.info_name_key(3)) is None
    assert genv.app.ready("shipping")


async def test_unused_statuses_and_formats_are_optional(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path)
    try:
        settings = tenv.app.settings
        apply_settings(
            tenv, replace(settings, shipping=replace(settings.shipping, excluded_areas={}, not_available=()))
        )
        assert not definition(tenv, shipping.status_indicator_key("partial"))[1]
        assert not definition(tenv, shipping.status_indicator_key("none"))[1]
        assert definition(tenv, shipping.status_indicator_key("full"))[1]
        assert not definition(tenv, shipping.FORMAT_EXCLUDED)[1] and not definition(tenv, shipping.FORMAT_LAST_AREA)[1]
        assert not definition(tenv, shipping.NOT_AVAILABLE_HEADING)[1]
        assert tenv.app.ready("shipping")
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    ("feature", "key"),
    [
        ("how_to_buy", how_to_buy.TITLE),
        ("how_to_buy", how_to_buy.step_value_key(3)),
        ("how_to_buy", how_to_buy.link_label_key("faq")),
        ("faq", faq.section_title_key("orders")),
        ("faq", faq.answer_key("general", 2)),
        ("shipping", shipping.INTRODUCTION),
        ("shipping", shipping.status_indicator_key("none")),
        ("shipping", shipping.group_name_key("europe")),
        ("shipping", shipping.info_value_key(1)),
        ("shipping", shipping.FORMAT_EXCLUDED),
    ],
)
async def test_missing_required_text_makes_only_that_guide_not_ready(tmp_path: Path, feature: str, key: str) -> None:
    tenv = await guide_env(tmp_path, content={key: ""})
    try:
        assert not tenv.app.ready(feature)
        assert key in problem_keys(tenv, feature)
        others = {"how_to_buy", "faq", "shipping"} - {feature}
        assert all(tenv.app.ready(name) for name in others)
    finally:
        await tenv.close()


async def test_optional_text_can_stay_empty(tmp_path: Path) -> None:
    empty = {
        faq.TITLE: "",
        faq.INTRODUCTION: "",
        how_to_buy.link_emoji_key("faq"): "",
        shipping.LEGEND: "",
        shipping.NOT_AVAILABLE_OTHERS: "",
    }
    tenv = await guide_env(tmp_path, content=empty)
    try:
        assert all(tenv.app.ready(name) for name in ("how_to_buy", "faq", "shipping"))
    finally:
        await tenv.close()


async def test_guides_are_disabled_by_their_flags(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, {"features": {"how_to_buy": False, "faq": False, "shipping": False}})
    try:
        for name in ("how_to_buy", "faq", "shipping"):
            assert not tenv.app.readiness.is_enabled(name) and not tenv.app.ready(name)
    finally:
        await tenv.close()


def test_settings_problems_for_channels() -> None:
    settings = make_settings(
        {
            "how_to_buy": {"channel": "", "link_channels": ["nowhere", "staff_alerts"]},
            "faq": {"channel": "missing"},
            "shipping": {"channel": ""},
        }
    )
    htb = how_to_buy.settings_problems(settings)
    assert any(p.startswith("how_to_buy.channel: set the channel key") for p in htb)
    assert "how_to_buy.link_channels: channel 'nowhere' is not defined in server.channels" in htb
    assert any("'staff_alerts' is a staff/admin channel" in p for p in htb)
    faq_problems = faq.settings_problems(settings)
    assert "faq.channel: channel 'missing' is not defined in server.channels" in faq_problems
    assert any(p.startswith("faq.sections: add at least one") for p in faq_problems)
    assert any(p.startswith("shipping.channel: set the channel key") for p in shipping.settings_problems(settings))


def test_shipping_without_groups_is_a_problem() -> None:
    settings = make_settings({"shipping": {"channel": "catalog", "groups": []}})
    assert any(p.startswith("shipping.groups: add at least one") for p in shipping.settings_problems(settings))


async def test_settings_problems_block_readiness(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, {"faq": {"sections": []}, "how_to_buy": {"link_channels": ["order_queue"]}})
    try:
        assert not tenv.app.ready("faq") and not tenv.app.ready("how_to_buy")
        assert any("faq.sections" in p for p in problems(tenv, "faq"))
        assert any("order_queue" in p for p in problems(tenv, "how_to_buy"))
        assert tenv.app.ready("shipping")
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({shipping.FORMAT_EXCLUDED: "[line] {country}"}, "shipping.format.excluded must contain {areas}"),
        ({shipping.FORMAT_EXCLUDED: "[line] {areas}"}, "shipping.format.excluded must contain {country}"),
        ({shipping.FORMAT_LAST_AREA: "[line] {areas}"}, "shipping.format.last_area must contain {last}"),
    ],
)
async def test_country_line_formats_are_checked(tmp_path: Path, content: dict[str, str], expected: str) -> None:
    tenv = await guide_env(tmp_path, content=content)
    try:
        assert not tenv.app.ready("shipping")
        assert any(p.startswith(expected) for p in problems(tenv, "shipping")), problems(tenv, "shipping")
    finally:
        await tenv.close()


async def test_unknown_placeholders_in_guide_text_are_invalid(tmp_path: Path) -> None:
    tenv = await guide_env(tmp_path, content={how_to_buy.INTRODUCTION: "[intro] {channel_nowhere}"})
    try:
        assert not tenv.app.ready("how_to_buy")
        status = tenv.app.readiness.statuses["how_to_buy"]
        assert any(p.kind == "invalid_content" and p.key == how_to_buy.INTRODUCTION for p in status.problems)
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    ("feature", "key"),
    [
        ("how_to_buy", how_to_buy.TITLE),
        ("how_to_buy", how_to_buy.step_name_key(1)),
        ("how_to_buy", how_to_buy.link_label_key("catalog")),
        ("faq", faq.question_key("general", 1)),
        ("faq", faq.section_title_key("orders")),
        ("shipping", shipping.group_name_key("north")),
        ("shipping", shipping.info_name_key(1)),
    ],
)
async def test_channel_link_where_discord_cannot_show_it_is_a_problem(tmp_path: Path, feature: str, key: str) -> None:
    # Such text would reach customers as a raw "{channel_catalog}", so the guide stays hidden until it is fixed.
    tenv = await guide_env(tmp_path, content={key: "[text] {channel_catalog}"})
    try:
        assert not tenv.app.ready(feature)
        expected = f"{key}: {{channel_catalog}} cannot be a channel link here"
        assert any(p.startswith(expected) for p in problems(tenv, feature)), problems(tenv, feature)
    finally:
        await tenv.close()
