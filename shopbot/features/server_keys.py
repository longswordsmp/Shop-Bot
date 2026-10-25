"""Visible names for the server structure defined in ``settings.server``.

A resource with ``id = 0`` is created by /setup and therefore needs a configured name. A resource adopted
by ``id`` keeps its existing name unless a name is configured (then /setup renames it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import MEMBER_ACCESS_LEVELS, Settings

if TYPE_CHECKING:
    from shopbot.context import App

SERVER = "server"


# -- clickable channel links inside text ---------------------------------------------------------------------
# Any content key can declare ``channel_placeholders(settings)`` so the owner may write e.g. "{channel_catalog}" in
# their text; it renders as a clickable #channel link (Discord channel mention) once /setup created the channel.


def channel_placeholder(channel_key: str) -> str:
    return f"channel_{channel_key}"


def channel_placeholders(settings: Settings) -> tuple[str, ...]:
    return tuple(channel_placeholder(channel.key) for channel in settings.server.channels)


def channel_mentions(app: App) -> dict[str, str]:
    """Values for ``channel_placeholders``: ``<#id>`` for every configured channel /setup has created or adopted."""
    values: dict[str, str] = {}
    for channel in app.settings.server.channels:
        channel_id = app.resources.channel_id(channel.key)
        if channel_id:
            values[channel_placeholder(channel.key)] = f"<#{channel_id}>"
    return values


def channel_url(app: App, channel_key: str) -> str | None:
    """A https:// link to a configured channel (for link buttons), or None before /setup created it."""
    channel_id = app.resources.channel_id(channel_key)
    if not channel_id:
        return None
    return f"https://discord.com/channels/{app.env.guild_id}/{channel_id}"


def role_name_key(key: str) -> str:
    return f"server.role.{key}.name"


def category_name_key(key: str) -> str:
    return f"server.category.{key}.name"


def channel_name_key(key: str) -> str:
    return f"server.channel.{key}.name"


def channel_topic_key(key: str) -> str:
    return f"server.channel.{key}.topic"


def server_keys(settings: Settings) -> list[ContentKey]:
    keys: list[ContentKey] = []
    for role in settings.server.roles:
        keys.append(
            ck(
                role_name_key(role.key),
                Kind.ROLE_NAME,
                SERVER,
                "Role names",
                f"Name of the '{role.key}' role.",
                required=role.id == 0,
            )
        )
    for category in settings.server.categories:
        keys.append(
            ck(
                category_name_key(category.key),
                Kind.CATEGORY_NAME,
                SERVER,
                "Category names",
                f"Name of the '{category.key}' channel category.",
                required=category.id == 0,
            )
        )
    for channel in settings.server.channels:
        keys.append(
            ck(
                channel_name_key(channel.key),
                Kind.CHANNEL_NAME,
                SERVER,
                "Channel names",
                f"Name of the '{channel.key}' channel.",
                required=channel.id == 0,
            )
        )
        keys.append(
            ck(
                channel_topic_key(channel.key),
                Kind.CHANNEL_TOPIC,
                SERVER,
                "Channel topics",
                f"Optional topic (description) of the '{channel.key}' channel.",
                required=False,
            )
        )
    return keys


def reference_problems(
    settings: Settings,
    *,
    roles: tuple[str, ...] = (),
    channels: tuple[tuple[str, str], ...] = (),
    categories: tuple[tuple[str, str], ...] = (),
) -> list[str]:
    """Check that settings references point to defined resources.

    ``channels``/``categories`` are (setting path, key) pairs; empty keys are skipped.
    """
    problems: list[str] = []
    for key in roles:
        if settings.server.role(key) is None:
            problems.append(f"role '{key}' is not defined in server.roles")
    for path, key in channels:
        if key and settings.server.channel(key) is None:
            problems.append(f"{path}: channel '{key}' is not defined in server.channels")
    for path, key in categories:
        if key and settings.server.category(key) is None:
            problems.append(f"{path}: category '{key}' is not defined in server.categories")
    return problems


PRIVATE_ACCESS = frozenset({"staff", "admin"})


def effective_access(settings: Settings, channel_key: str) -> str | None:
    """The access level a channel gets from settings ('inherit' resolved through its category)."""
    channel = settings.server.channel(channel_key)
    if channel is None:
        return None
    if channel.access != "inherit":
        return channel.access
    category = settings.server.category(channel.category) if channel.category else None
    return category.access if category is not None else None


def private_channel_problems(settings: Settings, references: tuple[tuple[str, str], ...]) -> list[str]:
    """Channels that receive staff/admin-only output must be configured as staff or admin channels."""
    problems: list[str] = []
    for path, key in references:
        if not key or settings.server.channel(key) is None:
            continue
        access = effective_access(settings, key)
        if access not in PRIVATE_ACCESS:
            problems.append(f"{path}: channel '{key}' must have staff or admin access (it is '{access}')")
    return problems


def server_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    for channel in settings.server.channels:
        if channel.category and settings.server.category(channel.category) is None:
            problems.append(f"server.channels '{channel.key}': category '{channel.category}' is not defined")
        if channel.access == "inherit" and not channel.category:
            problems.append(f"server.channels '{channel.key}': access 'inherit' requires a category")
    perms = settings.permissions
    named = tuple(key for key in (perms.owner_role, perms.member_role) if key)
    problems.extend(reference_problems(settings, roles=perms.admin_roles + perms.staff_roles + named))
    uses_members = [c.key for c in settings.server.categories if c.access in MEMBER_ACCESS_LEVELS] + [
        c.key for c in settings.server.channels if c.access in MEMBER_ACCESS_LEVELS
    ]
    if uses_members and not perms.member_role:
        problems.append(f"permissions.member_role: required because {uses_members[0]!r} uses a members_* access level")
    problems.extend(reference_problems(settings, channels=(("audit.channel", settings.audit.channel),)))
    problems.extend(private_channel_problems(settings, (("audit.channel", settings.audit.channel),)))
    return problems


def staff_role_lists(settings: Settings) -> list[tuple[str, tuple[str, ...]]]:
    """Every setting that gives a role staff or admin powers somewhere, as (setting path, role keys).

    A role members give themselves (the Member role, the new-arrivals notification role) must not appear in any.
    """
    perms = settings.permissions
    lists: list[tuple[str, tuple[str, ...]]] = [
        ("permissions.admin_roles", perms.admin_roles),
        ("permissions.staff_roles", perms.staff_roles),
        ("permissions.owner_role", (perms.owner_role,) if perms.owner_role else ()),
        ("tickets.staff_roles", settings.tickets.staff_roles),
        ("checkout.staff_roles", settings.checkout.staff_roles),
        ("catalog.manager_roles", settings.catalog.manager_roles),
        ("problems.staff_roles", settings.problems.staff_roles),
        ("announcements.roles", settings.announcements.roles),
    ]
    for category in settings.tickets.categories:
        lists.append((f"tickets.categories '{category.id}' staff_roles", category.staff_roles))
    return lists
