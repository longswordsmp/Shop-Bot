"""The content TOML renderer: valid TOML that round-trips every value exactly."""

from __future__ import annotations

import random
import tomllib

import pytest

from shopbot.config.content import flatten
from shopbot.config.content_keys import Kind, Registry, ck
from shopbot.features.admin.content_toml import (
    UnrepresentableValue,
    basic_string,
    render_content_toml,
    toml_string,
)
from tests.helpers import TestEnv

TRICKY_PIECES = (
    "a",
    "Z",
    "0",
    " ",
    "  ",
    "\t",
    "\n",
    "\n\n",
    "\r",
    "\r\n",
    '"',
    '""',
    '"""',
    "'",
    "''",
    "'''",
    "''''",
    "\\",
    "\\n",
    "\\u0041",
    "`",
    "```",
    "{",
    "}",
    "{order_number}",
    "#",
    "# not a comment",
    "=",
    "[",
    "]",
    "[table]",
    "é",
    "ß",
    "日本語",
    "👋",
    "👋🏽",
    "👨‍👩‍👧",
    "🇺🇳",
    "‍",
    "\u200b",
    "﻿",
    " ",
    " ",
    "\x85",
    "\xa0",
    "\x00",
    "\x01",
    "\x08",
    "\x0b",
    "\x0c",
    "\x1b",
    "\x1f",
    "\x7f",
    "<@123>",
    "@everyone",
    "**bold**",
    "\U0010ffff",
)


def tricky(rng: random.Random) -> str:
    return "".join(rng.choice(TRICKY_PIECES) for _ in range(rng.randint(0, 12)))


def roundtrip(registry: Registry, values: dict[str, str], **kwargs: object) -> dict[str, str]:
    text = render_content_toml(registry, values, **kwargs)  # type: ignore[arg-type]
    loaded, errors = flatten(tomllib.loads(text))
    assert errors == []
    return loaded


def small_registry() -> Registry:
    return Registry(
        [
            ck("alpha.panel.title", Kind.TITLE, "alpha", "Panel", "Panel title."),
            ck("alpha.panel.body", Kind.DESCRIPTION, "alpha", "Panel", "Panel text.", placeholders=("name",)),
            ck("alpha.button.open", Kind.BUTTON_LABEL, "alpha", "Buttons", "Open button.", required=False),
            ck("beta.message", Kind.DESCRIPTION, "beta", "Messages", "Line one\nline two\x00 with a control char"),
            ck(
                "beta.short",
                Kind.TITLE,
                "beta",
                "Messages",
                "Optional with fallback.",
                fallback="alpha.panel.title",
            ),
            ck("alpha.zz.late", Kind.FOOTER, "alpha", "Panel", "Declared in another table order."),
        ]
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "plain",
        "  leading and trailing  ",
        "line1\nline2",
        "\nstarts with newline",
        "ends with newline\n",
        "\n",
        "\n\n\n",
        "tab\tinside",
        "trailing space on line \nnext",
        "crlf\r\nline",
        "lone\rcr",
        'double "quotes" and """triple"""',
        "single 'quotes' and '''triple'''",
        "ends with quote'",
        "ends with quotes''",
        "multi\nline ends with quote'",
        "back\\slash \\n not a newline",
        "emoji 👋🏽 family 👨‍👩‍👧 flag 🇺🇳",
        "{placeholder} and {unknown} and {{double}}",
        "nul\x00 and del\x7f and esc\x1b",
        "zero\u200bwidth﻿bom sep",
        "'''\nline after triple quote",
        '# looks like a comment\nkey = "value"',
    ],
)
def test_single_values_roundtrip_exactly(value: str) -> None:
    registry = small_registry()
    values = {d.key: "" for d in registry.all()}
    values["alpha.panel.body"] = value
    assert roundtrip(registry, values) == values
    assert roundtrip(registry, values, include_docs=False) == values


def test_random_tricky_values_roundtrip_property() -> None:
    registry = small_registry()
    rng = random.Random(20261025)
    for _ in range(500):
        values = {d.key: tricky(rng) for d in registry.all()}
        assert roundtrip(registry, values) == values
        assert roundtrip(registry, values, include_docs=False) == values


