"""/announce preset:shipping_areas: ``{excluded_areas}`` written from ``[shipping.excluded_areas]`` as #shipping writes
them (one, two, three areas, two countries), the preview and Post of a preset that shows it (read again at Post), and
the problem without a Post button while nothing is excluded."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.loader import load_config
from shopbot.features.announcements import MODULE, presets
from shopbot.features.announcements.content_keys import PRESET_DESCRIPTION, PRESET_TITLE, preset_key
from shopbot.features.guides.shipping import FORMAT_LAST_AREA
from shopbot.ui.spec import MessageSpec
from tests.announcements.test_presets import buttons, card_of, post
from tests.announcements.util import BASE_OVERRIDES, AnnounceEnv, announce_env, invoke, rows
from tests.helpers import deep_merge
from tools.check_limits import check_state, unrouted_components
from tools.offline import ROOT, build_offline_app

PRESET_ID = "shipping_areas"
SETTINGS: dict[str, Any] = deep_merge(
    {
        "server": {
            "channels": [
                *BASE_OVERRIDES["server"]["channels"],
                {"key": "shipping", "category": "support", "access": "inherit"},
            ]
        }
    },
    {
        "shipping": {
            "channel": "shipping",
            "groups": [{"id": "north_america", "countries": ["US", "CA"]}],
            "excluded_areas": {"US": ["AK", "ND"]},
        },
        "announcements": {"presets": [{"id": PRESET_ID, "name": "[Shipping update]", "banner": "shipping"}]},
    },
)
TITLE = "[An update on shipping to] {excluded_areas}"
DESCRIPTION = "[We can't ship to] **{excluded_areas}** [right now.] {channel_shipping}"
CONTENT = {
    preset_key(PRESET_ID, PRESET_TITLE): TITLE,
    preset_key(PRESET_ID, PRESET_DESCRIPTION): DESCRIPTION,
    FORMAT_LAST_AREA: "{areas} and {last}",
}


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[AnnounceEnv]:
    announce = await announce_env(tmp_path, SETTINGS, content=CONTENT)
    try:
        yield announce
    finally:
        await announce.close()


def exclude(env: AnnounceEnv, areas: dict[str, tuple[str, ...]]) -> None:
    """``[shipping.excluded_areas]`` changed (as after an edit of settings.toml and /reload)."""
    settings = env.app.settings
    env.app.settings = replace(settings, shipping=replace(settings.shipping, excluded_areas=areas))


def op(env: AnnounceEnv, key: str, **values: Any) -> str:
    return env.app.op(key, **values)


# -- {excluded_areas} -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("areas", "expected"),
    [
        ({}, ""),
        ({"US": ("AK",)}, "Alaska"),
        ({"US": ("AK", "ND")}, "Alaska and North Dakota"),
        ({"US": ("AK", "ND", "HI")}, "Alaska, North Dakota and Hawaii"),
        ({"US": ("DC",)}, "Washington, D.C."),  # the name #shipping shows
        # Areas of two countries: each country's areas followed by its name, in the order #shipping lists them.
        ({"CA": ("Yukon",), "US": ("AK", "ND")}, "Alaska and North Dakota (United States) and Yukon (Canada)"),
    ],
)
async def test_the_excluded_areas_are_written_as_shipping_writes_them(
    env: AnnounceEnv, areas: dict[str, tuple[str, ...]], expected: str
) -> None:
    exclude(env, areas)
    assert presets.area_text(env.app) == expected


# -- the preview and Post -------------------------------------------------------------------------------------------


async def test_preview_and_post_show_the_excluded_areas(env: AnnounceEnv) -> None:
    staff = env.staff()
    shipping = f"<#{env.app.resources.channel_id('shipping')}>"
    copy, card = (await invoke(env, staff, preset=PRESET_ID)).sent
    assert copy.embeds[0].title == "[An update on shipping to] Alaska and North Dakota"
    description = f"[We can't ship to] **Alaska and North Dakota** [right now.] {shipping}"
    assert copy.embeds[0].description == description
    assert buttons(copy) == []  # no coupon, no button
    assert any(cid and cid.startswith("sb:an:post:") for cid, _, _ in buttons(card))
    # The card does not mention a button the announcement does not have.
    assert op(env, "announcements.preset.preview_intro_plain") in card.text()
    assert op(env, "announcements.preset.preview_intro") not in card.text()
    await post(env, staff, card)
    assert len(env.channel.messages) == 1
    message = env.channel.messages[0]
    assert message.embeds[0].title == "[An update on shipping to] Alaska and North Dakota"
    assert message.embeds[0].description == description
    assert buttons(message) == []
    assert card.embeds[0].title == op(env, "announcements.done.title")


async def test_post_reads_the_areas_again(env: AnnounceEnv) -> None:
    """Post writes the areas excluded at that moment, so the post matches #shipping and the address step."""
    staff = env.staff()
    card = card_of(await invoke(env, staff, preset=PRESET_ID))
    exclude(env, {"US": ("AK", "ND", "HI")})
    await post(env, staff, card)
    assert env.channel.messages[0].embeds[0].title == "[An update on shipping to] Alaska, North Dakota and Hawaii"


