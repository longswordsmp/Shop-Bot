from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.channels import BOT_ALLOW
from shopbot.features.server_keys import category_name_key, channel_name_key, channel_topic_key, role_name_key
from shopbot.features.setup import overwrites as ow
from shopbot.features.setup.listeners import on_channel_delete, on_ready
from tests.fakes import FakeCategory, FakeTextChannel, http_error, next_id
from tests.helpers import TestEnv, make_settings, make_test_env
from tests.setup.util import all_lines, audit_events, restarted_app, setup, shipped_server


def _category(tenv: TestEnv, key: str) -> FakeCategory:
    category = tenv.app.resources.category(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(category, FakeCategory), key
    return category


def _channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    channel = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel), key
    return channel


def _by_id(overwrites: dict[Any, discord.PermissionOverwrite]) -> dict[int, discord.PermissionOverwrite]:
    return {target.id: value for target, value in overwrites.items()}


def _snapshot(tenv: TestEnv) -> dict[str, Any]:
    guild = tenv.guild
    audit_id = tenv.app.resources.channel_id("audit_log")
    return {
        "roles": sorted(r.id for r in guild.roles),
        "channels": sorted(c.id for c in guild.channels),
        "creates": len(guild.audit),
        "channel_edits": sum(len(c.edits) + len(c.permission_edits) for c in guild.channels),
        "role_edits": sum(len(r.edits) for r in guild.roles),
        # Panel messages (the audit channel receives one entry per real run, checked separately).
        "messages": sum(len(c.messages) for c in guild.text_channels if c.id != audit_id),
    }


async def _env(tmp_path: Path, server: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    overrides: dict[str, Any] = kwargs.pop("overrides", {})
    if server is not None:
        overrides = {**overrides, "server": server}
    return await make_test_env(tmp_path, settings=make_settings(overrides), **kwargs)


# --- creation ------------------------------------------------------------------------------------


async def test_first_run_creates_everything_with_configured_names(tenv: TestEnv) -> None:
    report = await setup(tenv, publish=False)
    app, guild = tenv.app, tenv.guild

    assert tenv.staff_role.name == tenv.text(role_name_key("staff"))
    for category_def in app.settings.server.categories:
        assert _category(tenv, category_def.key).name == tenv.text(category_name_key(category_def.key))
    for channel_def in app.settings.server.channels:
        channel = _channel(tenv, channel_def.key)
        assert channel.name == tenv.text(channel_name_key(channel_def.key))
        assert channel.topic == tenv.text(channel_topic_key(channel_def.key))
        assert channel.category_id == _category(tenv, channel_def.category).id
    assert len(guild.categories) == len(app.settings.server.categories)
    assert len(guild.text_channels) == len(app.settings.server.channels)
    counts = report.count_values()
    assert counts["created"] == len(app.settings.server.categories) + len(app.settings.server.channels)
    assert counts["updated"] == 1 and counts["failed"] == 0  # the pre-existing staff role was renamed


async def test_overwrites_follow_access_levels(tenv: TestEnv) -> None:
    await setup(tenv)
    everyone_id, staff_id, bot_id = tenv.guild.default_role.id, tenv.staff_role.id, tenv.guild.me.id
    bot_allow = discord.PermissionOverwrite(**BOT_ALLOW)

    support = _by_id(_category(tenv, "support").overwrites)  # public_readonly
    assert support[everyone_id] == discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        add_reactions=False,
        create_public_threads=False,
        create_private_threads=False,
    )
    assert support[staff_id] == discord.PermissionOverwrite(send_messages=True)
    assert support[bot_id] == bot_allow

    tickets = _by_id(_category(tenv, "tickets").overwrites)  # staff
    assert tickets[everyone_id] == discord.PermissionOverwrite(view_channel=False)
    assert tickets[staff_id] == discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True
    )
    assert tickets[bot_id] == bot_allow

    admin = _by_id(_category(tenv, "admin").overwrites)  # admin: staff has no access
    assert admin[everyone_id] == discord.PermissionOverwrite(view_channel=False)
    assert staff_id not in admin and admin[bot_id] == bot_allow

    # "inherit" channels carry exactly their category's overwrites (synced).
    for channel_def in tenv.app.settings.server.channels:
        channel = _channel(tenv, channel_def.key)
        assert ow.same(channel.overwrites, _category(tenv, channel_def.category).overwrites)
        assert channel.permissions_synced

    # Effective permissions: a customer can read the shop but not post; staff can post; staff
    # cannot see the admin channel.
    customer = tenv.member()
    staff = tenv.member(staff=True)
    shop = _channel(tenv, "shop")
    assert shop.permissions_for(customer).view_channel and not shop.permissions_for(customer).send_messages
    assert shop.permissions_for(staff).send_messages
    assert not _channel(tenv, "audit_log").permissions_for(staff).view_channel
    assert not _channel(tenv, "staff_alerts").permissions_for(customer).view_channel
    assert _channel(tenv, "staff_alerts").permissions_for(staff).view_channel


