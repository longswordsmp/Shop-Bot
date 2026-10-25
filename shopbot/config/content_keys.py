"""Content key definitions.

Every customer-facing string has a ``ContentKey``: a unique dotted identifier, the kind of Discord
element it fills (which determines its length limit), the feature it belongs to, and the placeholders
it may contain. Keys are documented for the operator; values are supplied only by the business.

Keys are internal identifiers. They are never shown to customers.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


class Kind(StrEnum):
    TITLE = "title"
    DESCRIPTION = "description"
    FIELD_NAME = "field_name"
    FIELD_VALUE = "field_value"
    FOOTER = "footer"
    AUTHOR = "author"
    BUTTON_LABEL = "button_label"
    EMOJI = "emoji"
    SELECT_PLACEHOLDER = "select_placeholder"
    OPTION_LABEL = "option_label"
    OPTION_DESCRIPTION = "option_description"
    MODAL_TITLE = "modal_title"
    INPUT_LABEL = "input_label"
    INPUT_DESCRIPTION = "input_description"
    INPUT_PLACEHOLDER = "input_placeholder"
    ROLE_NAME = "role_name"
    CATEGORY_NAME = "category_name"
    CHANNEL_NAME = "channel_name"
    CHANNEL_TOPIC = "channel_topic"
    CHANNEL_PREFIX = "channel_prefix"
    URL = "url"
    FORMAT = "format"
    COMMAND_DESCRIPTION = "command_description"


# Discord limits per element (characters). Values are validated against these; never truncated.
MAX_LENGTH: dict[Kind, int] = {
    Kind.TITLE: 256,
    Kind.DESCRIPTION: 4096,
    Kind.FIELD_NAME: 256,
    Kind.FIELD_VALUE: 1024,
    Kind.FOOTER: 2048,
    Kind.AUTHOR: 256,
    Kind.BUTTON_LABEL: 80,
    Kind.EMOJI: 100,
    Kind.SELECT_PLACEHOLDER: 150,
    Kind.OPTION_LABEL: 100,
    Kind.OPTION_DESCRIPTION: 100,
    Kind.MODAL_TITLE: 45,
    Kind.INPUT_LABEL: 45,
    Kind.INPUT_DESCRIPTION: 100,
    Kind.INPUT_PLACEHOLDER: 100,
    Kind.ROLE_NAME: 100,
    Kind.CATEGORY_NAME: 100,
    Kind.CHANNEL_NAME: 100,
    Kind.CHANNEL_TOPIC: 1024,
    Kind.CHANNEL_PREFIX: 60,
    Kind.URL: 2048,
    Kind.FORMAT: 100,
    Kind.COMMAND_DESCRIPTION: 100,
}


@dataclass(frozen=True)
class ContentKey:
    key: str
    kind: Kind
    feature: str
    group: str
    doc: str
    required: bool = True
    placeholders: tuple[str, ...] = ()
    fallback: str | None = None

    def __post_init__(self) -> None:
        if not KEY_RE.match(self.key):
            raise ValueError(f"invalid content key {self.key!r}")
        if self.fallback is not None and self.required:
            raise ValueError(f"{self.key}: a key with a fallback must be optional")

    @property
    def max_length(self) -> int:
        return MAX_LENGTH[self.kind]


class Registry:
    """All content keys valid for the current settings."""

    def __init__(self, keys: Iterable[ContentKey] = ()) -> None:
        self._keys: dict[str, ContentKey] = {}
        self.add(*keys)

    def add(self, *keys: ContentKey) -> None:
        for key in keys:
            existing = self._keys.get(key.key)
            if existing is not None and existing != key:
                raise ValueError(f"content key {key.key!r} registered twice with different definitions")
            self._keys[key.key] = key

    def get(self, key: str) -> ContentKey | None:
        return self._keys.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def all(self) -> list[ContentKey]:
        return sorted(self._keys.values(), key=lambda k: k.key)

    def for_feature(self, feature: str) -> list[ContentKey]:
        return [k for k in self.all() if k.feature == feature]

    def check_no_prefix_collisions(self) -> list[str]:
        """A key must not be a prefix of another (TOML could not represent both)."""
        keys = sorted(self._keys)
        problems = []
        for a, b in itertools.pairwise(keys):
            if b.startswith(a + "."):
                problems.append(f"{a} is a prefix of {b}")
        return problems


def ck(
    key: str,
    kind: Kind,
    feature: str,
    group: str,
    doc: str,
    *,
    required: bool = True,
    placeholders: tuple[str, ...] = (),
    fallback: str | None = None,
) -> ContentKey:
    """Shorthand constructor used by feature key modules."""
    return ContentKey(
        key=key,
        kind=kind,
        feature=feature,
        group=group,
        doc=doc,
        required=required and fallback is None,
        placeholders=placeholders,
        fallback=fallback,
    )
