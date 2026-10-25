from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.content_keys import Kind
from shopbot.config.readiness import build_registry
from shopbot.config.settings import Settings
from shopbot.db.repos import state
from shopbot.features import core_keys as ck
from shopbot.features.info_panels import MODULE
from shopbot.features.info_panels import content_keys as keys
from shopbot.features.info_panels.service import publish, record_key
from shopbot.features.server_keys import channel_name_key
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import ButtonSpec, MessageSpec
from tests.fakes import FakeMessage, FakeTextChannel, http_error
from tests.helpers import TestEnv, make_settings, make_test_env, provision_channels, shipped_settings_dict
from tests.setup.util import restarted_app, setup

RULES = {"key": "rules", "channel": "rules", "fields": 2, "inline_fields": True, "links": ["site", "docs"]}
FAQ = {"key": "faq", "channel": "faq", "fields": 0, "links": []}


def panel_settings(*panels: dict[str, Any], extra: dict[str, Any] | None = None) -> Settings:
    server = shipped_settings_dict()["server"]
    server["channels"] += [
        {"key": "rules", "category": "support", "access": "inherit"},
        {"key": "faq", "category": "support", "access": "inherit"},
    ]
    return make_settings({"server": server, "info_panels": list(panels), **(extra or {})})


async def panel_env(tmp_path: Path, *panels: dict[str, Any], **kwargs: Any) -> TestEnv:
    tenv = await make_test_env(tmp_path, settings=panel_settings(*(panels or (RULES,))), **kwargs)
    await provision_channels(tenv)
    return tenv


@pytest.fixture
async def penv(tmp_path: Path) -> Any:
    tenv = await panel_env(tmp_path, RULES, FAQ)
    try:
        yield tenv
    finally:
        await tenv.close()


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return found


def bot_messages(tenv: TestEnv, key: str) -> list[FakeMessage]:
    return list(channel(tenv, key).messages)


def apply_settings(tenv: TestEnv, settings: Settings) -> None:
    app = tenv.app
    app.settings = settings
    app.content.replace(registry=build_registry(settings, app.modules))
    app.recompute_readiness()


# --- keys and readiness ------------------------------------------------------------------------------


async def test_content_keys_for_each_panel(penv: TestEnv) -> None:
    registry = penv.app.content.registry
    expect = {
        keys.title_key("rules"): (Kind.TITLE, False),
        keys.description_key("rules"): (Kind.DESCRIPTION, False),
        keys.image_key("rules"): (Kind.URL, False),
        keys.thumbnail_key("rules"): (Kind.URL, False),
        keys.field_name_key("rules", 1): (Kind.FIELD_NAME, True),
        keys.field_value_key("rules", 2): (Kind.FIELD_VALUE, True),
        keys.link_label_key("rules", "site"): (Kind.BUTTON_LABEL, True),
        keys.link_url_key("rules", "docs"): (Kind.URL, True),
        keys.link_emoji_key("rules", "docs"): (Kind.EMOJI, False),
    }
    for key, (kind, required) in expect.items():
        definition = registry.get(key)
        assert definition is not None, key
        assert (definition.kind, definition.required, definition.feature) == (kind, required, "info_panels")
        assert definition.doc and definition.group
    assert registry.get(keys.field_name_key("rules", 3)) is None
    assert registry.get(keys.field_name_key("faq", 1)) is None
    assert penv.app.ready("info_panels")


async def test_title_or_description_is_required(tmp_path: Path) -> None:
    tenv = await panel_env(tmp_path, content={keys.title_key("rules"): "", keys.description_key("rules"): ""})
    try:
        assert not tenv.app.ready("info_panels")
        problems = [p.message for p in tenv.app.readiness.statuses["info_panels"].problems]
        assert any("rules" in p and keys.title_key("rules") in p for p in problems)
    finally:
        await tenv.close()
    tenv = await panel_env(tmp_path / "b", content={keys.title_key("rules"): ""})
    try:
        assert tenv.app.ready("info_panels")  # a description alone is enough
    finally:
        await tenv.close()


async def test_panel_channel_must_be_defined(tmp_path: Path) -> None:
    settings = make_settings({"info_panels": [{"key": "rules", "channel": "nowhere"}, {"key": "x", "channel": ""}]})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        assert not tenv.app.ready("info_panels")
        messages = [p.message for p in tenv.app.readiness.statuses["info_panels"].problems]
        assert any("nowhere" in m for m in messages)
        assert any("info_panels[1].channel" in m for m in messages)
    finally:
        await tenv.close()


# --- publishing ----------------------------------------------------------------------------------


