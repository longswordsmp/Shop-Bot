"""Acceptance: with the shipped settings, text and banners, /setup builds the server and posts every panel."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.loader import load_config
from shopbot.features.setup import reset
from tests.helpers import OWNER_ID, ROOT, TestEnv, banner_names, make_test_env
from tests.setup.util import confirm, phrase, preview, setup

PANEL_CHANNELS = {  # channel name (as /setup creates it) -> banner id its first bot message shows
    "📜・welcome": "welcome",
    "🧭・how-to-order": "how-it-works",
    "❔・faq": "faq",
    "🚚・shipping": "shipping",
    "📦・catalog": "catalog",
    "✨・new-arrivals": "new-arrivals",
    "🛒・place-order": "purchase-panel",
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
        assert len(first.embeds) == 1, f"#{name}: one card per message"
        names = banner_names(first)
        assert len(names) == 1 and names[0].startswith(f"banner-{banner_id}-"), (name, names)
        for later in messages[1:]:
            assert banner_names(later) == [], f"#{name}: only the first message shows the banner"
    # Running it again changes nothing and posts nothing twice.
    counts = {name: len(bot_messages(shipped, name)) for name in PANEL_CHANNELS}
    await setup(shipped)
    assert {name: len(bot_messages(shipped, name)) for name in PANEL_CHANNELS} == counts


async def test_after_a_reset_setup_builds_the_same_server_again(
    shipped: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reset, "PACE_SECONDS", 0)
    await setup(shipped)
    names_before = sorted(c.name for c in shipped.guild.channels)
    owner = shipped.guild.add_member(id=OWNER_ID, name="owner")
    await confirm(shipped, owner, await preview(shipped, owner), phrase(shipped))
    [setup_channel] = shipped.guild.channels
    report = await setup(shipped)
    assert report.counts.get("failed", 0) == 0, report.lines
    # The same layout again (plus the private channel the reset made for /setup), every panel with its banner.
    assert sorted(c.name for c in shipped.guild.channels if c is not setup_channel) == names_before
    for name, banner_id in PANEL_CHANNELS.items():
        messages = bot_messages(shipped, name)
        assert messages, f"#{name} has no panel"
        assert banner_names(messages[0])[0].startswith(f"banner-{banner_id}-")