def test_every_single_character_roundtrips() -> None:
    registry = small_registry()
    base = {d.key: "" for d in registry.all()}
    for code in [*range(0, 0x100), 0x2028, 0x2029, 0xFEFF, 0xFFFD, 0xFFFF, 0x1F600, 0x10FFFF]:
        values = dict(base)
        values["beta.message"] = f"a{chr(code)}b\n{chr(code)}"
        assert roundtrip(registry, values) == values, f"U+{code:04X}"


async def test_full_registry_roundtrip_with_random_values(tenv: TestEnv) -> None:
    registry = tenv.app.content.registry
    rng = random.Random(7)
    for _ in range(20):
        values = {d.key: tricky(rng) for d in registry.all()}
        assert roundtrip(registry, values) == values


async def test_unset_keys_are_written_as_empty_strings(tenv: TestEnv) -> None:
    registry = tenv.app.content.registry
    loaded = roundtrip(registry, {})
    assert loaded == {d.key: "" for d in registry.all()}


def test_multiline_values_use_readable_literal_blocks() -> None:
    assert toml_string("line one\nline two") == "'''\nline one\nline two'''"
    # Forms that would be ambiguous or fragile fall back to escaped basic strings.
    assert toml_string("ends with quote\n'").startswith('"')
    assert toml_string("has '''\ntriple").startswith('"')
    assert toml_string("trailing space \nx").startswith('"')
    assert toml_string("cr\r\nlf").startswith('"')
    assert toml_string("single line") == '"single line"'


def test_basic_string_escapes() -> None:
    assert basic_string('a"b\\c\n\t\x00\x7f') == '"a\\"b\\\\c\\n\\t\\u0000\\u007F"'
    with pytest.raises(UnrepresentableValue):
        basic_string("bad \ud800 surrogate")


def test_output_is_grouped_by_feature_then_group_with_docs() -> None:
    registry = small_registry()
    values = {"alpha.panel.body": "Hello {name}"}
    text = render_content_toml(registry, values, feature_order=["beta", "alpha"])
    assert text.index("# Feature: beta") < text.index("# Feature: alpha")
    # Groups appear in the order of their first key (registry order is sorted by key).
    assert text.index("# --- Buttons ---") < text.index("# --- Panel ---")
    assert text.count("# --- Panel ---") == 1  # alpha.zz.late is listed with its group, not separately
    assert text.index("# Feature: alpha") < text.index("alpha.panel.title =") < text.index("alpha.zz.late =")
    assert "# Panel text." in text
    assert "# description · max 4096 characters · required · placeholders: {name}" in text
    assert "# button_label · max 80 characters · optional" in text
    assert "optional; falls back to alpha.panel.title" in text
    # A doc with a newline and a control character still yields valid comment lines.
    assert "# Line one\n# line two  with a control char" in text
    assert 'alpha.panel.body = "Hello {name}"' in text
    assert 'alpha.button.open = ""' in text
    tomllib.loads(text)


def test_without_docs_has_no_comments() -> None:
    text = render_content_toml(small_registry(), {}, include_docs=False)
    assert not any(line.startswith("#") for line in text.splitlines())


def test_unregistered_keys_are_kept_without_collisions() -> None:
    registry = small_registry()
    values = {d.key: "x" for d in registry.all()}
    values["alpha.panel"] = "would collide as a dotted key"  # prefix of registered keys
    values["gamma.removed.key"] = "kept"
    values["solo"] = "single segment"
    loaded = roundtrip(registry, values)
    assert loaded == values


def test_unregistered_single_segment_clash_is_reported() -> None:
    with pytest.raises(ValueError):
        render_content_toml(small_registry(), {"alpha": "clashes with the alpha table"})


def test_values_are_never_modified() -> None:
    registry = small_registry()
    value = "  Exactly as typed.  \n\n  Second paragraph with trailing newline\n"
    text = render_content_toml(registry, {"beta.message": value})
    loaded, _ = flatten(tomllib.loads(text))
    assert loaded["beta.message"] == value
