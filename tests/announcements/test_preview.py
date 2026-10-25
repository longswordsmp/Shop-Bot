"""Preview states of /announce: complete, within Discord limits, every component routed, synthetic data only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content import MissingContent
from shopbot.features.announcements import MODULE
from shopbot.features.announcements.content_keys import field_name_key
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.announcements.util import announce_env
from tools.check_limits import check_state, run_checks, unrouted_components
from tools.offline import ROOT, build_offline_app

THREE_FIELDS: dict[str, Any] = {
    "announcements": {
        "mention_roles": ["restock_alerts"],
        "fields": [
            {"id": "when", "style": "short", "required": True, "max_length": 200},
            {"id": "details", "style": "paragraph", "max_length": 1024},
            {"id": "where", "max_length": 1},
        ],
    }
}


def _names(states: list[Any]) -> set[str]:
    return {s.name for s in states}


async def test_preview_states_in_the_test_env(tmp_path: Path) -> None:
    env = await announce_env(tmp_path)
    try:
        assert MODULE.preview is not None
        states = MODULE.preview(env.app)
        assert {
            "form",
            "form_retry",
            "announcement",
            "announcement_plain",
            "published",
            "already_published",
            "error_input",
            "error_channel",
            "error_discord",
            "error_options",
            "notice_busy",
            "notice_expired",
        } <= _names(states)
        assert "form_example_fields" not in _names(states)  # fields are configured
        for state in states:
            assert state.feature == "announcements" and state.note
            assert check_state(state) == [], state.name
            assert unrouted_components(state, env.app.router) == [], state.name
        announcement = next(s for s in states if s.name == "announcement").spec
        assert isinstance(announcement, MessageSpec)
        embed = announcement.embeds[0]
        assert embed.title == "[sample title]" and embed.image_url == "attachment://announcement.png"
        assert [f.name for f in embed.fields] == [env.text(field_name_key("when")), env.text(field_name_key("details"))]
        assert announcement.mention_role_ids
        retry = next(s for s in states if s.name == "form_retry").spec
        assert isinstance(retry, ModalSpec) and retry.inputs[0].default == "[sample title]"
    finally:
        await env.close()


@pytest.mark.parametrize("extra", [None, THREE_FIELDS])
async def test_preview_with_the_shipped_configuration(extra: dict[str, Any] | None) -> None:
    app = await build_offline_app(ROOT / "config", content_mode="fixtures", all_features=True, extra_settings=extra)
    try:
        assert MODULE.preview is not None
        states = MODULE.preview(app)
        names = _names(states)
        if extra is None:
            assert {"form_example_fields", "announcement_example_fields"} <= names
        else:
            assert "form_example_fields" not in names
            form = next(s for s in states if s.name == "form").spec
            assert isinstance(form, ModalSpec) and len(form.inputs) == 5
        for state in states:
            assert check_state(state) == [], state.name
            assert unrouted_components(state, app.router) == [], state.name
    finally:
        await app.db.close()


async def test_preview_skips_the_announcement_until_field_headings_are_configured() -> None:
    app = await build_offline_app(
        ROOT / "config", content_mode="actual", all_features=True, extra_settings=THREE_FIELDS
    )
    try:
        assert MODULE.preview is not None
        names = _names(MODULE.preview(app))  # must not raise MissingContent
        assert "announcement" not in names
        assert {"form", "announcement_plain", "published"} <= names
    except MissingContent as exc:  # pragma: no cover - would be a bug
        pytest.fail(f"preview raised MissingContent({exc.key})")
    finally:
        await app.db.close()


async def test_limits_checker_passes_in_fixture_mode() -> None:
    report = await run_checks(ROOT / "config", all_features=True, fixtures=True)
    assert not report.failed, (report.violations, report.unrouted, report.errors)
    assert "announcements" not in report.incomplete
