"""Pure view builders for the admin tools (operator text only; no Discord calls).

Every builder takes the ``Ui`` (for operator embeds and banners) and the operator-text function (``app.op``) plus
plain data, and returns ``MessageSpec``/``ModalSpec`` objects that respect Discord's limits: long lists are split
into fields and pages, never cut mid-text except operator diagnostics (audit details, task errors) that are
explicitly clipped. Every reply carries the banner of its kind (dashboard, confirmation, error...); a reply split
across several messages shows it on the first message only, and a paginated message keeps it on every page.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.readiness import FeatureStatus, Problem
from shopbot.db.repos.audit import AuditEvent
from shopbot.db.repos.tasks import Task
from shopbot.features.admin.service import (
    ClearResult,
    FeatureLine,
    KeyInfo,
    KeyRow,
    PanelRefresh,
    ReadinessChange,
    ReloadReport,
    StatusSnapshot,
)
from shopbot.ui import limits
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
)
from shopbot.ui.style import Ui, chunk_lines

ROUTE = "ad"
ALL_SCOPE = "-"  # custom_id argument meaning "all features"
VALUE_INPUT_ID = "value"
PAGE_BUDGET = 3500  # characters of fields per page; leaves room for title/description/footer within 6000
MAX_FIELDS_PER_PAGE = 10
LIST_FIELD_BUDGET = 1000
MESSAGE_MARGIN = 100  # headroom below the 6000-character total for footers and timestamps
AUDIT_DETAILS_MAX = 300
PROBLEM_KINDS = frozenset({"missing_content", "invalid_content", "settings", "dependency"})
TASK_ERROR_MAX = 300


class OpFn(Protocol):
    def __call__(self, key: str, /, **values: object) -> str: ...


@dataclass(frozen=True)
class Reply:
    """A message plus an optional text attachment ``(filename, text)``."""

    spec: MessageSpec
    attachment: tuple[str, str] | None = None


@dataclass(frozen=True)
class Section:
    title: str
    lines: tuple[str, ...]


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def clip(text: str, limit: int) -> str:
    """Shorten operator diagnostics to ``limit`` characters (never used for configured content values)."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def code_block(value: str, limit: int = limits.EMBED_DESCRIPTION) -> str | None:
    """``value`` in a code block, or None if it cannot be shown exactly that way (backticks, length)."""
    if "```" in value or value.startswith("`") or value.endswith("`"):
        return None
    block = f"```\n{value}\n```"
    return block if len(block) <= limit else None


def inline_code(text: str) -> str:
    return f"`{text}`" if text and "`" not in text and "\n" not in text else escape_markdown(text)


def yes_no(op: OpFn, flag: bool) -> str:
    return op("admin.yes") if flag else op("admin.no")


def scope_arg(feature: str | None) -> str:
    return feature if feature else ALL_SCOPE


def with_footer(embed: EmbedSpec, text: str | None) -> EmbedSpec:
    return replace(embed, footer_text=text) if text else embed


def clamp_page(page: int, pages: int) -> int:
    return max(1, min(page, max(1, pages)))


def bounded_list(op: OpFn, items: Sequence[str], limit: int = LIST_FIELD_BUDGET) -> str:
    """Newline-joined items that fit in ``limit`` characters, followed by a count of the rest."""
    if not items:
        return op("admin.none")
    shown: list[str] = []
    used = 0
    for index, item in enumerate(items):
        cost = len(item) + (1 if shown else 0)
        rest_after = len(items) - index - 1
        reserve = len(op("admin.list.more", count=rest_after)) + 1 if rest_after else 0
        if used + cost + reserve > limit:
            shown.append(op("admin.list.more", count=len(items) - index))
            break
        shown.append(item)
        used += cost
    return "\n".join(shown)


