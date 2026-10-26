"""Banner pictures on the /setup replies: the report (first message only) and the refusals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.setup.preview import preview
from shopbot.features.setup.service import setup_lock
from shopbot.ui.icons import IconFile, IconLibrary
from shopbot.ui.spec import ButtonSpec, MessageSpec, PreviewState
from tests.helpers import TestEnv, banner_names, make_banner_env
from tests.setup.util import invoke_setup


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await make_banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def message_spec(state: PreviewState) -> MessageSpec:
    assert isinstance(state.spec, MessageSpec), state.name
    return state.spec


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


async def test_report_shows_the_dashboard_banner_on_its_first_message(benv: TestEnv) -> None:
    interaction = await invoke_setup(benv, benv.member(admin=True))
    first, *rest = interaction.sent
    assert first.embeds[0].title == benv.app.op("server.setup.title")
    assert banner_names(first) == [banner_file(benv, "staff-dashboard")]
    assert all(banner_names(message) == [] for message in rest)


async def test_long_report_continues_without_banners(benv: TestEnv) -> None:
    parts = [state for state in preview(benv.app) if state.name.startswith("report_large_part_")]
    assert len(parts) > 1
    assert message_spec(parts[0]).banner == benv.app.ui.banner("staff-dashboard")
    assert all(message_spec(part).banner is None for part in parts[1:])
    for name in ("report", "report_dry_run"):
        (state,) = [s for s in preview(benv.app) if s.name == name]
        assert message_spec(state).banner == benv.app.ui.banner("staff-dashboard"), name


async def test_busy_and_unavailable_server_replies(benv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    async with setup_lock(benv.app):
        busy = await invoke_setup(benv, benv.member(admin=True))
    assert busy.last_sent is not None and busy.last_sent.embeds[0].title == benv.app.op("server.setup.busy")
    assert banner_names(busy.last_sent) == [banner_file(benv, "submission-and-processing")]
    monkeypatch.setattr(benv.bot, "get_guild", lambda guild_id: None)
    unavailable = await invoke_setup(benv, benv.member(admin=True))
    assert unavailable.last_sent is not None
    assert unavailable.last_sent.embeds[0].title == benv.app.op("server.setup.no_guild")
    assert banner_names(unavailable.last_sent) == [banner_file(benv, "error")]


async def test_the_reset_preview_warns_with_the_error_banner_and_shows_its_icons(benv: TestEnv) -> None:
    """What /reset is about to delete closes with the error banner (not the dashboard); its headings and buttons show
    the bot's icons, rendered from the operator text (nothing while an icon is not uploaded, never raw text)."""
    states = {s.name: s for s in preview(benv.app)}
    for name in ("reset_preview", "reset_preview_wipe", "reset_preview_large"):
        assert message_spec(states[name]).banner == benv.app.ui.banner("error"), name
    ids = {"trash": 111111111111111111, "alert": 222222222222222222, "cross": 333333333333333333}
    files = {name: IconFile(name, b"png", name, "image/png") for name in ids}
    benv.app.icons = IconLibrary(files=files, ids=ids, enabled=True)  # the icons, uploaded
    spec = message_spec({s.name: s for s in preview(benv.app)}["reset_preview"])
    names = [field.name for field in spec.embeds[0].fields]
    assert names[0].startswith("<:trash:111111111111111111> ") and names[-1].startswith("<:alert:222222222222222222> ")
    labels = [component.label for row in spec.rows for component in row if isinstance(component, ButtonSpec)]
    assert labels[0] == "<:alert:222222222222222222> " + benv.app.op("server.reset.button.continue").split(" ", 1)[1]
    assert labels[1] is not None and labels[1].startswith("<:cross:333333333333333333> ")
    assert not any("{icon_" in text for text in [*names, *(label or "" for label in labels)])
