"""The /reset messages (operator wording, ``server.reset.*``): the private preview with Continue/Cancel, the
confirmation form, and the progress and result DMs. Pure builders over plain data, shared by the handlers and the
offline preview."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import discord

from shopbot.config.banner_ids import BannerId
from shopbot.features.setup import reset
from shopbot.ui import limits
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, EmbedField, MessageSpec, ModalSpec, TextInputSpec, rows_of
from shopbot.ui.style import Ui

ROUTE = "rs"
CONTINUE_ACTION = "go"
CANCEL_ACTION = "no"
CONFIRM_ACTION = "ok"
PHRASE_INPUT_ID = "phrase"

Op = Callable[..., str]


@dataclass(frozen=True)
class PlanView:
    """What the preview shows: display names, so samples and the live plan render the same way."""

    server_name: str
    channels: tuple[str, ...]
    categories: tuple[str, ...]
    roles: tuple[str, ...]
    kept: tuple[reset.Kept, ...]
    open_orders: int
    open_tickets: int
    wipe: bool
    fingerprint: str

    @property
    def total(self) -> int:
        return len(self.channels) + len(self.categories) + len(self.roles)


def plan_view(server_name: str, plan: reset.ResetPlan, work: reset.OpenWork, *, wipe: bool) -> PlanView:
    return PlanView(
        server_name=server_name,
        channels=tuple(c.name for c in plan.channels),
        categories=tuple(c.name for c in plan.categories),
        roles=tuple(r.name for r in plan.roles),
        kept=plan.kept,
        open_orders=work.orders,
        open_tickets=work.tickets,
        wipe=wipe,
        fingerprint=plan.fingerprint,
    )


def display_name(kind: str, name: str) -> str:
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
    if kind == reset.CHANNEL:
        return f"#{escaped}"
    if kind == reset.ROLE:
        return f"@{escaped}"
    return escaped


def name_list(op: Op, kind: str, names: Sequence[str], limit: int = limits.EMBED_FIELD_VALUE) -> str:
    """Names joined with commas; a list longer than ``limit`` characters ends with "… and N more"."""
    return _join_limited(op, [display_name(kind, name) for name in names], limit)


def _join_limited(op: Op, items: Sequence[str], limit: int) -> str:
    if not items:
        return op("server.reset.none")
    full = ", ".join(items)
    if len(full) <= limit:
        return full
    shown: list[str] = []
    for item in items:
        rest = op("server.reset.more", count=len(items) - len(shown) - 1)
        if len(", ".join([*shown, item])) + 1 + len(rest) > limit:
            break
        shown.append(item)
    more = op("server.reset.more", count=len(items) - len(shown))
    return f"{', '.join(shown)} {more}" if shown else more


# Characters of names per reason in the "Kept" field (four reasons and @everyone share one field).
KEPT_NAMES_LIMIT = 160


def _kept_lines(op: Op, kept: Sequence[reset.Kept]) -> list[str]:
    lines = [op("server.reset.kept.everyone")]
    for reason, items in reset.kept_by_reason(kept).items():
        names = _join_limited(op, [display_name(item.kind, item.name) for item in items], KEPT_NAMES_LIMIT)
        lines.append(op(f"server.reset.kept.{reason}", names=names))
    return lines


def _fit(op: Op, lines: Sequence[str], limit: int = limits.EMBED_FIELD_VALUE) -> str:
    """Whole lines, one per row; a list longer than ``limit`` characters ends with a line "… and N more"."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    shown: list[str] = []
    for line in lines:
        rest = op("server.reset.more", count=len(lines) - len(shown) - 1)
        if len("\n".join([*shown, line, rest])) > limit:
            break
        shown.append(line)
    return "\n".join([*shown, op("server.reset.more", count=len(lines) - len(shown))])


