"""Form submission: posting, fields, image, mentions, validation, Discord failures, duplicates, restart, audit."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import discord
import pytest

from shopbot.core.clock import to_datetime
from shopbot.features import core_keys as ck
from shopbot.features.announcements import repo, service
from shopbot.features.announcements.content_keys import field_name_key
from tests.announcements.util import (
    CDN_URL,
    JPEG,
    PNG,
    AnnounceEnv,
    Upload,
    announce_env,
    audit_events,
    button_ids,
    form_fields,
    inputs_by_id,
    link_urls,
    open_form,
    reply_text,
    reply_title,
    restarted_app,
    row,
    rows,
    submit,
    token_of,
)
from tests.fakes import click, http_error, modal_of
from tests.helpers import GUILD_ID


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[AnnounceEnv]:
    announce = await announce_env(tmp_path)
    try:
        yield announce
    finally:
        await announce.close()


def _published_title(env: AnnounceEnv) -> str:
    return env.app.op("announcements.done.title")


def _error_title(env: AnnounceEnv) -> str:
    return env.app.op("announcements.error.title")


# --------------------------------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------------------------------


async def test_publishes_one_formatted_embed(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    interaction = await submit(
        env, staff, modal, form_fields("  [big news]  ", "[line 1]\n**[line 2]**", when="[friday]", details="")
    )
    assert len(env.channel.messages) == 1
    message = env.channel.messages[0]
    embed = message.embeds[0]
    assert embed.title == "[big news]"  # surrounding whitespace removed, the rest verbatim
    assert embed.description == "[line 1]\n**[line 2]**"
    assert [(f.name, f.value, f.inline) for f in embed.fields] == [
        (env.text(field_name_key("when")), "[friday]", False)
    ]
    assert embed.footer.text == env.text(ck.FOOTER_TEXT)
    assert embed.author.name == env.text(ck.AUTHOR_NAME)
    assert embed.color is not None and embed.color.value == env.app.settings.branding.colors["primary"]
    assert embed.timestamp == to_datetime(env.tenv.clock.now())
    assert embed.image.url is None
    assert message.content == "" and message.files == []
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False and mentions.roles is False and mentions.users is False
    # private operator confirmation with a link to the post
    assert interaction.response_kinds() == ["defer"]
    assert interaction.last_sent is not None and interaction.last_sent.flags.ephemeral
    assert reply_title(interaction) == _published_title(env)
    assert message.jump_url in reply_text(interaction)
    assert link_urls(interaction) == [message.jump_url]
    stored = await row(env, token_of(modal))
    assert stored.status == "published" and stored.message_id == message.id and stored.channel_id == env.channel.id
    assert stored.title is None and stored.message is None and stored.field_values == {}  # text not kept


async def test_audit_records_ids_and_lengths_but_not_the_text(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, mention="alerts")
    await submit(env, staff, modal, form_fields("[secret title]", "[secret message]", when="[when]"))
    events = await audit_events(env)
    assert [e.action for e in events] == ["announcements.published"]
    event = events[0]
    message = env.channel.messages[0]
    assert event.actor_id == staff.id and event.target_type == "message" and event.target_id == str(message.id)
    assert event.details["channel_id"] == env.channel.id
    assert event.details["title_length"] == len("[secret title]")
    assert event.details["fields"] == ["when"] and event.details["mention_role"] == "alerts"
    assert event.details["image"] is False
    assert "[secret" not in str(event.details)


async def test_fields_follow_settings_order_and_empty_optional_fields_are_left_out(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await submit(env, staff, modal, form_fields(details="[details]", when="[when]"))
    names = [f.name for f in env.channel.messages[0].embeds[0].fields]
    assert names == [env.text(field_name_key("when")), env.text(field_name_key("details"))]


async def test_without_configured_fields(tmp_path: Path) -> None:
    env = await announce_env(tmp_path, {"announcements": {"fields": []}})
    try:
        staff = env.staff()
        modal = await open_form(env, staff)
        assert list(inputs_by_id(modal)) == ["title", "message"]
        await submit(env, staff, modal, form_fields(when="[ignored: not configured]"))
        assert env.channel.messages[0].embeds[0].fields == []
    finally:
        await env.close()


async def test_announcer_role_can_publish(env: AnnounceEnv) -> None:
    announcer = env.announcer()
    modal = await open_form(env, announcer)
    interaction = await submit(env, announcer, modal, form_fields(when="[when]"))
    assert reply_title(interaction) == _published_title(env)
    assert len(env.channel.messages) == 1


# --------------------------------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------------------------------


async def test_image_is_attached_to_the_message(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    interaction = await submit(env, staff, modal, form_fields(when="[when]"))
    message = env.channel.messages[0]
    assert env.cdn.requests == [CDN_URL]
    assert [f.filename for f in message.files] == ["announcement.png"]
    assert message.files[0].fp.read() == PNG
    assert message.embeds[0].image.url == "attachment://announcement.png"
    assert env.app.op("announcements.done.image") in reply_text(interaction)
    assert (await audit_events(env))[0].details["image"] is True


async def test_image_format_is_detected_from_the_file(env: AnnounceEnv) -> None:
    env.cdn.files[CDN_URL] = JPEG  # declared as png, really a jpeg
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    await submit(env, staff, modal, form_fields(when="[when]"))
    message = env.channel.messages[0]
    assert [f.filename for f in message.files] == ["announcement.jpg"]
    assert message.embeds[0].image.url == "attachment://announcement.jpg"


async def test_not_an_image_is_refused(env: AnnounceEnv) -> None:
    env.cdn.files[CDN_URL] = b"%PDF-1.7 not an image"
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    interaction = await submit(env, staff, modal, form_fields(when="[when]"))
    assert env.channel.messages == []
    assert reply_title(interaction) == _error_title(env)
    assert button_ids(interaction) == []  # a different image is needed: run /announce again


@pytest.mark.parametrize(
    "failure",
    [
        http_error(discord.NotFound, 404, 0, "asset not found"),
        http_error(discord.Forbidden, 403, 0, "cannot retrieve asset"),
        TimeoutError(),
    ],
)
async def test_image_download_failure_posts_nothing(env: AnnounceEnv, failure: BaseException) -> None:
    env.cdn.fail = failure
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    interaction = await submit(env, staff, modal, form_fields(when="[when]"))
    assert env.channel.messages == []
    assert env.app.op("announcements.error.image_download") in reply_text(interaction)
    assert button_ids(interaction) == [f"sb:an:retry:{token_of(modal)}"]
    assert (await row(env, token_of(modal))).status == "draft"
    env.cdn.fail = None
    retried = await submit(env, staff, modal, form_fields(when="[when]"))
    assert reply_title(retried) == _published_title(env)
    assert len(env.channel.messages) == 1


async def test_image_disabled_after_the_form_opened(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload())
    settings = env.app.settings
    env.app.settings = replace(settings, announcements=replace(settings.announcements, allow_image=False))
    interaction = await submit(env, staff, modal, form_fields(when="[when]"))
    assert env.channel.messages == []
    assert env.app.op("announcements.error.image_disabled") in reply_text(interaction)
    assert button_ids(interaction) == []


# --------------------------------------------------------------------------------------------------
# Mentions
# --------------------------------------------------------------------------------------------------


async def test_chosen_role_is_mentioned_and_nothing_else(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, mention="alerts")
    interaction = await submit(env, staff, modal, form_fields("[t] @everyone", "[m] <@&123> @here", when="[w]"))
    message = env.channel.messages[0]
    assert message.content == f"<@&{env.alerts_role.id}>"
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False and mentions.users is False
    assert isinstance(mentions.roles, list) and [r.id for r in mentions.roles] == [env.alerts_role.id]
    assert env.app.op("announcements.done.mention", role=f"<@&{env.alerts_role.id}>") in reply_text(interaction)


async def test_mention_role_that_became_unpingable_posts_nothing(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, mention="alerts")
    env.alerts_role.mentionable = False
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert "<@&" in reply_text(interaction) and button_ids(interaction) == [f"sb:an:retry:{token_of(modal)}"]
    env.alerts_role.mentionable = True
    retry = await click(env.app, user=staff, custom_id=button_ids(interaction)[0])
    reopened = modal_of(retry)
    assert reopened is not None and reopened.custom_id == modal.custom_id
    await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages[0].content == f"<@&{env.alerts_role.id}>"


async def test_mention_role_removed_from_settings_after_the_form_opened(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, mention="alerts")
    settings = env.app.settings
    env.app.settings = replace(settings, announcements=replace(settings.announcements, mention_roles=("quiet",)))
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert env.app.op("announcements.error.mention_not_allowed", role="alerts") in reply_text(interaction)
    assert button_ids(interaction) == []


# --------------------------------------------------------------------------------------------------
# Invalid input (nothing posted, the form can be reopened prefilled)
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected_key"),
    [
        (form_fields("   ", "[m]", when="[w]"), "announcements.error.title_empty"),
        (form_fields("[t]", " \n ", when="[w]"), "announcements.error.message_empty"),
        (form_fields("[t]", "[m]", when="   "), "announcements.error.field_required"),
        (form_fields("[t]", "[m]"), "announcements.error.field_required"),
        (form_fields("x" * 257, "[m]", when="[w]"), "announcements.error.title_too_long"),
        (form_fields("[t]", "x" * 4001, when="[w]"), "announcements.error.message_too_long"),
        (form_fields("[t]", "[m]", when="x" * 101), "announcements.error.field_too_long"),
    ],
)
async def test_invalid_input_posts_nothing(env: AnnounceEnv, fields: dict[str, str], expected_key: str) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    interaction = await submit(env, staff, modal, fields)
    assert env.channel.messages == []
    assert reply_title(interaction) == _error_title(env)
    template = env.app.operator_defaults[expected_key].split("{", 1)[0]
    assert template in reply_text(interaction)
    assert button_ids(interaction) == [f"sb:an:retry:{token_of(modal)}"]
    stored = await row(env, token_of(modal))
    assert stored.status == "draft" and stored.message_id is None
    assert await audit_events(env) == []  # input problems are not audited


async def test_total_embed_length_over_6000_posts_nothing(tmp_path: Path) -> None:
    three_long_fields = [{"id": name, "style": "paragraph", "max_length": 1024} for name in ("a", "b", "c")]
    env = await announce_env(tmp_path, {"announcements": {"fields": three_long_fields}})
    try:
        staff = env.staff()
        modal = await open_form(env, staff)
        long_values = {name: name * 1024 for name in ("a", "b", "c")}
        interaction = await submit(env, staff, modal, form_fields("t" * 256, "m" * 4000, **long_values))
        assert env.channel.messages == []
        assert reply_title(interaction) == _error_title(env)
        assert "6000" in reply_text(interaction)
        assert button_ids(interaction) == [f"sb:an:retry:{token_of(modal)}"]
        shorter = await submit(env, staff, modal, form_fields("t" * 256, "m" * 1000, **long_values))
        assert reply_title(shorter) == _published_title(env)
    finally:
        await env.close()


async def test_retry_reopens_the_form_prefilled_and_then_publishes(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    failed = await submit(env, staff, modal, form_fields("[t]", "[kept message]", details="[kept details]"))
    retry = await click(env.app, user=staff, custom_id=button_ids(failed)[0])
    reopened = modal_of(retry)
    assert reopened is not None
    inputs = inputs_by_id(reopened)
    assert inputs["title"].default == "[t]" and inputs["message"].default == "[kept message]"
    assert inputs["field_details"].default == "[kept details]" and not inputs["field_when"].default
    done = await submit(env, staff, reopened, form_fields("[t]", "[kept message]", when="[w]", details="[d]"))
    assert reply_title(done) == _published_title(env)
    assert len(env.channel.messages) == 1


async def test_retry_refused_for_other_users_and_after_publishing(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    failed = await submit(env, staff, modal, form_fields("[t]", "[m]"))
    retry_id = button_ids(failed)[0]
    customer_click = await click(env.app, user=env.customer(), custom_id=retry_id)
    assert modal_of(customer_click) is None
    assert customer_click.last_sent is not None
    assert customer_click.last_sent.embeds[0].description == env.text(ck.ERROR_PERMISSION)
    other_click = await click(env.app, user=env.staff("other"), custom_id=retry_id)
    assert modal_of(other_click) is None
    assert env.app.op("announcements.error.not_owner") in reply_text(other_click)
    await submit(env, staff, modal, form_fields(when="[w]"))
    late = await click(env.app, user=staff, custom_id=retry_id)
    assert modal_of(late) is None
    message = env.channel.messages[0]
    assert f"{GUILD_ID}/{env.channel.id}/{message.id}" in reply_text(late)


# --------------------------------------------------------------------------------------------------
# Channel and Discord failures
# --------------------------------------------------------------------------------------------------


async def test_channel_deleted_after_the_form_opened(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await env.channel.delete()
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert reply_title(interaction) == _error_title(env)
    assert env.app.op("announcements.error.channel_missing", channel="announcements") in reply_text(interaction)
    assert button_ids(interaction) == [f"sb:an:retry:{token_of(modal)}"]
    assert [e.action for e in await audit_events(env)] == ["announcements.failed"]
    assert (await audit_events(env))[0].details == {"problems": ["channel_missing"]}


async def test_missing_permission_at_submit(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=discord.PermissionOverwrite(embed_links=False))
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert "embed_links" in reply_text(interaction)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (http_error(discord.Forbidden, 403, 50013, "Missing Permissions"), "announcements.error.forbidden"),
        (http_error(discord.HTTPException, 500, 0, "Internal"), "announcements.error.send_failed"),
    ],
)
async def test_discord_refusal_posts_nothing_and_never_leaks_internals(
    env: AnnounceEnv, failure: discord.HTTPException, expected: str
) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    env.channel.fail_send = failure
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert reply_title(interaction) == _error_title(env)
    text = reply_text(interaction)
    assert env.app.operator_defaults[expected].split("{", 1)[0] in text
    assert "Traceback" not in text and "Missing Permissions" not in text
    assert (await row(env, token_of(modal))).status == "draft"
    env.channel.fail_send = None
    assert reply_title(await submit(env, staff, modal, form_fields(when="[w]"))) == _published_title(env)
    assert len(env.channel.messages) == 1


async def test_unexpected_error_releases_the_form(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    env.channel.fail_send = RuntimeError("boom")
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert interaction.last_sent is not None and "boom" not in interaction.last_sent.text()
    assert (await row(env, token_of(modal))).status == "draft"


# --------------------------------------------------------------------------------------------------
# Duplicates, ownership, expiry, permissions
# --------------------------------------------------------------------------------------------------


async def test_second_submission_of_the_same_form_does_not_post_again(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    first = await submit(env, staff, modal, form_fields(when="[w]"))
    second = await submit(env, staff, modal, form_fields("[other]", "[other]", when="[w]"))
    assert len(env.channel.messages) == 1
    assert reply_title(first) == _published_title(env)
    message = env.channel.messages[0]
    assert f"{GUILD_ID}/{env.channel.id}/{message.id}" in reply_text(second)
    assert link_urls(second) == [f"https://discord.com/channels/{GUILD_ID}/{env.channel.id}/{message.id}"]
    assert len(await audit_events(env)) == 1


async def test_concurrent_clicks_post_once(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await asyncio.gather(
        submit(env, staff, modal, form_fields(when="[w]")),
        submit(env, staff, modal, form_fields("[t2]", "[m2]", when="[w]")),
        submit(env, staff, modal, form_fields("[t3]", "[m3]", when="[w]")),
    )
    assert len(env.channel.messages) == 1
    assert len([e for e in await audit_events(env) if e.action == "announcements.published"]) == 1


async def test_concurrent_claims_in_the_database_post_once(env: AnnounceEnv) -> None:
    """Bypasses the router's in-flight guard: only the atomic claim protects against duplicates here."""
    staff = env.staff()
    modal = await open_form(env, staff)
    replies = await asyncio.gather(
        *(
            service.submit(env.app, token=token_of(modal), actor_id=staff.id, fields=form_fields(when=f"[w{i}]"))
            for i in range(4)
        )
    )
    assert len(env.channel.messages) == 1
    titles = [r.title for r in replies]
    assert titles.count(_published_title(env)) >= 1
    assert sum(1 for r in replies if r.tone == "success") == 1


