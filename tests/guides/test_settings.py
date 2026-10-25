"""Parsing and validation of [how_to_buy], [faq] and [shipping]."""

from __future__ import annotations

from typing import Any

import pytest

from shopbot.config.loader import load_config
from shopbot.config.settings import (
    SHIPPING_STATUSES,
    FaqSection,
    HowToBuySettings,
    SettingsError,
    ShippingRegion,
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
    assert settings.shipping.statuses == SHIPPING_STATUSES
    assert settings.shipping.regions == () and settings.shipping.info_sections == 0
    assert settings.shipping.info_position == "after"


def test_valid_sections_are_parsed() -> None:
    settings = guide_settings({"shipping": {"statuses": ["partial", "full"], "info_position": "before"}})
    assert settings.how_to_buy.steps == 3
    assert settings.how_to_buy.link_channels == ("catalog", "purchase", "faq")
    assert settings.faq.sections == (FaqSection("general", 2), FaqSection("orders", 1))
    assert settings.shipping.statuses == ("partial", "full")  # display order is the configured order
    assert settings.shipping.regions[0] == ShippingRegion("north", "full")
    assert settings.shipping.info_position == "before"


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
        ({"statuses": ["full", "some"]}, "shipping.statuses: 'some' is not one of"),
        ({"statuses": ["full", "full"]}, "shipping.statuses: contains duplicates"),
        ({"regions": [{"id": "a", "status": "maybe"}]}, "shipping.regions[0].status: must be one of"),
        ({"regions": [{"id": "a"}]}, "shipping.regions[0].status: is required"),
        ({"regions": [{"status": "full"}]}, "shipping.regions[0].id: is required"),
        ({"regions": [{"id": "a", "status": "full"}] * 2}, "shipping.regions: duplicate id 'a'"),
        ({"regions": [{"id": f"r{n}", "status": "full"} for n in range(501)]}, "shipping.regions: at most 500"),
        ({"info_sections": 26}, "shipping.info_sections: must be <= 25"),
        ({"info_sections": -1}, "shipping.info_sections: must be >= 0"),
        ({"info_position": "middle"}, "shipping.info_position: must be one of"),
        ({"regions": [{"id": "a", "status": "full", "name": "A"}]}, "shipping.regions[0].name: unknown setting"),
    ],
)
def test_shipping_errors(table: dict[str, Any], expected: str) -> None:
    errors = parse_errors("shipping", table)
    assert any(e.startswith(expected) for e in errors), errors


def test_empty_status_list_is_allowed() -> None:
    settings = parse_settings(deep_merge(base_settings_dict(), {"shipping": {"statuses": []}}))
    assert settings.shipping.statuses == ()


def test_shipped_settings_configure_the_three_guides() -> None:
    settings = load_config(ROOT / "config").settings
    assert settings.how_to_buy.channel == "how_to_buy" and settings.how_to_buy.steps == 4
    assert settings.how_to_buy.link_channels == ("catalog", "purchase", "faq")
    assert [s.id for s in settings.faq.sections] == [
        "general",
        "transactions",
        "order_status",
        "cancellations",
        "problems",
    ]
    assert [s.questions for s in settings.faq.sections] == [3, 3, 3, 2, 2]
    assert settings.shipping.statuses == ("full", "none")
    # The owner ships to all of the US and Europe, except Alaska and North Dakota.
    assert [(r.id, r.status) for r in settings.shipping.regions] == [
        ("united_states", "full"),
        ("europe", "full"),
        ("alaska", "none"),
        ("north_dakota", "none"),
        ("rest_of_world", "none"),
    ]
    assert settings.shipping.info_sections == 2
    # The shipped structure itself is consistent.
    assert how_to_buy.settings_problems(settings) == []
    assert faq.settings_problems(settings) == []
    assert shipping.settings_problems(settings) == []
