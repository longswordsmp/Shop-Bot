"""/announce: metadata, permissions, readiness, option validation and the form it opens."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.features.announcements import service
from shopbot.features.announcements.commands import mention_autocomplete
from shopbot.features.announcements.content_keys import field_name_key
from tests.announcements.util import (
    AnnounceEnv,
    Upload,
    announce_command,
    announce_env,
    inputs_by_id,
    invoke,
    open_form,
    reply_text,
    reply_title,
    rows,
    token_of,
)
from tests.fakes import FakeInteraction, modal_of


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[AnnounceEnv]:
    announce = await announce_env(tmp_path)
    try:
        yield announce
    finally:
        await announce.close()


def test_command_metadata(env: AnnounceEnv) -> None:
    command = announce_command(env.app)
    assert command.default_permissions == discord.Permissions(manage_messages=True)
    assert command.guild_only is True
    assert command.description
    params = {p.name: p for p in command.parameters}
    assert set(params) == {"image", "mention"}
    assert all(not p.required and p.description for p in params.values())
    assert params["image"].type == discord.AppCommandOptionType.attachment
    assert params["mention"].autocomplete
    assert len(command.checks) == 1


async def test_customers_are_refused_and_nothing_is_stored(env: AnnounceEnv) -> None:
    interaction = await invoke(env, env.customer())
    assert modal_of(interaction) is None
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == env.text(ck.ERROR_PERMISSION)
    assert await rows(env) == []


@pytest.mark.parametrize("who", ["staff", "announcer", "admin", "owner"])
async def test_staff_announcer_role_and_admins_get_the_form(env: AnnounceEnv, who: str) -> None:
    if who == "staff":
        user = env.staff()
    elif who == "announcer":
        user = env.announcer()
    elif who == "admin":
        user = env.tenv.member(admin=True)
    else:
        from tests.helpers import OWNER_ID

        user = env.tenv.guild.add_member(id=OWNER_ID, name="owner")
    modal = await open_form(env, user)
    assert str(modal.custom_id).startswith("sb:an:submit:")
    assert service.TOKEN_RE.match(token_of(modal))
    stored = await rows(env)
    assert len(stored) == 1 and stored[0]["actor_id"] == user.id and stored[0]["status"] == "draft"


async def test_form_inputs_follow_the_settings(env: AnnounceEnv) -> None:
    modal = await open_form(env, env.staff())
    assert modal.title == env.app.op("announcements.form.title")
    inputs = inputs_by_id(modal)
    assert list(inputs) == ["title", "message", "field_when", "field_details"]
    assert inputs["title"].max_length == 256 and inputs["title"].required
    assert inputs["message"].max_length == 4000 and inputs["message"].style == discord.TextStyle.paragraph
    assert inputs["field_when"].required and inputs["field_when"].max_length == 100
    assert inputs["field_when"].style == discord.TextStyle.short
    assert not inputs["field_details"].required and inputs["field_details"].style == discord.TextStyle.paragraph
    labels = {item.text: item for item in modal.walk_children() if isinstance(item, discord.ui.Label)}
    # The configured field heading is used as the label when it fits (fixture headings are short).
    assert env.text(field_name_key("when")) in labels
    assert env.app.op("announcements.form.title_label") in labels


async def test_long_field_heading_falls_back_to_an_operator_label(tmp_path: Path) -> None:
    long_name = "[" + "h" * 60 + "]"
    env = await announce_env(tmp_path, content={field_name_key("when"): long_name})
    try:
        modal = await open_form(env, env.staff())
        labels = [item.text for item in modal.walk_children() if isinstance(item, discord.ui.Label)]
        assert long_name not in labels
        assert env.app.op("announcements.form.field_label", field_id="when") in labels
    finally:
        await env.close()


async def test_not_ready_feature_answers_with_operator_text(env: AnnounceEnv) -> None:
    env.app.readiness.statuses["announcements"].ready = False
    interaction = await invoke(env, env.staff())
    assert modal_of(interaction) is None
    assert reply_title(interaction) == env.app.op("command.feature_disabled", feature="announcements")
    assert await rows(env) == []


async def _error(env: AnnounceEnv, **kwargs: object) -> FakeInteraction:
    interaction = await invoke(env, env.staff(), **kwargs)
    assert modal_of(interaction) is None
    assert reply_title(interaction) == env.app.op("announcements.error.title")
    assert interaction.last_sent is not None and interaction.last_sent.custom_ids() == []  # no retry: no form yet
    assert await rows(env) == []
    return interaction


async def test_image_options_are_validated(env: AnnounceEnv) -> None:
    pdf = await _error(env, image=Upload(filename="menu.pdf", content_type="application/pdf"))
    assert env.app.op("announcements.error.image_type", filename="menu.pdf") in reply_text(pdf)
    huge = await _error(env, image=Upload(size=50 * 1_048_576))
    assert env.app.op("announcements.error.image_too_large", size="50.0", limit="10.0") in reply_text(huge)
    foreign = await _error(env, image=Upload(url="https://example.com/picture.png"))
    assert "picture.png" in reply_text(foreign)


async def test_image_type_from_the_file_name_when_discord_sends_none(env: AnnounceEnv) -> None:
    await open_form(env, env.staff(), image=Upload(filename="photo.JPG", content_type=None))
    stored = await rows(env)
    assert stored[0]["image_filename"] == "announcement.jpg" and stored[0]["image_content_type"] == "image/jpeg"


async def test_images_can_be_turned_off(tmp_path: Path) -> None:
    env = await announce_env(tmp_path, {"announcements": {"allow_image": False}})
    try:
        interaction = await _error(env, image=Upload())
        assert env.app.op("announcements.error.image_disabled") in reply_text(interaction)
        assert modal_of(await invoke(env, env.staff())) is not None  # without an image it still works
    finally:
        await env.close()


async def test_mention_options_are_validated(env: AnnounceEnv) -> None:
    unknown = await _error(env, mention="everyone")
    assert env.app.op("announcements.error.mention_not_allowed", role="everyone") in reply_text(unknown)
    quiet = await _error(env, mention="quiet")
    assert env.app.op(
        "announcements.error.mention_not_pingable", role=f"<@&{env.quiet_role.id}>", channel=env.channel.mention
    ) in reply_text(quiet)


async def test_mention_accepts_the_key_or_the_role_name(env: AnnounceEnv) -> None:
    await open_form(env, env.staff(), mention="alerts")
    await open_form(env, env.staff(), mention=env.alerts_role.name.upper())
    assert [r["mention_role"] for r in await rows(env)] == ["alerts", "alerts"]


async def test_no_mention_roles_configured(tmp_path: Path) -> None:
    env = await announce_env(tmp_path, {"announcements": {"mention_roles": []}})
    try:
        interaction = await _error(env, mention="alerts")
        assert env.app.op("announcements.error.mention_none") in reply_text(interaction)
    finally:
        await env.close()


async def test_unpingable_role_works_when_the_bot_may_mention_everyone(env: AnnounceEnv) -> None:
    env.tenv.guild.bot_role._permissions |= discord.Permissions(mention_everyone=True).value
    await open_form(env, env.staff(), mention="quiet")


async def test_missing_channel_is_reported_before_the_form_opens(env: AnnounceEnv) -> None:
    await env.app.resources.forget("channel", "announcements")
    interaction = await _error(env)
    assert env.app.op("announcements.error.channel_missing", channel="announcements") in reply_text(interaction)


async def test_missing_bot_permissions_are_reported(env: AnnounceEnv) -> None:
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=discord.PermissionOverwrite(attach_files=False))
    assert modal_of(await invoke(env, env.staff())) is not None  # posting text needs no attach permission
    interaction = await invoke(env, env.staff(), image=Upload())
    assert modal_of(interaction) is None
    assert "attach_files" in reply_text(interaction)
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=discord.PermissionOverwrite(send_messages=False))
    denied = await invoke(env, env.staff())
    assert modal_of(denied) is None and "send_messages" in reply_text(denied)


async def test_expired_forms_are_purged_when_a_new_one_opens(env: AnnounceEnv) -> None:
    await open_form(env, env.staff())
    env.tenv.clock.advance(service.DRAFT_TTL_SECONDS + 1)
    await open_form(env, env.staff())
    assert len(await rows(env)) == 1


async def test_autocomplete_suggests_existing_mention_roles_to_staff_only(env: AnnounceEnv) -> None:
    def interaction_for(user: object) -> FakeInteraction:
        return FakeInteraction(
            client=env.app.bot,
            user=user,
            guild=env.tenv.guild,
            type=discord.InteractionType.autocomplete,
        )

    staff_choices = await mention_autocomplete(interaction_for(env.staff()), "")  # type: ignore[arg-type]
    assert [(c.name, c.value) for c in staff_choices] == [
        (env.alerts_role.name, "alerts"),
        (env.quiet_role.name, "quiet"),
    ]
    filtered = await mention_autocomplete(interaction_for(env.staff()), "ALERT")  # type: ignore[arg-type]
    assert [c.value for c in filtered] == ["alerts"]
    assert await mention_autocomplete(interaction_for(env.customer()), "") == []  # type: ignore[arg-type]
    await env.app.resources.forget("role", "quiet")
    remaining = await mention_autocomplete(interaction_for(env.staff()), "")  # type: ignore[arg-type]
    assert [c.value for c in remaining] == ["alerts"]


async def test_boosted_servers_are_still_capped_at_the_per_message_file_limit(
    env: AnnounceEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shopbot.ui.limits import FILES_TOTAL_BYTES

    monkeypatch.setattr(type(env.tenv.guild), "filesize_limit", 100 * 1_048_576, raising=False)  # boost level 3
    assert service.upload_limit(env.app) == FILES_TOTAL_BYTES
    big = await _error(env, image=Upload(size=FILES_TOTAL_BYTES + 1))
    assert env.app.op("announcements.error.image_too_large", size="10.0", limit="10.0") in reply_text(big)
