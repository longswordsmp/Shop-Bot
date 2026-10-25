"""Preview states, content keys, shared keys, settings checks, and readiness of the catalog."""

from __future__ import annotations

import re
from pathlib import Path

from discord.utils import escape_markdown

from shopbot.config.content_keys import Kind
from shopbot.features import core_keys as ck
from shopbot.features.catalog import MODULE
from shopbot.features.catalog import content_keys as keys
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.helpers import TestEnv, make_settings, make_test_env
from tools.check_limits import unrouted_components

EXPECTED_STATES = {
    "listing_message_1",
    "listing_empty",
    "browse_first",
    "browse_middle",
    "browse_last",
    "browse_empty",
    "product_add_form",
    "product_edit_form",
    "product_details",
    "product_list",
}


async def test_every_preview_state_is_valid_routed_and_synthetic(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    assert {s.name for s in states} == EXPECTED_STATES
    for state in states:
        assert state.feature == "catalog" and state.note
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name

    by_name = {s.name: s.spec for s in states}
    listing = by_name["listing_message_1"]
    assert isinstance(listing, MessageSpec)
    header, *cards = listing.embeds
    assert (header.title, header.description) == (tenv.text(keys.LISTING_TITLE), tenv.text(keys.LISTING_DESCRIPTION))
    assert [c.title for c in cards] == [escape_markdown(f"[sample product {i}]") for i in (1, 2, 3)]
    availability = [c.fields[1].value for c in cards]
    assert availability == [
        tenv.text(keys.AVAILABILITY_IN_STOCK).replace("{count}", "10"),
        tenv.text(keys.AVAILABILITY_UNLIMITED),
        tenv.text(keys.AVAILABILITY_OUT_OF_STOCK),
    ]
    assert listing.rows[0][0].label == tenv.text(keys.BUTTON_BROWSE)  # type: ignore[union-attr]
    empty = by_name["listing_empty"]
    assert isinstance(empty, MessageSpec) and empty.embeds[1].description == tenv.text(keys.LISTING_EMPTY)


async def test_preview_skips_listing_states_without_listing_channel(tmp_path: Path) -> None:
    tenv = await make_test_env(
        tmp_path, settings=make_settings({"catalog": {"listing_channel": "", "browse_enabled": False}})
    )
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert names == {"product_add_form", "product_edit_form", "product_details", "product_list"}
    finally:
        await tenv.close()


async def test_preview_without_content_builds_only_operator_states(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert names == {"product_add_form", "product_edit_form", "product_details", "product_list"}
    finally:
        await tenv.close()


def test_content_keys_definitions() -> None:
    settings = make_settings()
    defined = {k.key: k for k in keys.content_keys(settings)}
    assert set(defined) == {
        keys.LISTING_TITLE,
        keys.LISTING_DESCRIPTION,
        keys.LISTING_EMPTY,
        keys.FIELD_PRICE,
        keys.FIELD_AVAILABILITY,
        keys.FIELD_MAX_PER_ORDER,
        keys.AVAILABILITY_IN_STOCK,
        keys.AVAILABILITY_UNLIMITED,
        keys.AVAILABILITY_OUT_OF_STOCK,
        keys.BUTTON_BROWSE,
    }
    assert all(k.feature == "catalog" and k.doc and k.group for k in defined.values())
    assert defined[keys.FIELD_PRICE].kind is Kind.FIELD_NAME
    assert defined[keys.AVAILABILITY_IN_STOCK].kind is Kind.FIELD_VALUE
    assert defined[keys.AVAILABILITY_IN_STOCK].placeholders == ("count",)
    assert defined[keys.LISTING_EMPTY].kind is Kind.DESCRIPTION
    assert defined[keys.BUTTON_BROWSE].kind is Kind.BUTTON_LABEL
    required = {k for k, d in defined.items() if d.required}
    assert required == set(defined) - {keys.FIELD_MAX_PER_ORDER}

    no_listing = make_settings({"catalog": {"listing_channel": "", "browse_enabled": False}})
    required = {k.key for k in keys.content_keys(no_listing) if k.required}
    assert keys.LISTING_TITLE not in required and keys.BUTTON_BROWSE not in required
    assert keys.LISTING_EMPTY not in required and keys.FIELD_PRICE in required

    assert keys.shared_keys(settings) == [ck.MONEY_FORMAT, ck.BUTTON_PREV, ck.BUTTON_NEXT]
    assert keys.shared_keys(make_settings({"catalog": {"browse_enabled": False}})) == [ck.MONEY_FORMAT]


def test_settings_problems() -> None:
    assert keys.settings_problems(make_settings()) == []
    no_currency = make_settings({"checkout": {"currency": ""}})
    assert keys.settings_problems(no_currency) == ["settings.checkout.currency is required to show prices"]
    bad = make_settings(
        {"catalog": {"listing_channel": "nowhere", "manager_roles": ["ghost"], "image_hosts": ["https://x.com/"]}}
    )
    problems = keys.settings_problems(bad)
    assert "role 'ghost' is not defined in server.roles" in problems
    assert "catalog.listing_channel: channel 'nowhere' is not defined in server.channels" in problems
    assert any(p.startswith("catalog.image_hosts: 'https://x.com/'") for p in problems)
    assert keys.settings_problems(make_settings({"catalog": {"image_hosts": ["cdn.example.com"]}})) == []


async def test_readiness(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    try:
        assert tenv.app.ready("catalog")
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "b", settings=make_settings({"checkout": {"currency": ""}}))
    try:
        assert not tenv.app.ready("catalog")
        problems = [p.message for p in tenv.app.readiness.statuses["catalog"].problems]
        assert "settings.checkout.currency is required to show prices" in problems
    finally:
        await tenv.close()
    values_missing_shared = {ck.BUTTON_NEXT: ""}
    tenv = await make_test_env(tmp_path / "c", content=values_missing_shared)
    try:
        assert not tenv.app.ready("catalog")
    finally:
        await tenv.close()


def test_operator_text_keys_are_prefixed_and_used_keys_exist() -> None:
    assert all(key.startswith("catalog.") for key in MODULE.operator_text)
    source = "\n".join(
        (Path(__file__).resolve().parents[2] / "shopbot" / "features" / "catalog" / name).read_text()
        for name in ("service.py", "views.py", "commands.py", "handlers.py", "tasks.py", "preview.py")
    )
    used = set(re.findall(r'op\(\s*"(catalog\.[a-z0-9_.]+)"', source))
    used |= set(re.findall(r'"(catalog\.(?:list|view|cmd|form|invalid|refresh)\.[a-z0-9_]+)"', source))
    missing = sorted(key for key in used if key not in MODULE.operator_text)
    assert missing == []
