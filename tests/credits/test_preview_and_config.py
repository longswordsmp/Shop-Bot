"""Content keys, readiness, operator text, preview states and Discord limits."""

from __future__ import annotations

import re
from pathlib import Path

from shopbot.config.content_keys import Kind
from shopbot.features import core_keys as ck
from shopbot.features.credits import MODULE, content_keys
from shopbot.features.credits.content_keys import (
    BALANCE_DESCRIPTION,
    BALANCE_TITLE,
    BUTTON_BALANCE,
    CREDITS,
    NOTICE_DESCRIPTION,
    NOTICE_TITLE,
)
from shopbot.features.credits.text import OPERATOR_TEXT
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.helpers import TestEnv, make_settings, make_test_env
from tools.check_limits import unrouted_components

PACKAGE = Path(__file__).resolve().parents[2] / "shopbot" / "features" / "credits"


def test_content_keys_are_declared_with_kinds_and_placeholders() -> None:
    keys = {k.key: k for k in content_keys.content_keys(make_settings())}
    assert set(keys) == {BUTTON_BALANCE, BALANCE_TITLE, BALANCE_DESCRIPTION, NOTICE_TITLE, NOTICE_DESCRIPTION}
    assert keys[BUTTON_BALANCE].kind is Kind.BUTTON_LABEL and keys[BUTTON_BALANCE].required
    assert keys[BALANCE_DESCRIPTION].kind is Kind.DESCRIPTION and keys[BALANCE_DESCRIPTION].required
    assert keys[BALANCE_DESCRIPTION].placeholders == ("amount",)
    assert keys[BALANCE_TITLE].kind is Kind.TITLE and not keys[BALANCE_TITLE].required
    for key in (NOTICE_TITLE, NOTICE_DESCRIPTION):
        assert not keys[key].required
        assert keys[key].placeholders == ("amount", "balance")
    for definition in keys.values():
        assert definition.feature == CREDITS and definition.group and definition.doc


def test_shared_keys_and_settings_problems() -> None:
    assert MODULE.shared_keys(make_settings()) == [ck.MONEY_FORMAT]
    assert MODULE.settings_problems(make_settings()) == []
    problems = MODULE.settings_problems(make_settings({"checkout": {"currency": ""}}))
    assert len(problems) == 1 and "checkout.currency" in problems[0]
    assert MODULE.depends_on == ("checkout",)
    assert MODULE.is_enabled(make_settings()) is True
    assert MODULE.is_enabled(make_settings({"features": {"credits": False}})) is False


async def test_readiness_requires_checkout(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"features": {"checkout": False}}))
    try:
        status = tenv.app.readiness.statuses[CREDITS]
        assert not status.ready
        assert any(p.kind == "dependency" and "checkout" in p.message for p in status.problems)
    finally:
        await tenv.close()


async def test_optional_keys_do_not_block_readiness(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={BALANCE_TITLE: "", NOTICE_TITLE: "", NOTICE_DESCRIPTION: ""})
    try:
        assert tenv.app.ready(CREDITS)
    finally:
        await tenv.close()


def test_operator_text_keys_are_prefixed_and_used() -> None:
    assert all(key.startswith("credits.") for key in OPERATOR_TEXT)
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    used = set(re.findall(r'"(credits\.[a-z_.]+)"', source))
    dynamic = {f"credits.kind.{kind}" for kind in ("admin_adjust", "order_debit", "order_return", "refund_to_credit")}
    content_key_names = {k.key for k in content_keys.content_keys(make_settings())}
    identifiers = {"credits.adjust", "credits.adjusted"}  # audit action and notification kind, not text
    operator_used = (used - content_key_names - identifiers) | dynamic
    assert operator_used <= set(OPERATOR_TEXT), operator_used - set(OPERATOR_TEXT)
    assert set(OPERATOR_TEXT) <= operator_used, set(OPERATOR_TEXT) - operator_used


def test_every_content_key_used_in_code_is_declared() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    declared = {k.key for k in content_keys.content_keys(make_settings())}
    constants = set(re.findall(r'= "(credits\.(?:button|balance|adjusted_notice)\.[a-z_]+)"', source))
    assert constants == declared


async def test_every_preview_state_passes_discord_limits(tenv: TestEnv) -> None:
    states = MODULE.preview(tenv.app)  # type: ignore[misc]
    names = {s.name for s in states}
    assert names == {
        "balance",
        "balance_large",
        "adjusted_notice_added",
        "adjusted_notice_removed",
        "staff_history",
        "staff_history_empty",
    }
    for state in states:
        assert state.feature == CREDITS and state.note
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == []
        else:
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name


async def test_preview_uses_exact_configured_text(tenv: TestEnv) -> None:
    states = {s.name: s.spec for s in MODULE.preview(tenv.app)}  # type: ignore[misc]
    balance = states["balance"]
    assert isinstance(balance, MessageSpec)
    money = tenv.app.ui.money(12_345)
    assert money is not None
    assert balance.embeds[0].description == tenv.text(BALANCE_DESCRIPTION).replace("{amount}", money)
    assert balance.embeds[0].title == tenv.text(BALANCE_TITLE)


async def test_preview_skips_states_without_configured_text(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={NOTICE_TITLE: "", NOTICE_DESCRIPTION: ""})
    try:
        names = {s.name for s in MODULE.preview(tenv.app)}  # type: ignore[misc]
        assert "adjusted_notice_added" not in names and "adjusted_notice_removed" not in names
        assert "balance" in names
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "unconfigured", fill_content=False)
    try:
        names = {s.name for s in MODULE.preview(tenv.app)}  # type: ignore[misc]
        # Customer states need configured text; staff history pages use operator text only.
        assert names == {"staff_history", "staff_history_empty"}
    finally:
        await tenv.close()


async def test_preview_sample_data_is_synthetic(tenv: TestEnv) -> None:
    for state in MODULE.preview(tenv.app):  # type: ignore[misc]
        if state.name == "staff_history":
            assert isinstance(state.spec, MessageSpec)
            notes = [f.value for f in state.spec.embeds[0].fields if "sample note" in f.value]
            assert notes, "sample notes are bracketed placeholders"
