from __future__ import annotations

import discord
import pytest

from shopbot.core.channels import BOT_ALLOW
from shopbot.features.setup import overwrites as ow
from tests.fakes import FakeGuild

GUILD_ID = 111_111_111_111_111_111


def _env() -> tuple[FakeGuild, discord.Role, discord.Role, discord.Object]:
    guild = FakeGuild(id=GUILD_ID)
    staff = guild.add_role(name="staff", position=5)
    admin = guild.add_role(name="admin", position=6)
    return guild, staff, admin, ow.bot_target(guild.me.id)


def _compute(access: str) -> tuple[dict[int, discord.PermissionOverwrite], FakeGuild, discord.Role, discord.Role, int]:
    guild, staff, admin, bot = _env()
    result = ow.access_overwrites(
        access, everyone=guild.default_role, staff_roles=[staff], admin_roles=[admin], bot=bot
    )
    return {target.id: value for target, value in result.items()}, guild, staff, admin, bot.id


def test_public_readonly() -> None:
    by_id, guild, staff, admin, bot_id = _compute("public_readonly")
    everyone = by_id[guild.default_role.id]
    assert everyone.view_channel is True
    assert everyone.send_messages is False
    assert everyone.add_reactions is False
    assert everyone.create_public_threads is False
    assert everyone.create_private_threads is False
    for role in (staff, admin):
        assert by_id[role.id] == discord.PermissionOverwrite(send_messages=True)
    assert by_id[bot_id] == discord.PermissionOverwrite(**BOT_ALLOW)


def test_public_interactive() -> None:
    by_id, guild, staff, admin, bot_id = _compute("public_interactive")
    assert by_id[guild.default_role.id] == discord.PermissionOverwrite(view_channel=True, send_messages=True)
    assert staff.id not in by_id and admin.id not in by_id
    assert by_id[bot_id] == discord.PermissionOverwrite(**BOT_ALLOW)


def test_staff_access_hides_from_everyone_and_admits_staff_and_admins() -> None:
    by_id, guild, staff, admin, bot_id = _compute("staff")
    assert by_id[guild.default_role.id] == discord.PermissionOverwrite(view_channel=False)
    expected = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    assert by_id[staff.id] == expected
    assert by_id[admin.id] == expected
    assert by_id[bot_id] == discord.PermissionOverwrite(**BOT_ALLOW)


def test_admin_access_excludes_staff() -> None:
    by_id, guild, staff, admin, bot_id = _compute("admin")
    assert by_id[guild.default_role.id] == discord.PermissionOverwrite(view_channel=False)
    assert staff.id not in by_id
    assert by_id[admin.id].view_channel is True and by_id[admin.id].read_message_history is True
    assert bot_id in by_id


def test_bot_always_gets_full_channel_permissions() -> None:
    for access in ow.OWN_ACCESS_LEVELS:
        by_id, *_, bot_id = _compute(access)
        allow, deny = by_id[bot_id].pair()
        assert deny.value == 0
        for name in (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
            "attach_files",
            "manage_messages",
            "manage_channels",
        ):
            assert getattr(allow, name) is True, (access, name)
        # Discord only lets administrators grant Manage Permissions in an overwrite; the bot relies on its
        # guild-level Manage Roles instead (granting it here makes channel creation fail with 403).
        assert allow.manage_roles is False, access


def test_role_in_both_lists_gets_one_overwrite() -> None:
    guild, staff, _, bot = _env()
    result = ow.access_overwrites(
        "staff", everyone=guild.default_role, staff_roles=[staff], admin_roles=[staff], bot=bot
    )
    assert len([t for t in result if t.id == staff.id]) == 1


@pytest.mark.parametrize("access", ["inherit", "nonsense"])
def test_inherit_and_unknown_levels_are_rejected(access: str) -> None:
    guild, staff, admin, bot = _env()
    with pytest.raises(ValueError):
        ow.access_overwrites(access, everyone=guild.default_role, staff_roles=[staff], admin_roles=[admin], bot=bot)


def test_comparison_ignores_aliases_and_empty_overwrites() -> None:
    guild, staff, _, _ = _env()
    a = {guild.default_role: discord.PermissionOverwrite(view_channel=True), staff: discord.PermissionOverwrite()}
    b = {discord.Object(guild.default_role.id): discord.PermissionOverwrite(read_messages=True)}
    assert ow.same(a, b)
    assert not ow.same(a, {guild.default_role: discord.PermissionOverwrite(view_channel=False)})


def test_repair_and_merge_touch_only_managed_targets() -> None:
    guild, staff, admin, bot = _env()
    owner_role = guild.add_role(name="owner-added", position=3)
    desired = ow.access_overwrites(
        "admin", everyone=guild.default_role, staff_roles=[staff], admin_roles=[admin], bot=bot
    )
    managed = ow.managed_ids(everyone=guild.default_role, staff_roles=[staff], admin_roles=[admin], bot_id=bot.id)
    current = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True),
        staff: discord.PermissionOverwrite(view_channel=True),  # stale access from an earlier level
        owner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
    }
    assert ow.needs_repair(current, desired, managed)
    merged = ow.merge(current, desired, managed)
    by_id = ow.pairs(merged)
    assert staff.id not in by_id  # managed target without a computed overwrite loses its overwrite
    assert by_id[owner_role.id] == ow.pairs({owner_role: current[owner_role]})[owner_role.id]
    assert ow.pairs(merged)[guild.default_role.id] == ow.pairs(desired)[guild.default_role.id]
    assert not ow.needs_repair(merged, desired, managed)


def test_creatable_drops_role_placeholders_only() -> None:
    _, staff, _, bot = _env()
    ghost_role = discord.Object(id=42, type=discord.Role)
    member = discord.Object(id=43, type=discord.Member)
    overwrites = {
        staff: discord.PermissionOverwrite(view_channel=True),
        ghost_role: discord.PermissionOverwrite(view_channel=True),
        member: discord.PermissionOverwrite(view_channel=True),
        bot: discord.PermissionOverwrite(view_channel=True),
    }
    kept = ow.creatable(overwrites)
    assert set(t.id for t in kept) == {staff.id, member.id, bot.id}