def pack_sections(
    op: OpFn,
    sections: Sequence[Section],
    *,
    budget: int | None = None,
    max_fields: int | None = None,
) -> list[tuple[EmbedField, ...]]:
    """Split sections into embed fields (<= 1024 characters each) and fields into pages."""
    budget = PAGE_BUDGET if budget is None else budget
    max_fields = MAX_FIELDS_PER_PAGE if max_fields is None else max_fields
    fields: list[EmbedField] = []
    for section in sections:
        lines = [clip(line, limits.EMBED_FIELD_VALUE) for line in section.lines if line.strip()]
        title = clip(section.title, limits.EMBED_FIELD_NAME)
        for index, block in enumerate(chunk_lines(lines, limits.EMBED_FIELD_VALUE)):
            continued = op("admin.section.continued", title=section.title)
            name = title if index == 0 else clip(continued, limits.EMBED_FIELD_NAME)
            fields.append(EmbedField(name, block))
    pages: list[tuple[EmbedField, ...]] = []
    current: list[EmbedField] = []
    size = 0
    for item in fields:
        length = len(item.name) + len(item.value)
        if current and (size + length > budget or len(current) >= max_fields):
            pages.append(tuple(current))
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        pages.append(tuple(current))
    return pages


def pager_rows(op: OpFn, action: str, scope: str, page: int, pages: int) -> tuple[tuple[ButtonSpec, ...], ...]:
    """Previous/next buttons (admin-only route) for a paginated ephemeral message."""
    if pages <= 1:
        return ()
    previous = ButtonSpec(
        label=clip(op("admin.page.previous"), limits.BUTTON_LABEL),
        custom_id=cid(ROUTE, action, scope, page - 1),
        disabled=page <= 1,
    )
    following = ButtonSpec(
        label=clip(op("admin.page.next"), limits.BUTTON_LABEL),
        custom_id=cid(ROUTE, action, scope, page + 1),
        disabled=page >= pages,
    )
    return ((previous, following),)


def page_footer(op: OpFn, page: int, pages: int) -> str | None:
    return op("admin.page.indicator", page=page, pages=pages) if pages > 1 else None


def paged_messages(
    ui: Ui,
    op: OpFn,
    title: str,
    description: str | None,
    sections: Sequence[Section],
    *,
    tone: str = "info",
    banner: BannerId,
) -> list[MessageSpec]:
    """Several messages (one embed each) when a list does not fit one message; for one-shot replies. The ``banner``
    tops the first message only."""
    pages = pack_sections(op, sections) or [()]
    specs: list[MessageSpec] = []
    for index, fields in enumerate(pages, start=1):
        embed = _embed(ui, title, description if index == 1 else None, tone=tone, fields=fields)
        if embed.is_empty():
            embed = replace(embed, description=op("admin.list.empty"))
        specs.append(
            MessageSpec(
                embeds=(with_footer(embed, page_footer(op, index, len(pages))),),
                banner=ui.banner(banner) if index == 1 else None,
            )
        )
    return specs


def _embed(
    ui: Ui, title: str, description: str | None = None, *, tone: str = "info", fields: Sequence[EmbedField] = ()
) -> EmbedSpec:
    """Operator embed; the title is clipped because operators may override operator text with longer wording."""
    return ui.operator_embed(clip(title, limits.EMBED_TITLE), description, tone=tone, fields=fields)


def simple(
    ui: Ui, op: OpFn, title: str, description: str | None = None, *, tone: str = "info", banner: BannerId
) -> MessageSpec:
    """A one-embed operator reply under ``banner``."""
    return MessageSpec(embeds=(_embed(ui, title, description, tone=tone),), banner=ui.banner(banner))


# --------------------------------------------------------------------------------------------------
# Readiness lines
# --------------------------------------------------------------------------------------------------


def state_text(op: OpFn, line: FeatureLine) -> str:
    if not line.enabled:
        return op("admin.state.disabled")
    if line.ready:
        return op("admin.state.ready")
    return op("admin.state.not_ready", problems=line.problems)


def feature_status_line(op: OpFn, line: FeatureLine) -> str:
    if not line.enabled:
        return op("admin.status.feature_disabled", feature=line.name, problems=line.problems)
    if line.ready:
        return op("admin.status.feature_ready", feature=line.name)
    return op("admin.status.feature_not_ready", feature=line.name, problems=line.problems)