async def test_second_run_changes_nothing(tenv: TestEnv) -> None:
    await setup(tenv)
    before = _snapshot(tenv)
    report = await setup(tenv)
    after = _snapshot(tenv)
    assert after["roles"] == before["roles"]
    assert after["channels"] == before["channels"]
    assert after["creates"] == before["creates"]
    assert after["channel_edits"] == before["channel_edits"]
    assert after["role_edits"] == before["role_edits"]
    assert after["messages"] == before["messages"]
    counts = report.count_values()
    assert counts["created"] == counts["updated"] == counts["adopted"] == counts["failed"] == 0
    assert counts["unchanged"] == 1 + len(tenv.app.settings.server.categories) + len(tenv.app.settings.server.channels)


async def test_restart_uses_recorded_ids(tenv: TestEnv) -> None:
    await setup(tenv)
    before = _snapshot(tenv)
    app = restarted_app(tenv)
    await app.resources.refresh()
    await on_ready(app)
    report = await setup(tenv, app=app)
    after = _snapshot(tenv)
    assert after["channels"] == before["channels"] and after["creates"] == before["creates"]
    assert after["channel_edits"] == before["channel_edits"]
    assert report.count_values()["created"] == 0


async def test_audit_records_setup_run_with_counts(tenv: TestEnv) -> None:
    report = await setup(tenv)
    events = await audit_events(tenv, "setup.run")
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == tenv.app.env.owner_id
    assert event.details["created"] == report.count_values()["created"]
    assert event.details["dry_run"] is False
    # The audit channel was created by this run, so the entry is also posted there.
    assert _channel(tenv, "audit_log").messages


# --- dry run --------------------------------------------------------------------------------------


async def test_dry_run_changes_nothing(tenv: TestEnv) -> None:
    before = _snapshot(tenv)
    report = await setup(tenv, dry_run=True)
    after = _snapshot(tenv)
    assert after == before
    assert tenv.staff_role.name == "staff"
    assert tenv.app.resources.category_id("support") is None
    assert await audit_events(tenv, "setup.run") == []
    text = all_lines(report)
    assert "would create" in text and "would update" in text
    assert tenv.app.op("server.setup.publish_skipped_dry_run") in report.lines["panels"]
    assert report.count_values()["created"] == len(tenv.app.settings.server.categories) + len(
        tenv.app.settings.server.channels
    )


async def test_dry_run_reports_planned_repairs_without_applying_them(tenv: TestEnv) -> None:
    await setup(tenv)
    support = _category(tenv, "support")
    await support.set_permissions(tenv.guild.default_role, overwrite=discord.PermissionOverwrite(send_messages=True))
    shop = _channel(tenv, "shop")
    await shop.edit(name="drifted")
    before = _snapshot(tenv)
    report = await setup(tenv, dry_run=True)
    assert _snapshot(tenv) == before
    assert shop.name == "drifted"
    assert any("`support`: would update" in line for line in report.lines["categories"])
    assert any("`shop`: would update" in line for line in report.lines["channels"])
    # Channels already match the category as it will be after the repair.
    assert any("`catalog`: up to date" in line for line in report.lines["channels"])


# --- adoption and re-creation ----------------------------------------------------------------------