async def test_publish_posts_configured_text_and_link_buttons(penv: TestEnv) -> None:
    lines = await publish(penv.app)
    messages = bot_messages(penv, "rules")
    assert len(messages) == 1
    embed = messages[0].embeds[0]
    assert embed.title == penv.text(keys.title_key("rules"))
    assert embed.description == penv.text(keys.description_key("rules"))
    assert [(f.name, f.value, f.inline) for f in embed.fields] == [
        (penv.text(keys.field_name_key("rules", n)), penv.text(keys.field_value_key("rules", n)), True) for n in (1, 2)
    ]
    assert embed.image.url == penv.text(keys.image_key("rules"))
    assert embed.thumbnail.url == penv.text(keys.thumbnail_key("rules"))
    assert embed.footer.text == penv.text(ck.FOOTER_TEXT)
    buttons = messages[0].components()
    assert [b.style for b in buttons] == [discord.ButtonStyle.link, discord.ButtonStyle.link]
    assert [b.label for b in buttons] == [penv.text(keys.link_label_key("rules", link)) for link in ("site", "docs")]
    assert [b.url for b in buttons] == [penv.text(keys.link_url_key("rules", link)) for link in ("site", "docs")]
    assert all(str(b.emoji) == penv.text(keys.link_emoji_key("rules", "site")) for b in buttons)
    assert messages[0].custom_ids() == []  # link buttons carry no custom_id
    faq = bot_messages(penv, "faq")[0]
    assert faq.embeds[0].fields == [] and faq.components() == []
    assert any("`rules`" in line and "posted" in line for line in lines)
    assert messages[0].allowed_mentions is not None and messages[0].allowed_mentions.everyone is False


async def test_publish_twice_keeps_one_unchanged_message(penv: TestEnv) -> None:
    await publish(penv.app)
    first = bot_messages(penv, "rules")[0]
    lines = await publish(penv.app)
    messages = bot_messages(penv, "rules")
    assert messages == [first] and first.edits == []
    assert any("`rules`" in line and "up to date" in line for line in lines)


async def test_content_change_edits_the_same_message(penv: TestEnv) -> None:
    await publish(penv.app)
    message = bot_messages(penv, "rules")[0]
    penv.app.content.set_override(keys.description_key("rules"), "[changed description]")
    lines = await publish(penv.app)
    assert bot_messages(penv, "rules") == [message]
    assert len(message.edits) == 1
    assert message.embeds[0].description == "[changed description]"
    assert any("`rules`" in line and "updated" in line for line in lines)


async def test_deleted_message_is_reposted(penv: TestEnv) -> None:
    await publish(penv.app)
    await bot_messages(penv, "rules")[0].delete()
    lines = await publish(penv.app)
    assert len(bot_messages(penv, "rules")) == 1
    assert any("`rules`" in line and "re-posted" in line for line in lines)


async def test_moved_panel_follows_its_channel(penv: TestEnv) -> None:
    await publish(penv.app)
    moved = dict(RULES, channel="faq")
    apply_settings(penv, panel_settings(moved, FAQ))
    lines = await publish(penv.app)
    assert bot_messages(penv, "rules") == []
    assert len(bot_messages(penv, "faq")) == 2
    assert any("`rules`" in line and "moved" in line for line in lines)


async def test_removed_panel_is_cleaned_up(penv: TestEnv) -> None:
    await publish(penv.app)
    apply_settings(penv, panel_settings(FAQ))
    lines = await publish(penv.app)
    assert bot_messages(penv, "rules") == []
    assert len(bot_messages(penv, "faq")) == 1
    record = await penv.app.db.run(state.published_get, penv.app.env.guild_id, record_key("rules"))
    assert record is None
    assert any("`rules`" in line and "removed" in line for line in lines)
    apply_settings(penv, panel_settings())
    lines = await publish(penv.app)
    assert bot_messages(penv, "faq") == []
    lines = await publish(penv.app)
    assert lines == [penv.app.op("info_panels.publish.none_configured")]


async def test_optional_elements_are_omitted_when_not_configured(tmp_path: Path) -> None:
    empty = {
        keys.image_key("rules"): "",
        keys.thumbnail_key("rules"): "",
        keys.link_emoji_key("rules", "site"): "",
        keys.link_emoji_key("rules", "docs"): "",
        keys.title_key("rules"): "",
    }
    tenv = await panel_env(tmp_path, content=empty)
    try:
        await publish(tenv.app)
        message = bot_messages(tenv, "rules")[0]
        embed = message.embeds[0]
        assert embed.title is None and embed.description == tenv.text(keys.description_key("rules"))
        assert embed.image.url is None and embed.thumbnail.url is None
        assert all(b.emoji is None for b in message.components())
    finally:
        await tenv.close()


async def test_missing_required_content_blocks_the_panel(tmp_path: Path) -> None:
    tenv = await panel_env(tmp_path, content={keys.link_url_key("rules", "docs"): ""})
    try:
        assert not tenv.app.ready("info_panels")
        status = tenv.app.readiness.statuses["info_panels"]
        assert any(p.key == keys.link_url_key("rules", "docs") for p in status.problems)
        report = await setup(tenv)
        assert bot_messages(tenv, "rules") == []
        assert any("`info_panels`" in line for line in report.lines["problems"])
        # Even if called directly, nothing incomplete is posted and no wording is invented.
        lines = await publish(tenv.app)
        assert bot_messages(tenv, "rules") == []
        assert any(keys.link_url_key("rules", "docs") in line for line in lines)
    finally:
        await tenv.close()


