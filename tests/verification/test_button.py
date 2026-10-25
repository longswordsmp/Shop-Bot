"""The 'I agree, enter the server' button: granting the Member role, idempotence, failures, DMs and restarts."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.db.repos import notifications as notif_repo
from shopbot.features import core_keys as ck
from shopbot.features.server_keys import channel_mentions
from shopbot.features.verification import content_keys as k
from shopbot.features.verification import repo, views
from tests.fakes import FakeInteraction, FakeMember, click, http_error
from tests.helpers import OWNER_ID, TestEnv
from tests.setup.util import restarted_app, setup
from tests.verification.util import (
    AGREE_ID,
    acceptance,
    agree,
    audit,
    channel,
    has_member_role,
    member_role,
    panel_messages,
    publish,
    reply,
    verification_env,
)

ERROR_WITH_REFERENCE = {ck.ERROR_GENERIC: "[generic error] {error_id}"}


def expected(tenv: TestEnv, key: str, user: Any) -> str | None:
    values = views.reply_values(channel_mentions(tenv.app), user_name=user.display_name, user_id=user.id)
    return tenv.app.content.render(key, **values)


def error_reference(message: Any) -> str:
    description = message.embeds[0].description or ""
    assert description.startswith("[generic error] "), description
    return description.removeprefix("[generic error] ")


@pytest.fixture
async def venv(tmp_path: Path) -> Any:
    tenv = await verification_env(tmp_path, content=ERROR_WITH_REFERENCE)
    try:
        yield tenv
    finally:
        await tenv.close()


# --- happy path -----------------------------------------------------------------------------------------


async def test_click_grants_the_member_role_and_confirms_privately(venv: TestEnv) -> None:
    newcomer = venv.member(name="newcomer")
    interaction = await agree(venv, newcomer)
    assert has_member_role(venv, newcomer)
    assert interaction.response_kinds() == ["defer"]
    message = reply(interaction)
    (embed,) = message.embeds
    assert embed.title == expected(venv, k.CONFIRMED_TITLE, newcomer)
    assert embed.description == expected(venv, k.CONFIRMED_DESCRIPTION, newcomer)
    assert f"<@{newcomer.id}>" in (embed.description or "")
    success = venv.app.settings.branding.colors["success"]
    assert embed.color is not None and embed.color.value == success
    assert embed.timestamp is not None
    (event,) = await audit(venv, "verification.accepted")
    assert (event.actor_id, event.target_type, event.target_id) == (newcomer.id, "member", str(newcomer.id))
    assert event.details == {"role_id": member_role(venv).id, "acceptances": 1}
    record = await acceptance(venv, newcomer.id)
    assert record is not None and record.acceptances == 1
    assert record.first_accepted_at == record.last_accepted_at == 1_700_000_000
    assert newcomer.dms == []  # verification.dm_confirmation is off


async def test_display_names_are_escaped(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, content={k.CONFIRMED_DESCRIPTION: "Hi {user_name}"})
    try:
        member = tenv.member(name="**bold**_name")
        message = reply(await agree(tenv, member))
        assert message.embeds[0].description == "Hi \\*\\*bold\\*\\*\\_name"
    finally:
        await tenv.close()


async def test_role_reveals_the_member_only_channels_right_away(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, provision=False, role=False)
    try:
        await setup(tenv)  # creates the roles, channels and overwrites, and posts the panel
        newcomer = tenv.member(name="newcomer")
        lounge, welcome = channel(tenv, "lounge"), channel(tenv, "welcome")
        assert welcome.permissions_for(newcomer).view_channel is True  # the entry channel is public
        assert welcome.permissions_for(newcomer).send_messages is False
        assert lounge.permissions_for(newcomer).view_channel is False
        panel = panel_messages(tenv)[0]
        await agree(tenv, newcomer, message=panel)
        assert lounge.permissions_for(newcomer).view_channel is True
        assert lounge.permissions_for(newcomer).send_messages is False  # members_readonly
        staff_chat = channel(tenv, "staff_alerts")
        assert staff_chat.permissions_for(newcomer).view_channel is False  # never staff channels
    finally:
        await tenv.close()


@pytest.mark.parametrize("who", ["staff", "admin", "owner"])
async def test_staff_and_owner_can_click_too(venv: TestEnv, who: str) -> None:
    if who == "owner":
        user = venv.guild.get_member(OWNER_ID) or venv.guild.add_member(id=OWNER_ID, name="owner")
    else:
        user = venv.member(name=who, staff=who == "staff", admin=who == "admin")
    message = reply(await agree(venv, user))
    assert has_member_role(venv, user)
    assert message.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, user)


# --- idempotence ----------------------------------------------------------------------------------------


async def test_already_verified_member_gets_a_private_note_and_nothing_changes(venv: TestEnv) -> None:
    member = venv.member(name="regular")
    member.add_role(member_role(venv))
    calls: list[Any] = []
    original = member.add_roles

    async def tracking(*roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        calls.append(roles)
        await original(*roles, reason=reason, atomic=atomic)

    member.add_roles = tracking  # type: ignore[method-assign]
    message = reply(await agree(venv, member))
    assert calls == []
    (embed,) = message.embeds
    assert embed.title == expected(venv, k.ALREADY_TITLE, member)
    assert embed.description == expected(venv, k.ALREADY_DESCRIPTION, member)
    info = venv.app.settings.branding.colors["info"]
    assert embed.color is not None and embed.color.value == info
    assert await audit(venv, "verification.accepted") == []
    assert await acceptance(venv, member.id) is None


async def test_second_click_does_not_assign_twice(venv: TestEnv) -> None:
    member = venv.member(name="clicker")
    first = reply(await agree(venv, member))
    second = reply(await agree(venv, member))
    assert first.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, member)
    assert second.embeds[0].description == expected(venv, k.ALREADY_DESCRIPTION, member)
    assert has_member_role(venv, member)
    assert len(await audit(venv, "verification.accepted")) == 1
    record = await acceptance(venv, member.id)
    assert record is not None and record.acceptances == 1


async def test_stale_click_payload_is_rechecked_with_discord(venv: TestEnv) -> None:
    member = venv.member(name="clicker")
    await agree(venv, member)
    # A second click whose payload was created before the role was granted (it still lists no role).
    stale = FakeMember(venv.guild, id=member.id, name=member.name)  # type: ignore[arg-type]
    message = reply(await agree(venv, stale))
    assert message.embeds[0].description == expected(venv, k.ALREADY_DESCRIPTION, member)
    assert has_member_role(venv, member)
    assert len(await audit(venv, "verification.accepted")) == 1


async def test_concurrent_clicks_grant_the_role_once(venv: TestEnv) -> None:
    member = venv.member(name="fast")
    original = member.add_roles

    async def slow(*roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        await asyncio.sleep(0.05)
        await original(*roles, reason=reason, atomic=atomic)

    member.add_roles = slow  # type: ignore[method-assign]
    first, second = await asyncio.gather(agree(venv, member), agree(venv, member))
    assert has_member_role(venv, member)
    assert len(await audit(venv, "verification.accepted")) == 1
    texts = sorted(m.embeds[0].description or "" for i in (first, second) for m in i.sent)
    assert texts == sorted([expected(venv, k.CONFIRMED_DESCRIPTION, member) or "", venv.text(ck.ERROR_BUSY)])


async def test_rejoining_member_accepts_again(venv: TestEnv) -> None:
    member = venv.member(name="returning")
    await agree(venv, member)
    await member.remove_roles(member_role(venv))  # e.g. left and rejoined the server
    venv.clock.advance(3600)
    message = reply(await agree(venv, member))
    assert message.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, member)
    record = await acceptance(venv, member.id)
    assert record is not None and record.acceptances == 2
    assert record.last_accepted_at == record.first_accepted_at + 3600
    assert [e.details["acceptances"] for e in await audit(venv, "verification.accepted")] == [2, 1]


async def test_acceptances_are_unique_per_member(venv: TestEnv) -> None:
    guild_id = venv.app.env.guild_id
    await venv.app.db.transaction(repo.record_acceptance, guild_id, 5, 10)
    await venv.app.db.transaction(repo.record_acceptance, guild_id, 5, 20)
    await venv.app.db.transaction(repo.record_acceptance, guild_id, 6, 30)
    assert await venv.app.db.run(repo.count_acceptances, guild_id) == 2
    record = await venv.app.db.run(repo.get_acceptance, guild_id, 5)
    assert record == repo.Acceptance(guild_id, 5, 10, 20, 2)
    with pytest.raises(sqlite3.IntegrityError, match=r"UNIQUE|PRIMARY"):
        await venv.app.db.run(
            lambda c: c.execute(
                "INSERT INTO verification_acceptances (guild_id, user_id, first_accepted_at, last_accepted_at) "
                "VALUES (?, 5, 1, 1)",
                (guild_id,),
            )
        )


# --- the bot cannot grant the role -------------------------------------------------------------------------


async def _assert_failed(tenv: TestEnv, interaction: FakeInteraction, user: Any, reason: str) -> dict[str, Any]:
    reference = error_reference(reply(interaction))
    assert len(reference) == 12
    (event,) = await audit(tenv, "verification.failed")
    assert event.actor_id == user.id and event.details["reason"] == reason
    assert event.details["error_id"] == reference  # the member's reference matches the audit entry
    assert await audit(tenv, "verification.accepted") == []
    assert await acceptance(tenv, user.id) is None
    return event.details


async def test_missing_role_answers_with_the_generic_error(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, role=False, content=ERROR_WITH_REFERENCE)
    try:
        member = tenv.member(name="newcomer")
        details = await _assert_failed(tenv, await agree(tenv, member), member, "role_missing")
        assert details["role_key"] == "member"
    finally:
        await tenv.close()


async def test_role_above_the_bot_is_not_attempted(venv: TestEnv) -> None:
    role = member_role(venv)
    role.position = venv.guild.bot_role.position + 1
    member = venv.member(name="newcomer")
    await _assert_failed(venv, await agree(venv, member), member, "role_not_assignable")
    assert not any(r.id == role.id for r in member.roles)


async def test_managed_role_is_not_attempted(venv: TestEnv) -> None:
    member_role(venv).managed = True
    member = venv.member(name="newcomer")
    await _assert_failed(venv, await agree(venv, member), member, "role_not_assignable")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (http_error(discord.Forbidden, 403, 50013, "Missing Permissions"), "forbidden"),
        (http_error(discord.NotFound, 404, 10011, "Unknown Role"), "not_found"),
        (http_error(discord.HTTPException, 500, 0, "Internal Server Error"), "http_error"),
    ],
)
async def test_discord_refusal_is_logged_audited_and_answered(venv: TestEnv, error: Any, reason: str) -> None:
    member = venv.member(name="newcomer")

    async def refuse(*roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        raise error

    member.add_roles = refuse  # type: ignore[method-assign]
    details = await _assert_failed(venv, await agree(venv, member), member, reason)
    assert details["role_id"] == member_role(venv).id
    assert not has_member_role(venv, member)


async def test_member_who_left_is_reported(venv: TestEnv) -> None:
    ghost = FakeMember(venv.guild, name="ghost")  # type: ignore[arg-type]  # not (any longer) in the server
    await _assert_failed(venv, await agree(venv, ghost), ghost, "member_not_found")


async def test_member_lookup_failure_falls_back_to_the_click_payload(venv: TestEnv, monkeypatch: Any) -> None:
    member = venv.member(name="newcomer")

    async def unavailable(member_id: int) -> Any:
        raise http_error(discord.HTTPException, 503, 0, "Service Unavailable")

    monkeypatch.setattr(venv.guild, "fetch_member", unavailable)
    message = reply(await agree(venv, member))
    assert message.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, member)
    assert has_member_role(venv, member)


async def test_history_failure_does_not_turn_success_into_an_error(venv: TestEnv, monkeypatch: Any) -> None:
    def broken(*args: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "record_acceptance", broken)
    member = venv.member(name="newcomer")
    message = reply(await agree(venv, member))
    assert message.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, member)
    assert has_member_role(venv, member)
    (event,) = await audit(venv, "verification.accepted")
    assert event.details["acceptances"] is None


async def test_failed_answer_still_records_the_acceptance(venv: TestEnv) -> None:
    member = venv.member(name="newcomer")
    interaction = FakeInteraction(client=venv.bot, user=member, guild=venv.guild)

    async def expired(*args: Any, **kwargs: Any) -> Any:
        raise http_error(discord.NotFound, 404, 10015, "Unknown Webhook")

    interaction.followup.send = expired  # type: ignore[method-assign]
    await venv.app.router.dispatch(interaction, AGREE_ID, kind="component")  # type: ignore[arg-type]
    assert has_member_role(venv, member)
    assert len(await audit(venv, "verification.accepted")) == 1
    record = await acceptance(venv, member.id)
    assert record is not None and record.acceptances == 1


# --- availability ---------------------------------------------------------------------------------------


async def test_not_ready_feature_answers_unavailable_and_grants_nothing(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path)
    try:
        values = dict(tenv.content)
        del values[k.CONFIRMED_DESCRIPTION]
        tenv.app.content.replace(file_values=values)
        tenv.app.recompute_readiness()
        member = tenv.member(name="newcomer")
        interaction = await agree(tenv, member)
        assert reply(interaction).embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)
        assert not has_member_role(tenv, member)
        assert await audit(tenv, "verification.accepted") == []
    finally:
        await tenv.close()


async def test_disabled_feature_answers_unavailable(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"features": {"verification": False}})
    try:
        member = tenv.member(name="newcomer")
        assert reply(await agree(tenv, member)).embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)
        assert not has_member_role(tenv, member)
    finally:
        await tenv.close()


async def test_direct_message_clicks_are_ignored(venv: TestEnv) -> None:
    member = venv.member(name="newcomer")
    interaction = await click(venv.app, user=member, custom_id=AGREE_ID, dm=True)
    assert interaction.sent == [] and not has_member_role(venv, member)


async def test_other_servers_are_ignored(venv: TestEnv) -> None:
    from tests.fakes import FakeGuild

    other = FakeGuild(id=999)
    stranger = other.add_member(name="stranger")
    interaction = await click(venv.app, user=stranger, custom_id=AGREE_ID, guild=other)
    assert interaction.sent == [] and not has_member_role(venv, stranger)


# --- restart ----------------------------------------------------------------------------------------------


async def test_button_keeps_working_after_a_restart(venv: TestEnv) -> None:
    await publish(venv)
    panel = panel_messages(venv)[0]
    (custom_id,) = panel.custom_ids()
    earlier = venv.member(name="earlier")
    await agree(venv, earlier, message=panel)
    await earlier.remove_roles(member_role(venv))

    app = restarted_app(venv)
    await app.resources.refresh()
    assert app.ready(k.VERIFICATION)
    newcomer = venv.member(name="newcomer")
    message = reply(await click(app, user=newcomer, custom_id=custom_id, message=panel))
    assert message.embeds[0].description == expected(venv, k.CONFIRMED_DESCRIPTION, newcomer)
    assert has_member_role(venv, newcomer)
    await click(app, user=earlier, custom_id=custom_id, message=panel)
    record = await acceptance(venv, earlier.id)
    assert record is not None and record.acceptances == 2  # history survives the restart


# --- direct message confirmation -------------------------------------------------------------------------------


async def test_dm_confirmation_is_sent_once_per_acceptance(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"dm_confirmation": True}})
    try:
        member = tenv.member(name="newcomer")
        await agree(tenv, member)
        await agree(tenv, member)  # already verified: no second DM
        (dm,) = member.dms
        assert dm.embeds[0].title == expected(tenv, k.DM_TITLE, member)
        assert dm.embeds[0].description == expected(tenv, k.DM_DESCRIPTION, member)
        guild_id = tenv.app.env.guild_id
        assert await tenv.app.db.run(notif_repo.already_sent, f"verification.dm:{guild_id}:{member.id}:1")
        await member.remove_roles(member_role(tenv))
        await agree(tenv, member)
        assert len(member.dms) == 2
    finally:
        await tenv.close()


async def test_closed_dms_are_audited_and_do_not_affect_the_verification(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"dm_confirmation": True}})
    try:
        member = tenv.member(name="private")
        member.dm_forbidden = True
        message = reply(await agree(tenv, member))
        assert message.embeds[0].description == expected(tenv, k.CONFIRMED_DESCRIPTION, member)
        assert has_member_role(tenv, member)
        (event,) = await audit(tenv, "verification.dm_failed")
        assert event.actor_id == member.id
        assert member.dms == []
        # No fallback post anywhere (the private confirmation already told the member).
        assert not any(member.mention in m.content for ch in tenv.guild.text_channels for m in ch.messages)
    finally:
        await tenv.close()


async def test_no_dm_when_direct_messages_are_switched_off(tmp_path: Path) -> None:
    extra = {"verification": {"dm_confirmation": True}, "features": {"dm_notifications": False}}
    tenv = await verification_env(tmp_path, extra)
    try:
        member = tenv.member(name="newcomer")
        reply(await agree(tenv, member))
        assert member.dms == [] and await audit(tenv, "verification.dm_failed") == []
    finally:
        await tenv.close()
