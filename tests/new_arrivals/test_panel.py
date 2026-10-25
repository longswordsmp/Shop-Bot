"""The #new-arrivals panel: idempotent publishing, the on/off button, failures, restarts and /setup reruns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.panels import withdraw_unready_panels
from shopbot.db.repos import state
from shopbot.features import core_keys as ck
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals import service
from tests.fakes import http_error
from tests.helpers import GUILD_ID
from tests.new_arrivals.util import (
    ROLE_KEY,
    NaEnv,
    audit,
    has_role,
    na_env,
    reply_text,
    role_count,
    toggle,
)
from tests.setup.util import restarted_app, setup


def unset(env: NaEnv, key: str) -> None:
    """Remove one configured value (as if it were cleared in content.toml) and recompute readiness."""
    env.app.content.replace(file_values={**env.tenv.content, key: ""})
    env.app.recompute_readiness()


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[NaEnv]:
    na = await na_env(tmp_path)
    try:
        yield na
    finally:
        await na.close()


# --------------------------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------------------------


async def test_publish_posts_one_panel_and_is_idempotent(env: NaEnv) -> None:
    lines = await service.publish(env.app)
    assert lines == [env.app.op("new_arrivals.publish.created", channel=env.channel.mention)]
    [panel] = env.panels()
    embed = panel.embeds[0]
    assert embed.title == env.text(keys.PANEL_TITLE)
    assert embed.description == env.app.ui.text(keys.PANEL_DESCRIPTION, **service.text_values(env.app))
    [button] = panel.components()
    assert button.item.custom_id == "sb:na:toggle"  # type: ignore[attr-defined]
    assert button.item.label == env.text(keys.BUTTON_TOGGLE)  # type: ignore[attr-defined]
    assert panel.content == ""  # the panel pings nobody

    for _ in range(3):
        again = await service.publish(env.app)
        assert again == [env.app.op("new_arrivals.publish.unchanged", channel=env.channel.mention)]
    assert len(env.panels()) == 1 and panel.edits == []
    record = await env.app.db.run(state.published_get, GUILD_ID, service.PANEL_KEY)
    assert record is not None and record.message_id == panel.id


async def test_text_change_edits_the_panel_in_place(env: NaEnv) -> None:
    await service.publish(env.app)
    [panel] = env.panels()
    env.app.content.set_override(keys.PANEL_TITLE, "[changed title]")
    lines = await service.publish(env.app)
    assert lines[0] == env.app.op("new_arrivals.publish.updated", channel=env.channel.mention)
    assert env.panels() == [panel] and panel.embeds[0].title == "[changed title]"


async def test_deleted_panel_is_reposted_once(env: NaEnv) -> None:
    await service.publish(env.app)
    [panel] = env.panels()
    await panel.delete()
    lines = await service.publish(env.app)
    assert lines[0] == env.app.op("new_arrivals.publish.recreated", channel=env.channel.mention)
    assert len(env.panels()) == 1


async def test_panel_role_placeholder_renders_the_role_mention(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.PANEL_DESCRIPTION: "[panel] {role} {channel_catalog} {unknown}"})
    try:
        await service.publish(env.app)
        catalog_id = env.app.resources.channel_id("catalog")
        assert env.panels()[0].embeds[0].description == f"[panel] <@&{env.role.id}> <#{catalog_id}> {{unknown}}"
    finally:
        await env.close()


async def test_publish_reports_problems_without_posting(env: NaEnv) -> None:
    op = env.app.op
    await env.app.resources.forget("channel", "new_arrivals")
    assert await service.publish(env.app) == [op("new_arrivals.publish.channel_missing", channel="new_arrivals")]
    await env.app.resources.remember("channel", "new_arrivals", env.channel.id)

    unset(env, keys.BUTTON_TOGGLE)
    assert await service.publish(env.app) == [
        op("new_arrivals.publish.missing_content", content_key=keys.BUTTON_TOGGLE)
    ]
    env.app.content.replace(file_values=env.tenv.content)

    me = env.tenv.guild.me  # the bot is denied sending in the channel
    env.channel._fake_overwrites[me] = discord.PermissionOverwrite(send_messages=False)
    lines = await service.publish(env.app)
    assert lines == [
        op("new_arrivals.publish.missing_permissions", permissions="send_messages", channel=env.channel.mention)
    ]
    del env.channel._fake_overwrites[me]

    env.channel.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
    assert await service.publish(env.app) == [op("new_arrivals.publish.failed", http_status=500, code=0)]
    assert env.channel.messages == []


async def test_publish_warns_when_the_role_cannot_be_used(env: NaEnv) -> None:
    env.role.position = 99  # above the bot's role
    lines = await service.publish(env.app)
    assert lines[1] == env.app.op("new_arrivals.publish.role_not_manageable", role=env.role.mention)
    await env.role.delete()
    lines = await service.publish(env.app)
    assert lines[1] == env.app.op("new_arrivals.publish.role_missing", role=ROLE_KEY)


async def test_panel_is_withdrawn_when_the_feature_is_not_ready(env: NaEnv) -> None:
    await service.publish(env.app)
    [panel] = env.panels()
    unset(env, keys.PANEL_DESCRIPTION)
    assert not env.app.ready("new_arrivals")
    results = await withdraw_unready_panels(env.app)
    assert [(r.feature, r.key, r.ok) for r in results] == [("new_arrivals", service.PANEL_KEY, True)]
    assert panel.deleted and env.panels() == []


# --------------------------------------------------------------------------------------------------
# The on/off button
# --------------------------------------------------------------------------------------------------


async def test_click_turns_notifications_on_then_off(env: NaEnv) -> None:
    member = env.member()
    on = await toggle(env, member)
    assert has_role(member, env.role) and role_count(member, env.role) == 1
    [reply] = on.sent
    assert getattr(reply, "ephemeral", False)
    assert reply.embeds[0].title == env.text(keys.SUBSCRIBED_TITLE)
    values = service.text_values(env.app)
    assert reply.embeds[0].description == env.app.ui.text(keys.SUBSCRIBED_DESCRIPTION, **values)
    assert f"<@&{env.role.id}>" in (reply.embeds[0].description or "")
    assert reply.embeds[0].timestamp is not None
    assert reply.embeds[0].colour is not None and reply.embeds[0].colour.value == env.app.ui.color("success")

    off = await toggle(env, member)
    assert not has_role(member, env.role)
    [reply] = off.sent
    assert getattr(reply, "ephemeral", False)
    assert reply.embeds[0].title == env.text(keys.UNSUBSCRIBED_TITLE)
    assert reply.embeds[0].description == env.app.ui.text(keys.UNSUBSCRIBED_DESCRIPTION, **values)

    actions = [(e.action, e.actor_id, e.target_id) for e in await audit(env)]
    assert actions == [
        ("new_arrivals.subscribed", member.id, str(env.role.id)),
        ("new_arrivals.unsubscribed", member.id, str(env.role.id)),
    ]


async def test_confirmation_placeholders_are_rendered(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.SUBSCRIBED_DESCRIPTION: "[on] {role} {channel_new_arrivals}"})
    try:
        interaction = await toggle(env, env.member())
        assert interaction.sent[0].embeds[0].description == f"[on] <@&{env.role.id}> <#{env.channel.id}>"
    finally:
        await env.close()


async def test_optional_confirmation_title_is_omitted_when_empty(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.SUBSCRIBED_TITLE: ""})
    try:
        interaction = await toggle(env, env.member())
        assert interaction.sent[0].embeds[0].title is None
        assert interaction.sent[0].embeds[0].description
    finally:
        await env.close()


async def test_member_who_has_the_role_is_unsubscribed_never_given_it_twice(env: NaEnv) -> None:
    member = env.member(subscribed=True)
    calls: list[str] = []
    original_add = member.add_roles

    async def add_roles(*roles: Any, **kwargs: Any) -> None:
        calls.append("add")
        await original_add(*roles, **kwargs)

    member.add_roles = add_roles  # type: ignore[method-assign]
    await toggle(env, member)
    assert calls == [] and not has_role(member, env.role)


async def test_concurrent_double_click_applies_once(env: NaEnv) -> None:
    member = env.member()
    first, second = await asyncio.gather(toggle(env, member), toggle(env, member))
    assert role_count(member, env.role) == 1
    texts = [reply_text(first), reply_text(second)]
    assert env.text(ck.ERROR_BUSY) in texts[1]
    assert [e.action for e in await audit(env)] == ["new_arrivals.subscribed"]


async def test_role_change_uses_an_audit_reason(env: NaEnv) -> None:
    member = env.member()
    seen: dict[str, Any] = {}

    async def add_roles(*roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        seen["roles"], seen["reason"] = roles, reason

    member.add_roles = add_roles  # type: ignore[method-assign]
    await toggle(env, member)
    assert seen["roles"] == (env.role,)
    assert seen["reason"] == env.app.op("new_arrivals.role_reason")


async def test_forbidden_role_change_answers_generic_error_and_is_audited(env: NaEnv) -> None:
    member = env.member()

    async def refused(*roles: Any, **kwargs: Any) -> None:
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    member.add_roles = refused  # type: ignore[method-assign]
    interaction = await toggle(env, member)
    assert not has_role(member, env.role)
    [failure] = await audit(env)
    assert failure.action == "new_arrivals.toggle_failed"
    assert failure.details["reason"] == "forbidden" and failure.details["status"] == 403
    error_id = failure.details["error_id"]
    text = reply_text(interaction)
    assert env.text(ck.ERROR_GENERIC) in text
    assert env.app.ui.text(keys.SUBSCRIBED_DESCRIPTION, **service.text_values(env.app)) not in text
    assert len(error_id) == 12
    assert getattr(interaction.sent[-1], "ephemeral", False)


async def test_removal_failure_keeps_the_role(env: NaEnv) -> None:
    member = env.member(subscribed=True)

    async def refused(*roles: Any, **kwargs: Any) -> None:
        raise http_error(discord.HTTPException, 500, 0, "server error")

    member.remove_roles = refused  # type: ignore[method-assign]
    interaction = await toggle(env, member)
    assert has_role(member, env.role)
    assert env.text(ck.ERROR_GENERIC) in reply_text(interaction)
    [failure] = await audit(env)
    assert failure.details["reason"] == "discord_error"


async def test_missing_role_answers_generic_error_and_is_audited(env: NaEnv) -> None:
    await env.role.delete()
    member = env.member()
    interaction = await toggle(env, member)
    assert env.text(ck.ERROR_GENERIC) in reply_text(interaction)
    [failure] = await audit(env)
    assert failure.action == "new_arrivals.toggle_failed" and failure.details["reason"] == "role_missing"
    assert failure.target_id == ROLE_KEY


async def test_role_deleted_during_the_click_is_reported(env: NaEnv) -> None:
    member = env.member()

    async def unknown_role(*roles: Any, **kwargs: Any) -> None:
        raise http_error(discord.NotFound, 404, 10011, "Unknown Role")

    member.add_roles = unknown_role  # type: ignore[method-assign]
    interaction = await toggle(env, member)
    assert env.text(ck.ERROR_GENERIC) in reply_text(interaction)
    assert (await audit(env))[0].details["reason"] == "role_missing"


async def test_role_above_the_bot_is_not_attempted(tmp_path: Path) -> None:
    env = await na_env(tmp_path, role_position=99)
    try:
        member = env.member()
        interaction = await toggle(env, member)
        assert not has_role(member, env.role)
        assert env.text(ck.ERROR_GENERIC) in reply_text(interaction)
        assert (await audit(env))[0].details["reason"] == "role_not_manageable"
    finally:
        await env.close()


async def test_everyone_role_is_never_assigned(env: NaEnv) -> None:
    everyone = env.tenv.guild.default_role
    await env.app.resources.remember("role", ROLE_KEY, everyone.id)  # e.g. adopted by a wrong id
    member = env.member()
    calls: list[str] = []

    async def add_roles(*roles: Any, **kwargs: Any) -> None:
        calls.append("add")

    member.add_roles = add_roles  # type: ignore[method-assign]
    interaction = await toggle(env, member)
    assert calls == []
    assert env.text(ck.ERROR_GENERIC) in reply_text(interaction)
    assert (await audit(env))[0].details["reason"] == "role_not_manageable"
    lines = await service.publish(env.app)
    assert lines[1] == env.app.op("new_arrivals.publish.role_not_manageable", role=everyone.mention)


async def test_not_ready_feature_answers_unavailable_and_changes_nothing(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.SUBSCRIBED_DESCRIPTION: ""})
    try:
        assert not env.app.ready("new_arrivals")
        member = env.member()
        interaction = await toggle(env, member)
        assert not has_role(member, env.role)
        assert interaction.sent[0].embeds[0].description == env.text(ck.ERROR_UNAVAILABLE)
        assert await audit(env) == []
    finally:
        await env.close()


async def test_disabled_feature_answers_unavailable(tmp_path: Path) -> None:
    env = await na_env(tmp_path, {"features": {"new_arrivals": False}})
    try:
        member = env.member()
        interaction = await toggle(env, member)
        assert not has_role(member, env.role)
        assert interaction.sent[0].embeds[0].description == env.text(ck.ERROR_UNAVAILABLE)
    finally:
        await env.close()


async def test_button_from_another_server_is_ignored(env: NaEnv) -> None:
    from tests.fakes import FakeGuild, click

    other = FakeGuild(id=42)
    member = env.member()
    await click(env.app, user=member, custom_id="sb:na:toggle", guild=other)
    assert not has_role(member, env.role)


async def test_button_keeps_working_after_a_restart(env: NaEnv) -> None:
    await service.publish(env.app)
    [panel] = env.panels()
    member = env.member()
    fresh = restarted_app(env.tenv)
    await fresh.resources.refresh()  # done by the bot at startup
    assert fresh.ready("new_arrivals")
    interaction = await toggle(env, member, app=fresh, message=panel)
    assert has_role(member, env.role)
    assert env.text(keys.SUBSCRIBED_TITLE) == interaction.sent[0].embeds[0].title
    # /setup after the restart keeps the same panel message
    assert await service.publish(fresh) == [fresh.op("new_arrivals.publish.unchanged", channel=env.channel.mention)]
    assert env.panels() == [panel]


async def test_setup_reruns_never_post_updates_or_change_roles(tmp_path: Path) -> None:
    env = await na_env(tmp_path)
    try:
        subscribed, other = env.member("sub", subscribed=True), env.member("other")
        for _ in range(3):
            await setup(env.tenv)
        role = env.app.resources.role(env.tenv.guild, ROLE_KEY)  # type: ignore[arg-type]
        assert role is not None and role.id == env.role.id  # adopted, not duplicated
        assert len([r for r in env.tenv.guild.roles if r.id == env.role.id]) == 1
        channel = env.app.resources.text_channel(env.tenv.guild, "new_arrivals")  # type: ignore[arg-type]
        assert channel is not None
        panels = [m for m in channel.messages if "sb:na:toggle" in m.custom_ids()]
        assert len(panels) == 1
        assert [m for m in channel.messages if m not in panels] == []  # no product update was posted
        assert role_count(subscribed, env.role) == 1 and not has_role(other, env.role)
    finally:
        await env.close()
