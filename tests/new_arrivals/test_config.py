"""Settings parsing, settings checks, content keys and readiness of the new-arrivals feature."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content_keys import Kind
from shopbot.config.loader import load_config
from shopbot.config.settings import NewArrivalsSettings, SettingsError, parse_settings
from shopbot.features import all_modules
from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.new_arrivals import MODULE
from shopbot.features.new_arrivals import content_keys as keys
from tests.helpers import ROOT, base_settings_dict, deep_merge, make_test_env
from tests.new_arrivals.util import NA_SETTINGS, na_settings


def test_defaults_and_parsing() -> None:
    assert NewArrivalsSettings() == NewArrivalsSettings(
        channel="", role="", announce_new_products=True, announce_restocks=True, mention_role=True
    )
    settings = na_settings(
        {"new_arrivals": {"announce_new_products": False, "announce_restocks": True, "mention_role": False}}
    )
    na = settings.new_arrivals
    assert (na.channel, na.role) == ("new_arrivals", "restock_alerts")
    assert (na.announce_new_products, na.announce_restocks, na.mention_role) == (False, True, False)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ({"role": "Restock Alerts"}, "new_arrivals.role: must match"),
        ({"channel": 5}, "new_arrivals.channel: expected a string"),
        ({"mention_role": "yes"}, "new_arrivals.mention_role: expected true or false"),
        ({"announce_restocks": 1}, "new_arrivals.announce_restocks: expected true or false"),
        ({"annonce_restocks": True}, "new_arrivals.annonce_restocks: unknown setting"),
    ],
)
def test_invalid_settings_are_fatal(section: dict[str, Any], message: str) -> None:
    data = deep_merge(base_settings_dict(), {"new_arrivals": section})
    with pytest.raises(SettingsError) as exc:
        parse_settings(data)
    assert any(message in error for error in exc.value.errors), exc.value.errors


def test_shipped_settings_configure_the_restock_alerts_role() -> None:
    settings = load_config(ROOT / "config").settings
    na = settings.new_arrivals
    assert (na.channel, na.role) == ("new_arrivals", "restock_alerts")
    assert na.announce_new_products and na.announce_restocks and na.mention_role
    assert keys.settings_problems(settings) == []


def test_valid_settings_have_no_problems() -> None:
    assert keys.settings_problems(na_settings()) == []


def _problems(overrides: dict[str, Any]) -> list[str]:
    return keys.settings_problems(na_settings(overrides))


def test_channel_and_role_are_required_and_must_exist() -> None:
    problems = _problems({"new_arrivals": {"channel": "", "role": ""}})
    assert any(p.startswith("new_arrivals.channel: required") for p in problems)
    assert any(p.startswith("new_arrivals.role: required") for p in problems)
    problems = _problems({"new_arrivals": {"channel": "nowhere", "role": "nobody"}})
    assert "new_arrivals.channel: channel 'nowhere' is not defined in server.channels" in problems
    assert "role 'nobody' is not defined in server.roles" in problems


def test_at_least_one_kind_of_update_must_be_on() -> None:
    problems = _problems({"new_arrivals": {"announce_new_products": False, "announce_restocks": False}})
    assert any("both false" in p for p in problems)
    assert _problems({"new_arrivals": {"announce_new_products": False}}) == []


def test_channel_must_be_visible_to_members() -> None:
    problems = _problems({"new_arrivals": {"channel": "staff_alerts"}})
    assert any("'staff'-only" in p for p in problems), problems


@pytest.mark.parametrize("role", ["staff"])
def test_role_must_not_be_a_privileged_role(role: str) -> None:
    problems = _problems({"new_arrivals": {"role": role}})
    assert any("must be a separate role" in p for p in problems), problems


def test_role_used_as_a_manager_role_is_refused() -> None:
    problems = _problems({"catalog": {"manager_roles": ["restock_alerts"]}})
    assert any("must be a separate role" in p for p in problems), problems
    problems = _problems({"tickets": {"categories": [{"id": "general", "staff_roles": ["restock_alerts"]}]}})
    assert any("must be a separate role" in p for p in problems), problems


def test_role_must_not_grant_permissions() -> None:
    data = deep_merge(NA_SETTINGS, {})
    roles = data["server"]["roles"]
    roles[-1] = {"key": "restock_alerts", "mentionable": True, "permissions": ["manage_messages"]}
    problems = keys.settings_problems(na_settings({"server": data["server"]}))
    assert any("must not grant any permission" in p for p in problems), problems


def test_mentions_require_a_mentionable_role() -> None:
    data = deep_merge(NA_SETTINGS, {})
    data["server"]["roles"][-1] = {"key": "restock_alerts", "mentionable": False}
    problems = keys.settings_problems(na_settings({"server": data["server"]}))
    assert any("mentionable = true" in p for p in problems), problems
    quiet = na_settings({"server": data["server"], "new_arrivals": {"mention_role": False}})
    assert keys.settings_problems(quiet) == []


def test_content_keys_definitions() -> None:
    defined = {k.key: k for k in keys.content_keys(na_settings())}
    assert set(defined) == {
        keys.PANEL_TITLE,
        keys.PANEL_DESCRIPTION,
        keys.BUTTON_TOGGLE,
        keys.BUTTON_TOGGLE_EMOJI,
        keys.SUBSCRIBED_TITLE,
        keys.SUBSCRIBED_DESCRIPTION,
        keys.UNSUBSCRIBED_TITLE,
        keys.UNSUBSCRIBED_DESCRIPTION,
        keys.NEW_PRODUCT_TITLE,
        keys.NEW_PRODUCT_FOOTER,
        keys.RESTOCK_TITLE,
        keys.RESTOCK_FOOTER,
        keys.BUTTON_VIEW_CATALOG,
    }
    assert all(k.feature == "new_arrivals" and k.doc and k.group for k in defined.values())
    required = {k for k, d in defined.items() if d.required}
    assert required == {
        keys.PANEL_TITLE,
        keys.PANEL_DESCRIPTION,
        keys.BUTTON_TOGGLE,
        keys.SUBSCRIBED_DESCRIPTION,
        keys.UNSUBSCRIBED_DESCRIPTION,
        keys.NEW_PRODUCT_TITLE,
        keys.RESTOCK_TITLE,
    }
    assert defined[keys.BUTTON_TOGGLE].kind is Kind.BUTTON_LABEL
    assert defined[keys.BUTTON_TOGGLE_EMOJI].kind is Kind.EMOJI
    assert defined[keys.NEW_PRODUCT_TITLE].placeholders == ("name",)
    assert defined[keys.RESTOCK_TITLE].placeholders == ("name",)
    assert defined[keys.NEW_PRODUCT_FOOTER].kind is Kind.FOOTER
    # Descriptions offer the role mention and every channel link; titles (which Discord does not render mentions in)
    # offer none.
    description = defined[keys.PANEL_DESCRIPTION].placeholders
    assert description[0] == "role" and "channel_new_arrivals" in description and "channel_catalog" in description
    assert defined[keys.SUBSCRIBED_DESCRIPTION].placeholders == description
    assert defined[keys.SUBSCRIBED_TITLE].placeholders == ()
    assert defined[keys.UNSUBSCRIBED_TITLE].placeholders == ()


def test_update_titles_are_required_only_for_enabled_updates() -> None:
    settings = na_settings({"new_arrivals": {"announce_restocks": False}})
    required = {k.key for k in keys.content_keys(settings) if k.required}
    assert keys.NEW_PRODUCT_TITLE in required and keys.RESTOCK_TITLE not in required
    settings = na_settings({"new_arrivals": {"announce_new_products": False}})
    required = {k.key for k in keys.content_keys(settings) if k.required}
    assert keys.RESTOCK_TITLE in required and keys.NEW_PRODUCT_TITLE not in required


def test_shared_keys_are_the_catalog_price_and_availability_texts() -> None:
    assert set(keys.shared_keys(na_settings())) == {
        core.MONEY_FORMAT,
        catalog_keys.FIELD_PRICE,
        catalog_keys.FIELD_AVAILABILITY,
        catalog_keys.AVAILABILITY_IN_STOCK,
        catalog_keys.AVAILABILITY_UNLIMITED,
        catalog_keys.AVAILABILITY_OUT_OF_STOCK,
    }


def test_module_contract() -> None:
    names = [m.name for m in all_modules()]
    assert names.index("new_arrivals") > names.index("catalog")
    assert MODULE.depends_on == ("catalog",)
    assert MODULE.panel_prefixes == ("new_arrivals:",)
    assert MODULE.publish is not None and MODULE.preview is not None and MODULE.register is not None
    assert MODULE.is_enabled(na_settings())
    assert not MODULE.is_enabled(na_settings({"features": {"new_arrivals": False}}))


async def test_ready_only_when_enabled_configured_and_catalog_ready(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path / "ok", settings=na_settings())
    try:
        assert tenv.app.ready("new_arrivals")
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "missing", settings=na_settings(), content={keys.PANEL_TITLE: ""})
    try:
        status = tenv.app.readiness.statuses["new_arrivals"]
        assert not status.ready
        assert any(p.key == keys.PANEL_TITLE for p in status.problems)
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "nocatalog", settings=na_settings({"features": {"catalog": False}}))
    try:
        status = tenv.app.readiness.statuses["new_arrivals"]
        assert not status.ready and any(p.kind == "dependency" for p in status.problems)
    finally:
        await tenv.close()
    shared = await make_test_env(tmp_path / "shared", settings=na_settings(), content={core.MONEY_FORMAT: ""})
    try:
        assert not shared.app.ready("new_arrivals")
    finally:
        await shared.close()
