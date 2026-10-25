"""Authorization rules, enforced server-side for every sensitive operation.

- Admin: ``OWNER_ID``, members with Discord's Administrator permission, or members holding a role listed
  in ``settings.permissions.admin_roles``.
- Staff: admins, plus members holding a role in ``settings.permissions.staff_roles`` or in an extra
  role list supplied by the feature (e.g. a ticket category's ``staff_roles``).

Hidden buttons and default command permissions are conveniences only; these checks are authoritative.
"""

from __future__ import annotations

from collections.abc import Callable

import discord

from shopbot.config.settings import Settings
from shopbot.core.resources import Resources

# Permissions the bot needs, by capability. Used for diagnostics and the invite URL.
BOT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "base": ("view_channel", "send_messages", "embed_links", "read_message_history", "use_external_emojis"),
    # /setup denies these on @everyone in read-only channels; Discord only lets a bot set permissions it holds.
    "setup": ("manage_roles", "manage_channels", "add_reactions", "create_public_threads", "create_private_threads"),
    # Ticket overwrites deny thread use to owners; Discord only lets a bot deny permissions it holds itself.
    "tickets": (
        "manage_channels",
        "manage_roles",
        "attach_files",
        "manage_messages",
        "create_public_threads",
        "create_private_threads",
        "send_messages_in_threads",
    ),
    "transcripts": ("read_message_history", "attach_files"),
    "catalog": ("attach_files",),
}

CHANNEL_SEND_PERMISSIONS = ("view_channel", "send_messages", "embed_links", "read_message_history")


class Permissions:
    def __init__(self, settings: Callable[[], Settings], resources: Resources, owner_id: int | None) -> None:
        self._settings = settings
        self._resources = resources
        self.owner_id = owner_id

    def _role_ids(self, member: discord.Member) -> set[int]:
        return {role.id for role in member.roles}

    def is_owner(self, user_id: int) -> bool:
        return self.owner_id is not None and user_id == self.owner_id

    def is_admin(self, member: discord.Member | None, user_id: int | None = None) -> bool:
        uid = member.id if member is not None else user_id
        if uid is not None and self.is_owner(uid):
            return True
        if member is None:
            return False
        if member.guild_permissions.administrator:
            return True
        admin_roles = self._resources.role_ids(self._settings().permissions.admin_roles)
        return bool(admin_roles & self._role_ids(member))

    def is_staff(
        self, member: discord.Member | None, user_id: int | None = None, extra_role_keys: tuple[str, ...] = ()
    ) -> bool:
        if self.is_admin(member, user_id):
            return True
        if member is None:
            return False
        keys = self._settings().permissions.staff_roles + tuple(extra_role_keys)
        return bool(self._resources.role_ids(keys) & self._role_ids(member))


def missing_permissions(
    member: discord.Member, channel: discord.abc.GuildChannel | None, required: tuple[str, ...]
) -> list[str]:
    """Names of ``required`` permissions ``member`` lacks (in ``channel`` if given, else guild-wide)."""
    perms = channel.permissions_for(member) if channel is not None else member.guild_permissions
    return [name for name in required if not getattr(perms, name, False)]


def can_manage_role(bot_member: discord.Member, role: discord.Role) -> bool:
    """Role hierarchy check: the bot can only edit/assign roles strictly below its top role."""
    return not role.managed and role < bot_member.top_role
