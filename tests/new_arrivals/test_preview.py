"""Preview states of the new-arrivals feature: complete, within Discord limits, fully routed, synthetic data."""

from __future__ import annotations

from pathlib import Path

from shopbot.features.catalog.views import escape
from shopbot.features.new_arrivals import MODULE
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals.preview import SAMPLE_ROLE_ID
from shopbot.ui.discord_render import send_kwargs
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec
from tests.new_arrivals.util import na_env
from tools.check_limits import run_checks, unrouted_components
from tools.offline import ROOT

ALL_STATES = {
    "panel",
    "subscribed",
    "unsubscribed",
    "update_new",
    "update_new_mention",
    "update_restock",
    "update_restock_mention",
}


async def test_every_state_is_valid_routed_and_synthetic(tmp_path: Path) -> None:
    env = await na_env(tmp_path)
    try:
        assert MODULE.preview is not None
        states = MODULE.preview(env.app)
        assert {s.name for s in states} == ALL_STATES
        for state in states:
            assert state.feature == "new_arrivals" and state.note
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name
            assert unrouted_components(state, env.app.router) == [], state.name
            send_kwargs(state.spec)  # renders exactly as it would be sent
        by_name = {s.name: s.spec for s in states}
        panel = by_name["panel"]
        assert isinstance(panel, MessageSpec)
        assert panel.rows[0][0].custom_id == "sb:na:toggle"  # type: ignore[union-attr]
        for name in ("update_new", "update_restock"):
            spec = by_name[name]
            assert isinstance(spec, MessageSpec) and spec.mention_role_ids == ()
            assert "[sample product" in (spec.embeds[0].title or "")
        for name in ("update_new_mention", "update_restock_mention"):
            spec = by_name[name]
            assert isinstance(spec, MessageSpec) and spec.mention_role_ids == (SAMPLE_ROLE_ID,)
        new = by_name["update_new"]
        assert isinstance(new, MessageSpec)
        assert new.embeds[0].title == env.text(keys.NEW_PRODUCT_TITLE).replace("{name}", escape("[sample product 1]"))
    finally:
        await env.close()


async def test_states_follow_the_settings(tmp_path: Path) -> None:
    env = await na_env(tmp_path, {"new_arrivals": {"announce_restocks": False, "mention_role": False}})
    try:
        assert MODULE.preview is not None
        assert {s.name for s in MODULE.preview(env.app)} == {"panel", "subscribed", "unsubscribed", "update_new"}
    finally:
        await env.close()


async def test_states_without_text_are_skipped(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.PANEL_TITLE: "", keys.RESTOCK_TITLE: ""})
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(env.app)}
        assert names == ALL_STATES - {"panel", "update_restock", "update_restock_mention"}
    finally:
        await env.close()


async def test_offline_limits_checker_builds_every_state() -> None:
    report = await run_checks(ROOT / "config", all_features=True, fixtures=True)
    assert not report.failed, (report.violations, report.unrouted, report.errors)
    assert "new_arrivals" not in report.incomplete
