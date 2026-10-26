"""[announcements] settings parsing, settings problems, content keys and readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content_keys import Kind
from shopbot.config.loader import load_config
from shopbot.config.settings import AnnouncementField, AnnouncementPreset, SettingsError, parse_settings
from shopbot.features import all_modules
from shopbot.features.announcements import MODULE
from shopbot.features.announcements.content_keys import (
    content_keys,
    field_name_key,
    preset_key,
    settings_problems,
)
from tests.announcements.util import BASE_OVERRIDES, announce_env
from tests.helpers import ROOT, base_settings_dict, deep_merge, make_settings


def _parse(announcements: dict[str, Any]) -> Any:
    data = deep_merge(base_settings_dict(), {"announcements": announcements})
    return parse_settings(data)


def _errors(announcements: dict[str, Any]) -> list[str]:
    with pytest.raises(SettingsError) as exc:
        _parse(announcements)
    return exc.value.errors


def test_defaults() -> None:
    settings = _parse({})
    an = settings.announcements
    assert an.channel == "" and an.roles == () and an.mention_roles == () and an.fields == ()
    assert an.allow_image is True and an.presets == ()


def test_full_section_parses() -> None:
    an = _parse(
        {
            "channel": "news",
            "roles": ["announcer"],
            "mention_roles": ["alerts"],
            "allow_image": False,
            "fields": [
                {"id": "when", "style": "short", "required": True, "max_length": 100},
                {"id": "details", "style": "paragraph"},
            ],
        }
    ).announcements
    assert an.channel == "news" and an.roles == ("announcer",) and an.mention_roles == ("alerts",)
    assert an.allow_image is False
    assert an.fields == (
        AnnouncementField("when", "short", True, 100),
        AnnouncementField("details", "paragraph", False, 1024),
    )
    assert an.field("details") == an.fields[1] and an.field("nope") is None


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({"fields": [{"id": f"f{i}"} for i in range(4)]}, "at most 3 fields"),
        ({"fields": [{"id": "a"}, {"id": "a"}]}, "duplicate id 'a'"),
        ({"fields": [{"style": "short"}]}, "is required"),
        ({"fields": [{"id": "Bad Id"}]}, "must match"),
        ({"fields": [{"id": "a", "style": "long"}]}, "must be one of"),
        ({"fields": [{"id": "a", "max_length": 0}]}, "must be >= 1"),
        ({"fields": [{"id": "a", "max_length": 1025}]}, "must be <= 1024"),
        ({"fields": [{"id": "a", "required": "yes"}]}, "expected true or false"),
        ({"fields": [{"id": "a", "label": "x"}]}, "unknown setting"),
        ({"mention_roles": [f"r{i}" for i in range(26)]}, "at most 25 roles"),
        ({"mention_roles": ["a", "a"]}, "contains duplicates"),
        ({"roles": "staff"}, "expected a list of strings"),
        ({"allow_image": 1}, "expected true or false"),
        ({"channel": "Not A Key"}, "must match"),
        ({"typo": True}, "unknown setting"),
    ],
)
def test_invalid_settings_are_rejected(section: dict[str, Any], expected: str) -> None:
    errors = _errors(section)
    assert any(expected in error and error.startswith("announcements") for error in errors), errors


def test_presets_parse_and_normalize_the_coupon() -> None:
    an = _parse(
        {
            "presets": [
                {
                    "id": "warehouse_sale",
                    "name": "Warehouse sale",
                    "coupon": " bogo ",
                    "coupon_command": "/discount create code:BOGO type:Buy one, get one free",
                    "banner": "general-response",
                },
                {"id": "2026_closed", "name": "Closed"},
            ]
        }
    ).announcements
    assert an.presets == (
        AnnouncementPreset(
            "warehouse_sale",
            "Warehouse sale",
            "BOGO",
            "/discount create code:BOGO type:Buy one, get one free",
            "general-response",
        ),
        AnnouncementPreset("2026_closed", "Closed"),
    )
    assert an.presets[1].banner == "announcements" and an.preset("2026_closed") == an.presets[1]
    assert an.preset("nope") is None


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        ({"name": "x"}, "id: is required"),
        ({"id": "Bad-Id", "name": "x"}, "must match"),
        ({"id": "a" * 33, "name": "x"}, "must match"),
        ({"id": "a"}, "name: is required"),
        ({"id": "a", "name": " "}, "name: is required"),
        ({"id": "a", "name": "x" * 101}, "at most 100 characters"),
        ({"id": "a", "name": "two\nlines"}, "must be one line"),
        ({"id": "a", "name": "x", "coupon": "no spaces"}, "must be a discount code"),
        ({"id": "a", "name": "x", "coupon": "AB"}, "must be a discount code"),
        # 15 characters: "Use coupon <code>" would pass the 25 characters of a button label (B1).
        ({"id": "a", "name": "x", "coupon": "SPRING-SALE-123"}, "3 to 14 characters"),
        ({"id": "a", "name": "x", "coupon_command": "/discount create code:X"}, "needs a coupon"),
        ({"id": "a", "name": "x", "coupon": "BOGO", "coupon_command": "/discount create code:OTHER"}, "starting with"),
        ({"id": "a", "name": "x", "coupon": "BOGO", "coupon_command": "/discount create code:BOGOX"}, "starting with"),
        ({"id": "a", "name": "x", "banner": "nope"}, "must be one of"),
        ({"id": "a", "name": "x", "title": "x"}, "unknown setting"),
    ],
)
def test_invalid_presets_are_rejected(preset: dict[str, Any], expected: str) -> None:
    errors = _errors({"presets": [preset]})
    assert any(expected in error and error.startswith("announcements.presets[0]") for error in errors), errors


def test_preset_ids_are_unique_and_at_most_25() -> None:
    assert any("duplicate id 'a'" in e for e in _errors({"presets": [{"id": "a", "name": "x"}] * 2}))
    many = [{"id": f"p{i}", "name": "x"} for i in range(26)]
    assert any("at most 25 presets" in e for e in _errors({"presets": many}))


def test_preset_content_keys() -> None:
    settings = make_settings(
        {
            "announcements": {
                "presets": [{"id": "sale", "name": "Sale", "coupon": "SALE"}, {"id": "news", "name": "News"}]
            }
        }
    )
    keys = {k.key: k for k in content_keys(settings)}
    assert set(keys) == {
        preset_key("sale", "title"),
        preset_key("sale", "description"),
        preset_key("sale", "claim_button"),
        preset_key("sale", "ended_button"),
        preset_key("news", "title"),
        preset_key("news", "description"),
    }
    assert all(k.required and k.feature == "announcements" and k.doc for k in keys.values())
    assert keys[preset_key("sale", "title")].kind is Kind.TITLE
    assert keys[preset_key("sale", "claim_button")].kind is Kind.BUTTON_LABEL
    assert keys[preset_key("sale", "ended_button")].kind is Kind.BUTTON_LABEL
    description = keys[preset_key("sale", "description")]
    assert description.kind is Kind.DESCRIPTION and "channel_catalog" in description.placeholders


def test_settings_problems_check_references() -> None:
    settings = make_settings(
        {
            "features": {"announcements": True},
            "announcements": {"channel": "missing", "roles": ["ghost"], "mention_roles": ["phantom"]},
        }
    )
    problems = settings_problems(settings)
    assert "announcements.channel: channel 'missing' is not defined in server.channels" in problems
    assert "announcements.roles: role 'ghost' is not defined in server.roles" in problems
    assert "announcements.mention_roles: role 'phantom' is not defined in server.roles" in problems
    assert any("required" in p for p in settings_problems(make_settings({"announcements": {"channel": ""}})))
    assert settings_problems(make_settings(BASE_OVERRIDES)) == []


def test_content_keys_follow_the_configured_fields() -> None:
    settings = make_settings(BASE_OVERRIDES)
    keys = content_keys(settings)
    assert [k.key for k in keys] == [field_name_key("when"), field_name_key("details")]
    assert all(k.required and k.feature == "announcements" and k.doc for k in keys)
    assert content_keys(make_settings({})) == []


def test_module_is_registered_without_panels() -> None:
    names = [m.name for m in all_modules()]
    assert "announcements" in names
    assert MODULE.publish is None and MODULE.panel_prefixes == ()
    assert MODULE.customer_facing is True


def test_shipped_settings_enable_announcements() -> None:
    loaded = load_config(ROOT / "config")
    an = loaded.settings.announcements
    assert loaded.settings.features.announcements is True
    assert an.channel == "announcements" and an.fields == ()
    assert an.mention_roles == ("restock_alerts",)  # staff may choose to ping Restock Alerts
    sale = an.preset("warehouse_sale")
    assert sale is not None and sale.coupon == "BOGO" and sale.name == "Warehouse sale: buy one, get one free"
    assert sale.coupon_command.startswith("/discount create code:BOGO type:Buy one, get one free")
    assert loaded.content_values[preset_key("warehouse_sale", "claim_button")] == "{icon_gift} Claim coupon"
    assert settings_problems(loaded.settings) == []


async def test_missing_field_heading_keeps_the_feature_not_ready(tmp_path: Path) -> None:
    env = await announce_env(tmp_path)
    try:
        assert env.app.ready("announcements")
        env.app.content.replace(file_values={**env.tenv.content, field_name_key("when"): ""})
        env.app.recompute_readiness()
        status = env.app.readiness.statuses["announcements"]
        assert not status.ready
        assert any(p.key == field_name_key("when") for p in status.problems)
    finally:
        await env.close()


async def test_disabled_feature_is_not_ready(tmp_path: Path) -> None:
    env = await announce_env(tmp_path, {"features": {"announcements": False}})
    try:
        assert not env.app.ready("announcements")
    finally:
        await env.close()
