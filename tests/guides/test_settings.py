"""Parsing and validation of [how_to_buy], [faq] and [shipping]."""

from __future__ import annotations

from typing import Any

import pytest

from shopbot.config.loader import load_config
from shopbot.config.settings import (
    FaqSection,
    HowToBuySettings,
    SettingsError,
    ShippingGroup,
    parse_settings,
)
from shopbot.features.guides import faq, how_to_buy, shipping
from tests.guides.util import guide_settings
from tests.helpers import ROOT, base_settings_dict, deep_merge


def parse_errors(section: str, table: dict[str, Any]) -> list[str]:
    data = base_settings_dict()
    data[section] = table
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    return exc.value.errors


def test_defaults_without_sections() -> None:
    settings = parse_settings(base_settings_dict())
    assert settings.how_to_buy == HowToBuySettings(channel="", steps=4, link_channels=())
    assert settings.faq.sections == ()
    assert settings.shipping.groups == () and settings.shipping.not_available == ()
    assert dict(settings.shipping.excluded_areas) == {} and settings.shipping.info_sections == 0


def test_valid_sections_are_parsed() -> None:
    settings = guide_settings({"shipping": {"country_names": {"CZ": "Czech Republic"}}})
    assert settings.how_to_buy.steps == 3
    assert settings.how_to_buy.link_channels == ("catalog", "purchase", "faq")
    assert settings.faq.sections == (FaqSection("general", 2), FaqSection("orders", 1))
    assert settings.shipping.groups[0] == ShippingGroup("north", ("CA", "US", "MX"))  # in the configured order
    assert settings.shipping.countries == ("CA", "US", "MX", "DE", "FR")
    assert dict(settings.shipping.excluded_areas) == {"US": ("AK", "ND")}
    assert settings.shipping.ships_to("US") and not settings.shipping.ships_to("CU")
    assert (
        settings.shipping.country_name("CZ") == "Czech Republic" and settings.shipping.country_name("DE") == "Germany"
    )


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ({"steps": 0}, "how_to_buy.steps: must be >= 1"),
        ({"steps": 26}, "how_to_buy.steps: must be <= 25"),
        ({"steps": "4"}, "how_to_buy.steps: expected an integer"),
        ({"link_channels": ["Catalog"]}, "how_to_buy.link_channels: 'Catalog' is not a valid key"),
        ({"link_channels": ["faq", "faq"]}, "how_to_buy.link_channels: contains duplicates"),
        ({"link_channels": [f"c{n}" for n in range(26)]}, "how_to_buy.link_channels: at most 25 link buttons"),
        ({"channel": "How To Buy"}, "how_to_buy.channel: must match"),
        ({"stepz": 3}, "how_to_buy.stepz: unknown setting"),
    ],
)
def test_how_to_buy_errors(table: dict[str, Any], expected: str) -> None:
    errors = parse_errors("how_to_buy", table)
    assert any(e.startswith(expected) for e in errors), errors


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ({"sections": [{"id": "a", "questions": 0}]}, "faq.sections[0].questions: must be >= 1"),
        ({"sections": [{"id": "a", "questions": 26}]}, "faq.sections[0].questions: must be <= 25"),
        ({"sections": [{"questions": 1}]}, "faq.sections[0].id: is required"),
        ({"sections": [{"id": "Bad Id"}]}, "faq.sections[0].id: must match"),
        ({"sections": [{"id": "a"}, {"id": "a"}]}, "faq.sections: duplicate id 'a'"),
        ({"sections": [{"id": f"s{n}"} for n in range(26)]}, "faq.sections: at most 25 sections"),
        ({"sections": [{"id": "a", "title": "x"}]}, "faq.sections[0].title: unknown setting"),
        ({"sections": {"id": "a"}}, "faq.sections: expected an array of tables"),
    ],
)
def test_faq_errors(table: dict[str, Any], expected: str) -> None:
    errors = parse_errors("faq", table)
    assert any(e.startswith(expected) for e in errors), errors


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ({"groups": [{"id": "a", "countries": ["XX"]}]}, "shipping.groups[0].countries: 'XX' is not a country code"),
        ({"groups": [{"id": "a", "countries": ["us"]}]}, "shipping.groups[0].countries: 'us' is not a country code"),
        ({"groups": [{"id": "a", "countries": []}]}, "shipping.groups[0].countries: list at least one"),
        ({"groups": [{"countries": ["US"]}]}, "shipping.groups[0].id: is required"),
        ({"groups": [{"id": "a", "countries": ["US", "US"]}]}, "shipping.groups[0].countries: contains duplicates"),
        (
            {"groups": [{"id": "a", "countries": ["US"]}, {"id": "b", "countries": ["US"]}]},
            "shipping.groups: US is listed in more than one group",
        ),
        ({"groups": [{"id": "a", "countries": ["US"]}] * 2}, "shipping.groups: duplicate id 'a'"),
        ({"groups": [{"id": f"g{n}", "countries": ["US"]} for n in range(21)]}, "shipping.groups: at most 20"),
        (
            {"groups": [{"id": "a", "countries": ["US"]}], "not_available": ["US"]},
            "shipping.not_available: US is also listed in a group",
        ),
        ({"not_available": ["ZZ"]}, "shipping.not_available: 'ZZ' is not a country code"),
        ({"excluded_areas": {"US": ["AK"]}}, "shipping.excluded_areas.US: is not in any [[shipping.groups]]"),
        (
            {"groups": [{"id": "a", "countries": ["US"]}], "excluded_areas": {"US": ["XX"]}},
            "shipping.excluded_areas.US: 'XX' is not a state code of US",
        ),
        ({"excluded_areas": {"Narnia": ["x"]}}, "shipping.excluded_areas.Narnia: is not a country code"),
        ({"country_names": {"QQ": "x"}}, "shipping.country_names.QQ: is not a country code"),
        ({"info_sections": 11}, "shipping.info_sections: must be <= 10"),
        ({"info_sections": -1}, "shipping.info_sections: must be >= 0"),
        ({"regions": []}, "shipping.regions: unknown setting"),
        ({"groups": [{"id": "a", "countries": ["US"], "name": "A"}]}, "shipping.groups[0].name: unknown setting"),
        (
            {"groups": [{"id": "a", "countries": ["PT"]}], "list_areas": ["PT"]},
            "shipping.list_areas: 'PT' has no areas to list",
        ),
        ({"groups": [{"id": "a", "countries": ["CA"]}], "list_areas": ["US"]}, "shipping.list_areas: US is not in any"),
        ({"groups": [{"id": "a", "countries": ["US"]}], "list_areas": ["US", "US"]}, "shipping.list_areas: contains"),
    ],
)
def test_shipping_errors(table: dict[str, Any], expected: str) -> None:
    errors = parse_errors("shipping", table)
    assert any(e.startswith(expected) for e in errors), errors