def preview_message(ui: Ui, op: Op, view: PlanView) -> MessageSpec:
    phrase = reset.confirmation_phrase(view.server_name)
    data = op("server.reset.data_wiped") if view.wipe else op("server.reset.data_kept")
    description = op("server.reset.preview_description", data=data, phrase=discord.utils.escape_markdown(phrase))
    if view.wipe:
        work = op("server.reset.work_wiped", orders=view.open_orders, tickets=view.open_tickets)
    else:
        work = op("server.reset.work_kept", orders=view.open_orders, tickets=view.open_tickets)
    fields = (
        EmbedField(
            op("server.reset.field.channels", count=len(view.channels)), name_list(op, reset.CHANNEL, view.channels)
        ),
        EmbedField(
            op("server.reset.field.categories", count=len(view.categories)),
            name_list(op, reset.CATEGORY, view.categories),
        ),
        EmbedField(op("server.reset.field.roles", count=len(view.roles)), name_list(op, reset.ROLE, view.roles)),
        EmbedField(op("server.reset.field.kept"), _fit(op, _kept_lines(op, view.kept))),
        EmbedField(op("server.reset.field.work"), work),
    )
    embed = ui.operator_embed(op("server.reset.preview_title"), description, tone="danger", fields=fields)
    wipe_arg = 1 if view.wipe else 0
    buttons = rows_of(
        ButtonSpec(
            label=op("server.reset.button.continue"),
            custom_id=cid(ROUTE, CONTINUE_ACTION, wipe_arg, view.fingerprint),
            style="danger",
        ),
        ButtonSpec(label=op("server.reset.button.cancel"), custom_id=cid(ROUTE, CANCEL_ACTION), style="secondary"),
    )
    return MessageSpec(embeds=(embed,), rows=buttons, banner=ui.banner("staff-dashboard"))


def confirm_modal(op: Op, server_name: str, *, wipe: bool, fingerprint: str) -> ModalSpec:
    phrase = reset.confirmation_phrase(server_name)
    title = op("server.reset.form_title_wipe") if wipe else op("server.reset.form_title")
    return ModalSpec(
        custom_id=cid(ROUTE, CONFIRM_ACTION, 1 if wipe else 0, fingerprint),
        title=title,
        inputs=(
            TextInputSpec(
                custom_id=PHRASE_INPUT_ID,
                label=op("server.reset.form_label"),
                placeholder=phrase[: limits.INPUT_PLACEHOLDER],
                max_length=len(phrase) + 20,
            ),
        ),
    )


def notice(ui: Ui, title: str, description: str | None = None, *, tone: str, banner: BannerId) -> MessageSpec:
    return MessageSpec(embeds=(ui.operator_embed(title, description, tone=tone),), banner=ui.banner(banner))


def progress_message(ui: Ui, op: Op, done: int, total: int, failed: int) -> MessageSpec:
    description = op("server.reset.progress_description", done=done, total=total, failed=failed)
    title = op("server.reset.progress_title")
    return notice(ui, title, description, tone="warning", banner="submission-and-processing")


def result_message(ui: Ui, op: Op, result: reset.ResetResult) -> MessageSpec:
    deleted = op(
        "server.reset.result_deleted",
        channels=result.deleted.get(reset.CHANNEL, 0),
        categories=result.deleted.get(reset.CATEGORY, 0),
        roles=result.deleted.get(reset.ROLE, 0),
    )
    if result.wipe:
        data = op(
            "server.reset.result_data_wiped",
            orders=result.wiped.get("orders", 0),
            products=result.wiped.get("products", 0),
            reviews=result.wiped.get("reviews", 0),
        )
    else:
        data = op("server.reset.result_data_kept")
    if result.setup_channel_id is not None:
        next_step = op("server.reset.result_next", channel=f"<#{result.setup_channel_id}>")
    else:
        next_step = op("server.reset.result_next_no_channel")
    description = "\n\n".join((deleted, op("server.reset.result_backup", backup=result.backup), data, next_step))
    fields: list[EmbedField] = []
    if result.failures:
        lines = [
            op(
                "server.reset.failure",
                name=display_name(f.kind, f.name),
                kind=op(f"server.reset.kind.{f.kind}"),
                status=f.status,
                code=f.code,
            )
            for f in result.failures
        ]
        fields.append(EmbedField(op("server.reset.field.failed", count=len(result.failures)), _fit(op, lines)))
    if result.kept:
        fields.append(EmbedField(op("server.reset.field.kept"), op("server.reset.result_kept", count=result.kept)))
    banner: BannerId
    if result.failures:
        title, tone, banner = op("server.reset.result_title_problems"), "warning", "staff-notifications"
    else:
        title, tone, banner = op("server.reset.result_title"), "success", "successful-confirmation"
    embed = ui.operator_embed(title, description, tone=tone, fields=fields)
    return MessageSpec(embeds=(embed,), banner=ui.banner(banner))
