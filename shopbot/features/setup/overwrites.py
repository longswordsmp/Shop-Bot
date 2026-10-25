"""Permission overwrites for managed categories and channels (pure functions, no Discord calls).

Managed targets are ``@everyone``, the bot, and every role listed in ``permissions.staff_roles`` /
``permissions.admin_roles``. /setup sets exactly the computed overwrite for each managed target (a managed
target without a computed overwrite has its overwrite removed, so e.g. staff roles lose access to an
``admin`` channel). Overwrites for any other role or member are never touched.

Access levels:
- ``public_readonly``: everyone can read but not post, react, or open threads; staff/admin roles can post.
- ``public_interactive``: everyone can read and post.
- ``members_readonly``: hidden from everyone; the member role (``permissions.member_role``, granted by the
  verification button) can read but not post; staff/admin roles can read and post.
- ``members_interactive``: hidden from everyone; the member role and staff/admin roles can read and post.
- ``staff``: hidden from everyone; staff and admin roles can read, post, and see history.
- ``admin``: hidden from everyone; admin roles can read, post, and see history.
- ``inherit`` (channels only): identical overwrites to the parent category (handled by the caller).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import discord

from shopbot.core.channels import BOT_ALLOW

OverwriteTarget = discord.Role | discord.Member | discord.Object
Overwrites = dict[OverwriteTarget, discord.PermissionOverwrite]

EVERYONE_READONLY: dict[str, bool] = {
    "view_channel": True,
    "send_messages": False,
    "add_reactions": False,
    "create_public_threads": False,
    "create_private_threads": False,
}
TEAM_CAN_POST: dict[str, bool] = {"send_messages": True}
EVERYONE_INTERACTIVE: dict[str, bool] = {"view_channel": True, "send_messages": True}
EVERYONE_HIDDEN: dict[str, bool] = {"view_channel": False}
MEMBER_ACCESS: dict[str, bool] = {"view_channel": True, "send_messages": True, "read_message_history": True}
MEMBER_READONLY: dict[str, bool] = {
    "view_channel": True,
    "read_message_history": True,
    "send_messages": False,
    "add_reactions": False,
    "create_public_threads": False,
    "create_private_threads": False,
}
TEAM_ACCESS: dict[str, bool] = {"view_channel": True, "send_messages": True, "read_message_history": True}

OWN_ACCESS_LEVELS = (
    "public_readonly",
    "public_interactive",
    "members_readonly",
    "members_interactive",
    "staff",
    "admin",
)


def bot_target(bot_id: int) -> discord.Object:
    """Overwrite key for the bot member (a typed Object, serialized as a member overwrite)."""
    return discord.Object(id=bot_id, type=discord.Member)


def access_overwrites(
    access: str,
    *,
    everyone: discord.Role,
    staff_roles: Sequence[discord.Role],
    admin_roles: Sequence[discord.Role],
    bot: OverwriteTarget,
    member_roles: Sequence[discord.Role] = (),
) -> Overwrites:
    """The overwrites a category/channel with ``access`` must have for its managed targets.

    Raises ``ValueError`` for ``inherit`` (it has no overwrites of its own) and unknown levels.
    """
    result: Overwrites = {}
    if access == "public_readonly":
        result[everyone] = discord.PermissionOverwrite(**EVERYONE_READONLY)
        for role in (*staff_roles, *admin_roles):
            result[role] = discord.PermissionOverwrite(**TEAM_CAN_POST)
    elif access == "public_interactive":
        result[everyone] = discord.PermissionOverwrite(**EVERYONE_INTERACTIVE)
    elif access in ("members_readonly", "members_interactive"):
        result[everyone] = discord.PermissionOverwrite(**EVERYONE_HIDDEN)
        member_access = MEMBER_READONLY if access == "members_readonly" else MEMBER_ACCESS
        for role in member_roles:
            result[role] = discord.PermissionOverwrite(**member_access)
        for role in (*staff_roles, *admin_roles):
            result[role] = discord.PermissionOverwrite(**TEAM_ACCESS)
    elif access == "staff":
        result[everyone] = discord.PermissionOverwrite(**EVERYONE_HIDDEN)
        for role in (*staff_roles, *admin_roles):
            result[role] = discord.PermissionOverwrite(**MEMBER_ACCESS)
    elif access == "admin":
        result[everyone] = discord.PermissionOverwrite(**EVERYONE_HIDDEN)
        for role in admin_roles:
            result[role] = discord.PermissionOverwrite(**MEMBER_ACCESS)
    else:
        raise ValueError(f"access level {access!r} has no overwrites of its own")
    result[bot] = discord.PermissionOverwrite(**BOT_ALLOW)
    return result


def managed_ids(
    *,
    everyone: discord.Role,
    staff_roles: Iterable[discord.Role],
    admin_roles: Iterable[discord.Role],
    bot_id: int,
    member_roles: Iterable[discord.Role] = (),
) -> set[int]:
    return {
        everyone.id,
        bot_id,
        *(r.id for r in staff_roles),
        *(r.id for r in admin_roles),
        *(r.id for r in member_roles),
    }


def pairs(overwrites: Mapping[Any, discord.PermissionOverwrite]) -> dict[int, tuple[int, int]]:
    """``{target id: (allow, deny)}`` ignoring empty overwrites; alias-insensitive comparison form."""
    out: dict[int, tuple[int, int]] = {}
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        if allow.value or deny.value:
            out[int(target.id)] = (allow.value, deny.value)
    return out


def needs_repair(current: Mapping[Any, discord.PermissionOverwrite], desired: Overwrites, managed: set[int]) -> bool:
    """True when any managed target's overwrite differs from the desired one."""
    have = pairs(current)
    want = pairs(desired)
    return any(have.get(target_id) != want.get(target_id) for target_id in managed)


def merge(current: Mapping[Any, discord.PermissionOverwrite], desired: Overwrites, managed: set[int]) -> Overwrites:
    """Desired overwrites for managed targets plus every other target's overwrite left as it is."""
    merged: Overwrites = {target: ow for target, ow in current.items() if int(target.id) not in managed}
    merged.update({target: ow for target, ow in desired.items() if not ow.is_empty()})
    return merged


def same(a: Mapping[Any, discord.PermissionOverwrite], b: Mapping[Any, discord.PermissionOverwrite]) -> bool:
    """Whether two overwrite sets grant exactly the same permissions (i.e. a channel is synced)."""
    return pairs(a) == pairs(b)


def creatable(overwrites: Mapping[Any, discord.PermissionOverwrite]) -> Overwrites:
    """Overwrites safe to pass when *creating* a channel.

    discord.py serializes every ``Object`` target as a member overwrite on creation, so role-typed
    placeholders (roles missing from the cache, i.e. deleted roles) are dropped.
    """
    return {
        target: ow
        for target, ow in overwrites.items()
        if not (isinstance(target, discord.Object) and target.type is discord.Role)
    }