def test_excluded_areas_of_other_countries_are_names() -> None:
    table = {"groups": [{"id": "eu", "countries": ["PT"]}], "excluded_areas": {"PT": ["Azores", "Madeira"]}}
    settings = parse_settings(deep_merge(base_settings_dict(), {"shipping": table}))
    assert dict(settings.shipping.excluded_areas) == {"PT": ("Azores", "Madeira")}


def test_shipped_settings_configure_the_three_guides() -> None:
    settings = load_config(ROOT / "config").settings
    assert settings.how_to_buy.channel == "how_to_buy" and settings.how_to_buy.steps == 6
    assert settings.how_to_buy.link_channels == ("catalog", "purchase", "faq")
    assert [s.id for s in settings.faq.sections] == ["ordering", "payment", "shipping", "tracking", "problems"]
    # ordering: + the two bulk-order answers; payment: + discount codes
    assert [s.questions for s in settings.faq.sections] == [5, 6, 6, 2, 2]
    # The owner ships to every country in the Americas and to Europe; in the US not to Alaska or North Dakota.
    # Cuba, Russia and Belarus are listed as not available (sanctions; most carriers refuse parcels).
    s = settings.shipping
    assert [g.id for g in s.groups] == ["north_america", "central_america", "caribbean", "south_america", "europe"]
    assert dict(s.excluded_areas) == {"US": ("AK", "ND")}
    assert s.not_available == ("CU", "RU", "BY")
    americas = {code for g in s.groups[:4] for code in g.countries}
    assert len(americas) == 34 and "CU" not in americas  # the 35 countries of the Americas except Cuba
    assert {"GB", "DE", "FR", "UA", "XK", "VA"} <= set(s.groups[4].countries) and len(s.groups[4].countries) == 44
    assert settings.shipping.info_sections == 2
    # The shipped structure itself is consistent.
    assert how_to_buy.settings_problems(settings) == []
    assert faq.settings_problems(settings) == []
    assert shipping.settings_problems(settings) == []