async def test_nothing_excluded_shows_the_problem_and_no_post(env: AnnounceEnv) -> None:
    exclude(env, {})
    interaction = await invoke(env, env.staff(), preset=PRESET_ID)
    assert len(interaction.sent) == 1  # only the card: the copy would name no area
    card = card_of(interaction)
    assert card.embeds[0].title == op(env, "announcements.preset.problem_title", name="[Shipping update]")
    assert op(env, "announcements.preset.nothing_excluded") in card.text()
    assert buttons(card) == []
    assert await rows(env) == [] and env.channel.messages == []


async def test_areas_removed_before_post_are_refused(env: AnnounceEnv) -> None:
    staff = env.staff()
    card = card_of(await invoke(env, staff, preset=PRESET_ID))
    exclude(env, {})
    await post(env, staff, card)
    assert env.channel.messages == []
    text = card.text()
    assert op(env, "announcements.preset.nothing_excluded") in text
    assert op(env, "announcements.preset.post_rerun_hint", preset=PRESET_ID) in text
    assert buttons(card) == []  # run the preset again once something is excluded
    assert [r["status"] for r in await rows(env)] == ["draft"]


async def test_a_missing_joining_text_is_named(env: AnnounceEnv) -> None:
    """Two areas need #shipping's joining text (shipping.format.last_area); without it nothing can be posted."""
    env.app.content.replace(file_values={**env.tenv.content, FORMAT_LAST_AREA: ""})
    card = card_of(await invoke(env, env.staff(), preset=PRESET_ID))
    assert env.app.op("announcements.preset.text_missing", key=FORMAT_LAST_AREA) in card.text()
    assert buttons(card) == []


async def test_a_preset_without_the_areas_does_not_need_the_joining_text(tmp_path: Path) -> None:
    """Only a preset whose texts show {excluded_areas} writes the areas: another preset (e.g. warehouse_sale) is
    previewed, posted and shown in the preview while two areas are excluded and #shipping's joining text is not set."""
    other = "closed"
    presets_table = [*SETTINGS["announcements"]["presets"], {"id": other, "name": "[Closed]"}]
    texts = {preset_key(other, PRESET_TITLE): "[Closed title]", preset_key(other, PRESET_DESCRIPTION): "[Closed]"}
    announce = await announce_env(
        tmp_path, deep_merge(SETTINGS, {"announcements": {"presets": presets_table}}), content={**CONTENT, **texts}
    )
    try:
        app = announce.app
        app.content.replace(file_values={**announce.tenv.content, FORMAT_LAST_AREA: ""})
        assert dict(app.settings.shipping.excluded_areas) == {"US": ("AK", "ND")}  # two areas: the text is needed
        staff = announce.staff()
        copy, card = (await invoke(announce, staff, preset=other)).sent
        assert copy.embeds[0].title == "[Closed title]"
        await post(announce, staff, card)
        assert [message.embeds[0].title for message in announce.channel.messages] == ["[Closed title]"]
        assert MODULE.preview is not None
        names = {state.name for state in MODULE.preview(app)}
        assert {f"preset_{other}_preview_announcement", f"preset_{other}_announcement"} <= names
        assert f"preset_{PRESET_ID}_announcement" not in names  # the preset showing the areas needs the text
    finally:
        await announce.close()


# -- the shipped preset -----------------------------------------------------------------------------------------------


def test_the_shipped_preset() -> None:
    loaded = load_config(ROOT / "config")
    preset = loaded.settings.announcements.preset(PRESET_ID)
    assert preset is not None and preset.coupon == "" and preset.banner == "shipping"
    assert preset.name == "Shipping update: areas we can't ship to right now"
    for part in (PRESET_TITLE, PRESET_DESCRIPTION):
        assert "{excluded_areas}" in loaded.content_values[preset_key(PRESET_ID, part)], part
    assert "{channel_shipping}" in loaded.content_values[preset_key(PRESET_ID, PRESET_DESCRIPTION)]


async def test_the_shipped_texts_name_the_excluded_areas_from_the_settings() -> None:
    app = await build_offline_app(ROOT / "config", content_mode="actual", all_features=True)
    try:
        assert MODULE.preview is not None
        states = {state.name: state for state in MODULE.preview(app)}
        areas = presets.area_text(app)
        names = [f"{PRESET_ID}_preview_announcement", f"{PRESET_ID}_preview", f"{PRESET_ID}_announcement"]
        for name in [f"preset_{n}" for n in names] + [f"preset_{PRESET_ID}_preview_nothing_excluded"]:
            assert check_state(states[name]) == [], name
            assert unrouted_components(states[name], app.router) == [], name
        posted = states[f"preset_{PRESET_ID}_announcement"].spec
        assert isinstance(posted, MessageSpec)
        embed = posted.embeds[0]
        assert embed.title == f"An update on shipping to {areas}"
        assert embed.description is not None and f"**{areas}**" in embed.description
        assert posted.banner == app.ui.banner("shipping")
        nothing = states[f"preset_{PRESET_ID}_preview_nothing_excluded"].spec
        assert isinstance(nothing, MessageSpec) and nothing.rows == ()
        assert app.op("announcements.preset.nothing_excluded") in (nothing.embeds[0].description or "")
    finally:
        await app.db.close()
