"""The /setup report: collected lines plus a pure packer that turns them into limit-respecting messages.

The report is operator output (admin-only, ephemeral). It can be long on large servers, so its lines are
grouped into embed fields with ``chunk_lines``; fields are packed into embeds (25 fields each), embeds into
messages (10 embeds and 6000 characters each), and any remainder goes into additional messages. No line
is ever cut: a single line longer than a field value is split across consecutive lines.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from shopbot.ui import limits
from shopbot.ui.spec import EmbedField, EmbedSpec, MessageSpec
from shopbot.ui.style import Ui, chunk_lines

SECTIONS = ("roles", "categories", "channels", "panels", "problems")
COUNTERS = ("created", "adopted", "updated", "unchanged", "skipped", "failed")

Op = Callable[..., str]


@dataclass
class SetupReport:
    dry_run: bool = False
    publish: bool = True
    lines: dict[str, list[str]] = field(default_factory=lambda: {section: [] for section in SECTIONS})
    counts: Counter[str] = field(default_factory=Counter)

    def add(self, section: str, line: str, status: str | None = None) -> None:
        self.lines[section].append(line)
        if status is not None:
            self.counts[status] += 1

    def count_values(self) -> dict[str, int]:
        return {name: self.counts.get(name, 0) for name in COUNTERS}

    def audit_details(self) -> dict[str, int | bool]:
        details: dict[str, int | bool] = {"dry_run": self.dry_run, "publish": self.publish}
        details.update(self.count_values())
        details["panel_lines"] = len(self.lines["panels"])
        details["problems"] = len(self.lines["problems"])
        return details


def split_long_line(line: str, limit: int) -> list[str]:
    """Split a line longer than ``limit`` into consecutive pieces (nothing is dropped)."""
    if len(line) <= limit:
        return [line]
    return [line[start : start + limit] for start in range(0, len(line), limit)]


def report_fields(report: SetupReport, op: Op) -> list[EmbedField]:
    fields: list[EmbedField] = []
    for section in SECTIONS:
        lines = [piece for line in report.lines[section] for piece in split_long_line(line, limits.EMBED_FIELD_VALUE)]
        if not lines:
            lines = [op("server.setup.none")]
        name = op(f"server.setup.section.{section}")
        fields.extend(EmbedField(name, block) for block in chunk_lines(lines, limits.EMBED_FIELD_VALUE))
    return fields


def pack_fields(
    ui: Ui,
    *,
    title: str,
    description: str | None,
    continued_title: str,
    fields: Sequence[EmbedField],
    tone: str = "info",
) -> list[MessageSpec]:
    """Pack fields into as few messages as Discord's limits allow (first embed carries the title)."""
    messages: list[MessageSpec] = []
    embeds: list[EmbedSpec] = []
    current: list[EmbedField] = []
    head_title: str | None = title
    head_description = description
    used = len(title) + len(description or "")

    def embed_of(head: str | None, body: str | None, items: list[EmbedField]) -> EmbedSpec:
        embed = ui.operator_embed(head or "", body, tone=tone, fields=items)
        return embed if head else replace(embed, title=None)

    for item in fields:
        size = len(item.name) + len(item.value)
        embed_full = len(current) >= limits.EMBED_FIELDS
        message_full = used + size > limits.EMBED_TOTAL_CHARS or (
            embed_full and len(embeds) + 1 >= limits.EMBEDS_PER_MESSAGE
        )
        if message_full:
            embeds.append(embed_of(head_title, head_description, current))
            messages.append(MessageSpec(embeds=tuple(embeds)))
            embeds, current = [], []
            head_title, head_description = continued_title, None
            used = len(continued_title)
        elif embed_full:
            embeds.append(embed_of(head_title, head_description, current))
            current = []
            head_title = head_description = None
        current.append(item)
        used += size
    embeds.append(embed_of(head_title, head_description, current))
    messages.append(MessageSpec(embeds=tuple(embeds)))
    return messages


def report_messages(ui: Ui, op: Op, report: SetupReport) -> list[MessageSpec]:
    counts = report.count_values()
    if report.dry_run:
        title = op("server.setup.title_dry_run")
        summary = op("server.setup.summary_dry_run", **counts)
    else:
        title = op("server.setup.title")
        summary = op("server.setup.summary", **counts)
    tone = "danger" if counts["failed"] else ("warning" if report.lines["problems"] else "success")
    return pack_fields(
        ui,
        title=title,
        description=summary,
        continued_title=op("server.setup.title_continued"),
        fields=report_fields(report, op),
        tone=tone,
    )