def problem_line(op: OpFn, problem: Problem) -> str:
    message = escape_markdown(problem.message)
    if problem.key is not None:
        template = "admin.problem.keyed_invalid" if problem.kind == "invalid_content" else "admin.problem.keyed"
        return op(template, content_key=problem.key, message=message)
    kind = op(f"admin.problem.kind.{problem.kind}") if problem.kind in PROBLEM_KINDS else problem.kind
    return op("admin.problem.unkeyed", kind=kind, message=message)


def change_line(op: OpFn, change: ReadinessChange) -> str:
    if change.changed:
        return op(
            "admin.content.readiness_change",
            feature=change.feature,
            before=state_text(op, change.before),
            after=state_text(op, change.after),
        )
    return op("admin.content.readiness_same", feature=change.feature, state=state_text(op, change.after))


# --------------------------------------------------------------------------------------------------
# /status
# --------------------------------------------------------------------------------------------------


def status_overview(ui: Ui, op: OpFn, snap: StatusSnapshot) -> MessageSpec:
    enabled = [f for f in snap.features if f.enabled]
    ready = [f for f in enabled if f.ready]
    description = "\n".join(
        [op("admin.status.summary", ready=len(ready), enabled=len(enabled)), op("admin.status.hint")]
    )
    feature_fields = list_field(op, op("admin.status.features"), [feature_status_line(op, f) for f in snap.features])
    if not snap.guild_available:
        permissions = op("admin.no_guild")
    elif snap.missing_permissions:
        permissions = bounded_list(
            op,
            [
                op("admin.status.permissions_missing", capability=cap, permissions=", ".join(names))
                for cap, names in snap.missing_permissions
            ],
        )
    else:
        permissions = op("admin.status.permissions_ok")
    intents = [
        op(
            "admin.status.intents_value",
            message_content=yes_no(op, snap.message_content_intent),
            members=yes_no(op, snap.members_intent),
        )
    ]
    if snap.transcripts_need_intent:
        intents.append(op("admin.status.intent_warning"))
    intents += [op("admin.status.intent_not_enabled", intent=name) for name in snap.intents_not_enabled]
    counts = snap.task_counts
    fields = [
        *feature_fields,
        EmbedField(op("admin.status.unknown_keys"), bounded_list(op, [inline_code(k) for k in snap.unknown_keys])),
        EmbedField(op("admin.status.permissions"), permissions),
        EmbedField(op("admin.status.intents"), "\n".join(intents)),
        EmbedField(
            op("admin.status.tasks"),
            op(
                "admin.status.tasks_value",
                pending=counts.get("pending", 0),
                running=counts.get("running", 0),
                failed=counts.get("failed", 0),
            ),
        ),
        EmbedField(op("admin.status.database"), inline_code(clip(snap.db_path, 1000))),
        *list_field(op, op("admin.status.pictures"), [clip(p, 200) for p in snap.picture_problems]),
    ]
    healthy = len(ready) == len(enabled) and snap.guild_available and not snap.missing_permissions
    tone = "success" if healthy and not snap.unknown_keys and not snap.picture_problems else "warning"
    return MessageSpec(
        embeds=(_embed(ui, op("admin.status.title"), description, tone=tone, fields=fields),),
        banner=ui.banner("staff-dashboard"),
    )


def status_feature(ui: Ui, op: OpFn, feature: str, status: FeatureStatus, page: int) -> MessageSpec:
    lines = tuple(problem_line(op, p) for p in status.problems)
    pages = pack_sections(op, [Section(op("admin.status.problems"), lines)]) or [()]
    page = clamp_page(page, len(pages))
    parts = [
        op(
            "admin.status.feature_state",
            enabled=yes_no(op, status.enabled),
            ready=yes_no(op, status.ready),
            problems=len(status.problems),
        )
    ]
    if not lines:
        parts.append(op("admin.status.no_problems"))
    tone = "success" if status.ready else ("warning" if status.enabled else "neutral")
    embed = _embed(
        ui, op("admin.status.feature_title", feature=feature), "\n".join(parts), tone=tone, fields=pages[page - 1]
    )
    embed = with_footer(embed, page_footer(op, page, len(pages)))
    return MessageSpec(
        embeds=(embed,), rows=pager_rows(op, "status", feature, page, len(pages)), banner=ui.banner("staff-dashboard")
    )


