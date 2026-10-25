"""Find the configured texts a bot message shows (the "Edit text" message command).

An administrator right-clicks one of the bot's messages (Apps → Edit text); the bot lists the content keys whose
current values appear in it, and picking one opens the ``/content set`` form prefilled with that value.

The message itself does not record which keys built it, so keys are found from what it shows: every text of the
message (embed author, title, description, field names and values, footer; button labels, select placeholders and
options) is compared with every key's current value. A value's ``{placeholders}`` (data such as an order number or a
channel link) and its icons (``{icon_<name>}``) match any text. A key matches when its value is a whole text of the
message, a whole line of one, or a bold part of a line (a card's section headings, the rule names), or appears
inside a longer text (descriptions often join several values) and has at least ``MIN_SEARCH_LITERAL`` characters of
its own, so short words such as "Yes" do not match every description. Keys are listed in the order their text
appears.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import discord

from shopbot.config.content_keys import ContentKey, Kind

PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
MIN_WHOLE_LITERAL = 2  # fixed characters a value needs to be recognised as a whole text
MIN_SEARCH_LITERAL = 10  # fixed characters a value needs to be recognised inside a longer text
SKIPPED_KINDS = frozenset({Kind.EMOJI, Kind.URL})  # not shown as text


def message_texts(message: discord.Message) -> list[str]:
    """Every text the message shows, in display order (embeds first, then components)."""
    texts: list[str] = []
    for embed in message.embeds:
        texts += [embed.author.name or "", embed.title or "", embed.description or ""]
        for field in embed.fields:
            texts += [field.name or "", field.value or ""]
        texts.append(embed.footer.text or "")
    for row in message.components:
        for child in getattr(row, "children", ()):
            texts += _component_texts(child)
    return [t for t in texts if t]


def _component_texts(component: Any) -> list[str]:
    if isinstance(component, discord.Button):
        return [component.label or ""]
    if isinstance(component, discord.SelectMenu):
        texts = [component.placeholder or ""]
        for option in component.options:
            texts += [option.label, option.description or ""]
        return texts
    return []


def _pattern(value: str, placeholders: Iterable[str]) -> tuple[re.Pattern[str], int]:
    """A pattern matching ``value`` with each declared placeholder standing for any text, and the number of fixed
    (non-space) characters the value has of its own."""
    declared = set(placeholders)
    parts: list[str] = []
    literal = 0
    last = 0
    for match in PLACEHOLDER_RE.finditer(value):
        if match.group(1) not in declared and not match.group(1).startswith("icon_"):
            continue  # not a placeholder of this key (nor an icon): shown exactly as written
        fixed = value[last : match.start()]
        parts += [re.escape(fixed), r"[\s\S]*?"]
        literal += len("".join(fixed.split()))
        last = match.end()
    fixed = value[last:]
    parts.append(re.escape(fixed))
    literal += len("".join(fixed.split()))
    return re.compile("".join(parts)), literal


def find_keys(texts: list[str], values: Mapping[str, str], definitions: Iterable[ContentKey]) -> list[str]:
    """Content keys whose current value the message shows, in the order they appear in ``texts``."""
    found: list[tuple[int, int, str]] = []
    for definition in definitions:
        value = values.get(definition.key)
        if not value or definition.kind in SKIPPED_KINDS:
            continue
        pattern, literal = _pattern(value, definition.placeholders)
        position = _first_position(texts, pattern, literal)
        if position is not None:
            found.append((*position, definition.key))
    found.sort()
    return [key for _, _, key in found]


def _parts(text: str) -> Iterator[tuple[int, str]]:
    """The lines of ``text`` and the bold parts of each line (a card's section headings, rule names), with their
    offsets: each counts as a whole text of its own."""
    offset = 0
    for line in text.split("\n"):
        yield offset, line
        for match in _BOLD_RE.finditer(line):
            yield offset + match.start(1), match.group(1)
        offset += len(line) + 1


def _first_position(texts: list[str], pattern: re.Pattern[str], literal: int) -> tuple[int, int] | None:
    for index, text in enumerate(texts):
        if literal >= MIN_WHOLE_LITERAL:
            if pattern.fullmatch(text):
                return index, 0
            for offset, part in _parts(text):
                if pattern.fullmatch(part):
                    return index, offset
        if literal >= MIN_SEARCH_LITERAL:
            match = pattern.search(text)
            if match is not None:
                return index, match.start()
    return None
