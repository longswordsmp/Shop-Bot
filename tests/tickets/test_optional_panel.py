"""The standalone ticket panel is optional (tickets.panel_channel = ""): private tickets are then opened by other
features (Report a Problem), and categories of other features get their staff through the external-category hook."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from shopbot.config.content import ContentStore
from shopbot.config.readiness import build_registry, compute_readiness
from shopbot.db.repos import state
from shopbot.features import all_modules
from shopbot.features.tickets import MODULE, service
from shopbot.features.tickets import commands as ticket_commands
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets.panel import PANEL_KEY
from tests.helpers import GUILD_ID, fixture_content, make_settings
from tests.tickets.util import channel, make_env

NO_PANEL = {"tickets": {"panel_channel": "", "categories": []}}


def test_no_panel_is_not_a_settings_problem() -> None:
    assert MODULE.settings_problems(make_settings(NO_PANEL)) == []
    assert MODULE.settings_problems(make_settings({"tickets": {"panel_channel": ""}})) == []


def test_a_panel_needs_a_category() -> None:
    problems = MODULE.settings_problems(make_settings({"tickets": {"categories": []}}))
    assert any("tickets.categories: define at least one ticket category" in p for p in problems), problems


def test_panel_keys_are_required_only_with_a_panel() -> None:
    with_panel = {
        key.key: key.required for key in k.content_keys(make_settings({"tickets": {"panel_style": "select"}}))
    }
    assert with_panel[k.PANEL_TITLE] and with_panel[k.PANEL_DESCRIPTION] and with_panel[k.PANEL_SELECT_PLACEHOLDER]
    settings = make_settings({"tickets": {"panel_channel": "", "categories": [], "panel_style": "select"}})
    without = {key.key: key.required for key in k.content_keys(settings)}
    assert not without[k.PANEL_TITLE] and not without[k.PANEL_DESCRIPTION] and not without[k.PANEL_SELECT_PLACEHOLDER]


def test_tickets_are_ready_without_panel_text_when_there_is_no_panel() -> None:
    settings = make_settings(NO_PANEL)
    modules = all_modules()
    registry = build_registry(settings, modules)
    values = fixture_content(registry)
    for key in (k.PANEL_TITLE, k.PANEL_DESCRIPTION, k.PANEL_SELECT_PLACEHOLDER):
        values.pop(key)
    readiness = compute_readiness(settings, ContentStore(registry, values), modules)
    assert readiness.is_ready(k.FEATURE), readiness.statuses[k.FEATURE].problems


async def test_publish_without_panel_posts_nothing(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, NO_PANEL)
    try:
        assert MODULE.publish is not None
        lines = await MODULE.publish(tenv.app)
        assert all(not ch.messages for ch in tenv.guild.text_channels)
        assert tenv.app.op("tickets.publish.panel_removed") not in lines
        assert await tenv.app.db.run(state.published_get, GUILD_ID, PANEL_KEY) is None
    finally:
        await tenv.close()


async def test_publish_removes_an_earlier_panel_once_the_panel_is_switched_off(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        panel_channel = channel(tenv, "ticket_panel")
        assert len(panel_channel.messages) == 1
        tenv.app.settings = replace(tenv.app.settings, tickets=replace(tenv.app.settings.tickets, panel_channel=""))
        lines = await MODULE.publish(tenv.app)
        assert lines[0] == tenv.app.op("tickets.publish.panel_removed")
        assert panel_channel.messages == []
        assert await tenv.app.db.run(state.published_get, GUILD_ID, PANEL_KEY) is None
        again = await MODULE.publish(tenv.app)  # nothing left to remove
        assert tenv.app.op("tickets.publish.panel_removed") not in again
    finally:
        await tenv.close()


async def test_preview_has_no_panel_states_without_a_panel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, NO_PANEL)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert "panel.buttons" not in names and "panel.select" not in names
        assert "closed" in names  # the rest of the ticket system is unchanged
    finally:
        await tenv.close()


async def test_external_categories_get_their_staff_roles(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        app = tenv.app
        assert service.support_staff_keys(app, "elsewhere") == ()
        service.register_external_category(app, "elsewhere", lambda _app: ("helpers",))
        assert service.support_staff_keys(app, "elsewhere") == ("helpers",)
        assert service.support_staff_keys(app, "general") == ()  # configured categories are unaffected
        assert "helpers" in ticket_commands.command_staff_keys(app)
    finally:
        await tenv.close()