def unknown_feature(ui: Ui, op: OpFn, feature: str, features: Sequence[str]) -> MessageSpec:
    known = op("admin.known_features", features=", ".join(f"`{name}`" for name in features))
    title = clip(op("admin.unknown_feature", feature=escape_markdown(feature)), limits.EMBED_TITLE)
    return simple(ui, op, title, clip(known, limits.EMBED_DESCRIPTION), tone="danger", banner="error")


# --------------------------------------------------------------------------------------------------
# /content
# --------------------------------------------------------------------------------------------------


def unknown_key(ui: Ui, op: OpFn, key: str, suggestions: Sequence[str]) -> MessageSpec:
    description = (
        op("admin.content.did_you_mean", suggestions=", ".join(f"`{s}`" for s in suggestions)) if suggestions else None
    )
    title = clip(op("admin.content.unknown_key", content_key=escape_markdown(key)), limits.EMBED_TITLE)
    return simple(ui, op, title, description, tone="danger", banner="error")


def content_modal(op: OpFn, token: str, key: str, current: str | None) -> ModalSpec:
    """The ``/content set`` form: one paragraph input prefilled with the current value."""
    prefill = current if current is not None and len(current) <= limits.INPUT_VALUE else None
    if current is not None and prefill is None:
        description: str | None = op("admin.content.input_too_long")
    else:
        description = op("admin.content.input_description", content_key=key)
    if description is not None and len(description) > limits.INPUT_DESCRIPTION:
        description = None
    return ModalSpec(
        custom_id=cid(ROUTE, "cset", token),
        title=clip(op("admin.content.modal_title"), limits.MODAL_TITLE),
        inputs=(
            TextInputSpec(
                custom_id=VALUE_INPUT_ID,
                label=clip(op("admin.content.input_label"), limits.INPUT_LABEL),
                style="paragraph",
                description=description,
                default=prefill,
                required=True,
                min_length=1,
                max_length=limits.INPUT_VALUE,
            ),
        ),
    )


def _value_display(op: OpFn, value: str, budget: int = limits.EMBED_DESCRIPTION) -> tuple[str, tuple[str, str] | None]:
    """(description text, attachment) showing ``value`` exactly: a code block when it fits in ``budget``
    characters, otherwise an attached text file (configured text is never shortened)."""
    block = code_block(value, min(budget, limits.EMBED_DESCRIPTION))
    if block is not None:
        return block, None
    return op("admin.content.value_attached"), ("value.txt", value)


def _embed_text(title: str, fields: Sequence[EmbedField]) -> int:
    return len(title) + sum(len(f.name) + len(f.value) for f in fields)


def list_field(op: OpFn, name: str, lines: Sequence[str]) -> list[EmbedField]:
    """One field listing ``lines`` (the rest is counted, not shown), or nothing for an empty list."""
    if not lines:
        return []
    return [EmbedField(clip(name, limits.EMBED_FIELD_NAME), bounded_list(op, lines, LIST_FIELD_BUDGET))]


def _value_reply(
    ui: Ui, op: OpFn, title: str, value: str | None, fields: list[EmbedField], tone: str, banner: BannerId
) -> Reply:
    """An embed with fields plus the exact value (code block if the 6000-character total allows), under ``banner``."""
    title = clip(title, limits.EMBED_TITLE)
    budget = limits.EMBED_TOTAL_CHARS - _embed_text(title, fields) - MESSAGE_MARGIN
    if value is None:
        description, attachment = None, None
    else:
        description, attachment = _value_display(op, value, budget)
    embed = _embed(ui, title, description, tone=tone, fields=fields)
    return Reply(MessageSpec(embeds=(embed,), banner=ui.banner(banner)), attachment)


def content_saved(
    ui: Ui,
    op: OpFn,
    key: str,
    value: str,
    changes: Sequence[ReadinessChange],
    panels: Sequence[str] = (),
) -> Reply:
    """``panels``: lines about re-posting the panels after the change (empty when they were not refreshed)."""
    fields = list_field(op, op("admin.content.readiness"), [change_line(op, c) for c in changes])
    fields += list_field(op, op("admin.content.panels"), panels)
    title = op("admin.content.saved_title", content_key=key)
    return _value_reply(ui, op, title, value, fields, "success", "successful-confirmation")


