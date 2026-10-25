"""Discord-independent description of messages, components, and forms.

Feature code builds ``MessageSpec``/``ModalSpec`` objects from content and state. The same spec is
converted to discord.py objects by ``shopbot.ui.discord_render``, validated by ``shopbot.ui.limits``,
and rendered to HTML by the offline preview — so what is checked is exactly what is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ButtonStyle = Literal["primary", "secondary", "success", "danger", "link"]
InputStyle = Literal["short", "paragraph"]


@dataclass(frozen=True)
class EmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class EmbedSpec:
    title: str | None = None
    description: str | None = None
    url: str | None = None
    color: int | None = None
    fields: tuple[EmbedField, ...] = ()
    author_name: str | None = None
    author_icon_url: str | None = None
    footer_text: str | None = None
    footer_icon_url: str | None = None
    thumbnail_url: str | None = None
    image_url: str | None = None
    timestamp: int | None = None

    def is_empty(self) -> bool:
        return not (self.title or self.description or self.fields or self.image_url or self.author_name)

    def text_length(self) -> int:
        """Characters counted toward Discord's 6000-character per-message embed total."""
        total = len(self.title or "") + len(self.description or "")
        total += len(self.author_name or "") + len(self.footer_text or "")
        total += sum(len(f.name) + len(f.value) for f in self.fields)
        return total


@dataclass(frozen=True)
class ButtonSpec:
    label: str | None = None
    custom_id: str | None = None
    style: ButtonStyle = "secondary"
    emoji: str | None = None
    url: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class SelectOptionSpec:
    label: str
    value: str
    description: str | None = None
    emoji: str | None = None
    default: bool = False


@dataclass(frozen=True)
class SelectSpec:
    custom_id: str
    options: tuple[SelectOptionSpec, ...]
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


Component = ButtonSpec | SelectSpec


@dataclass(frozen=True)
class MessageSpec:
    embeds: tuple[EmbedSpec, ...] = ()
    rows: tuple[tuple[Component, ...], ...] = ()
    # Plain message content is used only for pings (mentions); all text lives in embeds.
    mention_user_ids: tuple[int, ...] = ()
    mention_role_ids: tuple[int, ...] = ()

    @property
    def mention_text(self) -> str | None:
        parts = [f"<@{uid}>" for uid in self.mention_user_ids] + [f"<@&{rid}>" for rid in self.mention_role_ids]
        return " ".join(parts) or None


@dataclass(frozen=True)
class TextInputSpec:
    custom_id: str
    label: str
    style: InputStyle = "short"
    description: str | None = None
    placeholder: str | None = None
    default: str | None = None
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None


@dataclass(frozen=True)
class ModalSpec:
    custom_id: str
    title: str
    inputs: tuple[TextInputSpec, ...] = field(default_factory=tuple)


def rows_of(*components: Component | None, per_row: int = 5) -> tuple[tuple[Component, ...], ...]:
    """Pack buttons into rows of up to ``per_row``; each select gets its own row. ``None`` is skipped."""
    rows: list[tuple[Component, ...]] = []
    current: list[Component] = []
    for component in components:
        if component is None:
            continue
        if isinstance(component, SelectSpec):
            if current:
                rows.append(tuple(current))
                current = []
            rows.append((component,))
            continue
        current.append(component)
        if len(current) == per_row:
            rows.append(tuple(current))
            current = []
    if current:
        rows.append(tuple(current))
    return tuple(rows)
