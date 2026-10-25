"""Staff command visibility (specification section 8, role-restricted commands): the shipped Staff role carries
Manage Messages, the default permission that shows the staff slash commands in Discord's command picker. The bot's
own staff checks stay authoritative."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.config.settings import parse_settings
from shopbot.core.permissions import BOT_PERMISSIONS
from shopbot.features.setup.service import role_permissions
from shopbot.features.verification import content_keys as verification_keys
from tests.checkout.util import run_command as order_command
from tests.helpers import base_settings_dict, make_settings, make_test_env, shipped_settings_dict
from tests.setup.util import setup

STAFF_COMMANDS = ("orders", "order", "ticket", "review", "announce")


def _shipped_roles() -> dict[str, tuple[str, ...]]:
    settings = parse_settings(shipped_settings_dict())
    return {role.key: role.permissions for role in settings.server.roles}


def _granted(names: tuple[str, ...]) -> discord.Permissions:
    return role_permissions(names)


def test_the_shipped_staff_role_has_manage_messages_and_no_other_role_has_permissions() -> None:
    roles = _shipped_roles()
    assert roles["staff"] == ("manage_messages",)
    assert {key: perms for key, perms in roles.items() if key != "staff"} == {
        "owner": (),
        "member": (),
        "customer": (),
        "restock_alerts": (),
    }
    # The Member role (given to everyone who accepts the rules) still carries nothing elevated.
    settings = parse_settings(shipped_settings_dict())
    assert not any("must not have" in problem for problem in verification_keys.settings_problems(settings))


def test_the_bot_requests_manage_messages_so_setup_can_grant_it() -> None:
    requested = {name for names in BOT_PERMISSIONS.values() for name in names}
    assert "manage_messages" in requested  # Discord only lets a bot grant permissions it holds itself


async def test_every_staff_command_is_visible_with_the_staff_role(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    try:
        staff = _granted(_shipped_roles()["staff"])
        assert tenv.app.bot is not None
        tree = tenv.app.bot.tree
        for name in STAFF_COMMANDS:
            command = tree.get_command(name, guild=tenv.app.guild_object)
            assert command is not None, name
            required = command.default_permissions
            assert required is not None, name
            assert required.is_subset(staff), name
        # /product (catalog managers) needs Manage Server, which Staff does not get: admins allow it per command
        # under Server Settings -> Integrations and add the role to catalog.manager_roles.
        product = tree.get_command("product", guild=tenv.app.guild_object)
        assert product is not None and product.default_permissions is not None
        assert product.default_permissions.manage_guild and not product.default_permissions.is_subset(staff)
        assert not staff.manage_guild and not staff.administrator
    finally:
        await tenv.close()


async def test_setup_gives_the_staff_role_manage_messages_once(tmp_path: Path) -> None:
    server: dict[str, Any] = base_settings_dict()["server"]
    shipped_staff = next(r for r in shipped_settings_dict()["server"]["roles"] if r["key"] == "staff")
    server["roles"] = [shipped_staff if r["key"] == "staff" else r for r in server["roles"]]
    tenv = await make_test_env(tmp_path, settings=make_settings({"server": server}))
    try:
        assert not tenv.staff_role.permissions.manage_messages
        report = await setup(tenv, publish=False)
        assert report.count_values()["failed"] == 0
        assert tenv.staff_role.permissions.manage_messages
        assert tenv.staff_role.permissions.value == discord.Permissions(manage_messages=True).value
        edits = len(tenv.staff_role.edits)
        await setup(tenv, publish=False)  # idempotent: nothing to change the second time
        assert len(tenv.staff_role.edits) == edits
        member = tenv.member(name="staffer", staff=True)
        assert member.guild_permissions.manage_messages
    finally:
        await tenv.close()


async def test_the_permission_alone_does_not_authorize_staff_commands(tmp_path: Path) -> None:
    """Visibility is a convenience: a member with Manage Messages but without a staff role is still refused."""
    from shopbot.features import core_keys as core

    tenv = await make_test_env(tmp_path)
    try:
        moderator_role = tenv.guild.add_role(
            name="moderator", position=3, permissions=discord.Permissions(manage_messages=True)
        )
        moderator = tenv.guild.add_member(name="moderator", roles=[moderator_role])
        assert moderator.guild_permissions.manage_messages
        refused = await order_command(tenv, "list", user=moderator)
        assert refused.last_sent is not None
        assert refused.last_sent.embeds[0].description == tenv.text(core.ERROR_PERMISSION)
    finally:
        await tenv.close()
