"""Content store: the single source of customer-facing text.

Lookup order for a key: database override (entered with ``/content set``) → ``config/content.toml``.
Empty strings count as *not configured*. Optional keys may declare a ``fallback`` key.

Rendering substitutes only the placeholders a key declares (``{order_number}``); every other character,
including other braces, is preserved exactly as written. The store never appends, trims, or rewrites
the configured text.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shopbot.config.content_keys import ContentKey, Kind, Registry

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:[A-Za-z0-9_]{2,32}:\d{15,21}>$")
OPERATOR_PREFIX = "operator."


class MissingContent(Exception):
    """A required content key has no value. Callers must treat the dependent output as unavailable."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class UnknownContentKey(KeyError):
    """Code referenced a key that is not registered (a programming error, caught by tests)."""


class ContentFileError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


def flatten(data: Mapping[str, Any], prefix: str = "") -> tuple[dict[str, str], list[str]]:
    """Flatten nested TOML tables into dotted keys. Returns (values, errors)."""
    values: dict[str, str] = {}
    errors: list[str] = []
    for name, value in data.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            sub_values, sub_errors = flatten(value, key)
            values.update(sub_values)
            errors.extend(sub_errors)
        elif isinstance(value, str):
            values[key] = value
        else:
            errors.append(f"{key}: content values must be strings (wrap numbers in quotes)")
    return values, errors


def load_content_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ContentFileError([f"{path.name}: {exc}"]) from exc
    values, errors = flatten(data)
    if errors:
        raise ContentFileError(errors)
    return values


def is_valid_emoji(value: str) -> bool:
    """Accept a custom emoji (<:name:id>) or a short unicode sequence without letters/digits."""
    if _CUSTOM_EMOJI_RE.match(value):
        return True
    if not value or len(value) > 16 or any(ch.isspace() for ch in value):
        return False
    return not any(ch.isascii() and ch.isalnum() for ch in value)


@dataclass(frozen=True)
class ValueProblem:
    key: str
    message: str


def validate_value(key: ContentKey, value: str) -> list[str]:
    """Check one configured value against its key's Discord constraints. Returns problems (no fixing)."""
    problems: list[str] = []
    if len(value) > key.max_length:
        problems.append(f"is {len(value)} characters; Discord allows at most {key.max_length} here")
    if key.kind is Kind.EMOJI and not is_valid_emoji(value):
        problems.append("is not a valid emoji (use a unicode emoji or <:name:id>)")
    if key.kind is Kind.URL and not value.startswith("https://"):
        problems.append("must be an https:// URL")
    if key.kind in (Kind.CHANNEL_NAME, Kind.CHANNEL_PREFIX) and value.strip() != value:
        problems.append("must not start or end with whitespace")
    if not value.strip():
        problems.append("contains only whitespace; Discord cannot display it (leave it empty to unset)")
    unknown = sorted(set(_PLACEHOLDER_RE.findall(value)) - set(key.placeholders))
    if unknown and key.placeholders:
        problems.append(
            f"uses placeholders {unknown} that are not available here (available: {list(key.placeholders)})"
        )
    return problems


class ContentStore:
    def __init__(
        self,
        registry: Registry,
        file_values: Mapping[str, str] | None = None,
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self._file: dict[str, str] = dict(file_values or {})
        self._overrides: dict[str, str] = dict(overrides or {})

    # -- raw access -------------------------------------------------------------------------------

    def _definition(self, key: str) -> ContentKey:
        definition = self.registry.get(key)
        if definition is None:
            raise UnknownContentKey(key)
        return definition

    def source(self, key: str) -> str | None:
        """Where the effective value comes from: 'override', 'file', or None."""
        if self._overrides.get(key):
            return "override"
        if self._file.get(key):
            return "file"
        return None

    def raw(self, key: str) -> str | None:
        """The configured value for exactly this key (no fallback), or None if unset/empty."""
        self._definition(key)
        value = self._overrides.get(key)
        if value:
            return value
        value = self._file.get(key)
        return value or None

    def get(self, key: str) -> str | None:
        """Configured value, following the key's fallback chain. None when not configured."""
        definition = self._definition(key)
        value = self.raw(key)
        if value is None and definition.fallback is not None:
            return self.get(definition.fallback)
        return value

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def has_all(self, *keys: str) -> bool:
        return all(self.has(k) for k in keys)

    # -- rendering --------------------------------------------------------------------------------

    def render(self, key: str, /, **values: str | int) -> str | None:
        """Configured text with declared placeholders substituted; None when not configured."""
        text = self.get(key)
        if text is None:
            return None
        definition = self._definition(key)
        allowed = set(definition.placeholders)
        if definition.fallback is not None:
            fallback_def = self.registry.get(definition.fallback)
            if fallback_def is not None:
                allowed |= set(fallback_def.placeholders)
        if not allowed:
            return text

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in allowed:
                return match.group(0)  # not a placeholder of this key: the owner's text, kept verbatim
            # A declared placeholder with no value in this situation (e.g. no error reference) renders as nothing,
            # so a raw "{name}" never reaches a customer.
            return str(values[name]) if name in values else ""

        return _PLACEHOLDER_RE.sub(substitute, text)

    def require(self, key: str, /, **values: str | int) -> str:
        text = self.render(key, **values)
        if text is None:
            raise MissingContent(key)
        return text

    # -- management -------------------------------------------------------------------------------

    def set_override(self, key: str, value: str) -> None:
        self._definition(key)
        self._overrides[key] = value

    def clear_override(self, key: str) -> None:
        self._overrides.pop(key, None)

    def replace(
        self,
        *,
        registry: Registry | None = None,
        file_values: Mapping[str, str] | None = None,
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        if registry is not None:
            self.registry = registry
        if file_values is not None:
            self._file = dict(file_values)
        if overrides is not None:
            self._overrides = dict(overrides)

    def effective_values(self) -> dict[str, str]:
        """All configured values for registered keys (override wins), without fallbacks."""
        result: dict[str, str] = {}
        for definition in self.registry.all():
            value = self.raw(definition.key)
            if value is not None:
                result[definition.key] = value
        return result

    def unknown_keys(self) -> list[str]:
        """Configured keys that are not registered (likely typos or keys for removed settings)."""
        configured = {k for k, v in {**self._file, **self._overrides}.items() if v}
        return sorted(k for k in configured if k not in self.registry and not k.startswith(OPERATOR_PREFIX))

    def all_configured_values(self) -> dict[str, str]:
        """Every non-empty value from the file and overrides (override wins), including keys that are not
        registered for the current settings and ``operator.*`` keys - used by exports so no text is lost."""
        merged = {k: v for k, v in self._file.items() if v}
        merged.update({k: v for k, v in self._overrides.items() if v})
        return merged

    def operator_value(self, key: str) -> str | None:
        value = self._overrides.get(OPERATOR_PREFIX + key) or self._file.get(OPERATOR_PREFIX + key)
        return value or None

    def value_problems(self) -> list[ValueProblem]:
        problems: list[ValueProblem] = []
        for definition in self.registry.all():
            value = self.raw(definition.key)
            if value is None:
                continue
            for message in validate_value(definition, value):
                problems.append(ValueProblem(definition.key, message))
        return problems
