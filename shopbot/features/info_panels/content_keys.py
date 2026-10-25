"""Content keys and configuration checks for informational panels (rules, FAQ, announcements...).

Each ``[[info_panels]]`` entry in settings gets its own keys under ``info_panels.<key>.*``. A panel needs a
title or a description (or both); its fields and link buttons are required once declared in settings.
"""

from __future__ import annotations

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.server_keys import reference_problems

INFO_PANELS = "info_panels"


def title_key(panel: str) -> str:
    return f"info_panels.{panel}.title"


def description_key(panel: str) -> str:
    return f"info_panels.{panel}.description"


def image_key(panel: str) -> str:
    return f"info_panels.{panel}.image_url"


def thumbnail_key(panel: str) -> str:
    return f"info_panels.{panel}.thumbnail_url"


def field_name_key(panel: str, number: int) -> str:
    return f"info_panels.{panel}.field.{number}.name"


def field_value_key(panel: str, number: int) -> str:
    return f"info_panels.{panel}.field.{number}.value"


def link_label_key(panel: str, link: str) -> str:
    return f"info_panels.{panel}.link.{link}.label"


def link_url_key(panel: str, link: str) -> str:
    return f"info_panels.{panel}.link.{link}.url"


def link_emoji_key(panel: str, link: str) -> str:
    return f"info_panels.{panel}.link.{link}.emoji"


def info_panel_keys(settings: Settings) -> list[ContentKey]:
    keys: list[ContentKey] = []
    for panel in settings.info_panels:
        p = panel.key
        group = f"Info panel '{p}'"
        where = f"the '{p}' info panel (posted by /setup in the '{panel.channel}' channel)"
        keys += [
            ck(
                title_key(p),
                Kind.TITLE,
                INFO_PANELS,
                group,
                f"Title of {where}. Configure a title, a description, or both.",
                required=False,
            ),
            ck(
                description_key(p),
                Kind.DESCRIPTION,
                INFO_PANELS,
                group,
                f"Main text of {where}. Configure a title, a description, or both.",
                required=False,
            ),
            ck(
                image_key(p),
                Kind.URL,
                INFO_PANELS,
                group,
                f"Optional large image (https:// URL) at the bottom of {where}.",
                required=False,
            ),
            ck(
                thumbnail_key(p),
                Kind.URL,
                INFO_PANELS,
                group,
                f"Optional small image (https:// URL) in the top-right corner of {where}.",
                required=False,
            ),
        ]
        for number in range(1, panel.fields + 1):
            keys += [
                ck(
                    field_name_key(p, number),
                    Kind.FIELD_NAME,
                    INFO_PANELS,
                    group,
                    f"Heading of section {number} of {panel.fields} in {where}.",
                ),
                ck(
                    field_value_key(p, number),
                    Kind.FIELD_VALUE,
                    INFO_PANELS,
                    group,
                    f"Text of section {number} of {panel.fields} in {where}.",
                ),
            ]
        for link in panel.links:
            keys += [
                ck(
                    link_label_key(p, link),
                    Kind.BUTTON_LABEL,
                    INFO_PANELS,
                    group,
                    f"Label of the '{link}' link button under {where}.",
                ),
                ck(
                    link_url_key(p, link),
                    Kind.URL,
                    INFO_PANELS,
                    group,
                    f"https:// address the '{link}' link button under {where} opens.",
                ),
                ck(
                    link_emoji_key(p, link),
                    Kind.EMOJI,
                    INFO_PANELS,
                    group,
                    f"Optional emoji shown on the '{link}' link button under {where}.",
                    required=False,
                ),
            ]
    return keys


def info_panel_settings_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    references: list[tuple[str, str]] = []
    for index, panel in enumerate(settings.info_panels):
        path = f"info_panels[{index}].channel"
        if not panel.channel:
            problems.append(f"{path}: panel '{panel.key}' needs a channel key from server.channels")
        references.append((path, panel.channel))
    problems.extend(reference_problems(settings, channels=tuple(references)))
    return problems


def info_panel_content_problems(settings: Settings, content: ContentStore) -> list[str]:
    problems: list[str] = []
    for panel in settings.info_panels:
        if not (content.has(title_key(panel.key)) or content.has(description_key(panel.key))):
            problems.append(
                f"info panel '{panel.key}': configure {title_key(panel.key)} or {description_key(panel.key)}"
            )
    return problems
