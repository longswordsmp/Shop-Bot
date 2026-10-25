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
from shopbot.ui.spec import ButtonSpec, MessageSpec, ModalSpec
from tests.helpers import TestEnv, make_settings, make_test_env
from tools.check_limits import unrouted_components

OPERATOR_STATES = {
    "product_add_form",
    "product_edit_form",
    "product_details",
    "product_list",
    "product_remove_confirm",
    "reply_image_uploaded",
    "reply_image_rejected",
    "reply_removed",
    "reply_remove_reserved",
}
EXPECTED_STATES = {
    "listing_message_1",
    "listing_message_2",
    "listing_empty",
    "browse_first",
    "browse_middle",
    "browse_sold_out",
    "browse_last",
    "browse_empty",
    *OPERATOR_STATES,
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
    head = by_name["listing_message_1"]
    assert isinstance(head, MessageSpec)
    (header,) = head.embeds
    assert (header.title, header.description) == (tenv.text(keys.LISTING_TITLE), tenv.text(keys.LISTING_DESCRIPTION))
    assert head.rows[0][0].label == tenv.text(keys.BUTTON_BROWSE)  # type: ignore[union-attr]
    listing = by_name["listing_message_2"]
    assert isinstance(listing, MessageSpec)
    cards = listing.embeds
    assert [c.title for c in cards] == [escape_markdown(f"[sample product {i}]") for i in (1, 2, 3, 4)]
    availability = [c.fields[1].value for c in cards]
    assert availability == [
        tenv.text(keys.AVAILABILITY_IN_STOCK).replace("{count}", "10"),
        tenv.text(keys.AVAILABILITY_UNLIMITED),
        tenv.text(keys.AVAILABILITY_OUT_OF_STOCK),
        tenv.text(keys.AVAILABILITY_IN_STOCK).replace("{count}", "1"),
    ]
    colors = tenv.app.settings.branding.colors
    assert [c.color for c in cards] == [colors["success"], colors["success"], colors["danger"], colors["success"]]
    disabled = [b.disabled for row in listing.rows for b in row if isinstance(b, ButtonSpec)]
    assert disabled == [False, False, True, False]
    # the uploaded sample image is attached and shown with attachment://
    assert listing.files is not None and len(listing.files) == 1 and listing.files[0].data
    assert cards[0].image_url == f"attachment://{listing.files[0].filename}"
    assert all(c.image_url is None for c in cards[1:])
    browse = by_name["browse_first"]
    assert isinstance(browse, MessageSpec) and browse.files and browse.embeds[0].image_url.startswith("attachment://")  # type: ignore[union-attr]
    empty = by_name["listing_empty"]
    assert isinstance(empty, MessageSpec) and empty.embeds[1].description == tenv.text(keys.LISTING_EMPTY)


async def test_preview_skips_listing_states_without_listing_channel(tmp_path: Path) -> None:
    tenv = await make_test_env(
        tmp_path, settings=make_settings({"catalog": {"listing_channel": "", "browse_enabled": False}})
    )
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert names == OPERATOR_STATES
    finally:
        await tenv.close()


async def test_preview_without_content_builds_only_operator_states(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert names == OPERATOR_STATES
    finally:
        await tenv.close()


async def test_low_stock_preview_and_key(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"low_stock_threshold": 3}}))
    try:
        assert MODULE.preview is not None
        listing = {s.name: s.spec for s in MODULE.preview(tenv.app)}["listing_message_2"]
        assert isinstance(listing, MessageSpec)
        low = listing.embeds[3]  # [sample product 4] has 1 unit left
        assert low.fields[1].value == tenv.text(keys.AVAILABILITY_LOW_STOCK).replace("{count}", "1")
        assert low.color == tenv.app.settings.branding.colors["warning"]
        definition = tenv.app.content.registry.get(keys.AVAILABILITY_LOW_STOCK)
        assert definition is not None and not definition.required and definition.fallback == keys.AVAILABILITY_IN_STOCK
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
        keys.BUTTON_BUY,
        keys.BUTTON_BUY_SHORT,
        keys.BUTTON_BUY_EMOJI,
    }
    assert all(k.feature == "catalog" and k.doc and k.group for k in defined.values())
    assert defined[keys.FIELD_PRICE].kind is Kind.FIELD_NAME
    assert defined[keys.AVAILABILITY_IN_STOCK].kind is Kind.FIELD_VALUE
    assert defined[keys.AVAILABILITY_IN_STOCK].placeholders == ("count",)
    assert defined[keys.LISTING_EMPTY].kind is Kind.DESCRIPTION
    assert defined[keys.BUTTON_BROWSE].kind is Kind.BUTTON_LABEL
    required = {k for k, d in defined.items() if d.required}
    assert required == set(defined) - {keys.FIELD_MAX_PER_ORDER, keys.BUTTON_BUY_SHORT, keys.BUTTON_BUY_EMOJI}
    assert defined[keys.BUTTON_BUY].placeholders == ("name",)
    assert defined[keys.BUTTON_BUY_EMOJI].kind is Kind.EMOJI
    assert keys.AVAILABILITY_LOW_STOCK not in defined  # only with catalog.low_stock_threshold > 0
    no_checkout = make_settings({"features": {"checkout": False}})
    assert not next(k for k in keys.content_keys(no_checkout) if k.key == keys.BUTTON_BUY).required

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
