"""Pure builders for info panels: content -> ``PanelText`` -> ``MessageSpec`` (no Discord calls).

The same functions build the live panel and the offline preview. At runtime missing required text raises
``MissingContent`` (nothing incomplete is ever posted). The preview passes ``preview=True`` so a panel
that is not fully configured still shows its structure, with each missing value replaced by a visible
``[content.key]`` marker (or an example.com address for URLs).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shopbot.config.content import MissingContent
from shopbot.config.settings import InfoPanelDef
from shopbot.features.info_panels.content_keys import (
    description_key,
    field_name_key,
    field_value_key,
    image_key,
    link_emoji_key,
    link_label_key,
    link_url_key,
    thumbnail_key,
    title_key,
)
from shopbot.ui.spec import ButtonSpec, EmbedField, MessageSpec, rows_of
from shopbot.ui.style import Ui

Getter = Callable[[str], str | None]


@dataclass(frozen=True)
class PanelLink:
    label: str
    url: str
    emoji: str | None = None


@dataclass(frozen=True)
class PanelText:
    title: str | None
    description: str | None
    fields: tuple[tuple[str, str], ...] = ()
    image_url: str | None = None
    thumbnail_url: str | None = None
    links: tuple[PanelLink, ...] = ()


def resolve_panel_text(get: Getter, panel: InfoPanelDef, *, preview: bool = False) -> PanelText:
    """Collect a panel's configured text. Raises ``MissingContent`` for missing required text."""

    def required(key: str) -> str:
        value = get(key)
        if value:
            return value
        if preview:
            return f"[{key}]"
        raise MissingContent(key)

    def required_url(key: str) -> str:
        value = get(key)
        if value:
            return value
        if preview:
            return f"https://example.com/{key}"
        raise MissingContent(key)

    p = panel.key
    title = get(title_key(p))
    description = get(description_key(p))
    if not title and not description:
        if not preview:
            raise MissingContent(title_key(p))
        title = f"[{title_key(p)}]"
    fields = tuple(
        (required(field_name_key(p, n)), required(field_value_key(p, n))) for n in range(1, panel.fields + 1)
    )
    links = tuple(
        PanelLink(
            label=required(link_label_key(p, link)),
            url=required_url(link_url_key(p, link)),
            emoji=get(link_emoji_key(p, link)),
        )
        for link in panel.links
    )
    return PanelText(
        title=title,
        description=description,
        fields=fields,
        image_url=get(image_key(p)),
        thumbnail_url=get(thumbnail_key(p)),
        links=links,
    )


def panel_message(ui: Ui, text: PanelText, *, inline_fields: bool) -> MessageSpec:
    embed = ui.embed(
        title=text.title,
        description=text.description,
        tone="primary",
        fields=[EmbedField(name, value, inline=inline_fields) for name, value in text.fields],
        image_url=text.image_url,
        thumbnail_url=text.thumbnail_url,
        panel=True,
    )
    buttons = [ButtonSpec(label=link.label, style="link", url=link.url, emoji=link.emoji) for link in text.links]
    # Panel contents are owner-defined (rules, about, opening hours...), so the neutral banner fits every panel.
    return MessageSpec(embeds=(embed,), rows=rows_of(*buttons), banner=ui.banner("general-response"))


def build_panel(ui: Ui, panel: InfoPanelDef) -> MessageSpec:
    """The live panel message. Raises ``MissingContent`` if required text is not configured."""
    return panel_message(ui, resolve_panel_text(ui.content.get, panel), inline_fields=panel.inline_fields)