async def test_form_of_another_staff_member_is_refused(env: AnnounceEnv) -> None:
    modal = await open_form(env, env.staff("author"))
    interaction = await submit(env, env.staff("intruder"), modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert env.app.op("announcements.error.not_owner") in reply_text(interaction)


async def test_forged_submissions_from_customers_are_refused(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    customer = env.customer()
    interaction = await submit(env, customer, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == env.text(ck.ERROR_PERMISSION)
    assert (await row(env, token_of(modal))).status == "draft"


async def test_staff_who_lost_the_role_cannot_submit(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    await staff.remove_roles(env.tenv.staff_role)
    await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []


@pytest.mark.parametrize("custom_id", ["sb:an:submit:nothex", "sb:an:submit:0123456789abcdef01234567", "sb:an:submit"])
async def test_unknown_or_malformed_forms(env: AnnounceEnv, custom_id: str) -> None:
    interaction = await submit(env, env.staff(), custom_id, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert env.app.op("announcements.error.form_unknown") in reply_text(interaction)


async def test_expired_form_posts_nothing(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    env.tenv.clock.advance(service.DRAFT_TTL_SECONDS)
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    hours = service.DRAFT_TTL_SECONDS // 3600
    assert env.app.op("announcements.error.expired", hours=hours) in reply_text(interaction)


async def test_not_ready_feature_refuses_submissions(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    env.app.readiness.statuses["announcements"].ready = False
    interaction = await submit(env, staff, modal, form_fields(when="[w]"))
    assert env.channel.messages == []
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == env.text(ck.ERROR_UNAVAILABLE)


# --------------------------------------------------------------------------------------------------
# Restart
# --------------------------------------------------------------------------------------------------


async def test_form_opened_before_a_restart_is_published_after_it(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff, image=Upload(), mention="alerts")
    app = restarted_app(env)
    await app.resources.refresh()
    interaction = await submit(env, staff, modal, form_fields(when="[w]"), app=app)
    assert reply_title(interaction) == _published_title(env)
    message = env.channel.messages[0]
    assert message.content == f"<@&{env.alerts_role.id}>"
    assert [f.filename for f in message.files] == ["announcement.png"]


async def test_retry_button_works_after_a_restart(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    failed = await submit(env, staff, modal, form_fields("[t]", "[m]"))
    app = restarted_app(env)
    await app.resources.refresh()
    retry = await click(app, user=staff, custom_id=button_ids(failed)[0])
    reopened = modal_of(retry)
    assert reopened is not None and inputs_by_id(reopened)["message"].default == "[m]"


async def test_recovery_marks_interrupted_posts_and_never_reposts(env: AnnounceEnv) -> None:
    staff = env.staff()
    modal = await open_form(env, staff)
    token = token_of(modal)

    def claim_by_old_process(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE announcements SET status = 'posting', claimed_by = 'previous-process' WHERE token = ?", (token,)
        )

    await env.app.db.transaction(claim_by_old_process)
    app = restarted_app(env)
    await app.resources.refresh()
    await service.recover(app)
    stored = await row(env, token)
    assert stored.status == "interrupted" and stored.title is None
    assert [e.action for e in await audit_events(env)] == ["announcements.interrupted"]
    interaction = await submit(env, staff, modal, form_fields(when="[w]"), app=app)
    assert env.channel.messages == []
    assert env.app.op("announcements.error.interrupted", channel=env.channel.mention) in reply_text(interaction)
    # a submission this process is still posting is left alone
    other = await open_form(env, staff)

    def claim_by_this_process(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE announcements SET status = 'posting', claimed_by = ? WHERE token = ?",
            (service.PROCESS_TOKEN, token_of(other)),
        )

    await env.app.db.transaction(claim_by_this_process)
    await service.recover(app)
    assert (await row(env, token_of(other))).status == "posting"


async def test_recovery_purges_expired_unpublished_forms(env: AnnounceEnv) -> None:
    staff = env.staff()
    published = await open_form(env, staff)
    await submit(env, staff, published, form_fields(when="[w]"))
    await open_form(env, staff)
    env.tenv.clock.advance(service.DRAFT_TTL_SECONDS)
    await service.recover(env.app)
    remaining = await rows(env)
    assert [(r["token"], r["status"]) for r in remaining] == [(token_of(published), "published")]


async def test_repository_rejects_impossible_rows(env: AnnounceEnv) -> None:
    def insert_published_without_message(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO announcements (guild_id, token, actor_id, status, created_at, expires_at, updated_at) "
            "VALUES (?, ?, 1, 'published', 1, 2, 1)",
            (GUILD_ID, "f" * 24),
        )

    with pytest.raises(sqlite3.IntegrityError):
        await env.app.db.transaction(insert_published_without_message)
    created = await env.app.db.transaction(
        repo.create_draft, GUILD_ID, token="a" * 24, actor_id=1, mention_role=None, image=None, now=1, ttl_seconds=5
    )

    def duplicate_token(conn: sqlite3.Connection) -> None:
        repo.create_draft(
            conn, GUILD_ID, token=created.token, actor_id=2, mention_role=None, image=None, now=1, ttl_seconds=5
        )

    with pytest.raises(sqlite3.IntegrityError):
        await env.app.db.transaction(duplicate_token)