def panel_refresh_lines(op: OpFn, refresh: PanelRefresh) -> list[str]:
    """What happened to the posted panels: each refreshed feature's report line (posted, updated, up to date, or why
    it could not be posted), the failures, then where else the new text appears."""
    lines = list(refresh.lines) or [op("admin.content.panels_none")]
    if refresh.failed:
        lines.append(op("admin.content.panels_failed", count=refresh.failed, error_id=refresh.error_id or "-"))
    return [*lines, op("admin.content.panels_hint")]


# --------------------------------------------------------------------------------------------------
# Edit text (message command)
# --------------------------------------------------------------------------------------------------

EDIT_ACTION = "etext"


def edit_text_select_id() -> str:
    return cid(ROUTE, EDIT_ACTION)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def edit_text_choices(ui: Ui, op: OpFn, found: Sequence[tuple[str, str]]) -> MessageSpec:
    """The texts found in a message (``found``: key and current value, in display order) as a select menu; picking
    one opens the ``/content set`` form. At most 25 are offered; the rest are counted."""
    offered = [(key, value) for key, value in found if len(key) <= limits.OPTION_VALUE][: limits.SELECT_OPTIONS]
    description = op("admin.edit.description")
    if len(found) > len(offered):
        description += "\n\n" + op("admin.edit.more", count=len(found) - len(offered))
    options = tuple(
        SelectOptionSpec(
            label=clip(_one_line(value) or key, limits.OPTION_LABEL),
            value=key,
            description=clip(key, limits.OPTION_DESCRIPTION),
        )
        for key, value in offered
    )
    select = SelectSpec(
        custom_id=edit_text_select_id(),
        options=options,
        placeholder=clip(op("admin.edit.placeholder"), limits.SELECT_PLACEHOLDER),
    )
    return MessageSpec(
        embeds=(_embed(ui, op("admin.edit.title"), description),),
        rows=((select,),),
        banner=ui.banner("general-response"),
    )


def content_invalid(ui: Ui, op: OpFn, key: str, value: str, problems: Sequence[str]) -> Reply:
    lines = [op("admin.content.problem_line", problem=escape_markdown(p)) for p in problems]
    if value:
        lines.append(op("admin.content.submitted_value"))
    fields = list_field(op, op("admin.content.problems"), lines)
    title = op("admin.content.invalid_title", content_key=key)
    return _value_reply(ui, op, title, value or None, fields, "danger", "rejected-or-unsuccessful-submission")


def _source_text(op: OpFn, source: str | None, fallback: str | None) -> str:
    if source == "override":
        return op("admin.content.source.override")
    if source == "file":
        return op("admin.content.source.file")
    if fallback:
        return op("admin.content.source.fallback", fallback=fallback)
    return op("admin.content.source.unset")


def content_show(ui: Ui, op: OpFn, info: KeyInfo) -> Reply:
    definition = info.definition
    placeholders = ", ".join("`{" + p + "}`" for p in definition.placeholders) or op("admin.none")
    fields = [
        EmbedField(op("admin.content.field.source"), _source_text(op, info.source, info.fallback_used), inline=True),
        EmbedField(op("admin.content.field.kind"), definition.kind.value, inline=True),
        EmbedField(
            op("admin.content.field.max_length"),
            op("admin.content.characters", count=definition.max_length),
            inline=True,
        ),
        EmbedField(op("admin.content.field.required"), yes_no(op, info.required), inline=True),
        EmbedField(op("admin.content.field.placeholders"), clip(placeholders, 1024), inline=True),
        EmbedField(
            op("admin.content.field.fallback"),
            f"`{definition.fallback}`" if definition.fallback else op("admin.none"),
            inline=True,
        ),
        EmbedField(
            op("admin.content.field.feature"),
            clip(op("admin.content.feature_group", feature=definition.feature, group=definition.group), 1024),
        ),
        EmbedField(op("admin.content.field.doc"), clip(escape_markdown(definition.doc), 1024)),
    ]
    fields += list_field(
        op, op("admin.content.field.problems"), [op("admin.content.problem_line", problem=p) for p in info.problems]
    )
    tone = "danger" if info.problems else ("success" if info.shown is not None else "warning")
    title = op("admin.content.show_title", content_key=definition.key)
    if info.shown is None:
        embed = _embed(ui, clip(title, limits.EMBED_TITLE), op("admin.content.unset_value"), tone=tone, fields=fields)
        return Reply(MessageSpec(embeds=(embed,), banner=ui.banner("staff-dashboard")))
    return _value_reply(ui, op, title, info.shown, fields, tone, "staff-dashboard")


