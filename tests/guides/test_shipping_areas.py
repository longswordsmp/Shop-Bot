"""#shipping lists every US state by name (``shipping.list_areas``) at the bottom of the shipping card itself (one
message, one embed, no fields): a heading, then the 50 states and DC alphabetically on one line per status, like the
countries (the available ones after the blue dot, the excluded ones after the grey dot)."""

from __future__ import annotations

from dataclasses import replace

from shopbot.config.loader import load_config
from shopbot.core.countries import LISTED_AREAS, US_STATES, area_display_name, area_name, matches_area
from shopbot.features.guides import common, shipping
from shopbot.features.guides.common import Texts
from shopbot.ui.limits import validate_message
from tests.helpers import ROOT, TestEnv

# As long as the bot's icons (a custom emoji is "<:name:id>" with a 19-digit id).
FULL, NONE = "<:dot_blue:1300000000000000001>", "<:dot_grey:1300000000000000002>"
HEADING = "[states of] {country}"


def texts() -> Texts:
    values = {
        shipping.status_indicator_key("full"): FULL,
        shipping.status_indicator_key("none"): NONE,
        shipping.AREAS_HEADING: HEADING,
        shipping.AREAS_INTRODUCTION: "[intro {country}]",
    }

    def sample(key: str) -> str | None:
        return values.get(key, f"[{key}]")

    return common.sample_texts(sample)


def test_every_state_and_dc_is_listed_alphabetically() -> None:
    codes = LISTED_AREAS["US"]
    assert len(codes) == 51 and "DC" in codes and not {"PR", "GU", "VI", "AS", "MP"} & set(codes)
    names = [area_display_name("US", c) for c in codes]
    assert names == sorted(names)
    assert names[names.index("Washington") + 1] == "Washington, D.C."  # as people know it, not District of Columbia


def test_dc_is_shown_by_its_everyday_name_but_matched_by_both() -> None:
    assert area_display_name("US", "DC") == "Washington, D.C." and area_display_name("US", "TX") == "Texas"
    assert area_name("US", "DC") == US_STATES["DC"] == "District of Columbia"  # address checks keep their data
    assert matches_area("US", "DC", "district of columbia") and matches_area("US", "DC", "DC")


def test_the_shipped_shipping_card_ends_with_the_states_in_the_same_embed(tenv: TestEnv) -> None:
    settings = load_config(ROOT / "config").settings
    s = settings.shipping
    assert s.list_areas == ("US",)
    specs = shipping.shipping_messages(tenv.app.ui, s, settings.checkout, texts())
    # One message, one card, no fields: no separate states message, no gaps between parts of the list.
    assert len(specs) == 1 and len(specs[0].embeds) == 1 and validate_message(specs[0]) == []
    (card,) = specs[0].embeds
    assert card.title == "[shipping.title]" and card.fields == ()
    # After the information sections: the states' heading and optional line, then one line per status, like the
    # countries (the available states after the blue dot, Alaska and North Dakota after the grey one).
    text = (card.description or "").split("\n")
    heading = text.index("**[states of] United States**")
    assert text[heading - 1] == "" and text[heading - 2] == f"[{shipping.info_value_key(s.info_sections)}]"
    shipped = [area_display_name("US", c) for c in LISTED_AREAS["US"] if c not in s.excluded_areas["US"]]
    # A name's own spaces are no-break spaces, so "New Jersey" never wraps apart on a phone.
    available = f"{FULL} " + " · ".join(name.replace(" ", "\u00a0") for name in shipped)
    assert text[heading + 1 :] == ["[intro United States]", available, f"{NONE} Alaska · North\u00a0Dakota"]
    assert "New\u00a0Jersey" in available and "New Jersey" not in available
    assert available.startswith(f"{FULL} Alabama · Arizona") and available.endswith(" · Wisconsin · Wyoming")
    assert len(shipped) == 49 and "Texas" in shipped and "Washington, D.C." in shipped


def test_the_areas_section_lists_every_area_once() -> None:
    settings = load_config(ROOT / "config").settings
    section = shipping.areas_section(texts(), settings.shipping, "US")
    assert section.heading == "[states of] United States"
    names = [n for line in section.lines[1:] for n in line.split(" ", 1)[1].split(shipping.COUNTRY_SEPARATOR)]
    names = [n.replace(shipping.NO_BREAK_SPACE, " ") for n in names]
    assert sorted(names) == sorted(area_display_name("US", c) for c in LISTED_AREAS["US"]) and len(names) == 51


def test_without_excluded_areas_there_is_one_line(tenv: TestEnv) -> None:
    settings = load_config(ROOT / "config").settings
    s = replace(settings.shipping, excluded_areas={})
    section = shipping.areas_section(texts(), s, "US")
    assert len(section.lines) == 2 and section.lines[1].startswith(f"{FULL} Alabama")


def test_a_further_listed_country_is_one_more_section(tenv: TestEnv) -> None:
    settings = load_config(ROOT / "config").settings
    s = replace(settings.shipping, list_areas=("US", "US"))
    specs = shipping.shipping_messages(tenv.app.ui, s, settings.checkout, texts())
    assert all(validate_message(spec) == [] for spec in specs)
    assert all(embed.fields == () for spec in specs for embed in spec.embeds)
    text = "\n".join(embed.description or "" for spec in specs for embed in spec.embeds)
    assert text.count("**[states of] United States**") == 2


def test_without_listed_areas_the_card_has_no_states(tenv: TestEnv) -> None:
    settings = load_config(ROOT / "config").settings
    s = replace(settings.shipping, list_areas=())
    (spec,) = shipping.shipping_messages(tenv.app.ui, s, settings.checkout, texts())
    assert spec.embeds[0].fields == () and "[states of]" not in (spec.embeds[0].description or "")
