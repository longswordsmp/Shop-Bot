"""Banner pictures on /announce: the published announcement (banner first, then the staff image), the image cap
that leaves room for the banner, and the private replies to staff."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.announcements import service
from shopbot.features.announcements.preview import preview
from shopbot.ui.limits import FILES_TOTAL_BYTES
from shopbot.ui.spec import MessageSpec
from tests.announcements.util import (
    PNG,
    AnnounceEnv,
    Upload,
    announce_env,
    form_fields,
    invoke,
    open_form,
    reply_text,
    submit,
    token_of,
)
from tests.fakes import FakeInteraction, modal_of
from tests.helpers import banner_names


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[AnnounceEnv]:
    announce = await announce_env(tmp_path, banners=True)
    try:
        yield announce
    finally:
        await announce.close()


def banner_file(env: AnnounceEnv, banner_id: BannerId) -> str:
    banner = env.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def reply_banner(interaction: FakeInteraction) -> list[str]:
    assert interaction.last_sent is not None
    return banner_names(interaction.last_sent)


async def _set_status(env: AnnounceEnv, token: str, status: str, claimed_by: str | None) -> None:
    def update(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE announcements SET status = ?, claimed_by = ? WHERE token = ?", (status, claimed_by, token))

    await env.app.db.transaction(update)


# -- the announcement --------------------------------------------------------------------------------------------


async def test_announcement_shows_the_announcements_banner(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    interaction = await submit(env, staff, modal, form_fields(when="[when]"))
    (message,) = env.channel.messages
    assert [f.filename for f in message.files] == [banner_file(env, "announcements")]
    assert message.embeds[0].image.url is None  # the banner is never shown inside the embed
    assert reply_banner(interaction) == [banner_file(env, "successful-confirmation")]


async def test_staff_image_is_uploaded_after_the_banner(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    await submit(env, staff, modal, form_fields(when="[when]"))
    (message,) = env.channel.messages
    assert [f.filename for f in message.files] == [banner_file(env, "announcements"), "announcement.png"]
    assert message.files[1].fp.read() == PNG
    assert message.embeds[0].image.url == "attachment://announcement.png"


async def test_image_cap_leaves_room_for_the_banner_before_the_form_opens(
    env: AnnounceEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    banner = env.app.ui.banner("announcements")
    assert banner is not None
    monkeypatch.setattr(type(env.tenv.guild), "filesize_limit", 100 * 1_048_576, raising=False)  # boost level 3
    limit = FILES_TOTAL_BYTES - banner.size
    assert service.upload_limit(env.app) == limit
    too_big = await invoke(env, env.staff(), image=Upload(size=limit + 1))
    assert modal_of(too_big) is None  # refused before the form opens: nothing to type in vain
    expected = env.app.op(
        "announcements.error.image_too_large", size=service.megabytes(limit + 1), limit=service.megabytes(limit)
    )
    assert expected in reply_text(too_big)
    assert reply_banner(too_big) == [banner_file(env, "error")]
    assert modal_of(await invoke(env, env.staff(), image=Upload(size=limit))) is not None


async def test_the_banner_needs_attach_files_even_without_an_image(env: AnnounceEnv) -> None:
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=discord.PermissionOverwrite(attach_files=False))
    interaction = await invoke(env, env.staff())
    assert modal_of(interaction) is None
    assert "attach_files" in reply_text(interaction)


# -- the private replies -----------------------------------------------------------------------------------------


async def test_already_published_and_error_replies(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await submit(env, staff, modal, form_fields(when="[w]"))
    again = await submit(env, staff, modal, form_fields(when="[w]"))
    assert reply_banner(again) == [banner_file(env, "general-response")]
    other = await open_form(env, staff)
    invalid = await submit(env, staff, other, form_fields("", "[message]", when="[w]"))
    assert env.app.op("announcements.error.title_empty") in reply_text(invalid)
    assert reply_banner(invalid) == [banner_file(env, "error")]


async def test_form_notices_carry_their_banner(env: AnnounceEnv) -> None:
    staff = env.staff()
    intruder = await submit(env, env.staff("intruder"), await open_form(env, staff), form_fields(when="[w]"))
    assert reply_banner(intruder) == [banner_file(env, "error")]
    unknown = await submit(env, staff, "sb:an:submit:nothex", form_fields(when="[w]"))
    assert reply_banner(unknown) == [banner_file(env, "error")]
    busy_form = await open_form(env, staff)
    await _set_status(env, token_of(busy_form), "posting", service.PROCESS_TOKEN)
    busy = await submit(env, staff, busy_form, form_fields(when="[w]"))
    assert env.app.op("announcements.error.busy") in reply_text(busy)
    assert reply_banner(busy) == [banner_file(env, "submission-and-processing")]
    interrupted_form = await open_form(env, staff)
    await _set_status(env, token_of(interrupted_form), "interrupted", None)
    interrupted = await submit(env, staff, interrupted_form, form_fields(when="[w]"))
    assert reply_banner(interrupted) == [banner_file(env, "error")]
    expired_form = await open_form(env, staff)
    env.tenv.clock.advance(service.DRAFT_TTL_SECONDS)
    expired = await submit(env, staff, expired_form, form_fields(when="[w]"))
    assert env.app.op("announcements.error.expired", hours=service.DRAFT_TTL_SECONDS // 3600) in reply_text(expired)
    assert reply_banner(expired) == [banner_file(env, "cancellation")]
    assert env.channel.messages == []


async def test_disabled_feature_reply_shows_the_error_banner(env: AnnounceEnv) -> None:
    env.app.readiness.statuses["announcements"].ready = False
    interaction = await invoke(env, env.staff())
    assert modal_of(interaction) is None
    assert reply_banner(interaction) == [banner_file(env, "error")]


PREVIEW_BANNERS: dict[str, BannerId] = {
    "announcement": "announcements",
    "announcement_plain": "announcements",
    "published": "successful-confirmation",
    "published_plain": "successful-confirmation",
    "already_published": "general-response",
    "error_input": "error",
    "error_too_long": "error",
    "error_channel": "error",
    "error_discord": "error",
    "error_options": "error",
    "notice_busy": "submission-and-processing",
    "notice_expired": "cancellation",
    "notice_interrupted": "error",
    "notice_unknown": "error",
    "notice_not_owner": "error",
}


async def test_preview_states_show_the_same_banners(env: AnnounceEnv) -> None:
    specs = {state.name: state.spec for state in preview(env.app)}
    for name, banner_id in PREVIEW_BANNERS.items():
        spec = specs[name]
        assert isinstance(spec, MessageSpec), name
        assert spec.banner == env.app.ui.banner(banner_id), name
    limit = service.megabytes(service.upload_limit(env.app))  # the preview shows the cap staff really get
    options = specs["error_options"]
    assert isinstance(options, MessageSpec)
    expected = env.app.op("announcements.error.image_too_large", size="25.0", limit=limit)
    assert expected in (options.embeds[0].description or "")
