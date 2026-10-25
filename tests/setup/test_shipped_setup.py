"""Acceptance: with the shipped settings, text and banners, /setup builds the server and posts every panel."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.loader import load_config
from tests.helpers import ROOT, TestEnv, banner_names, make_test_env
from tests.setup.util import setup

PANEL_CHANNELS = {  # channel name -> banner id its first bot message shows
    "welcome": "welcome",
    "how-to-buy": "how-it-works",
    "faq": "faq",
    "shipping": "shipping",
    "catalog": "catalog",
    "new-arrivals": "new-arrivals",
    "purchase": "purchase-panel",
}


@pytest.fixture
async def shipped(tmp_path: Path) -> Any:
    loaded = load_config(ROOT / "config")
    shutil.copytree(ROOT / "config" / "banners", tmp_path / "config" / "banners")
    tenv = await make_test_env(tmp_path, settings=loaded.settings, content=loaded.content_values, fill_content=False)
    try:
        yield tenv
    finally:
        await tenv.close()


def bot_messages(tenv: TestEnv, name: str) -> list[Any]:
    channel = next((c for c in tenv.guild.text_channels if c.name == name), None)
    assert channel is not None, f"#{name} was not created"
    return [m for m in channel.messages if m.author == tenv.guild.me]


async def test_every_enabled_feature_is_ready_with_the_shipped_files(shipped: TestEnv) -> None:
    not_ready = {s.name: s.problems for s in shipped.app.readiness.statuses.values() if s.enabled and not s.ready}
    assert not_ready == {}
    assert shipped.app.banners.problems == () and len(shipped.app.banners.files) == 31


async def test_setup_posts_every_panel_with_its_banner(shipped: TestEnv) -> None:
    report = await setup(shipped)
    assert report.counts.get("failed", 0) == 0, report.lines
    for name, banner_id in PANEL_CHANNELS.items():
        messages = bot_messages(shipped, name)
        assert messages, f"#{name} has no panel"
        first = messages[0]
        assert first.embeds and first.embeds[0].title, f"#{name} panel has no text"
        names = banner_names(first)
        assert len(names) == 1 and names[0].startswith(f"banner-{banner_id}-"), (name, names)
        for later in messages[1:]:
            assert banner_names(later) == [], f"#{name}: only the first message shows the banner"
    # Running it again changes nothing and posts nothing twice.
    counts = {name: len(bot_messages(shipped, name)) for name in PANEL_CHANNELS}
    await setup(shipped)
    assert {name: len(bot_messages(shipped, name)) for name in PANEL_CHANNELS} == counts
