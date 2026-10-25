"""Settings parsing, settings problems, content keys, readiness, operator text and the module contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content_keys import ContentKey, Kind
from shopbot.config.readiness import build_registry
from shopbot.config.settings import SettingsError, VerificationSettings, parse_settings
from shopbot.features import all_modules
from shopbot.features.server_keys import channel_placeholders
from shopbot.features.verification import MODULE
from shopbot.features.verification import content_keys as k
from shopbot.features.verification.text import OPERATOR_TEXT
from tests.helpers import base_settings_dict, deep_merge, make_settings, shipped_settings_dict
from tests.verification.util import verification_env, verification_overrides, verification_settings

PACKAGE = Path(__file__).resolve().parents[2] / "shopbot" / "features" / "verification"

FIXED_KEYS = {
    k.WELCOME_TITLE,
    k.WELCOME_DESCRIPTION,
    k.WELCOME_IMAGE,
    k.WELCOME_THUMBNAIL,
    k.RULES_TITLE,
    k.RULES_DESCRIPTION,
    k.BUTTON_LABEL,
    k.BUTTON_EMOJI,
    k.CONFIRMED_TITLE,
    k.CONFIRMED_DESCRIPTION,
    k.ALREADY_TITLE,
    k.ALREADY_DESCRIPTION,
    k.DM_TITLE,
    k.DM_DESCRIPTION,
}


def _parse(section: dict[str, Any]) -> VerificationSettings:
    data = deep_merge(base_settings_dict(), {"verification": section})
    return parse_settings(data).verification


def _keys(extra: dict[str, Any] | None = None, *, rules: int = 3) -> dict[str, ContentKey]:
    return {d.key: d for d in k.content_keys(verification_settings(extra, rules=rules))}


# --------------------------------------------------------------------------------------------------
# Settings parsing
# --------------------------------------------------------------------------------------------------


def test_defaults() -> None:
    parsed = _parse({})
    assert parsed == VerificationSettings(channel="", rules=0, dm_confirmation=False, button_style="success")


def test_values_are_parsed() -> None:
    parsed = _parse({"channel": "welcome", "rules": 25, "dm_confirmation": True, "button_style": "danger"})
    assert parsed == VerificationSettings(channel="welcome", rules=25, dm_confirmation=True, button_style="danger")


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ({"rules": 26}, "verification.rules: must be <= 25"),
        ({"rules": -1}, "verification.rules: must be >= 0"),
        ({"rules": "3"}, "verification.rules: expected an integer"),
        ({"rules": True}, "verification.rules: expected an integer"),
        ({"dm_confirmation": "yes"}, "verification.dm_confirmation: expected true or false"),
        ({"button_style": "link"}, "verification.button_style: must be one of"),
        ({"button_style": ""}, "verification.button_style: must be one of"),
        ({"channel": "Welcome!"}, "verification.channel: must match"),
        ({"chanel": "welcome"}, "verification.chanel: unknown setting"),
    ],
)
def test_invalid_values_are_fatal(section: dict[str, Any], message: str) -> None:
    with pytest.raises(SettingsError) as exc:
        _parse(section)
    assert any(error.startswith(message) for error in exc.value.errors), exc.value.errors


# --------------------------------------------------------------------------------------------------
# Settings problems
# --------------------------------------------------------------------------------------------------


def test_complete_settings_have_no_problems() -> None:
    assert k.settings_problems(verification_settings()) == []


def test_shipped_settings_show_six_rules() -> None:
    settings = parse_settings(shipped_settings_dict())
    assert settings.features.verification is True
    assert settings.verification.channel == "welcome"
    assert settings.verification.rules == 6
    assert settings.verification.dm_confirmation is False
    assert k.settings_problems(settings) == []


def _problems(extra: dict[str, Any], *, rules: int = 3) -> list[str]:
    return k.settings_problems(verification_settings(extra, rules=rules))


def test_channel_is_required_and_must_be_defined() -> None:
    assert any(p.startswith("verification.channel: required") for p in _problems({"verification": {"channel": ""}}))
    undefined = _problems({"verification": {"channel": "nowhere"}})
    assert any("channel 'nowhere' is not defined in server.channels" in p for p in undefined)


@pytest.mark.parametrize("access", ["members_readonly", "members_interactive", "staff", "admin"])
def test_entry_channel_must_be_visible_before_accepting(access: str) -> None:
    overrides = verification_overrides()
    for channel in overrides["server"]["channels"]:
        if channel["key"] == "welcome":
            channel["access"] = access
    problems = k.settings_problems(make_settings(overrides))
    assert any("must be visible to members who have not accepted the rules" in p and access in p for p in problems)


def test_entry_channel_may_be_public_interactive() -> None:
    overrides = verification_overrides()
    for category in overrides["server"]["categories"]:
        if category["key"] == "start_here":
            category["access"] = "public_interactive"
    assert k.settings_problems(make_settings(overrides)) == []


def test_member_role_is_required_and_must_be_defined() -> None:
    unset = _problems({"permissions": {"member_role": ""}})
    assert any(p.startswith("permissions.member_role: required") for p in unset)
    undefined = _problems({"permissions": {"member_role": "verified"}})
    assert "permissions.member_role: role 'verified' is not defined in server.roles" in undefined


@pytest.mark.parametrize(
    ("extra", "where"),
    [
        ({"permissions": {"staff_roles": ["staff", "member"]}}, "permissions.staff_roles"),
        ({"permissions": {"admin_roles": ["member"]}}, "permissions.admin_roles"),
        ({"permissions": {"owner_role": "member"}}, "permissions.owner_role"),
        ({"tickets": {"staff_roles": ["member"]}}, "tickets.staff_roles"),
        ({"checkout": {"staff_roles": ["member"]}}, "checkout.staff_roles"),
        ({"catalog": {"manager_roles": ["member"]}}, "catalog.manager_roles"),
    ],
)
def test_member_role_must_not_grant_staff_access(extra: dict[str, Any], where: str) -> None:
    problems = _problems(extra)
    assert any(where in p and "would give staff access" in p for p in problems), problems


def test_member_role_must_not_be_a_ticket_category_staff_role() -> None:
    settings = verification_settings()
    data = verification_overrides()
    base = base_settings_dict()
    categories = [dict(c, staff_roles=["member"]) for c in base["tickets"]["categories"]]
    data = deep_merge(data, {"tickets": {"categories": categories}})
    problems = k.settings_problems(make_settings(data))
    assert any("tickets.categories" in p and "would give staff access" in p for p in problems), problems
    assert k.settings_problems(settings) == []


def test_member_role_must_not_carry_elevated_permissions() -> None:
    overrides = verification_overrides()
    for role in overrides["server"]["roles"]:
        if role["key"] == "member":
            role["permissions"] = ["send_messages", "manage_messages", "ban_members"]
    problems = k.settings_problems(make_settings(overrides))
    assert any("['ban_members', 'manage_messages']" in p for p in problems), problems
    for role in overrides["server"]["roles"]:
        if role["key"] == "member":
            role["permissions"] = ["send_messages", "attach_files"]
    assert k.settings_problems(make_settings(overrides)) == []


def test_rules_are_required() -> None:
    problems = _problems({}, rules=0)
    assert any(p.startswith("verification.rules is not set") for p in problems)


# --------------------------------------------------------------------------------------------------
# Content keys
# --------------------------------------------------------------------------------------------------


def test_one_key_pair_per_rule() -> None:
    keys = _keys(rules=3)
    rule_keys = {k.rule_name_key(n) for n in range(1, 4)} | {k.rule_value_key(n) for n in range(1, 4)}
    assert set(keys) == FIXED_KEYS | rule_keys
    assert set(_keys(rules=0)) == FIXED_KEYS
    assert len(_keys(rules=25)) == len(FIXED_KEYS) + 50
    for definition in keys.values():
        assert definition.feature == k.VERIFICATION and definition.group and definition.doc


def test_kinds_required_flags_and_placeholders() -> None:
    settings = verification_settings()
    links = channel_placeholders(settings)
    assert "channel_welcome" in links and "channel_lounge" in links
    users = ("user_name", "user_mention")
    expect = {
        k.WELCOME_TITLE: (Kind.TITLE, True, ()),
        k.WELCOME_DESCRIPTION: (Kind.DESCRIPTION, True, links),
        k.WELCOME_IMAGE: (Kind.URL, False, ()),
        k.WELCOME_THUMBNAIL: (Kind.URL, False, ()),
        k.RULES_TITLE: (Kind.TITLE, False, ()),
        k.RULES_DESCRIPTION: (Kind.DESCRIPTION, False, links),
        k.rule_name_key(2): (Kind.FIELD_NAME, True, ()),
        k.rule_value_key(2): (Kind.FIELD_VALUE, True, links),
        k.BUTTON_LABEL: (Kind.BUTTON_LABEL, True, ()),
        k.BUTTON_EMOJI: (Kind.EMOJI, False, ()),
        k.CONFIRMED_TITLE: (Kind.TITLE, False, ("user_name",)),
        k.CONFIRMED_DESCRIPTION: (Kind.DESCRIPTION, True, (*users, *links)),
        k.ALREADY_TITLE: (Kind.TITLE, False, ("user_name",)),
        k.ALREADY_DESCRIPTION: (Kind.DESCRIPTION, True, (*users, *links)),
        k.DM_TITLE: (Kind.TITLE, False, ("user_name",)),
        k.DM_DESCRIPTION: (Kind.DESCRIPTION, False, (*users, *links)),
    }
    keys = _keys()
    for key, (kind, required, placeholders) in expect.items():
        assert (keys[key].kind, keys[key].required, keys[key].placeholders) == (kind, required, placeholders), key


def test_dm_text_is_required_only_with_dm_confirmation() -> None:
    assert not _keys()[k.DM_DESCRIPTION].required
    assert _keys({"verification": {"dm_confirmation": True}})[k.DM_DESCRIPTION].required
    # Without direct messages the confirmation DM is never sent, so its text is not needed.
    no_dms = {"verification": {"dm_confirmation": True}, "features": {"dm_notifications": False}}
    assert not _keys(no_dms)[k.DM_DESCRIPTION].required


def test_keys_do_not_collide_with_other_features() -> None:
    registry = build_registry(verification_settings(rules=25), all_modules())
    assert registry.check_no_prefix_collisions() == []
    assert all(key.startswith("verification.") for key in (d.key for d in registry.for_feature(k.VERIFICATION)))


def test_every_content_key_used_in_code_is_declared() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    constants = set(re.findall(r'= "(verification\.[a-z_]+\.[a-z_]+)"', source))
    assert constants == FIXED_KEYS


# --------------------------------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------------------------------


async def test_ready_with_full_content(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        assert tenv.app.ready(k.VERIFICATION), tenv.app.readiness.statuses[k.VERIFICATION].problems
    finally:
        await tenv.close()


@pytest.mark.parametrize(
    "key",
    [
        k.WELCOME_TITLE,
        k.WELCOME_DESCRIPTION,
        k.BUTTON_LABEL,
        k.rule_name_key(3),
        k.rule_value_key(1),
        k.CONFIRMED_DESCRIPTION,
        k.ALREADY_DESCRIPTION,
    ],
)
async def test_missing_required_text_keeps_the_feature_not_ready(tmp_path: Path, key: str) -> None:
    tenv = await verification_env(tmp_path, content={key: ""})
    try:
        status = tenv.app.readiness.statuses[k.VERIFICATION]
        assert not status.ready
        assert any(p.kind == "missing_content" and p.key == key for p in status.problems)
    finally:
        await tenv.close()


async def test_optional_text_does_not_block_readiness(tmp_path: Path) -> None:
    optional = dict.fromkeys(
        (
            k.WELCOME_IMAGE,
            k.WELCOME_THUMBNAIL,
            k.RULES_TITLE,
            k.RULES_DESCRIPTION,
            k.BUTTON_EMOJI,
            k.CONFIRMED_TITLE,
            k.ALREADY_TITLE,
            k.DM_TITLE,
            k.DM_DESCRIPTION,
        ),
        "",
    )
    tenv = await verification_env(tmp_path, content=optional)
    try:
        assert tenv.app.ready(k.VERIFICATION)
    finally:
        await tenv.close()


async def test_dm_text_blocks_readiness_when_dm_confirmation_is_on(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"dm_confirmation": True}}, content={k.DM_DESCRIPTION: ""})
    try:
        assert not tenv.app.ready(k.VERIFICATION)
    finally:
        await tenv.close()


async def test_invalid_settings_keep_the_feature_not_ready(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, rules=0)
    try:
        status = tenv.app.readiness.statuses[k.VERIFICATION]
        assert not status.ready and any(p.kind == "settings" for p in status.problems)
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Module contract and operator text
# --------------------------------------------------------------------------------------------------


def test_module_contract() -> None:
    assert MODULE.name == "verification"
    assert MODULE.panel_prefixes == ("verification:",)
    assert MODULE.publish is not None and MODULE.preview is not None and MODULE.register is not None
    assert MODULE.is_enabled(verification_settings()) is True
    assert MODULE.is_enabled(verification_settings({"features": {"verification": False}})) is False
    names = [module.name for module in all_modules()]
    assert "verification" in names


def test_operator_text_keys_are_prefixed_and_used() -> None:
    assert all(key.startswith("verification.") for key in OPERATOR_TEXT)
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    used = set(re.findall(r'"(verification\.[a-z_.]+)"', source))
    dynamic = {f"verification.publish.{action}" for action in ("created", "updated", "unchanged", "recreated", "moved")}
    identifiers = {
        "verification.accepted",
        "verification.failed",
        "verification.dm_failed",
        "verification.confirmed",
        "verification.channel",
        "verification.rules",
    }
    operator_used = (used - FIXED_KEYS - identifiers) | dynamic
    assert operator_used <= set(OPERATOR_TEXT), operator_used - set(OPERATOR_TEXT)
    assert set(OPERATOR_TEXT) <= operator_used, set(OPERATOR_TEXT) - operator_used
