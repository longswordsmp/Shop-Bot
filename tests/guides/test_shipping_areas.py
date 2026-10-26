"""#shipping lists every US state one by one (``shipping.list_areas``): a card of its own after the shipping card,
the 50 states and DC alphabetically in three columns, each after its dot (grey for the excluded ones)."""

from __future__ import annotations

from shopbot.config.loader import load_config
from shopbot.core.countries import LISTED_AREAS, US_STATES, area_display_name, area_name, matches_area
from shopbot.features.guides import common, shipping
from shopbot.features.guides.common import Texts
from shopbot.ui.limits import validate_message
from tests.helpers import ROOT, TestEnv

FULL, NONE = "[full]", "[none]"


def texts() -> Texts:
    values = {
        shipping.status_indicator_key("full"): FULL,
        shipping.status_indicator_key("none"): NONE,
        shipping.AREAS_TITLE: "Shipping in the {country}",
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


def test_the_shipped_shipping_channel_lists_the_states_after_the_shipping_card(tenv: TestEnv) -> None:
    settings = load_config(ROOT / "config").settings
    s = settings.shipping
    assert s.list_areas == ("US",)
    card = shipping.areas_card(texts(), s, "US")
    assert card.title == "Shipping in the United States" and card.intro == "[intro United States]"
    columns = card.fields
    assert len(columns) == shipping.AREA_COLUMNS and all(c.inline for c in columns)
    lines = [line for column in columns for line in column.value.split("\n")]
    assert len(lines) == 51
    assert f"{NONE} Alaska" in lines and f"{NONE} North Dakota" in lines
    assert f"{FULL} Texas" in lines and f"{FULL} Washington, D.C." in lines
    assert sum(line.startswith(NONE) for line in lines) == len(s.excluded_areas["US"])
    # The shipping card, then the states in a message of their own (one card per message, within the limits).
    specs = shipping.shipping_messages(tenv.app.ui, s, settings.checkout, texts())
    assert all(validate_message(spec) == [] for spec in specs)
    assert all(len(spec.embeds) == 1 for spec in specs)
    assert [spec.embeds[0].title for spec in specs][-1] == "Shipping in the United States"
