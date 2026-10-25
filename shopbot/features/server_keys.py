"""Visible names for the server structure defined in ``settings.server``.

A resource with ``id = 0`` is created by /setup and therefore needs a configured name. A resource adopted
by ``id`` keeps its existing name unless a name is configured (then /setup renames it).
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings

SERVER = "server"


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


def server_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    for channel in settings.server.channels:
        if channel.category and settings.server.category(channel.category) is None:
            problems.append(f"server.channels '{channel.key}': category '{channel.category}' is not defined")
        if channel.access == "inherit" and not channel.category:
            problems.append(f"server.channels '{channel.key}': access 'inherit' requires a category")
    problems.extend(
        reference_problems(settings, roles=settings.permissions.admin_roles + settings.permissions.staff_roles)
    )
    problems.extend(reference_problems(settings, channels=(("audit.channel", settings.audit.channel),)))
    return problems
