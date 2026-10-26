"""A button or select option label that starts with a custom emoji shows it as the component's emoji.

Discord shows a component's emoji before its label, but a custom emoji written inside a label only as raw text
(``<:cart:123…>``). A button or option label may therefore start with one of the bot's icons (``{icon_cart} Start
order``, which ``ContentStore.render`` turns into ``<:cart:123…> Start order``): ``split_button`` / ``split_option``
move that emoji out of the label into the component's emoji, so Discord shows the cart icon followed by "Start order".

The split happens where components are checked and sent (``ui.limits``, ``ui.discord_render.to_view``, the HTML
preview), so the label limits apply to the label without its emoji and what is checked is what is sent. The specs
themselves keep the label as rendered (panel change detection compares them unchanged).

Embed titles, author lines and footers cannot show custom emoji at all (Discord prints the raw ``<:name:id>``), so
``strip_custom_emoji`` leaves them out there (``ui.discord_render.to_embed`` and the HTML preview); descriptions,
field names and field values show them.
"""

from __future__ import annotations

import re
from dataclasses import replace

from shopbot.ui.spec import ButtonSpec, SelectOptionSpec

_LEADING_EMOJI_RE = re.compile(r"^\s*(<a?:[A-Za-z0-9_]{2,32}:\d{15,21}>)")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,21}>[ \t]*")


def split_label(label: str | None, emoji: str | None) -> tuple[str | None, str | None]:
    """``(label, emoji)`` with a custom emoji at the very start of ``label`` moved into ``emoji``.

    The rest of the label is trimmed; nothing left (an icon-only label) gives no label. When the component already
    has an emoji (an ``emoji_key``), that emoji wins and the label's icon is left out. A label without a leading
    custom emoji is returned unchanged (a fallback such as a plain dot stays part of the label)."""
    if not label:
        return label, emoji
    match = _LEADING_EMOJI_RE.match(label)
    if match is None:
        return _without_inner_emoji(label), emoji
    rest = _without_inner_emoji(label[match.end() :].strip())
    return rest or None, emoji or match.group(1)


def _without_inner_emoji(label: str) -> str:
    """A label without custom emoji after its start (e.g. an icon inside a placeholder value such as an extra's name
    in "{icon_plus} {label}"), which Discord would show as raw text."""
    return _CUSTOM_EMOJI_RE.sub("", label).strip() if _CUSTOM_EMOJI_RE.search(label) else label


def split_button(button: ButtonSpec) -> ButtonSpec:
    """``button`` with a leading custom emoji of its label as its emoji (any style, link buttons included)."""
    label, emoji = split_label(button.label, button.emoji)
    if label == button.label and emoji == button.emoji:
        return button
    return replace(button, label=label, emoji=emoji)


def split_option(option: SelectOptionSpec) -> SelectOptionSpec:
    """``option`` with a leading custom emoji of its label as its emoji. An option needs a label, so one that would
    have none left (only an emoji) is returned unchanged."""
    label, emoji = split_label(option.label, option.emoji)
    if not label or (label == option.label and emoji == option.emoji):
        return option
    return replace(option, label=label, emoji=emoji)


def strip_custom_emoji(text: str | None) -> str | None:
    """``text`` without its custom emoji (each with the spaces after it), for the parts of an embed where Discord
    cannot show them; None when nothing is left. Unicode emoji are kept, and text without custom emoji is returned
    unchanged."""
    if not text or _CUSTOM_EMOJI_RE.search(text) is None:
        return text
    stripped = _CUSTOM_EMOJI_RE.sub("", text).strip()
    return stripped or None