async def test_missing_channel_is_reported(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=panel_settings(RULES))
    try:
        lines = await publish(tenv.app)
        assert any("`rules`" in line and "does not exist" in line for line in lines)
        record = await tenv.app.db.run(state.published_get, tenv.app.env.guild_id, record_key("rules"))
        assert record is None
    finally:
        await tenv.close()


async def test_discord_failure_is_reported_and_retried_next_time(penv: TestEnv) -> None:
    rules = channel(penv, "rules")
    rules.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    lines = await publish(penv.app)
    assert any("`rules`" in line and "HTTP 403" in line for line in lines)
    assert any("`faq`" in line and "posted" in line for line in lines)  # other panels unaffected
    assert await penv.app.db.run(state.published_get, penv.app.env.guild_id, record_key("rules")) is None
    rules.fail_send = None
    await publish(penv.app)
    assert len(rules.messages) == 1


async def test_missing_channel_permissions_are_reported(penv: TestEnv) -> None:
    rules = channel(penv, "rules")
    await rules.set_permissions(
        discord.Object(id=penv.guild.me.id), overwrite=discord.PermissionOverwrite(send_messages=False)
    )
    lines = await publish(penv.app)
    assert rules.messages == []
    assert any("`rules`" in line and "send_messages" in line for line in lines)


async def test_panel_exceeding_discord_limits_is_not_sent(tmp_path: Path) -> None:
    content = {
        keys.description_key("rules"): "d" * 4000,
        keys.field_value_key("rules", 1): "v" * 1000,
        keys.field_value_key("rules", 2): "w" * 1000,
    }
    tenv = await panel_env(tmp_path, content=content)
    try:
        assert tenv.app.ready("info_panels")  # each value is valid on its own
        lines = await publish(tenv.app)
        assert bot_messages(tenv, "rules") == []
        assert any("`rules`" in line and "limits" in line for line in lines)
    finally:
        await tenv.close()


async def test_restart_keeps_the_same_message(penv: TestEnv) -> None:
    await publish(penv.app)
    message = bot_messages(penv, "rules")[0]
    app = restarted_app(penv)
    await app.resources.refresh()
    lines = await publish(app)
    assert bot_messages(penv, "rules") == [message] and message.edits == []
    assert any("`rules`" in line and "up to date" in line for line in lines)


async def test_setup_creates_the_channel_and_publishes_once(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=panel_settings(RULES))
    try:
        report = await setup(tenv)
        rules = channel(tenv, "rules")
        assert rules.name == tenv.text(channel_name_key("rules"))
        assert len(rules.messages) == 1
        assert any("`rules`" in line and "posted" in line for line in report.lines["panels"])
        report = await setup(tenv)
        assert len(rules.messages) == 1
        assert any("`rules`" in line and "up to date" in line for line in report.lines["panels"])
    finally:
        await tenv.close()


async def test_disabled_feature_is_never_published(tmp_path: Path) -> None:
    settings = panel_settings(RULES, extra={"features": {"info_panels": False}})
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        await setup(tenv)
        assert channel(tenv, "rules").messages == []
    finally:
        await tenv.close()


# --- preview -----------------------------------------------------------------------------------------


async def test_preview_states_render_and_pass_limits(penv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(penv.app)
    assert [s.name for s in states] == ["panel_rules", "panel_faq"]
    for preview_state in states:
        assert isinstance(preview_state.spec, MessageSpec)
        assert validate_message(preview_state.spec) == [], preview_state.name
    rules = states[0].spec
    assert isinstance(rules, MessageSpec)
    assert rules.embeds[0].title == penv.text(keys.title_key("rules"))
    assert all(isinstance(button, ButtonSpec) and button.style == "link" for row in rules.rows for button in row)


async def test_preview_without_panels_shows_marked_structure(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    assert [s.name for s in states] == ["panel_example"]
    spec = states[0].spec
    assert isinstance(spec, MessageSpec)
    assert validate_message(spec) == []
    assert spec.embeds[0].title == "[info_panels.example.title]"
    assert spec.embeds[0].fields[0].name == "[info_panels.example.field.1.name]"


async def test_preview_marks_missing_content_instead_of_inventing_text(tmp_path: Path) -> None:
    tenv = await panel_env(tmp_path, content={keys.field_value_key("rules", 2): ""})
    try:
        assert MODULE.preview is not None
        spec = MODULE.preview(tenv.app)[0].spec
        assert isinstance(spec, MessageSpec)
        assert spec.embeds[0].fields[1].value == f"[{keys.field_value_key('rules', 2)}]"
        assert validate_message(spec) == []
    finally:
        await tenv.close()
