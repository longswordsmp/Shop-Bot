"""Preview states: complete, within Discord limits, fully routed, and built by the runtime view functions."""

from __future__ import annotations

from pathlib import Path

from shopbot.features.verification import content_keys as k
from shopbot.features.verification import preview as preview_module
from shopbot.features.verification import views
from shopbot.features.verification.preview import EXAMPLE_RULE_COUNT, preview
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import ButtonSpec, MessageSpec
from tests.helpers import TestEnv
from tests.verification.util import AGREE_ID, verification_env
from tools.check_limits import check_state, unrouted_components

ALL_STATES = [
    "welcome_panel",
    "confirmation",
    "already_verified",
    "dm_confirmation",
    "welcome_mention",
    "error_cannot_grant",
]


def _panel(states: list[preview_module.PreviewState]) -> MessageSpec:
    spec = next(s.spec for s in states if s.name == "welcome_panel")
    assert isinstance(spec, MessageSpec)
    return spec


def _assert_valid(tenv: TestEnv, states: list[preview_module.PreviewState]) -> None:
    for state in states:
        assert state.feature == "verification" and state.note
        assert check_state(state) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name


async def test_every_state_is_built_within_limits_and_routed(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"dm_confirmation": True}})
    try:
        states = preview(tenv.app)
        assert [s.name for s in states] == ALL_STATES
        _assert_valid(tenv, states)
        panel = _panel(states)
        assert len(panel.embeds) == 1 and (panel.embeds[0].description or "").count("\n**") >= 3  # one card
        (row,) = panel.rows
        (button,) = row
        assert isinstance(button, ButtonSpec) and button.custom_id == AGREE_ID == views.agree_id()
        assert button.label == tenv.text(k.BUTTON_LABEL)
    finally:
        await tenv.close()


async def test_preview_panel_matches_the_published_panel(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        live = views.build_panel(tenv.app.ui, tenv.app.settings.verification, preview_module.sample_links(tenv.app))
        assert _panel(preview(tenv.app)) == live
    finally:
        await tenv.close()


async def test_twenty_five_rules_preview_within_limits(tmp_path: Path) -> None:
    short = {k.rule_value_key(n): f"[rule {n}]" for n in range(1, 26)}
    tenv = await verification_env(tmp_path, rules=25, content=short)
    try:
        states = preview(tenv.app)
        _assert_valid(tenv, states)
        description = _panel(states).embeds[0].description or ""
        assert all(f"** [rule {n}]" in description for n in range(1, 26))
    finally:
        await tenv.close()


async def test_unset_rule_count_previews_an_example_structure(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, rules=0)
    try:
        states = preview(tenv.app)
        panel = _panel(states)
        description = panel.embeds[0].description or ""
        assert f"**[{k.rule_name_key(1)}]**" in description
        assert f"**[{k.rule_name_key(EXAMPLE_RULE_COUNT)}]** [{k.rule_value_key(EXAMPLE_RULE_COUNT)}]" in description
        assert f"[{k.rule_name_key(EXAMPLE_RULE_COUNT + 1)}]" not in description
        note = next(s.note for s in states if s.name == "welcome_panel")
        assert "verification.rules is not set" in note
        _assert_valid(tenv, states)
    finally:
        await tenv.close()


async def test_unconfigured_text_shows_markers_and_skips_the_replies(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, rules=2)
    try:
        tenv.app.content.replace(file_values={})
        tenv.app.recompute_readiness()
        states = preview(tenv.app)
        assert [s.name for s in states] == ["welcome_panel"]
        panel = _panel(states)
        assert panel.embeds[0].title == f"[{k.WELCOME_TITLE}]"
        assert f"** [{k.rule_value_key(2)}]" in (panel.embeds[0].description or "")
        (row,) = panel.rows
        assert isinstance(row[0], ButtonSpec) and row[0].label == f"[{k.BUTTON_LABEL}]"
        assert validate_message(panel) == []
    finally:
        await tenv.close()


async def test_preview_uses_synthetic_data_only(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, content={k.CONFIRMED_DESCRIPTION: "{user_name} {user_mention}"})
    try:
        confirmation = next(s.spec for s in preview(tenv.app) if s.name == "confirmation")
        assert isinstance(confirmation, MessageSpec)
        description = confirmation.embeds[0].description or ""
        assert preview_module.SAMPLE_MEMBER_NAME in description
        assert f"<@{preview_module.SAMPLE_MEMBER_ID}>" in description
    finally:
        await tenv.close()