async def test_adopts_existing_objects_by_name_instead_of_duplicating(tmp_path: Path) -> None:
    server = shipped_server()
    server["roles"].append({"key": "vip", "id": 0})
    tenv = await _env(tmp_path, server)
    try:
        guild = tenv.guild
        vip = guild.add_role(name=tenv.text(role_name_key("vip")), position=4)
        support = guild.add_category(name=tenv.text(category_name_key("support")).upper())
        shop = guild.add_text_channel(name=tenv.text(channel_name_key("shop")).upper(), category=support)
        # Same name outside the configured category: not adopted.
        outside = guild.add_text_channel(name=tenv.text(channel_name_key("catalog")))
        report = await setup(tenv)
        assert tenv.app.resources.role_id("vip") == vip.id
        assert tenv.app.resources.category_id("support") == support.id
        assert tenv.app.resources.channel_id("shop") == shop.id
        assert tenv.app.resources.channel_id("catalog") != outside.id
        assert len([r for r in guild.roles if r.name == vip.name]) == 1
        assert report.count_values()["adopted"] == 3
        assert any("`support`: adopted existing" in line for line in report.lines["categories"])
        # Adopted objects are brought in line: the category name casing and overwrites are repaired.
        assert support.name == tenv.text(category_name_key("support"))
        assert ow.same(shop.overwrites, support.overwrites)
    finally:
        await tenv.close()


async def test_deleted_objects_are_recreated(tenv: TestEnv) -> None:
    await setup(tenv)
    old_channel = _channel(tenv, "shop")
    old_category = _category(tenv, "staff")
    await old_channel.delete()
    await old_category.delete()
    report = await setup(tenv)
    new_channel = _channel(tenv, "shop")
    new_category = _category(tenv, "staff")
    assert new_channel.id != old_channel.id and new_category.id != old_category.id
    assert any("re-created" in line for line in report.lines["channels"])
    assert any("re-created" in line for line in report.lines["categories"])
    # Channels of the deleted category are moved into its replacement and re-synced.
    alerts = _channel(tenv, "staff_alerts")
    assert alerts.category_id == new_category.id
    assert ow.same(alerts.overwrites, new_category.overwrites)


async def test_explicit_ids_are_used_and_missing_ones_reported(tmp_path: Path) -> None:
    server = shipped_server()
    shop_id = next_id()
    server["channels"] = [
        {**c, "id": shop_id} if c["key"] == "shop" else ({**c, "id": 999} if c["key"] == "reviews" else c)
        for c in server["channels"]
    ]
    tenv = await _env(tmp_path, server)
    try:
        existing = tenv.guild.add_text_channel(id=shop_id, name="legacy-shop")
        report = await setup(tenv)
        assert tenv.app.resources.channel_id("shop") == existing.id
        assert existing.name == tenv.text(channel_name_key("shop"))  # renamed to the configured name
        assert existing.category_id == _category(tenv, "support").id  # moved into its category
        assert any("`reviews`" in line and "999" in line for line in report.lines["problems"])
        assert not [c for c in tenv.guild.text_channels if c.name == tenv.text(channel_name_key("reviews"))]
    finally:
        await tenv.close()


async def test_missing_name_skips_creation_and_reports_content_key(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={channel_name_key("shop"): "", category_name_key("admin"): ""})
    try:
        report = await setup(tenv)
        assert tenv.app.resources.channel_id("shop") is None
        assert tenv.app.resources.category_id("admin") is None
        problems = "\n".join(report.lines["problems"])
        assert channel_name_key("shop") in problems and category_name_key("admin") in problems
        # The admin category is missing, so its channel cannot be placed.
        assert any("`audit_log`" in line and "`admin`" in line for line in report.lines["problems"])
        # Everything else is still created; the server feature is listed as not ready.
        assert tenv.app.resources.channel_id("catalog") is not None
        assert any("`server`" in line for line in report.lines["problems"])
        assert tenv.app.op("server.setup.status_hint") in report.lines["problems"]
    finally:
        await tenv.close()


# --- repair ---------------------------------------------------------------------------------------


async def test_repairs_managed_overwrites_and_keeps_owner_overwrites(tenv: TestEnv) -> None:
    await setup(tenv)
    guild = tenv.guild
    support = _category(tenv, "support")
    owner_role = guild.add_role(name="owner-added", position=2)
    await support.set_permissions(guild.default_role, overwrite=discord.PermissionOverwrite(send_messages=True))
    await support.set_permissions(owner_role, overwrite=discord.PermissionOverwrite(view_channel=False))
    expected_everyone = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        add_reactions=False,
        create_public_threads=False,
        create_private_threads=False,
    )

    report = await setup(tenv)
    by_id = _by_id(support.overwrites)
    assert by_id[guild.default_role.id] == expected_everyone
    assert by_id[owner_role.id] == discord.PermissionOverwrite(view_channel=False)
    assert any("`support`: updated" in line for line in report.lines["categories"])
    # Synced children follow the repaired category, including the owner's extra overwrite.
    shop = _channel(tenv, "shop")
    assert ow.same(shop.overwrites, support.overwrites)


