"""Shared building blocks for the server specification: member-only access, the Owner role, events, channel links."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import parse_settings
from shopbot.core.events import PRODUCT_CHANGED, EventBus
from shopbot.features import core_keys as core
from shopbot.features.catalog import service as catalog_service
from shopbot.features.server_keys import channel_mentions, channel_placeholders, channel_url, server_problems
from shopbot.features.setup import overwrites as ow
from tests.catalog.util import add_product
from tests.fakes import FakeGuild
from tests.helpers import (
    OWNER_ID,
    TestEnv,
    base_settings_dict,
    deep_merge,
    make_settings,
    make_test_env,
    provision_channels,
    server_extension,
)
from tests.setup.util import setup

GUILD_ID = 111_111_111_111_111_111


def _overwrites(access: str) -> tuple[dict[int, Any], FakeGuild, Any, Any, Any]:
    guild = FakeGuild(id=GUILD_ID)
    staff = guild.add_role(name="staff", position=5)
    admin = guild.add_role(name="owner", position=7)
    member = guild.add_role(name="member", position=2)
    result = ow.access_overwrites(
        access,
        everyone=guild.default_role,
        staff_roles=[staff],
        admin_roles=[admin],
        bot=ow.bot_target(guild.me.id),
        member_roles=[member],
    )
    return {target.id: value for target, value in result.items()}, guild, staff, admin, member


def test_members_readonly_hides_from_unverified_users_and_lets_members_read() -> None:
    by_id, guild, staff, admin, member = _overwrites("members_readonly")
    assert by_id[guild.default_role.id].view_channel is False
    assert by_id[member.id].view_channel is True and by_id[member.id].send_messages is False
    assert by_id[member.id].read_message_history is True
    for team in (staff, admin):
        assert by_id[team.id].view_channel is True and by_id[team.id].send_messages is True


def test_members_interactive_lets_members_post() -> None:
    by_id, guild, _staff, _admin, member = _overwrites("members_interactive")
    assert by_id[guild.default_role.id].view_channel is False
    assert by_id[member.id].view_channel is True and by_id[member.id].send_messages is True


def test_member_roles_are_managed_targets() -> None:
    guild = FakeGuild(id=GUILD_ID)
    member = guild.add_role(name="member", position=2)
    ids = ow.managed_ids(everyone=guild.default_role, staff_roles=[], admin_roles=[], bot_id=1, member_roles=[member])
    assert member.id in ids


def test_member_access_requires_a_member_role() -> None:
    data = deep_merge(base_settings_dict(), {"server": {"categories": [{"key": "shop", "access": "members_readonly"}]}})
    data["server"]["channels"] = []
    problems = server_problems(parse_settings(data))
    assert any("permissions.member_role: required" in p for p in problems), problems


def test_owner_and_member_roles_must_be_defined() -> None:
    settings = make_settings({"permissions": {"owner_role": "boss", "member_role": "verified"}})
    problems = server_problems(settings)
    assert "role 'boss' is not defined in server.roles" in problems
    assert "role 'verified' is not defined in server.roles" in problems


async def _owner_env(tmp_path: Path) -> TestEnv:
    overrides = deep_merge(
        server_extension(roles=[{"key": "owner"}]),
        {"permissions": {"owner_role": "owner", "admin_roles": ["owner"]}},
    )
    tenv = await make_test_env(tmp_path, settings=make_settings(overrides))
    tenv.app.content.set_override("server.role.owner.name", "Owner")
    tenv.app.recompute_readiness()
    return tenv


async def test_setup_gives_the_owner_role_to_owner_id_once(tmp_path: Path) -> None:
    tenv = await _owner_env(tmp_path)
    try:
        owner = tenv.guild.get_member(OWNER_ID) or tenv.guild.add_member(id=OWNER_ID, name="owner")
        tenv.guild.add_role(name="Owner", position=3)  # an existing role with the configured name is adopted
        dry = await setup(tenv, dry_run=True)
        assert not any(r.name == "Owner" for r in owner.roles)
        assert any("would give" in line for line in dry.lines["roles"])

        report = await setup(tenv)
        role = tenv.app.resources.role(tenv.guild, "owner")  # type: ignore[arg-type]
        assert role is not None and role in owner.roles
        assert any("gave" in line and str(OWNER_ID) in line for line in report.lines["roles"])

        again = await setup(tenv)
        assert sum(1 for r in owner.roles if r.id == role.id) == 1
        assert any("already has" in line for line in again.lines["roles"])
        assert tenv.app.perms.is_admin(owner)
    finally:
        await tenv.close()


async def test_setup_reports_an_owner_who_is_not_in_the_server(tmp_path: Path) -> None:
    tenv = await _owner_env(tmp_path)
    try:
        member = tenv.guild.get_member(OWNER_ID)
        if member is not None:
            tenv.guild._members.pop(OWNER_ID)  # type: ignore[attr-defined]
        report = await setup(tenv)
        assert any("is not a member of this server" in line for line in report.lines["problems"])
    finally:
        await tenv.close()


async def test_event_bus_runs_every_handler_even_when_one_fails() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def broken(**payload: Any) -> None:
        raise RuntimeError("boom")

    async def working(**payload: Any) -> None:
        seen.append(payload["value"])

    bus.subscribe("x", broken)
    bus.subscribe("x", working)
    bus.subscribe("x", working)  # subscribing twice does not run it twice
    await bus.publish("x", value="ok")
    assert seen == ["ok"]


async def test_catalog_changes_are_published_with_before_and_after(tenv: TestEnv) -> None:
    events: list[dict[str, Any]] = []

    async def record(**payload: Any) -> None:
        events.append(payload)

    tenv.app.events.subscribe(PRODUCT_CHANGED, record)
    product = await add_product(tenv, "[p]", stock=0)
    admin = tenv.member(admin=True)
    await catalog_service.change_stock(tenv.app, actor_id=admin.id, product_id=product.id, mode="set", amount=3)
    assert [e["action"] for e in events] == ["stock"]
    assert events[0]["before"].stock_on_hand == 0 and events[0]["after"].stock_on_hand == 3
    assert events[0]["actor_id"] == admin.id


async def test_channel_placeholders_render_clickable_links(tenv: TestEnv) -> None:
    await provision_channels(tenv)
    names = channel_placeholders(tenv.app.settings)
    assert "channel_catalog" in names
    mentions = channel_mentions(tenv.app)
    catalog_id = tenv.app.resources.channel_id("catalog")
    assert mentions["channel_catalog"] == f"<#{catalog_id}>"
    assert channel_url(tenv.app, "catalog") == f"https://discord.com/channels/{tenv.app.env.guild_id}/{catalog_id}"
    assert channel_url(tenv.app, "nonexistent") is None


@pytest.mark.parametrize("key", [core.ERROR_GENERIC])
def test_shared_keys_still_resolve(key: str) -> None:
    assert key.startswith("common.")


def test_self_assignable_roles_must_not_be_staff_roles_of_any_feature() -> None:
    from shopbot.features.new_arrivals import content_keys as na_keys
    from shopbot.features.verification import content_keys as verification_keys

    overrides = deep_merge(
        server_extension(
            roles=[{"key": "member"}, {"key": "alerts"}],
            channels=[
                {"key": "welcome", "category": "support", "access": "inherit"},
                {"key": "new_arrivals", "category": "support", "access": "inherit"},
            ],
        ),
        {
            "permissions": {"member_role": "member"},
            "features": {"verification": True, "new_arrivals": True},
            "verification": {"channel": "welcome", "rules": 1},
            "new_arrivals": {"channel": "new_arrivals", "role": "alerts"},
            "problems": {"staff_roles": ["member"]},
            "announcements": {"roles": ["alerts"]},
        },
    )
    settings = make_settings(overrides)
    assert any("problems.staff_roles" in p for p in verification_keys.settings_problems(settings))
    assert any("new_arrivals.role" in p for p in na_keys.settings_problems(settings))


async def test_add_mode_keeps_an_adopted_roles_other_permissions(tmp_path: Path) -> None:
    import discord

    overrides = deep_merge(
        server_extension(roles=[{"key": "helpers", "permissions": ["manage_messages"], "permissions_mode": "add"}]),
        {},
    )
    tenv = await make_test_env(tmp_path, settings=make_settings(overrides))
    try:
        tenv.app.content.set_override("server.role.helpers.name", "Helpers")
        existing = tenv.guild.add_role(name="Helpers", position=3, permissions=discord.Permissions(kick_members=True))
        await setup(tenv)
        assert existing.permissions.kick_members and existing.permissions.manage_messages
    finally:
        await tenv.close()