def content_cleared(ui: Ui, op: OpFn, result: ClearResult, panels: Sequence[str] = ()) -> MessageSpec:
    """``panels``: lines about updating the posted panels after the change (empty when they were not refreshed)."""
    if not result.removed:
        no_override = op("admin.content.no_override", content_key=result.key)
        return simple(ui, op, no_override, tone="warning", banner="general-response")
    description = op("admin.content.cleared_now", source=_source_text(op, result.source_after, None))
    fields = list_field(op, op("admin.content.readiness"), [change_line(op, c) for c in result.changes])
    fields += list_field(op, op("admin.content.panels"), panels)
    embed = _embed(
        ui,
        clip(op("admin.content.cleared_title", content_key=result.key), limits.EMBED_TITLE),
        description,
        tone="success",
        fields=fields,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("successful-confirmation"))


def content_missing(
    ui: Ui, op: OpFn, feature: str | None, grouped: Sequence[tuple[str, Sequence[Problem]]], page: int
) -> MessageSpec:
    sections = [
        Section(op("admin.section.feature", feature=name), tuple(problem_line(op, p) for p in problems))
        for name, problems in grouped
    ]
    pages = pack_sections(op, sections) or [()]
    page = clamp_page(page, len(pages))
    scope = (
        op("admin.content.missing_scope_feature", feature=feature) if feature else op("admin.content.missing_scope_all")
    )
    description = scope if grouped else "\n".join([scope, op("admin.content.missing_none")])
    embed = _embed(
        ui,
        op("admin.content.missing_title"),
        description,
        tone="warning" if grouped else "success",
        fields=pages[page - 1],
    )
    embed = with_footer(embed, page_footer(op, page, len(pages)))
    return MessageSpec(
        embeds=(embed,),
        rows=pager_rows(op, "miss", scope_arg(feature), page, len(pages)),
        banner=ui.banner("staff-dashboard"),
    )


def key_line(op: OpFn, row: KeyRow) -> str:
    if row.invalid:
        icon = op("admin.content.key_icon.invalid")
    elif row.source is not None or row.via_fallback:
        icon = op("admin.content.key_icon.ok")
    elif row.required:
        icon = op("admin.content.key_icon.missing")
    else:
        icon = op("admin.content.key_icon.optional")
    requirement = op("admin.content.requirement.required" if row.required else "admin.content.requirement.optional")
    return op(
        "admin.content.key_line",
        icon=icon,
        content_key=row.key,
        kind=row.kind,
        requirement=requirement,
        source=_source_text(op, row.source, row.fallback if row.via_fallback else None),
    )


def content_keys(
    ui: Ui, op: OpFn, feature: str | None, grouped: Sequence[tuple[str, Sequence[KeyRow]]], page: int
) -> MessageSpec:
    rows = [row for _, items in grouped for row in items]
    configured = sum(1 for r in rows if r.source is not None)
    missing = sum(1 for r in rows if r.required and r.source is None and not r.via_fallback)
    sections = [
        Section(op("admin.section.feature", feature=name), tuple(key_line(op, r) for r in items))
        for name, items in grouped
    ]
    pages = pack_sections(op, sections) or [()]
    page = clamp_page(page, len(pages))
    description = op("admin.content.keys_summary", total=len(rows), configured=configured, missing=missing)
    embed = _embed(ui, op("admin.content.keys_title"), description, fields=pages[page - 1])
    embed = with_footer(embed, page_footer(op, page, len(pages)))
    return MessageSpec(
        embeds=(embed,),
        rows=pager_rows(op, "keys", scope_arg(feature), page, len(pages)),
        banner=ui.banner("staff-dashboard"),
    )