async def test_inherit_channel_drift_is_resynced(tenv: TestEnv) -> None:
    await setup(tenv)
    shop = _channel(tenv, "shop")
    await shop.set_permissions(tenv.guild.default_role, overwrite=discord.PermissionOverwrite(send_messages=True))
    assert not shop.permissions_synced
    await setup(tenv)
    assert shop.edits[-1].get("sync_permissions") is True
    assert shop.permissions_synced


async def test_channel_name_topic_slowmode_and_category_are_restored(tmp_path: Path) -> None:
    server = shipped_server()
    for channel in server["channels"]:
        if channel["key"] == "catalog":
            channel["slowmode_seconds"] = 30
    tenv = await _env(tmp_path, server)
    try:
        await setup(tenv)
        catalog = _channel(tenv, "catalog")
        assert catalog.slowmode_delay == 30
        await catalog.edit(name="renamed", topic="changed", slowmode_delay=0, category=_category(tenv, "staff"))
        report = await setup(tenv)
        assert catalog.name == tenv.text(channel_name_key("catalog"))
        assert catalog.topic == tenv.text(channel_topic_key("catalog"))
        assert catalog.slowmode_delay == 30
        assert catalog.category_id == _category(tenv, "support").id
        assert ow.same(catalog.overwrites, _category(tenv, "support").overwrites)
        line = next(line for line in report.lines["channels"] if "`catalog`" in line)
        for change in ("name", "topic", "slowmode", "category"):
            assert tenv.app.op(f"server.setup.change.{change}") in line
    finally:
        await tenv.close()


async def test_channel_with_own_access_level(tmp_path: Path) -> None:
    server = shipped_server()
    server["channels"].append({"key": "lounge", "access": "public_interactive"})
    tenv = await _env(tmp_path, server)
    try:
        await setup(tenv)
        lounge = _channel(tenv, "lounge")
        assert lounge.category_id is None
        assert _by_id(lounge.overwrites)[tenv.guild.default_role.id] == discord.PermissionOverwrite(
            view_channel=True, send_messages=True
        )
        await lounge.set_permissions(tenv.guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=False))
        await setup(tenv)
        assert lounge.permissions_for(tenv.member()).send_messages
    finally:
        await tenv.close()


async def test_role_settings_are_applied_within_hierarchy(tmp_path: Path) -> None:
    server = shipped_server()
    server["roles"] = [
        {"key": "staff", "permissions": ["manage_messages", "kick_members"], "hoist": True, "color": "#112233"},
        {"key": "vip", "mentionable": True, "permissions": ["add_reactions"]},
        {"key": "boss", "id": 0},
    ]
    tenv = await _env(tmp_path, server)
    try:
        guild = tenv.guild
        boss = guild.add_role(name=tenv.text(role_name_key("boss")), position=60)  # above the bot (50)
        report = await setup(tenv, publish=False)
        staff = tenv.staff_role
        assert staff.hoist is True
        assert staff.permissions.manage_messages and staff.permissions.kick_members
        assert staff.edits[-1]["colour"] == discord.Colour(0x112233)
        vip = tenv.app.resources.role(guild, "vip")  # type: ignore[arg-type]
        assert vip is not None and vip.mentionable is True and vip.permissions.add_reactions
        assert vip.name == tenv.text(role_name_key("vip"))
        # The adopted role above the bot needs no change, so it is simply recorded.
        assert tenv.app.resources.role_id("boss") == boss.id and boss.edits == []
        assert report.count_values()["failed"] == 0
    finally:
        await tenv.close()


