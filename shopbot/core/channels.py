"""Private channel creation and permission overwrites shared by tickets and checkout."""

from __future__ import annotations

import re

import discord

# Owners/participants: may talk in their ticket, but may not create threads (thread messages would escape the
# lock-on-close and the transcript).
OWNER_ALLOW = dict(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    create_public_threads=False,
    create_private_threads=False,
)
STAFF_ALLOW = dict(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    manage_messages=True,
)
# The bot's own overwrite. It deliberately does NOT include manage_roles ("Manage Permissions"): Discord only lets
# administrators grant that in an overwrite, so including it makes channel creation fail with 403 for a bot without
# Administrator. The bot's guild-level Manage Roles permission already lets it edit overwrites of its channels.
BOT_ALLOW = dict(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    manage_messages=True,
    manage_channels=True,
)
# Permissions the bot must hold in the parent category (or guild) to create a private channel.
CREATE_PRIVATE_REQUIRED = ("view_channel", "manage_channels", "manage_roles", "send_messages", "embed_links")


def private_overwrites(
    guild: discord.Guild,
    *,
    owner_id: int,
    staff_roles: list[discord.Role],
    bot_member: discord.Member,
    extra_member_ids: tuple[int, ...] = (),
    owner_can_send: bool = True,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        bot_member: discord.PermissionOverwrite(**BOT_ALLOW),
    }
    for role in staff_roles:
        overwrites[role] = discord.PermissionOverwrite(**STAFF_ALLOW)
    owner_perms = dict(OWNER_ALLOW)
    if not owner_can_send:
        owner_perms.update(send_messages=False, attach_files=False)
    overwrites[discord.Object(owner_id)] = discord.PermissionOverwrite(**owner_perms)
    for member_id in extra_member_ids:
        if member_id != owner_id:
            overwrites[discord.Object(member_id)] = discord.PermissionOverwrite(**OWNER_ALLOW)
    return overwrites


def normalize_text_channel_name(name: str) -> str:
    """Approximate Discord's normalization of text channel names (for comparisons only, never for display).

    Discord lowercases text channel names, turns whitespace into dashes, drops most ASCII punctuation, and
    collapses repeated dashes. Non-ASCII characters (letters, emoji) are kept.
    """
    lowered = name.strip().lower()
    lowered = re.sub(r"\s+", "-", lowered)
    lowered = "".join(ch for ch in lowered if not (ch.isascii() and not (ch.isalnum() or ch in "-_")))
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered[:100]


def render_channel_name(pattern: str, *, prefix: str, number: int, padding: int, user: str = "") -> str:
    """Fill the channel-name pattern. ``prefix`` is configured content; nothing else is invented.

    The result is never truncated; callers must refuse names longer than ``CHANNEL_NAME_MAX``.
    """
    number_text = str(number).zfill(padding) if padding else str(number)
    return pattern.replace("{prefix}", prefix).replace("{number}", number_text).replace("{user}", user)


CHANNEL_NAME_MAX = 100
