from __future__ import annotations

import tomllib
from pathlib import Path

import discord
import pytest

from shopbot.config.content import ContentStore, is_valid_emoji, validate_value
from shopbot.config.content_keys import ContentKey, Kind, Registry, ck
from shopbot.config.loader import load_config
from shopbot.config.readiness import build_registry, compute_readiness
from shopbot.config.settings import GUILD_PERMISSION_NAMES, SettingsError, parse_settings
from shopbot.env import EnvError, load_env
from shopbot.features import all_modules
from tests.helpers import ROOT, deep_merge, fixture_content, make_settings, shipped_settings_dict

VALID_TOKEN = "A" * 24 + "." + "B" * 6 + "." + "C" * 38


def test_env_requires_core_variables() -> None:
    with pytest.raises(EnvError) as exc:
        load_env({})
    problems = " ".join(exc.value.problems)
    assert "DISCORD_TOKEN" in problems and "GUILD_ID" in problems and "DB_PATH" in problems


def test_env_rejects_placeholder_token_and_bad_ids() -> None:
    with pytest.raises(EnvError) as exc:
        load_env({"DISCORD_TOKEN": "replace-with-your-bot-token", "GUILD_ID": "abc", "DB_PATH": "/data/bot.db"})
    assert any("DISCORD_TOKEN" in p for p in exc.value.problems)
    assert any("GUILD_ID" in p for p in exc.value.problems)


def test_env_valid_and_token_not_in_repr(tmp_path: Path) -> None:
    env = load_env({"DISCORD_TOKEN": VALID_TOKEN, "GUILD_ID": "123456789012345678", "DB_PATH": str(tmp_path / "x.db")})
    assert env.guild_id == 123456789012345678
    assert VALID_TOKEN not in repr(env)
    assert env.owner_id is None


def test_shipped_settings_parse_with_everything_disabled() -> None:
    loaded = load_config(ROOT / "config")
    assert loaded.settings.features.enabled() == ()
    assert loaded.settings.checkout.currency == ""  # business decision left unset
    assert loaded.settings.payments.provider == ""  # business decision left unset


def test_shipped_content_file_has_no_values() -> None:
    path = ROOT / "config" / "content.toml"
    if not path.exists():
        pytest.skip("content template not generated yet")
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    def walk(node: object) -> list[str]:
        if isinstance(node, dict):
            return [v for value in node.values() for v in walk(value)]
        return [node] if isinstance(node, str) and node != "" else []

    assert walk(data) == [], "config/content.toml must ship with every value empty"


def test_settings_unknown_key_is_rejected() -> None:
    data = deep_merge(shipped_settings_dict(), {"tickets": {"max_open_per_usr": 2}})
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    assert any("max_open_per_usr" in e and "unknown" in e for e in exc.value.errors)


def test_settings_type_errors_are_reported_with_paths() -> None:
    data = deep_merge(shipped_settings_dict(), {"bot": {"scheduler_poll_seconds": "5"}, "checkout": {"currency": "us"}})
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    joined = "\n".join(exc.value.errors)
    assert "bot.scheduler_poll_seconds: expected an integer" in joined
    assert "checkout.currency" in joined


def test_channel_pattern_requires_number() -> None:
    data = deep_merge(shipped_settings_dict(), {"tickets": {"channel_name_pattern": "{prefix}"}})
    with pytest.raises(SettingsError):
        parse_settings(data)


def test_reserved_ticket_category_id() -> None:
    data = shipped_settings_dict()
    data["tickets"]["categories"][0]["id"] = "checkout"
    with pytest.raises(SettingsError):
        parse_settings(data)


def test_permission_names_exist_in_discord_py() -> None:
    assert all(hasattr(discord.Permissions, name) for name in GUILD_PERMISSION_NAMES)


def _store(*keys: ContentKey, values: dict[str, str] | None = None) -> ContentStore:
    return ContentStore(Registry(keys), values or {})


def test_render_substitutes_only_declared_placeholders_and_preserves_text() -> None:
    key = ck("x.msg", Kind.DESCRIPTION, "f", "g", "doc", placeholders=("order_number",))
    raw = "Order {order_number} — keep {this} and {{braces}} ✨ exactly!  "
    store = _store(key, values={"x.msg": raw})
    assert store.render("x.msg", order_number=42) == "Order 42 — keep {this} and {{braces}} ✨ exactly!  "


def test_empty_values_are_not_configured_and_fallback_works() -> None:
    primary = ck("a.button", Kind.BUTTON_LABEL, "f", "g", "doc", fallback="common.button")
    common = ck("common.button", Kind.BUTTON_LABEL, "f", "g", "doc", required=False)
    store = _store(primary, common, values={"a.button": "", "common.button": "Go"})
    assert store.raw("a.button") is None
    assert store.get("a.button") == "Go"
    store.set_override("a.button", "Override")
    assert store.get("a.button") == "Override"


def test_unknown_key_access_raises() -> None:
    store = _store()
    with pytest.raises(KeyError):
        store.get("missing.key")


def test_validate_value_reports_without_truncating() -> None:
    key = ck("b.label", Kind.BUTTON_LABEL, "f", "g", "doc")
    problems = validate_value(key, "x" * 81)
    assert problems and "80" in problems[0]
    assert validate_value(key, "x" * 80) == []


def test_emoji_validation() -> None:
    assert is_valid_emoji("✅")
    assert is_valid_emoji("<:shop:123456789012345678>")
    assert not is_valid_emoji("ok")
    assert not is_valid_emoji("")


def test_registry_rejects_conflicting_definitions_and_prefixes() -> None:
    registry = Registry([ck("a.b", Kind.TITLE, "f", "g", "doc")])
    with pytest.raises(ValueError):
        registry.add(ck("a.b", Kind.DESCRIPTION, "f", "g", "doc"))
    registry.add(ck("a.b.c", Kind.TITLE, "f", "g", "doc"))
    assert registry.check_no_prefix_collisions() == ["a.b is a prefix of a.b.c"]


def test_all_registered_keys_have_no_prefix_collisions_with_all_features() -> None:
    settings = make_settings()
    registry = build_registry(settings, all_modules())
    assert registry.check_no_prefix_collisions() == []


def test_readiness_blocks_features_without_content() -> None:
    settings = make_settings()
    modules = all_modules()
    registry = build_registry(settings, modules)
    readiness = compute_readiness(settings, ContentStore(registry, {}), modules)
    assert not readiness.is_ready("core")
    for name in ("tickets", "catalog", "checkout"):
        assert readiness.is_enabled(name)
        assert not readiness.is_ready(name)


def test_readiness_with_fixture_content_makes_core_ready() -> None:
    settings = make_settings()
    modules = all_modules()
    registry = build_registry(settings, modules)
    store = ContentStore(registry, fixture_content(registry))
    readiness = compute_readiness(settings, store, modules)
    assert readiness.is_ready("core"), readiness.statuses["core"].problems


def test_disabled_settings_make_nothing_customer_facing_ready() -> None:
    settings = parse_settings(shipped_settings_dict())
    modules = all_modules()
    registry = build_registry(settings, modules)
    store = ContentStore(registry, fixture_content(registry))
    readiness = compute_readiness(settings, store, modules)
    assert not any(s.ready for s in readiness.statuses.values() if s.name not in ("server", "admin"))