def content_export(ui: Ui, op: OpFn, *, total: int, configured: int, overrides: int) -> MessageSpec:
    description = op("admin.content.export_summary", total=total, configured=configured, overrides=overrides)
    return simple(
        ui, op, op("admin.content.export_title"), description, tone="success", banner="successful-confirmation"
    )


# --------------------------------------------------------------------------------------------------
# /reload, /backup
# --------------------------------------------------------------------------------------------------


def reload_report(ui: Ui, op: OpFn, report: ReloadReport) -> list[MessageSpec]:
    features = Section(op("admin.status.features"), tuple(feature_status_line(op, f) for f in report.features))
    if report.ok:
        hint = op("admin.reload.hint")
        title = op("admin.reload.ok_title")
        return paged_messages(ui, op, title, hint, [features], tone="success", banner="successful-confirmation")
    errors = Section(op("admin.reload.errors"), tuple(escape_markdown(e) for e in report.errors))
    title = op("admin.reload.failed_title")
    return paged_messages(ui, op, title, None, [errors, features], tone="danger", banner="error")


def backup_report(ui: Ui, op: OpFn, *, path: str, size: int, kept: int, removed: int) -> MessageSpec:
    description = "\n".join(
        [
            op("admin.backup.details", path=clip(path, 1500), size=size, kept=kept, removed=removed),
            op("admin.backup.private"),
        ]
    )
    return simple(ui, op, op("admin.backup.title"), description, tone="success", banner="successful-confirmation")


# --------------------------------------------------------------------------------------------------
# /audit, /tasks
# --------------------------------------------------------------------------------------------------


def _details_text(details: dict[str, Any]) -> str:
    text = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
    return inline_code(clip(text, AUDIT_DETAILS_MAX))


def audit_line(op: OpFn, event: AuditEvent) -> str:
    actor = f"<@{event.actor_id}>" if event.actor_id is not None else op("admin.audit.system")
    if event.target_type is None and event.target_id is None:
        target = op("admin.none")
    else:
        target = inline_code(clip(f"{event.target_type or ''} {event.target_id or ''}".strip(), 200))
    line = op(
        "admin.audit.line",
        id=event.id,
        ts=event.created_at,
        action=escape_markdown(event.action),
        actor=actor,
        target=target,
    )
    if event.details:
        line += "\n" + op("admin.audit.details", details=_details_text(event.details))
    return line


def audit_list(ui: Ui, op: OpFn, events: Sequence[AuditEvent]) -> list[MessageSpec]:
    if not events:
        return [simple(ui, op, op("admin.audit.title"), op("admin.audit.empty"), banner="activity-log")]
    section = Section(op("admin.audit.events"), tuple(audit_line(op, e) for e in events))
    return paged_messages(ui, op, op("admin.audit.title"), None, [section], banner="activity-log")


def task_line(op: OpFn, task: Task) -> str:
    line = op(
        "admin.tasks.line",
        id=task.id,
        kind=escape_markdown(task.kind),
        status=task.status,
        run_at=task.run_at,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
    )
    if task.last_error and task.status in ("failed", "pending"):
        line += "\n" + op("admin.tasks.error", error=inline_code(clip(task.last_error, TASK_ERROR_MAX)))
    return line


def tasks_list(ui: Ui, op: OpFn, tasks: Sequence[Task], counts: dict[str, int]) -> list[MessageSpec]:
    summary = op(
        "admin.status.tasks_value",
        pending=counts.get("pending", 0),
        running=counts.get("running", 0),
        failed=counts.get("failed", 0),
    )
    if not tasks:
        empty = "\n".join([summary, op("admin.tasks.empty")])
        return [simple(ui, op, op("admin.tasks.title"), empty, banner="staff-dashboard")]
    section = Section(op("admin.tasks.list"), tuple(task_line(op, t) for t in tasks))
    return paged_messages(ui, op, op("admin.tasks.title"), summary, [section], banner="staff-dashboard")
