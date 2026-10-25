"""The welcome panel: content, branding, channel links, idempotent publishing, withdrawal and /setup diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.core.panels import withdraw_unready_panels
from shopbot.db.repos import state
from shopbot.features import core_keys as ck
from shopbot.features.server_keys import channel_mentions
from shopbot.features.verification import content_keys as k
from shopbot.features.verification.service import PANEL_KEY
from tests.fakes import FakeMessage, http_error
from tests.helpers import TestEnv
from tests.setup.util import setup
from tests.verification.util import (
    AGREE_ID,
    channel,
    member_role,
    panel_messages,
    publish,
    verification_env,
    verification_settings,
)


def rendered(tenv: TestEnv, key: str) -> str | None:
    return tenv.app.content.render(key, **channel_mentions(tenv.app))


def buttons(message: FakeMessage) -> list[Any]:
    return [getattr(item, "item", item) for item in message.components()]


async def test_publish_posts_the_welcome_card_rules_and_button(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        lines = await publish(tenv)
        messages = panel_messages(tenv)
        assert len(messages) == 1
        welcome, rules = messages[0].embeds
        assert welcome.title == tenv.text(k.WELCOME_TITLE)
        assert welcome.description == rendered(tenv, k.WELCOME_DESCRIPTION)
        assert welcome.image.url == tenv.text(k.WELCOME_IMAGE)
        assert welcome.thumbnail.url == tenv.text(k.WELCOME_THUMBNAIL)
        assert welcome.author.name == tenv.text(ck.AUTHOR_NAME)
        assert welcome.footer.text is None  # the footer closes the message, on the rules card
        assert rules.title == tenv.text(k.RULES_TITLE)
        assert rules.description == rendered(tenv, k.RULES_DESCRIPTION)
        assert rules.author.name is None
        assert rules.footer.text == tenv.text(ck.FOOTER_TEXT)
        assert [(f.name, f.value, f.inline) for f in rules.fields] == [
            (tenv.text(k.rule_name_key(n)), rendered(tenv, k.rule_value_key(n)), False) for n in (1, 2, 3)
        ]
        primary = tenv.app.settings.branding.colors["primary"]
        assert welcome.color is not None and welcome.color.value == primary
        assert rules.color is not None and rules.color.value == primary
        (button,) = buttons(messages[0])
        assert messages[0].custom_ids() == [AGREE_ID]
        assert button.label == tenv.text(k.BUTTON_LABEL)
        assert str(button.emoji) == tenv.text(k.BUTTON_EMOJI)
        assert button.style == discord.ButtonStyle.success
        assert messages[0].allowed_mentions is not None and messages[0].allowed_mentions.everyone is False
        welcome_channel = channel(tenv, "welcome")
        assert lines[0] == tenv.app.op("verification.publish.created", channel=welcome_channel.mention)
        assert len(lines) == 1  # no diagnostics: the role exists and the bot can grant it
    finally:
        await tenv.close()


async def test_channel_placeholders_become_clickable_links(tmp_path: Path) -> None:
    content = {
        k.WELCOME_DESCRIPTION: "Read {channel_lounge} after {channel_welcome}. {unknown} stays.",
        k.rule_value_key(1): "See {channel_catalog}",
    }
    tenv = await verification_env(tmp_path, content=content)
    try:
        await publish(tenv)
        welcome, rules = panel_messages(tenv)[0].embeds
        lounge, welcome_channel = channel(tenv, "lounge"), channel(tenv, "welcome")
        assert welcome.description == f"Read <#{lounge.id}> after <#{welcome_channel.id}>. {{unknown}} stays."
        assert rules.fields[0].value == f"See <#{channel(tenv, 'catalog').id}>"
    finally:
        await tenv.close()


async def test_links_to_channels_that_do_not_exist_yet_are_reported(tmp_path: Path) -> None:
    content = {k.WELCOME_DESCRIPTION: "Go to {channel_lounge}", k.CONFIRMED_DESCRIPTION: "See {channel_catalog}"}
    tenv = await verification_env(tmp_path, content=content)
    try:
        await tenv.app.resources.forget("channel", "lounge")
        await tenv.app.resources.forget("channel", "catalog")
        lines = await publish(tenv)
        assert panel_messages(tenv)[0].embeds[0].description == "Go to "
        expected = tenv.app.op(
            "verification.publish.unresolved_links", placeholders="`{channel_catalog}`, `{channel_lounge}`"
        )
        assert expected in lines
    finally:
        await tenv.close()


async def test_branding_images_are_used_when_no_panel_images_are_configured(tmp_path: Path) -> None:
    extra = {
        "branding": {
            "logo_url": "https://example.com/logo.png",
            "banner_url": "https://example.com/banner.png",
            "panel_thumbnail": True,
            "panel_banner": True,
        }
    }
    tenv = await verification_env(tmp_path, extra, content={k.WELCOME_IMAGE: "", k.WELCOME_THUMBNAIL: ""})
    try:
        await publish(tenv)
        welcome, rules = panel_messages(tenv)[0].embeds
        assert welcome.thumbnail.url == "https://example.com/logo.png"
        assert welcome.image.url == "https://example.com/banner.png"
        assert welcome.author.icon_url == "https://example.com/logo.png"
        assert rules.image.url is None and rules.thumbnail.url is None
    finally:
        await tenv.close()


async def test_optional_text_is_omitted_cleanly(tmp_path: Path) -> None:
    empty = dict.fromkeys(
        (k.WELCOME_IMAGE, k.WELCOME_THUMBNAIL, k.RULES_TITLE, k.RULES_DESCRIPTION, k.BUTTON_EMOJI, ck.FOOTER_TEXT), ""
    )
    tenv = await verification_env(tmp_path, content=empty)
    try:
        await publish(tenv)
        message = panel_messages(tenv)[0]
        welcome, rules = message.embeds
        assert welcome.image.url is None and welcome.thumbnail.url is None
        assert rules.title is None and rules.description is None and rules.footer.text is None
        assert len(rules.fields) == 3
        (button,) = buttons(message)
        assert button.emoji is None and button.label == tenv.text(k.BUTTON_LABEL)
    finally:
        await tenv.close()


async def test_button_style_follows_the_setting(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"button_style": "danger"}})
    try:
        await publish(tenv)
        (button,) = buttons(panel_messages(tenv)[0])
        assert button.style == discord.ButtonStyle.danger
    finally:
        await tenv.close()


async def test_twenty_five_rules_fit_in_one_panel(tmp_path: Path) -> None:
    short = {k.rule_value_key(n): f"[rule {n}]" for n in range(1, 26)}
    tenv = await verification_env(tmp_path, rules=25, content=short)
    try:
        await publish(tenv)
        fields = panel_messages(tenv)[0].embeds[1].fields
        assert [f.value for f in fields] == [f"[rule {n}]" for n in range(1, 26)]
    finally:
        await tenv.close()


# --- idempotence ------------------------------------------------------------------------------------


async def test_publishing_twice_keeps_one_unchanged_message(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        first = panel_messages(tenv)[0]
        lines = await publish(tenv)
        assert panel_messages(tenv) == [first] and first.edits == []
        assert lines[0] == tenv.app.op("verification.publish.unchanged", channel=channel(tenv, "welcome").mention)
    finally:
        await tenv.close()


async def test_text_change_edits_the_same_message(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        message = panel_messages(tenv)[0]
        tenv.app.content.set_override(k.rule_value_key(2), "[changed rule]")
        lines = await publish(tenv)
        assert panel_messages(tenv) == [message] and len(message.edits) == 1
        assert message.embeds[1].fields[1].value == "[changed rule]"
        assert message.custom_ids() == [AGREE_ID]
        assert "updated" in lines[0]
    finally:
        await tenv.close()


async def test_deleted_panel_is_reposted_once(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        await panel_messages(tenv)[0].delete()
        lines = await publish(tenv)
        assert len(panel_messages(tenv)) == 1
        assert "re-posted" in lines[0]
    finally:
        await tenv.close()


async def test_panel_follows_a_channel_change(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        tenv.app.settings = verification_settings({"verification": {"channel": "ticket_panel"}})
        tenv.app.recompute_readiness()
        lines = await publish(tenv)
        assert panel_messages(tenv) == []
        assert len(channel(tenv, "ticket_panel").messages) == 1
        assert "moved" in lines[0]
    finally:
        await tenv.close()


async def test_setup_publishes_once_and_repeated_setup_does_not_duplicate(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, provision=False, role=False)
    try:
        report = await setup(tenv)
        assert any("Verification: welcome panel posted" in line for line in report.lines["panels"]), report.lines
        again = await setup(tenv)
        assert any("Verification: welcome panel up to date" in line for line in again.lines["panels"])
        assert len(panel_messages(tenv)) == 1
        role = member_role(tenv)
        assert sum(1 for r in tenv.guild.roles if r.name == role.name) == 1
    finally:
        await tenv.close()


# --- not ready / failures ------------------------------------------------------------------------------


async def test_missing_text_withdraws_the_panel_and_blocks_publishing(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        assert len(panel_messages(tenv)) == 1
        values = dict(tenv.content)
        del values[k.rule_name_key(2)]
        tenv.app.content.replace(file_values=values)
        tenv.app.recompute_readiness()
        assert not tenv.app.ready(k.VERIFICATION)
        results = await withdraw_unready_panels(tenv.app)
        assert [(r.feature, r.key, r.ok) for r in results] == [("verification", PANEL_KEY, True)]
        assert panel_messages(tenv) == []
        assert await tenv.app.db.run(state.published_get, tenv.app.env.guild_id, PANEL_KEY) is None
        # Even if called directly, nothing incomplete is posted and no wording is invented.
        lines = await publish(tenv)
        assert panel_messages(tenv) == []
        assert lines == [tenv.app.op("verification.publish.missing_content", content_key=k.rule_name_key(2))]
    finally:
        await tenv.close()


async def test_setup_withdraws_the_panel_when_the_feature_is_switched_off(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        await publish(tenv)
        tenv.app.settings = verification_settings({"features": {"verification": False}})
        tenv.app.recompute_readiness()
        report = await setup(tenv)
        assert panel_messages(tenv) == []
        assert any(PANEL_KEY in line for line in report.lines["panels"]), report.lines
    finally:
        await tenv.close()


async def test_missing_channel_is_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, provision=False)
    try:
        lines = await publish(tenv)
        assert lines == [tenv.app.op("verification.publish.channel_missing", channel="welcome")]
        assert await tenv.app.db.run(state.published_get, tenv.app.env.guild_id, PANEL_KEY) is None
    finally:
        await tenv.close()


async def test_missing_channel_permissions_are_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        welcome = channel(tenv, "welcome")
        await welcome.set_permissions(tenv.guild.me, overwrite=discord.PermissionOverwrite(send_messages=False))
        lines = await publish(tenv)
        assert panel_messages(tenv) == []
        assert lines == [
            tenv.app.op(
                "verification.publish.missing_permissions", permissions="send_messages", channel=welcome.mention
            )
        ]
    finally:
        await tenv.close()


async def test_discord_failure_is_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        channel(tenv, "welcome").fail_send = http_error(discord.HTTPException, 500, 0, "server error")
        lines = await publish(tenv)
        assert lines == [tenv.app.op("verification.publish.failed", http_status=500, code=0)]
        assert await tenv.app.db.run(state.published_get, tenv.app.env.guild_id, PANEL_KEY) is None
    finally:
        await tenv.close()


async def test_panel_over_discord_limits_is_not_posted(tmp_path: Path) -> None:
    long_rules = {k.rule_value_key(n): "x" * 1024 for n in range(1, 7)}
    tenv = await verification_env(tmp_path, rules=6, content=long_rules)
    try:
        lines = await publish(tenv)
        assert panel_messages(tenv) == []
        assert len(lines) == 1 and lines[0].startswith("Verification: welcome panel not posted because it exceeds")
        assert "6000" in lines[0]
    finally:
        await tenv.close()


# --- Member-role diagnostics in the /setup report -------------------------------------------------------


async def test_missing_member_role_is_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, role=False)
    try:
        lines = await publish(tenv)
        assert len(panel_messages(tenv)) == 1
        assert lines[1:] == [tenv.app.op("verification.role.missing", role="member")]
    finally:
        await tenv.close()


async def test_member_role_above_the_bot_is_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        role = member_role(tenv)
        role.position = tenv.guild.bot_role.position + 1
        lines = await publish(tenv)
        assert lines[1:] == [tenv.app.op("verification.role.not_assignable", role=role.mention)]
    finally:
        await tenv.close()


async def test_missing_manage_roles_is_reported(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        bot_role = tenv.guild.bot_role
        bot_role._permissions = discord.Permissions(
            bot_role.permissions.value & ~discord.Permissions(manage_roles=True).value
        ).value
        lines = await publish(tenv)
        assert tenv.app.op("verification.role.no_manage_roles", role=member_role(tenv).mention) in lines
    finally:
        await tenv.close()
