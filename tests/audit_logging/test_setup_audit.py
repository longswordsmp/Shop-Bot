"""#bot-log detail for /setup: the ``setup.run`` audit entry lists what changed (resource keys and change kinds) and the
Owner-role assignment, capped and sanitized; dry runs are not audited."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features.server_keys import category_name_key, channel_name_key
from shopbot.features.setup.report import MAX_AUDIT_CHANGES, SetupReport
from tests.audit_logging.util import audit_events, owner_env
from tests.fakes import FakeCategory, FakeTextChannel, http_error
from tests.helpers import OWNER_ID, TestEnv
from tests.setup.util import setup


def _channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    channel = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel), key
    return channel


def _category(tenv: TestEnv, key: str) -> FakeCategory:
    category = tenv.app.resources.category(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(category, FakeCategory), key
    return category


async def test_first_run_lists_created_resources_and_the_owner_role(tmp_path: Path) -> None:
    tenv = await owner_env(tmp_path)
    try:
        await setup(tenv)
        [event] = await audit_events(tenv.app, "setup.run")
        details = event.details
        server = tenv.app.settings.server
        assert details["categories"] == [f"{c.key}: created" for c in server.categories]
        assert details["channels"] == [f"{c.key}: created" for c in server.channels]
        # The pre-existing staff role was renamed; the owner role was created and given to OWNER_ID.
        assert details["roles"] == ["staff: updated (name)", "owner: created"]
        assert details["owner_role"] == {"role": "owner", "user_id": OWNER_ID, "result": "assigned"}
        assert details["created"] == len(server.categories) + len(server.channels) + 1
        # Only internal identifiers: no configured names reach the audit entry.
        for definition in server.channels:
            assert tenv.text(channel_name_key(definition.key)) not in str(details)
        for definition in server.categories:
            assert tenv.text(category_name_key(definition.key)) not in str(details)
        # The same detail is posted in the audit channel (created by this run), within Discord's limits.
        [posted] = _channel(tenv, "audit_log").messages
        text = posted.text()
        assert "support: created" in text and "owner: created" in text
        embed = posted.embeds[0]
        assert len(embed) <= 6000 and all(len(field.value or "") <= 1024 for field in embed.fields)
    finally:
        await tenv.close()


async def test_a_second_run_lists_no_changes(tmp_path: Path) -> None:
    tenv = await owner_env(tmp_path)
    try:
        await setup(tenv)
        await setup(tenv)
        first, second = await audit_events(tenv.app, "setup.run")
        assert "channels" in first.details
        for section in ("roles", "categories", "channels", "owner_role"):
            assert section not in second.details, section
        assert second.details["unchanged"] > 0 and second.details["created"] == 0
    finally:
        await tenv.close()


async def test_repairs_list_the_change_kinds(tenv: TestEnv) -> None:
    await setup(tenv)
    support = _category(tenv, "support")
    await support.set_permissions(tenv.guild.default_role, overwrite=discord.PermissionOverwrite(send_messages=True))
    shop = _channel(tenv, "shop")
    await shop.edit(name="drifted", topic="drifted topic")
    await setup(tenv)
    latest = (await audit_events(tenv.app, "setup.run"))[-1]
    assert latest.details["categories"] == ["support: updated (overwrites)"]
    assert latest.details["channels"] == ["shop: updated (name, topic)"]
    assert "roles" not in latest.details
    assert "drifted" not in str(latest.details)  # names and topics are never copied into the audit entry


async def test_adopted_resources_are_listed(tenv: TestEnv) -> None:
    tenv.guild.add_category(name=tenv.text(category_name_key("support")))
    await setup(tenv)
    [event] = await audit_events(tenv.app, "setup.run")
    # Adopted, then its permission overwrites were repaired: both are listed for the same key.
    assert "support: adopted; updated (overwrites)" in event.details["categories"]
    assert event.details["adopted"] == 1


async def test_failed_actions_are_listed_with_the_http_status(tenv: TestEnv) -> None:
    tenv.guild.fail_channel_create = http_error(discord.HTTPException, 500, 0, "Server Error")
    report = await setup(tenv)
    [event] = await audit_events(tenv.app, "setup.run")
    assert event.details["categories"] == [
        f"{c.key}: failed (create, HTTP 500)" for c in tenv.app.settings.server.categories
    ]
    assert event.details["failed"] == report.count_values()["failed"] > 0
    assert "Server Error" not in str(event.details)  # Discord's error text is not copied


async def test_a_failed_owner_role_assignment_is_audited(tmp_path: Path) -> None:
    tenv = await owner_env(tmp_path)
    try:
        owner = tenv.guild.get_member(OWNER_ID)
        assert owner is not None

        async def refuse(*roles: object, reason: str | None = None, atomic: bool = True) -> None:
            raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

        owner.add_roles = refuse  # type: ignore[method-assign]
        await setup(tenv)
        [event] = await audit_events(tenv.app, "setup.run")
        assert event.details["owner_role"] == {"role": "owner", "user_id": OWNER_ID, "result": "failed"}
        assert "owner: created; failed (assign, HTTP 403)" in event.details["roles"]
    finally:
        await tenv.close()


async def test_dry_runs_are_not_audited_but_their_plan_is_reported(tenv: TestEnv) -> None:
    report = await setup(tenv, dry_run=True)
    assert await audit_events(tenv.app, "setup.run") == []
    assert report.changes["channels"]  # the plan is computed (shown in the reply), never audited
    assert _messages_everywhere(tenv) == 0


def _messages_everywhere(tenv: TestEnv) -> int:
    return sum(len(channel.messages) for channel in tenv.guild.text_channels)


def test_audit_details_are_capped_per_section() -> None:
    report = SetupReport()
    for index in range(MAX_AUDIT_CHANGES + 7):
        report.change("channels", f"c{index}", "created")
    report.change("roles", "staff", "adopted")
    report.change("roles", "staff", "updated (permissions, color)")
    details = report.audit_details()
    assert len(details["channels"]) == MAX_AUDIT_CHANGES
    assert details["channels"][0] == "c0: created"
    assert details["channels_more"] == 7
    assert details["roles"] == ["staff: adopted; updated (permissions, color)"]
    assert "categories" not in details and "roles_more" not in details


async def test_resource_keys_are_never_masked_by_the_sanitizer(tenv: TestEnv) -> None:
    """Keys like "webhook_logs" match the sanitizer's sensitive-key pattern; entries are list items, so they stay
    readable (and a token-shaped value would still be redacted)."""
    report = SetupReport()
    report.change("channels", "webhook_logs", "created")
    await tenv.app.audit.record("setup.run", actor_id=1, details=report.audit_details())
    [event] = await audit_events(tenv.app, "setup.run")
    assert event.details["channels"] == ["webhook_logs: created"]
