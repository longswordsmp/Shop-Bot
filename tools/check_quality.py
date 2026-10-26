"""Offline checker for the house style (docs/QUALITY_STANDARDS.md): builds every preview state with the configured
text, like the HTML preview, and reports each violation of a rule marked [checked].

Usage:
    python -m tools.check_quality                # every feature's states, a summary per rule
    python -m tools.check_quality --strict       # exit 1 when anything is reported (scripts/quality.sh)
    python -m tools.check_quality --json         # one JSON object per violation
    python -m tools.check_quality --feature checkout

Customer and operator messages are told apart by the branding: a customer card is built with ``Ui.embed`` and its
first embed carries the shop's footer text (``branding.footer_text``), or it is a later message of a customer card
(no timestamp); a private operator reply (``Ui.operator_embed``) has no branded footer and a timestamp. The rules
about words and layout apply to customer messages; the rules about icons Discord cannot show and leftover
placeholders apply to every message and form.

Exit status: 0 = nothing reported (or not --strict), 1 = violations with --strict, 2 = the configuration cannot be
loaded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from shopbot.config.content import ContentFileError
from shopbot.config.settings import SettingsError
from shopbot.ui.component_emoji import split_button, split_option
from shopbot.ui.spec import ButtonSpec, EmbedSpec, MessageSpec, ModalSpec, PreviewState, SelectSpec
from tools.offline import ROOT, build_offline_app

# --- limits of the house style (docs/QUALITY_STANDARDS.md) ------------------------------------------------------
TITLE_MAX = 60
BUTTON_LABEL_MAX = 25
INLINE_FIELDS_MAX = 6
EMBEDS_MAX = 2
PARAGRAPH_MAX = 400

CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,21}>")
LEADING_EMOJI = re.compile(r"^\s*<a?:[A-Za-z0-9_]{2,32}:\d{15,21}>")
ICON_PLACEHOLDER = re.compile(r"\{icon_[a-z0-9_]+\}")
PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
MISSING_MARKER = re.compile(r"⟦[^⟧]*⟧")
# Discord markup that is not text: mentions, channel links, timestamps, custom emoji, links, code spans.
_MARKUP = re.compile(r"<[@#][!&]?\d+>|<t:\d+(?::[a-zA-Z])?>|<a?:\w+:\d+>|https?://\S+|`[^`]*`")
SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
INTERNAL_WORDS = re.compile(r"\b(?:None|True|False|null|Traceback|Exception|KeyError|ValueError)\b|\bsb:[a-z]")
DOUBLE_SPACE = re.compile(r"\S {2,}\S")
SPACE_BEFORE_PUNCTUATION = re.compile(r"\w \s*[,.!?;:](?:\s|$)")

SAMPLE_DATA = "[sample "  # how previews write data people type

# Accepted exceptions: (rule, feature, state name or prefix ending in "*", reason). Keep this short.
EXCEPTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "L1",
        "catalog",
        "listing_message_*",
        "catalog.products_per_listing_message (1-10) puts that many product cards in one listing message on purpose",
    ),
    ("W1", "shipping", "shipping_message_*", "a region's country list is one long line of names by nature"),
)


@dataclass(frozen=True)
class Finding:
    rule: str
    feature: str
    state: str
    where: str
    message: str


# --- helpers -------------------------------------------------------------------------------------------------------


def _visible(text: str) -> str:
    """``text`` with each piece of Discord markup (a mention, timestamp, custom emoji, link or code span) as one
    neutral character, so spacing and words around it are checked as customers see them."""
    return _MARKUP.sub("¤", text)


def is_synthetic(state: PreviewState) -> bool:
    """A stress-test state filled with synthetic sample text (``example_*``: the longest or most sections a guide can
    have, an example panel): only the icon rules apply to it."""
    return "example" in state.name


def is_customer_message(spec: MessageSpec, footer_text: str | None) -> bool:
    """Whether ``spec`` is a customer card (see the module docstring)."""
    if not spec.embeds:
        return True
    first = spec.embeds[0]
    if footer_text and first.footer_text and first.footer_text.startswith(footer_text):
        return True
    return first.timestamp is None or bool(first.footer_text)


def _raw_buttons(spec: MessageSpec) -> Iterator[ButtonSpec]:
    for row in spec.rows:
        for component in row:
            if isinstance(component, ButtonSpec):
                yield component


def _buttons(spec: MessageSpec) -> Iterator[ButtonSpec]:
    for row in spec.rows:
        for component in row:
            if isinstance(component, ButtonSpec):
                yield split_button(component)


def _selects(spec: MessageSpec) -> Iterator[SelectSpec]:
    for row in spec.rows:
        for component in row:
            if isinstance(component, SelectSpec):
                yield component


def _texts(embed: EmbedSpec) -> Iterator[tuple[str, str]]:
    """(where, text) of every text of an embed that customers read."""
    if embed.title:
        yield "title", embed.title
    if embed.description:
        yield "description", embed.description
    for index, item in enumerate(embed.fields, start=1):
        yield f"field {index} name", item.name
        yield f"field {index} value", item.value
    if embed.footer_text:
        yield "footer", embed.footer_text


# --- rules ---------------------------------------------------------------------------------------------------------


def check_message(spec: MessageSpec, *, customer: bool) -> list[tuple[str, str, str]]:
    """(rule, where, message) for one message."""
    out: list[tuple[str, str, str]] = []
    for number, embed in enumerate(spec.embeds, start=1):
        at = f"embed {number}"
        # I1: icons where Discord prints them as raw text.
        for part, text in (("title", embed.title), ("author", embed.author_name), ("footer", embed.footer_text)):
            if text and (CUSTOM_EMOJI.search(text) or ICON_PLACEHOLDER.search(text)):
                out.append(("I1", f"{at} {part}", f"icon in the {part} (Discord cannot show it there): {text!r}"))
        for where, text in _texts(embed):
            out += [(rule, f"{at} {where}", message) for rule, message in check_text(text, customer=customer)]
        if not customer:
            continue
        if embed.title:
            title = CUSTOM_EMOJI.sub("", embed.title).strip()
            if len(title) > TITLE_MAX:
                out.append(("L2", f"{at} title", f"title longer than {TITLE_MAX} characters ({len(title)})"))
            if title.endswith(".") and not title.endswith("..."):
                out.append(("L2", f"{at} title", f"title ends with a period: {title!r}"))
        inline = sum(1 for item in embed.fields if item.inline)
        if inline > INLINE_FIELDS_MAX:
            out.append(("L5", at, f"{inline} inline fields (at most {INLINE_FIELDS_MAX}; use a section instead)"))
        for where, text in _texts(embed):
            for line in text.split("\n"):
                length = len(_visible(line).strip())
                # "[sample ...]" stands for what a customer or staff member typed (a comment, an answer, a reason).
                if length > PARAGRAPH_MAX and SAMPLE_DATA not in line:
                    out.append(("W1", f"{at} {where}", f"a paragraph of {length} characters: {line[:60]!r}…"))
    if customer and len(spec.embeds) > EMBEDS_MAX:
        out.append(("L1", "message", f"{len(spec.embeds)} embeds (one card per message, at most {EMBEDS_MAX})"))
    for raw in _raw_buttons(spec):
        # The text as written: a custom emoji after its start would be left out when sent (it cannot show there).
        rest = LEADING_EMOJI.sub("", raw.label or "", count=1)
        if CUSTOM_EMOJI.search(rest) or ICON_PLACEHOLDER.search(rest):
            out.append(("I1", "button", f"an icon that is not at the start of the label: {raw.label!r}"))
    for button in _buttons(spec):
        label = button.label or ""
        out += [("W4", "button", message) for rule, message in check_text(label, customer=False) if rule == "W4"]
        visible = CUSTOM_EMOJI.sub("", label).strip()
        if customer and len(visible) > BUTTON_LABEL_MAX:
            out.append(("B1", "button", f"label longer than {BUTTON_LABEL_MAX} characters: {visible!r}"))
    for select in _selects(spec):
        if select.placeholder and (
            CUSTOM_EMOJI.search(select.placeholder) or ICON_PLACEHOLDER.search(select.placeholder)
        ):
            out.append(("I1", "menu", f"icon in the menu placeholder: {select.placeholder!r}"))
        for option in (split_option(o) for o in select.options):
            for text in (option.label, option.description or ""):
                if CUSTOM_EMOJI.search(text) or ICON_PLACEHOLDER.search(text):
                    out.append(("I1", "menu option", f"an icon inside an option text: {text!r}"))
    return out


def check_modal(spec: ModalSpec) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    texts = [("form title", spec.title)]
    for item in spec.inputs:
        texts += [("input label", item.label), ("input description", item.description or "")]
        texts += [("input placeholder", item.placeholder or "")]
    for upload in spec.uploads:
        texts += [("upload label", upload.label), ("upload description", upload.description or "")]
    for where, text in texts:
        if CUSTOM_EMOJI.search(text) or ICON_PLACEHOLDER.search(text):
            out.append(("I1", where, f"icon in a form (Discord cannot show it there): {text!r}"))
        out += [(rule, where, message) for rule, message in check_text(text, customer=False)]
    return out


def check_text(text: str, *, customer: bool) -> list[tuple[str, str]]:
    """(rule, message) for one text: leftovers for everyone (W4); internal words and spacing for customers (W3,
    W4)."""
    out: list[tuple[str, str]] = []
    if MISSING_MARKER.search(text):
        out.append(("W4", f"text not configured: {MISSING_MARKER.search(text).group(0)}"))  # type: ignore[union-attr]
    if ICON_PLACEHOLDER.search(text):
        out.append(("W4", f"icon placeholder shown as text: {ICON_PLACEHOLDER.search(text).group(0)}"))  # type: ignore[union-attr]
    visible = _visible(ICON_PLACEHOLDER.sub("¤", text))  # an icon placeholder is reported once, above
    leftover = PLACEHOLDER.search(visible)
    if leftover:
        out.append(("W4", f"placeholder shown as text: {leftover.group(0)}"))
    if not customer:
        return out
    for line in text.split("\n"):
        if line != line.rstrip():
            out.append(("W4", f"trailing spaces: {line!r}"))
            break
    if DOUBLE_SPACE.search(visible):
        out.append(("W4", f"double space: {DOUBLE_SPACE.search(visible).group(0)!r}"))  # type: ignore[union-attr]
    if SPACE_BEFORE_PUNCTUATION.search(visible):
        found = SPACE_BEFORE_PUNCTUATION.search(visible)
        out.append(("W4", f"space before punctuation: {found.group(0)!r}"))  # type: ignore[union-attr]
    if text != text.strip("\n"):
        out.append(("W4", "empty line at the start or end"))
    words = PLACEHOLDER.sub("¤", visible)  # a leftover placeholder is reported once, above
    internal = SNAKE_CASE.search(words) or INTERNAL_WORDS.search(words)
    if internal:
        out.append(("W3", f"internal word shown to customers: {internal.group(0)!r}"))
    return out


def check_consistency(states: Iterable[tuple[PreviewState, bool]]) -> list[Finding]:
    """I2: a button label always comes with the same icon (on customer messages)."""
    seen: dict[str, Counter[str]] = defaultdict(Counter)
    where: dict[tuple[str, str], tuple[str, str]] = {}
    for state, customer in states:
        if not customer or not isinstance(state.spec, MessageSpec):
            continue
        for button in _buttons(state.spec):
            label = (button.label or "").strip()
            if not label:
                continue
            emoji = button.emoji or ""
            emoji_name = CUSTOM_EMOJI.sub(lambda m: m.group(0).split(":")[1], emoji)
            seen[label][emoji_name] += 1
            where.setdefault((label, emoji_name), (state.feature, state.name))
    findings = []
    for label, emojis in sorted(seen.items()):
        if len(emojis) > 1:
            variants = ", ".join(f"{name or 'no icon'} ×{count}" for name, count in emojis.most_common())
            feature, name = where[(label, emojis.most_common()[-1][0])]
            findings.append(Finding("I2", feature, name, "button", f"{label!r} has different icons: {variants}"))
    return findings


def _excepted(finding: Finding) -> bool:
    for rule, feature, state, _ in EXCEPTIONS:
        if rule != finding.rule or feature != finding.feature:
            continue
        if state == finding.state or (state.endswith("*") and finding.state.startswith(state[:-1])):
            return True
    return False


def check_states(states: Sequence[PreviewState], footer_text: str | None) -> list[Finding]:
    findings: list[Finding] = []
    audiences: list[tuple[PreviewState, bool]] = []
    for state in states:
        spec = state.spec
        synthetic = is_synthetic(state)
        if isinstance(spec, ModalSpec):
            problems = check_modal(spec)
            audiences.append((state, False))
        elif isinstance(spec, MessageSpec):
            customer = is_customer_message(spec, footer_text) and not synthetic
            problems = check_message(spec, customer=customer)
            audiences.append((state, customer))
        else:
            continue
        if synthetic:
            problems = [p for p in problems if p[0] == "I1"]
        findings += [Finding(rule, state.feature, state.name, where, message) for rule, where, message in problems]
    findings += check_consistency(audiences)
    return [f for f in findings if not _excepted(f)]


async def collect(config_dir: Path, feature: str | None) -> tuple[list[PreviewState], str | None]:
    app = await build_offline_app(config_dir, content_mode="markers", all_features=True)
    try:
        states: list[PreviewState] = []
        for module in app.modules:
            if module.preview is None or (feature and module.name != feature):
                continue
            states += module.preview(app)
        footer = app.content.get("branding.footer_text") if "branding.footer_text" in app.content.registry else None
        return states, footer
    finally:
        await app.db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--feature", help="only this feature's states")
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    parser.add_argument("--json", action="store_true", help="one JSON object per finding")
    args = parser.parse_args(argv)
    try:
        states, footer = asyncio.run(collect(args.config_dir, args.feature))
    except (SettingsError, ContentFileError) as exc:
        print("Configuration cannot be loaded:")
        for error in exc.errors:
            print(f"  - {error}")
        return 2
    findings = check_states(states, footer)
    if args.json:
        for finding in findings:
            print(json.dumps(asdict(finding), ensure_ascii=False))
    else:
        for finding in findings:
            print(f"{finding.rule:3} [{finding.feature}] {finding.state} · {finding.where}: {finding.message}")
        counts = Counter(f.rule for f in findings)
        summary = ", ".join(f"{rule} {count}" for rule, count in sorted(counts.items())) or "none"
        print(f"\n{len(states)} states checked; findings: {summary}")
        print("RESULT: OK" if not findings else "RESULT: FINDINGS (see docs/QUALITY_STANDARDS.md)")
    return 1 if (findings and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
