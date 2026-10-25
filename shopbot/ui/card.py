"""One message, one card: the layout of the bot's customer messages.

A ``Card`` is one embed: a title, an introduction, then ``Section``s (a bold heading, usually starting with one of the
bot's icons, over a few lines), then optional key facts as fields (order number, total, status), with the branded
footer. Sections are separated by a blank line; there are no stacks of small embeds.

``card_embeds`` / ``card_messages`` turn cards into embeds and messages within Discord's limits (4096-character
descriptions, 25 fields and 6000 characters per embed; 10 embeds and 6000 characters per message) without ever
cutting text: a card longer than one embed continues in the next embed of the same colour (its title is not
repeated), and embeds that do not fit one message continue in the next message. Only the first embed carries the
author line and footer, and only the first message the banner.

``Block`` is the lower-level unit the guides build directly (a title and description followed by lines or fields);
a card is one block whose lines are its sections. In ``Block.lines`` an empty string starts a new paragraph.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from shopbot.config.banner_ids import BannerId
from shopbot.ui import limits
from shopbot.ui.spec import Component, EmbedField, EmbedSpec, MessageSpec
from shopbot.ui.style import Ui

PARAGRAPH = ""  # a ``Block.lines`` entry that starts a new paragraph (a blank line)


def _trim_paragraphs(lines: tuple[str, ...]) -> list[str]:
    """``lines`` without leading or trailing paragraph breaks (``PARAGRAPH`` may separate paragraphs inside)."""
    start, end = 0, len(lines)
    while start < end and lines[start] == PARAGRAPH:
        start += 1
    while end > start and lines[end - 1] == PARAGRAPH:
        end -= 1
    return list(lines[start:end])


@dataclass(frozen=True)
class Section:
    """A part of a card: a heading shown in bold (None: the lines only) over its lines; a ``PARAGRAPH`` line
    separates paragraphs inside the section."""

    heading: str | None = None
    lines: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not self.heading and not any(self.lines)


def heading(text: str) -> str:
    """A section heading as shown on a card. The text usually starts with an icon (``{icon_<name>}``)."""
    return f"**{text}**"


@dataclass(frozen=True)
class Card:
    tone: str = "primary"
    title: str | None = None
    intro: str | None = None
    sections: tuple[Section, ...] = ()
    fields: tuple[EmbedField, ...] = ()
    timestamp: bool = False
    footer_extra: str | None = None  # after the branded footer text, e.g. a page indicator
    image_url: str | None = None
    thumbnail_url: str | None = None

    def block(self) -> Block:
        lines: list[str] = []
        for section in self.sections:
            if section.is_empty():
                continue
            if lines:
                lines.append(PARAGRAPH)
            if section.heading:
                lines.append(heading(section.heading))
            lines.extend(_trim_paragraphs(section.lines))
        return Block(
            tone=self.tone,
            title=self.title,
            description=self.intro,
            lines=tuple(lines),
            fields=self.fields,
            timestamp=self.timestamp,
            footer_extra=self.footer_extra,
            image_url=self.image_url,
            thumbnail_url=self.thumbnail_url,
        )


@dataclass(frozen=True)
class Block:
    """One card of a guide: title and description on its first embed, then ``lines`` (appended to the description;
    ``PARAGRAPH`` starts a new paragraph) and ``fields``. A block that does not fit one embed continues in further
    embeds (without repeating its title)."""

    tone: str
    title: str | None = None
    description: str | None = None
    fields: tuple[EmbedField, ...] = ()
    lines: tuple[str, ...] = ()
    timestamp: bool = False
    footer_extra: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None

    def is_empty(self) -> bool:
        return not (self.title or self.description or self.fields or any(self.lines))


class _EmbedBuilder:
    def __init__(self, ui: Ui) -> None:
        self.ui = ui
        self.embeds: list[EmbedSpec] = []
        self._branded = False
        self.block = Block(tone="primary")
        self._reset(None, None, branding=False)

    def _reset(self, title: str | None, description: str | None, *, branding: bool) -> None:
        self.title = title
        self.description = description
        self.fields: list[EmbedField] = []
        self.branding = branding

    def _spec(self, description: str | None, fields: Sequence[EmbedField]) -> EmbedSpec:
        block = self.block
        return self.ui.embed(
            title=self.title,
            description=description,
            tone=block.tone,
            fields=fields,
            panel=self.branding,
            branding_text=self.branding,
            timestamp=block.timestamp and self.branding,
            footer_extra=block.footer_extra if self.branding else None,
            image_url=block.image_url if self.branding else None,
            thumbnail_url=block.thumbnail_url if self.branding else None,
        )

    def _length(self, description: str | None, fields: Sequence[EmbedField]) -> int:
        return self._spec(description, fields).text_length()

    def _pending(self) -> bool:
        return bool(self.title or self.description or self.fields)

    def flush(self) -> None:
        if self._pending():
            self.embeds.append(self._spec(self.description, self.fields))
        self._reset(None, None, branding=False)

    def start(self, block: Block) -> None:
        self.flush()
        self.block = block
        branding = not self._branded
        self._branded = True
        self._reset(block.title, block.description, branding=branding)

    def add_line(self, line: str, *, paragraph: bool) -> None:
        separator = ("\n\n" if paragraph else "\n") if self.description else ""
        candidate = f"{self.description or ''}{separator}{line}"
        fits = (
            len(candidate) <= limits.EMBED_DESCRIPTION
            and not self.fields
            and self._length(candidate, self.fields) <= limits.EMBED_TOTAL_CHARS
        )
        if not fits and self._pending():
            self.flush()
            candidate = line
        self.description = candidate

    def add_field(self, item: EmbedField) -> None:
        full = len(self.fields) >= limits.EMBED_FIELDS
        too_long = self._length(self.description, [*self.fields, item]) > limits.EMBED_TOTAL_CHARS
        if (full or too_long) and self._pending():
            self.flush()
        self.fields.append(item)


def build_embeds(ui: Ui, blocks: Iterable[Block]) -> list[EmbedSpec]:
    """Embeds for ``blocks`` in order. The first embed carries the branding (author, footer, panel images)."""
    builder = _EmbedBuilder(ui)
    for block in blocks:
        if block.is_empty():
            continue
        builder.start(block)
        paragraph = True
        for line in block.lines:
            if line == PARAGRAPH:
                paragraph = True
                continue
            builder.add_line(line, paragraph=paragraph)
            paragraph = False
        for item in block.fields:
            builder.add_field(item)
    builder.flush()
    return builder.embeds


def pack_messages(embeds: Sequence[EmbedSpec]) -> list[tuple[EmbedSpec, ...]]:
    """Group embeds into messages of at most 10 embeds and 6000 characters, keeping their order."""
    messages: list[tuple[EmbedSpec, ...]] = []
    current: list[EmbedSpec] = []
    used = 0
    for embed in embeds:
        length = embed.text_length()
        if current and (len(current) >= limits.EMBEDS_PER_MESSAGE or used + length > limits.EMBED_TOTAL_CHARS):
            messages.append(tuple(current))
            current, used = [], 0
        current.append(embed)
        used += length
    if current:
        messages.append(tuple(current))
    return messages


def block_messages(
    ui: Ui,
    blocks: Iterable[Block],
    rows: tuple[tuple[Component, ...], ...] = (),
    *,
    banner: BannerId | None,
) -> list[MessageSpec]:
    """The messages of ``blocks``; the ``banner`` picture tops the first message only and ``rows`` (buttons) are
    attached to the last message."""
    specs = [MessageSpec(embeds=group) for group in pack_messages(build_embeds(ui, blocks))]
    if specs and banner is not None:
        specs[0] = replace(specs[0], banner=ui.banner(banner))
    if specs and rows:
        specs[-1] = replace(specs[-1], rows=rows)
    return specs


def card_embeds(ui: Ui, card: Card) -> list[EmbedSpec]:
    return build_embeds(ui, [card.block()])


def card_messages(
    ui: Ui,
    card: Card,
    rows: tuple[tuple[Component, ...], ...] = (),
    *,
    banner: BannerId | None,
) -> list[MessageSpec]:
    """The card as messages: one message with one embed unless its text is longer than Discord allows in one."""
    return block_messages(ui, [card.block()], rows, banner=banner)


def card_message(
    ui: Ui,
    card: Card,
    rows: tuple[tuple[Component, ...], ...] = (),
    *,
    banner: BannerId | None,
    **extra: object,
) -> MessageSpec:
    """The card as a single message (replies and live panels, whose text always fits one message: at most 10
    embeds). ``extra`` sets further ``MessageSpec`` fields (mentions, files)."""
    embeds = tuple(card_embeds(ui, card))  # more than one message's worth is refused by the limits check, never cut
    spec = MessageSpec(embeds=embeds, rows=rows, banner=ui.banner(banner) if banner is not None else None)
    return replace(spec, **extra) if extra else spec  # type: ignore[arg-type]


def with_paragraph(embed: EmbedSpec, text: str | None) -> tuple[EmbedSpec, ...]:
    """``embed`` with ``text`` added as its last paragraph, so a note stays part of one card; a second card only when
    the combined text would be longer than Discord allows in one embed (nothing is cut)."""
    if not text:
        return (embed,)
    description = f"{embed.description}\n\n{text}" if embed.description else text
    if len(description) > limits.EMBED_DESCRIPTION or embed.text_length() + len(text) + 2 > limits.EMBED_TOTAL_CHARS:
        return (embed, EmbedSpec(description=text, color=embed.color))
    return (replace(embed, description=description),)
