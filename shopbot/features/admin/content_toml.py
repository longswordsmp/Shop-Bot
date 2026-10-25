"""Render content values as a ``content.toml`` document.

Used by ``/content export`` and the offline template generator (``python -m tools.checklist --template``).

Guarantees:
- the output is valid TOML;
- loading it with ``tomllib`` and flattening with ``shopbot.config.content.flatten`` returns exactly the
  values passed in (every registered key is present; unset keys are written as empty strings);
- values are never altered: no wording is added, nothing is trimmed, and every character (unicode, emoji,
  quotes, backslashes, newlines, leading/trailing whitespace, triple quotes) round-trips exactly.

Representation: a value is written as a multi-line literal string (``'''...'''``) when it contains line
breaks and that form is unambiguous and robust (no ``'''``, no control characters, no carriage returns, no
trailing whitespace on a line, no trailing quote); every other value is written as an escaped basic
string, which can represent any text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from shopbot.config.content_keys import ContentKey, Registry

_BARE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_RULE_WIDTH = 98

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}

HEADER = (
    "Content: every text customers can see. Keys are internal identifiers; customers never see them.",
    "",
    'Values are used exactly as written (never trimmed or rewritten). An empty value ("") means',
    '"not configured": the related message or panel is not shown until its required keys are set.',
    "{placeholders} listed for a key are replaced with data; any other text, including braces, is kept.",
    "Multi-line values use '''...''' blocks: the line break right after the opening ''' is not part",
    "of the value. Values entered with /content set in Discord take precedence over this file.",
)


class UnrepresentableValue(ValueError):
    """A value cannot be written to a UTF-8 TOML document (it contains a lone surrogate)."""


def _is_surrogate(code: int) -> bool:
    return 0xD800 <= code <= 0xDFFF


def _is_toml_control(code: int) -> bool:
    """Control characters TOML forbids unescaped in strings (tab is allowed but escaped for clarity)."""
    return code < 0x20 or code == 0x7F


def basic_string(value: str) -> str:
    """A TOML basic string that decodes to exactly ``value``."""
    out = ['"']
    for char in value:
        code = ord(char)
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif _is_toml_control(code):
            out.append(f"\\u{code:04X}")
        elif _is_surrogate(code):
            raise UnrepresentableValue(f"value contains a lone surrogate U+{code:04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _multiline_literal_ok(value: str) -> bool:
    if "\n" not in value or "'''" in value or value.endswith("'"):
        return False
    for char in value:
        code = ord(char)
        if char in "\n\t":
            continue
        if _is_toml_control(code) or _is_surrogate(code):
            return False
    # Trailing whitespace is invisible and easily lost by editors; use an escaped string instead.
    return all(line == line.rstrip(" \t") for line in value.split("\n"))


def toml_string(value: str) -> str:
    """The most readable TOML string literal that decodes to exactly ``value``."""
    if _multiline_literal_ok(value):
        # The newline right after the opening delimiter is trimmed by TOML parsers.
        return "'''\n" + value + "'''"
    return basic_string(value)


def toml_key(key: str) -> str:
    """Dotted TOML key whose flattened form is ``key`` (segments quoted only when necessary)."""
    segments = key.split(".")
    if all(_BARE_SEGMENT.match(segment) for segment in segments):
        return ".".join(segments)
    return ".".join(segment if _BARE_SEGMENT.match(segment) else basic_string(segment) for segment in segments)


def _comment(text: str) -> list[str]:
    """Comment lines for arbitrary text (control characters are not allowed in TOML comments)."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        clean = "".join(
            " " if (_is_toml_control(ord(c)) and c != "\t") else ("�" if _is_surrogate(ord(c)) else c) for c in raw
        )
        lines.append(f"# {clean}".rstrip())
    return lines


def _requirement(definition: ContentKey) -> str:
    if definition.required:
        return "required"
    if definition.fallback:
        return f"optional; falls back to {definition.fallback}"
    return "optional"


def _key_comment(definition: ContentKey) -> list[str]:
    meta = [definition.kind.value, f"max {definition.max_length} characters", _requirement(definition)]
    if definition.placeholders:
        meta.append("placeholders: " + ", ".join("{" + p + "}" for p in definition.placeholders))
    return [*_comment(definition.doc), *_comment(" · ".join(meta))]


def _default_feature_order() -> list[str]:
    try:
        from shopbot.features import all_modules

        return [module.name for module in all_modules()]
    except Exception:  # pragma: no cover - only reachable if a feature package fails to import
        return []


def _ordered_features(definitions: Iterable[ContentKey], order: Sequence[str]) -> list[str]:
    present = {d.feature for d in definitions}
    ordered = [name for name in order if name in present]
    return ordered + sorted(present - set(ordered))


def render_content_toml(
    registry: Registry,
    values: Mapping[str, str],
    *,
    include_docs: bool = True,
    feature_order: Sequence[str] | None = None,
) -> str:
    """Render every registered key (grouped by feature, then group) with its value from ``values``.

    Keys in ``values`` that are not registered are kept in a final section (as quoted keys) so nothing is
    lost. Raises ``UnrepresentableValue`` for text that cannot be stored in UTF-8 and ``ValueError`` for an
    unregistered key that would collide with a registered table.
    """
    definitions = registry.all()
    order = list(feature_order) if feature_order is not None else _default_feature_order()
    lines: list[str] = []
    if include_docs:
        lines += _comment("\n".join(HEADER))
        lines.append("")

    for feature in _ordered_features(definitions, order):
        feature_keys = [d for d in definitions if d.feature == feature]
        if include_docs:
            lines += ["# " + "=" * _RULE_WIDTH, *_comment(f"Feature: {feature}"), "# " + "=" * _RULE_WIDTH, ""]
        groups: dict[str, list[ContentKey]] = {}
        for definition in feature_keys:
            groups.setdefault(definition.group, []).append(definition)
        for group, members in groups.items():
            if include_docs:
                lines += [*_comment(f"--- {group} ---"), ""]
            for definition in members:
                if include_docs:
                    lines += _key_comment(definition)
                value = values.get(definition.key, "")
                lines.append(f"{toml_key(definition.key)} = {toml_string(value)}")
                if include_docs:
                    lines.append("")
        if not include_docs:
            lines.append("")

    extra = sorted(k for k in values if k not in registry)
    if extra:
        first_segments = {d.key.split(".", 1)[0] for d in definitions}
        clashes = [k for k in extra if "." not in k and k in first_segments]
        if clashes:
            raise ValueError(f"unregistered keys collide with registered tables: {clashes}")
        if include_docs:
            lines += [
                "# " + "=" * _RULE_WIDTH,
                *_comment("Keys not used by the current settings (kept so no text is lost)"),
                "# " + "=" * _RULE_WIDTH,
                "",
            ]
        for key in extra:
            # A quoted key flattens back to exactly ``key`` and never collides with a dotted table.
            lines.append(f"{basic_string(key)} = {toml_string(values[key])}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
