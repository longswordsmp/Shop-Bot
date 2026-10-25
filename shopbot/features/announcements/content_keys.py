"""Customer-facing content keys of ``/announce`` and its settings checks.

An announcement's title, message and field values are typed by staff when they publish it (runtime text, not
configuration). The only configured wording is the heading of each configured extra field,
``announcements.field.<id>.name`` (required for every ``[[announcements.fields]]`` entry), plus the shared branding
footer/author line. Everything staff see while publishing (the form, confirmations, errors) is operator text.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.server_keys import reference_problems

ANNOUNCEMENTS = "announcements"


def field_name_key(field_id: str) -> str:
    return f"announcements.field.{field_id}.name"


def content_keys(settings: Settings) -> list[ContentKey]:
    an = settings.announcements
    group = "Announcements (posted with /announce)"
    keys: list[ContentKey] = []
    for item in an.fields:
        need = "staff must fill it in" if item.required else "shown only when staff fill it in"
        keys.append(
            ck(
                field_name_key(item.id),
                Kind.FIELD_NAME,
                ANNOUNCEMENTS,
                group,
                f"Heading of the '{item.id}' field of announcements posted with /announce in the '{an.channel}' "
                f"channel. The text under it is what staff type in the form's '{item.id}' input ({need}). Also used "
                "as that input's label in the staff form when it is at most 45 characters long.",
            )
        )
    return keys


def settings_problems(settings: Settings) -> list[str]:
    an = settings.announcements
    problems: list[str] = []
    if not an.channel:
        problems.append("announcements.channel: required (the channel key /announce posts to)")
    problems.extend(reference_problems(settings, channels=(("announcements.channel", an.channel),)))
    for path, keys in (("announcements.roles", an.roles), ("announcements.mention_roles", an.mention_roles)):
        problems.extend(f"{path}: {problem}" for problem in reference_problems(settings, roles=keys))
    return problems
