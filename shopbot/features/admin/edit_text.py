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
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content_keys import ContentKey, Kind
from shopbot.ui import custom_ids

if TYPE_CHECKING:
    from shopbot.ui.routing import Router

PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
MIN_WHOLE_LITERAL = 2  # fixed characters a value needs to be recognised as a whole text
MIN_SEARCH_LITERAL = 10  # fixed characters a value needs to be recognised inside a longer text
# Never message text: emojis and links, and the server's names, topics, forms and command descriptions. (Form input
# labels stay: staff detail messages show them as field names.)
SKIPPED_KINDS = frozenset(
    {
        Kind.EMOJI,
        Kind.URL,
        Kind.ROLE_NAME,
        Kind.CATEGORY_NAME,
        Kind.CHANNEL_NAME,
        Kind.CHANNEL_TOPIC,
        Kind.CHANNEL_PREFIX,
        Kind.MODAL_TITLE,
        Kind.COMMAND_DESCRIPTION,
    }
)
SHARED_FEATURES = frozenset({"core"})  # shared texts (branding, buttons, errors) belong to every message


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


def message_features(message: discord.Message, router: Router) -> frozenset[str]:
    """The features whose buttons and menus the message carries (from their ``sb:<route>:...`` custom ids)."""
    by_route = {route.route: route.feature for route in router.routes() if route.feature}
    features: set[str] = set()
    for row in message.components:
        for child in getattr(row, "children", ()):
            parsed = custom_ids.parse(getattr(child, "custom_id", None) or "")
            feature = by_route.get(parsed[0]) if parsed is not None else None
            if feature:
                features.add(feature)
    return frozenset(features)


def find_keys(
    texts: list[str],
    values: Mapping[str, str],
    definitions: Iterable[ContentKey],
    *,
    features: frozenset[str] = frozenset(),
) -> list[str]:
    """Content keys whose current value the message shows, in the order they appear in ``texts``.

    Different features often use the same wording; when several keys match the same text, the keys of the message's
    own features come first and the others follow after every other match, so the message's own texts are not
    pushed out of the 25-option list. ``features``: the message's features (``message_features``); when unknown,
    the feature with the most unambiguous matches is taken."""
    found: list[tuple[int, int, str, str]] = []
    for definition in definitions:
        value = values.get(definition.key)
        if not value or definition.kind in SKIPPED_KINDS:
            continue
        pattern, literal = _pattern(value, definition.placeholders)
        position = _first_position(texts, pattern, literal)
        if position is not None:
            found.append((*position, definition.feature, definition.key))
    if not features:
        features = _likely_features(found)
    own = features | SHARED_FEATURES

    def rank(item: tuple[int, int, str, str]) -> tuple[int, int, bool, str]:
        index, position, feature, key = item
        return index, position, feature not in own, key

    found.sort(key=rank)
    owned_spots = {(index, position) for index, position, feature, _ in found if feature in own}
    primary = [key for i, p, feature, key in found if feature in own or (i, p) not in owned_spots]
    others = [key for i, p, feature, key in found if feature not in own and (i, p) in owned_spots]
    return primary + others


def _likely_features(found: list[tuple[int, int, str, str]]) -> frozenset[str]:
    spots = Counter((index, position) for index, position, _, _ in found)
    unique = Counter(feature for index, position, feature, _ in found if spots[(index, position)] == 1)
    unique.pop("core", None)
    if not unique:
        return frozenset()
    return frozenset({unique.most_common(1)[0][0]})


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
