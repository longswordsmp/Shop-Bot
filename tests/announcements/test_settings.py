"""[announcements] settings parsing, settings problems, content keys and readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.loader import load_config
from shopbot.config.settings import AnnouncementField, SettingsError, parse_settings
from shopbot.features import all_modules
from shopbot.features.announcements import MODULE
from shopbot.features.announcements.content_keys import content_keys, field_name_key, settings_problems
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
    assert an.allow_image is True


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


def test_shipped_settings_enable_announcements_without_business_text() -> None:
    loaded = load_config(ROOT / "config")
    an = loaded.settings.announcements
    assert loaded.settings.features.announcements is True
    assert an.channel == "announcements" and an.fields == () and an.mention_roles == ()
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
