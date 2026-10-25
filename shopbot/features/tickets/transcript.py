"""Ticket transcripts: a self-contained HTML file that contains only data.

The file holds the ticket number, created/closed timestamps (UTC, ISO 8601), the participants, and for
every message its timestamp, author display name and id, content, attachment URLs, and embed
titles/descriptions/fields. It contains no invented wording (no headings, labels, or explanations -
structure is conveyed by markup and CSS only) and no internal identifiers such as the category id: the
same file may be sent to the customer. Every value is escaped with
``html.escape``; links are only emitted for http(s) URLs.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from shopbot.core.clock import to_datetime


@dataclass(frozen=True)
class TranscriptEmbed:
    title: str | None
    description: str | None
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TranscriptMessage:
    id: int
    created_at: datetime
    author_id: int
    author_name: str
    content: str
    attachments: tuple[str, ...] = ()
    embeds: tuple[TranscriptEmbed, ...] = ()


@dataclass(frozen=True)
class TranscriptParticipant:
    user_id: int
    name: str | None


class _History(Protocol):
    def history(self, *, limit: int | None = ..., oldest_first: bool | None = ...) -> AsyncIterator[Any]: ...


def _iso(value: datetime | int | None) -> str | None:
    if value is None:
        return None
    moment = to_datetime(value) if isinstance(value, int) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _time(css_class: str, value: datetime | int | None) -> str:
    text = _iso(value)
    if text is None:
        return ""
    return f'<time class="{css_class}" datetime="{_e(text)}">{_e(text)}</time>'


def _link(url: str) -> str:
    if url.startswith(("https://", "http://")):
        return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>'
    return f"<span>{_e(url)}</span>"


async def collect_messages(channel: _History) -> list[TranscriptMessage]:
    """Read the whole channel history, oldest first."""
    messages: list[TranscriptMessage] = []
    async for message in channel.history(limit=None, oldest_first=True):
        author = message.author
        embeds = tuple(
            TranscriptEmbed(
                title=embed.title or None,
                description=embed.description or None,
                fields=tuple((str(f.name or ""), str(f.value or "")) for f in embed.fields),
            )
            for embed in message.embeds
        )
        messages.append(
            TranscriptMessage(
                id=int(message.id),
                created_at=message.created_at,
                author_id=int(author.id),
                author_name=str(getattr(author, "display_name", None) or getattr(author, "name", "")),
                content=str(message.content or ""),
                attachments=tuple(str(a.url) for a in message.attachments),
                embeds=embeds,
            )
        )
    return messages


_CSS = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:24px;color:#1f2328;background:#fff}"
    "header{border-bottom:1px solid #d0d7de;margin-bottom:16px;padding-bottom:8px}"
    ".ticket .number{font-size:1.4em;font-weight:600;margin-right:12px}"
    ".id,.author-id{color:#57606a;font-family:ui-monospace,monospace}"
    "time{color:#57606a;margin-right:12px}"
    "ul{margin:4px 0;padding-left:20px}"
    ".message{border-left:3px solid #d0d7de;margin:10px 0;padding:4px 10px}"
    ".author{font-weight:600;margin-right:8px}"
    ".content{white-space:pre-wrap;word-wrap:break-word;margin:4px 0}"
    ".embed{border-left:3px solid #8c959f;background:#f6f8fa;margin:4px 0;padding:4px 8px}"
    ".embed-title{font-weight:600;white-space:pre-wrap}"
    ".embed-description,.embed-fields dd{white-space:pre-wrap;margin:0 0 4px 0}"
    ".embed-fields dt{font-weight:600}"
    "@media (prefers-color-scheme:dark){body{color:#e6edf3;background:#0d1117}"
    ".embed{background:#161b22}time,.id,.author-id{color:#8b949e}}"
)


def build_html(
    *,
    number: str,
    created_at: int,
    closed_at: int | None,
    participants: Sequence[TranscriptParticipant],
    messages: Sequence[TranscriptMessage],
) -> str:
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(number)}</title><style>{_CSS}</style></head><body>",
        f'<header data-number="{_e(number)}">',
        f'<p class="ticket"><span class="number">{_e(number)}</span></p>',
        f'<p class="times">{_time("created", created_at)}{_time("closed", closed_at)}</p>',
        '<ul class="participants">',
    ]
    for person in participants:
        name = f'<span class="name">{_e(person.name)}</span> ' if person.name else ""
        parts.append(f'<li data-user-id="{person.user_id}">{name}<span class="id">{person.user_id}</span></li>')
    parts.append("</ul></header><main>")
    for message in messages:
        parts.append(f'<article class="message" id="m{message.id}" data-author-id="{message.author_id}">')
        parts.append(
            f'<p class="meta">{_time("sent", message.created_at)}<span class="author">{_e(message.author_name)}'
            f'</span><span class="author-id">{message.author_id}</span></p>'
        )
        if message.content:
            parts.append(f'<div class="content">{_e(message.content)}</div>')
        if message.attachments:
            parts.append('<ul class="attachments">')
            parts.extend(f"<li>{_link(url)}</li>" for url in message.attachments)
            parts.append("</ul>")
        for embed in message.embeds:
            parts.append('<section class="embed">')
            if embed.title:
                parts.append(f'<p class="embed-title">{_e(embed.title)}</p>')
            if embed.description:
                parts.append(f'<div class="embed-description">{_e(embed.description)}</div>')
            if embed.fields:
                parts.append('<dl class="embed-fields">')
                for name, value in embed.fields:
                    parts.append(f"<dt>{_e(name)}</dt><dd>{_e(value)}</dd>")
                parts.append("</dl>")
            parts.append("</section>")
        parts.append("</article>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def participants_of(
    owner_id: int, added: Sequence[int], messages: Sequence[TranscriptMessage], names: dict[int, str]
) -> list[TranscriptParticipant]:
    """Owner first, then added members, then everyone else who wrote in the channel (first appearance)."""
    known = dict(names)
    for message in messages:
        known.setdefault(message.author_id, message.author_name)
    order: list[int] = []
    for user_id in (owner_id, *added, *(m.author_id for m in messages)):
        if user_id not in order:
            order.append(user_id)
    return [TranscriptParticipant(user_id=uid, name=known.get(uid) or None) for uid in order]
