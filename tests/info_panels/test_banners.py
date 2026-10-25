"""Banner pictures on info panels: every panel shows the neutral banner; edits keep the uploaded picture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.features.info_panels import content_keys as keys
from shopbot.features.info_panels.preview import preview
from shopbot.features.info_panels.service import publish
from shopbot.ui.banners import EMPTY_LIBRARY
from shopbot.ui.spec import MessageSpec
from tests.fakes import FakeMessage, FakeTextChannel
from tests.helpers import TestEnv, banner_names, base_settings_dict, make_banner_env, provision_channels

RULES = {"key": "rules", "channel": "rules", "fields": 2, "inline_fields": True, "links": ["site", "docs"]}
FAQ = {"key": "faq", "channel": "faq", "fields": 0, "links": []}


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    server = base_settings_dict()["server"]
    server["channels"] += [
        {"key": "rules", "category": "support", "access": "inherit"},
        {"key": "faq", "category": "support", "access": "inherit"},
    ]
    tenv = await make_banner_env(tmp_path, {"server": server, "info_panels": [RULES, FAQ]})
    await provision_channels(tenv)
    try:
        yield tenv
    finally:
        await tenv.close()


def messages(tenv: TestEnv, key: str) -> list[FakeMessage]:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return list(found.messages)


def general_banner(tenv: TestEnv) -> str:
    banner = tenv.app.ui.banner("general-response")
    assert banner is not None
    return banner.filename


async def test_every_info_panel_shows_the_general_banner(benv: TestEnv) -> None:
    await publish(benv.app)
    for key in ("rules", "faq"):
        (panel,) = messages(benv, key)
        assert banner_names(panel) == [general_banner(benv)], key


async def test_text_change_keeps_the_banner_and_banners_off_remove_it(benv: TestEnv) -> None:
    await publish(benv.app)
    (panel,) = messages(benv, "rules")
    benv.app.content.set_override(keys.title_key("rules"), "[changed title]")
    await publish(benv.app)
    assert messages(benv, "rules") == [panel] and len(panel.edits) == 1
    assert "attachments" not in panel.edits[-1]
    assert banner_names(panel) == [general_banner(benv)]
    benv.app.banners = EMPTY_LIBRARY
    await publish(benv.app)
    assert panel.edits[-1]["attachments"] == [] and banner_names(panel) == []


async def test_preview_shows_the_banner(benv: TestEnv) -> None:
    for state in preview(benv.app):
        assert isinstance(state.spec, MessageSpec)
        assert state.spec.banner == benv.app.ui.banner("general-response"), state.name