async def test_roles_above_the_bot_or_managed_are_never_edited(tmp_path: Path) -> None:
    server = shipped_server()
    high_id, managed_id = next_id(), next_id()
    server["roles"] = [{"key": "staff", "id": high_id, "hoist": True}, {"key": "bots", "id": managed_id}]
    tenv = await _env(tmp_path, server)
    try:
        guild = tenv.guild
        high = guild.add_role(id=high_id, name="high", position=70)
        managed = guild.add_role(id=managed_id, name="integration", position=5, managed=True)
        report = await setup(tenv)
        assert high.edits == [] and high.name == "high"
        assert managed.edits == []
        problems = "\n".join(report.lines["problems"])
        assert "`staff`" in problems and "`bots`" in problems
        # Overwrites still use the resolved staff role.
        assert high.id in _by_id(_category(tenv, "tickets").overwrites)
    finally:
        await tenv.close()


async def test_adopted_role_keeps_its_permissions_when_none_are_configured(tmp_path: Path) -> None:
    server = shipped_server()
    server["roles"].append({"key": "mods", "id": 0})
    tenv = await _env(tmp_path, server)
    try:
        perms = discord.Permissions(kick_members=True, manage_messages=True)
        mods = tenv.guild.add_role(name=tenv.text(role_name_key("mods")), position=7, permissions=perms)
        await setup(tenv, publish=False)
        assert tenv.app.resources.role_id("mods") == mods.id
        assert mods.permissions == perms and mods.edits == []
    finally:
        await tenv.close()


# --- failures ---------------------------------------------------------------------------------------


async def test_forbidden_actions_are_reported_and_the_run_continues(tenv: TestEnv) -> None:
    tenv.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    report = await setup(tenv, publish=False)
    counts = report.count_values()
    expected = len(tenv.app.settings.server.categories)
    assert counts["failed"] == expected
    assert tenv.staff_role.name == tenv.text(role_name_key("staff"))  # roles were still reconciled
    problems = "\n".join(report.lines["problems"])
    assert "HTTP 403" in problems and "50013" in problems
    # Channels whose category could not be created are skipped (not created at top level).
    assert tenv.guild.text_channels == []
    assert len([line for line in report.lines["problems"] if "skipped" in line]) == len(
        tenv.app.settings.server.channels
    )


async def test_edit_failures_are_reported(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(**kwargs: Any) -> None:
        raise http_error(discord.HTTPException, 500, 0, "boom")

    monkeypatch.setattr(tenv.staff_role, "edit", refuse)
    await setup(tenv, publish=False)
    support = _category(tenv, "support")
    await support.set_permissions(tenv.guild.default_role, overwrite=discord.PermissionOverwrite(send_messages=True))
    monkeypatch.setattr(support, "edit", refuse)
    report = await setup(tenv, publish=False)
    problems = "\n".join(report.lines["problems"])
    assert "`staff`" in problems and "`support`" in problems and "HTTP 500" in problems
    assert report.count_values()["failed"] == 2


async def test_missing_bot_permissions_are_reported(tenv: TestEnv) -> None:
    tenv.guild.bot_role._permissions = discord.Permissions(view_channel=True, send_messages=True).value
    report = await setup(tenv, dry_run=True)
    line = report.lines["problems"][0]
    for name in ("manage_roles", "manage_channels", "embed_links", "read_message_history"):
        assert name in line


# --- listeners ---------------------------------------------------------------------------------------


async def test_deleting_a_managed_channel_logs_a_warning(tenv: TestEnv, caplog: pytest.LogCaptureFixture) -> None:
    await setup(tenv)
    shop = _channel(tenv, "shop")
    other = tenv.guild.add_text_channel(name="unmanaged")
    caplog.set_level(logging.WARNING)
    await on_channel_delete(tenv.app, other)
    assert not [r for r in caplog.records if r.getMessage() == "managed_channel_deleted"]
    await shop.delete()
    await tenv.bot.emit("guild_channel_delete", shop)  # the listener registered by MODULE.register
    records = [r for r in caplog.records if r.getMessage() == "managed_channel_deleted"]
    assert records and records[0].key == "shop"  # type: ignore[attr-defined]


async def test_on_ready_warns_about_resources_deleted_while_offline(
    tenv: TestEnv, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(tenv)
    caplog.set_level(logging.WARNING)
    await on_ready(tenv.app)
    assert not [r for r in caplog.records if r.getMessage() == "managed_resources_missing"]
    tenv.guild._channels.pop(_channel(tenv, "shop").id)
    await on_ready(tenv.app)
    records = [r for r in caplog.records if r.getMessage() == "managed_resources_missing"]
    assert records and "channel:shop" in records[0].resources  # type: ignore[attr-defined]
