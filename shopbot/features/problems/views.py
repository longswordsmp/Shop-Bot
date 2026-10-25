"""Pure builders for every Report a Problem message and form.

They take the ``Ui`` (content + branding), settings and plain data and return ``MessageSpec``/``ModalSpec`` objects;
they never call Discord, so the runtime, the offline preview and the limits checker render exactly the same specs.
Missing required content raises ``MissingContent``; optional elements whose content is missing are omitted.
Customer data (display names, form answers, staff reasons) is escaped with ``discord.utils.escape_markdown`` and never
pings: embeds do not mention, only the explicit ``mention_user_ids``/``mention_role_ids`` of a spec do.

Every message carries the banner of its kind (``Ui.banner``). The private step and the report panel are each ONE
message whose banner follows its state (``STATUS_BANNERS``, ``REFUSED_BANNER``); the answers posted under the panel have
none (they continue it).

Custom ids (route ``pr``) carry only identifiers; handlers reload and re-validate everything:
- ``sb:pr:start`` - the Report a Problem button on the #purchase panel;
- ``sb:pr:order:<nonce>:<type>`` / ``sb:pr:type:<nonce>:<order>`` - the order and issue-type menus of the private
  choice step (each carries the other menu's current choice), ``sb:pr:go:<nonce>:<order>:<type>`` - its Continue
  button, ``sb:pr:form:<nonce>:<order>:<type>`` - the issue form. ``<nonce>`` identifies one run of the flow (one
  report at most); ``<order>`` is ``-`` (not chosen), ``0`` (not about an order) or an order id; ``<type>`` is ``-``
  or an issue-type id;
- ``sb:pr:resolve:<report>`` / ``sb:pr:reject:<report>`` - the staff buttons on the report panel, and
  ``sb:pr:resolved:<report>`` / ``sb:pr:rejected:<report>`` - their staff forms.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import ProblemSettings, ProblemType
from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.tickets import content_keys as tk
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELDS, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE
from shopbot.ui.spec import (
    ButtonSpec,
    ButtonStyle,
    Component,
    EmbedField,
    EmbedSpec,
    FileSpec,
    InputStyle,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui

ROUTE = k.ROUTE
NOT_CHOSEN = "-"
NO_ORDER = "0"
NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
REASON_INPUT = "reason"
# Raw characters of a form answer per embed field. Escaping at most doubles the length, so every field value stays
# within Discord's 1024-character limit without cutting any character.
ANSWER_CHUNK = 500
# Options in one selection menu (Discord's limit).
MENU_OPTIONS = 25

Values = dict[str, str | int]
OpText = Callable[..., str]

_INPUT_STYLES: dict[str, InputStyle] = {"short": "short", "paragraph": "paragraph"}
_STATUS_TONES = {k.OPEN: "warning", k.RESOLVED: "success", k.REJECTED: "danger"}
# The banner of the report panel and of the decision notification for each report status.
STATUS_BANNERS: dict[str, BannerId] = {
    k.OPEN: "support-request",
    k.RESOLVED: "resolution",
    k.REJECTED: "rejected-or-unsuccessful-submission",
}
# Replies that refuse a new report (no order to report about, the open-report limit reached).
REFUSED_BANNER: BannerId = "rejected-or-unsuccessful-submission"


# -- custom ids ---------------------------------------------------------------------------------------------------


def start_id() -> str:
    return cid(ROUTE, "start")


def order_select_id(nonce: str, type_sel: str) -> str:
    return cid(ROUTE, "order", nonce, type_sel)


def type_select_id(nonce: str, order_sel: str) -> str:
    return cid(ROUTE, "type", nonce, order_sel)


def continue_id(nonce: str, order_sel: str, type_sel: str) -> str:
    return cid(ROUTE, "go", nonce, order_sel, type_sel)


def form_id(nonce: str, order_sel: str, type_id: str) -> str:
    return cid(ROUTE, "form", nonce, order_sel, type_id)


def resolve_id(report_id: int) -> str:
    return cid(ROUTE, "resolve", report_id)


def reject_id(report_id: int) -> str:
    return cid(ROUTE, "reject", report_id)


def resolve_form_id(report_id: int) -> str:
    return cid(ROUTE, "resolved", report_id)


def reject_form_id(report_id: int) -> str:
    return cid(ROUTE, "rejected", report_id)


def close_id(ticket_id: int) -> str:
    """The ticket system's own close button (its route handles permissions, confirmation and transcripts)."""
    return cid(tk.ROUTE, "close", ticket_id)


# -- data ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderInfo:
    """An order as the workflow shows it: the values of {order_number}, {total} and {status}."""

    order_id: int
    number: str
    total: str
    status: str

    def values(self) -> Values:
        return {"order_number": self.number, "total": self.total, "status": self.status}


@dataclass(frozen=True)
class Choice:
    """State of the private choice step (rebuilt from the database on every click)."""

    nonce: str
    orders: tuple[OrderInfo, ...]
    allow_none: bool
    show_orders: bool
    show_types: bool
    order_sel: str
    type_sel: str

    @property
    def complete(self) -> bool:
        return self.order_sel != NOT_CHOSEN and self.type_sel != NOT_CHOSEN

    @property
    def needs_input(self) -> bool:
        return self.show_orders or self.show_types

    @property
    def order_id(self) -> int | None:
        return int(self.order_sel) if self.order_sel not in (NOT_CHOSEN, NO_ORDER) else None


@dataclass(frozen=True)
class PanelData:
    """Everything the report panel shows (reloaded from the database whenever it is rendered)."""

    report_id: int
    ticket_id: int
    number: str
    user_id: int
    user_name: str
    type_id: str
    order: OrderInfo | None
    status: str
    reason: str | None = None
    decided_by: int | None = None
    ticket_open: bool = True


# -- helpers ------------------------------------------------------------------------------------------------------


def escape(text: str) -> str:
    return escape_markdown(text)


def user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


def channel_mention(channel_id: int) -> str:
    return f"<#{channel_id}>"


def _text(ui: Ui, key: str, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _button(
    ui: Ui, key: str, custom_id: str, *, style: ButtonStyle, emoji_key: str | None = None, disabled: bool = False
) -> ButtonSpec:
    if not ui.content.has(key):
        raise MissingContent(key)
    button = ui.button(key, custom_id, style=style, emoji_key=emoji_key, disabled=disabled)
    if button is None:
        raise MissingContent(key)
    return button


def _attachments_embed(ui: Ui) -> EmbedSpec:
    return ui.embed(description=_text(ui, k.ATTACHMENTS_NOTE), tone="info", branding_text=False)


def type_label(ui: Ui, settings: ProblemSettings, type_id: str) -> str | None:
    """The issue type's label; None when the type is no longer configured (its field is then omitted)."""
    if settings.type(type_id) is None:
        return None
    return _text(ui, k.type_key(type_id, "label"))


def _split(text: str, size: int) -> list[str]:
    """Split ``text`` into pieces of at most ``size`` characters, preferring line boundaries. Whitespace-only pieces
    are dropped (an embed field value cannot be blank). Nothing is cut away."""
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            pieces.append(current)
            current = ""
        current += line
    if current:
        pieces.append(current)
    return [p.strip("\n") for p in pieces if p.strip()]


def _order_field(ui: Ui, order: OrderInfo | None) -> EmbedField | None:
    if order is None:
        return None
    return EmbedField(name=_text(ui, k.FIELD_ORDER), value=_text(ui, k.ORDER_VALUE, **order.values()), inline=True)


def _type_field(ui: Ui, label: str | None) -> EmbedField | None:
    if label is None:
        return None
    return EmbedField(name=_text(ui, k.FIELD_TYPE), value=label, inline=True)


def _reason_field(ui: Ui, reason: str | None) -> EmbedField | None:
    if not reason:
        return None
    return EmbedField(name=_text(ui, k.FIELD_REASON), value=escape(reason))


def _fields(*items: EmbedField | None) -> tuple[EmbedField, ...]:
    return tuple(item for item in items if item is not None)


def _status_banner(ui: Ui, status: str) -> FileSpec | None:
    return ui.banner(STATUS_BANNERS.get(status, "general-response"))


# -- #purchase panel button ---------------------------------------------------------------------------------------


def report_button(ui: Ui) -> ButtonSpec:
    """The Report a Problem button placed on the #purchase panel (checkout's panel)."""
    return _button(ui, k.BUTTON_REPORT, start_id(), style="secondary", emoji_key=k.BUTTON_REPORT_EMOJI)


# -- private choice step ------------------------------------------------------------------------------------------


def _order_menu(ui: Ui, choice: Choice) -> SelectSpec:
    options: list[SelectOptionSpec] = []
    for order in choice.orders:
        values = order.values()
        options.append(
            SelectOptionSpec(
                label=_text(ui, k.ORDER_OPTION, **values),
                value=str(order.order_id),
                description=ui.text(k.ORDER_OPTION_DESCRIPTION, **values),
                default=choice.order_sel == str(order.order_id),
            )
        )
    if choice.allow_none:
        options.append(
            SelectOptionSpec(
                label=_text(ui, k.NO_ORDER_OPTION),
                value=NO_ORDER,
                description=ui.content.get(k.NO_ORDER_OPTION_DESCRIPTION),
                default=choice.order_sel == NO_ORDER,
            )
        )
    return SelectSpec(
        custom_id=order_select_id(choice.nonce, choice.type_sel),
        options=tuple(options),
        placeholder=_text(ui, k.ORDER_PLACEHOLDER),
    )


def _type_menu(ui: Ui, settings: ProblemSettings, choice: Choice) -> SelectSpec:
    options = tuple(
        SelectOptionSpec(
            label=_text(ui, k.type_key(t.id, "label")),
            value=t.id,
            description=ui.content.get(k.type_key(t.id, "description")),
            emoji=ui.content.get(k.type_key(t.id, "emoji")),
            default=choice.type_sel == t.id,
        )
        for t in settings.types
    )
    return SelectSpec(
        custom_id=type_select_id(choice.nonce, choice.order_sel),
        options=options,
        placeholder=_text(ui, k.TYPE_PLACEHOLDER),
    )


def choose_message(
    ui: Ui, settings: ProblemSettings, choice: Choice, *, links: Mapping[str, str] | None = None
) -> MessageSpec:
    """The private step: the order menu and/or the issue-type menu, then Continue (enabled once both are chosen)."""
    description = _text(ui, k.CHOOSE_DESCRIPTION, **dict(links or {}))
    main = ui.embed(title=ui.text(k.CHOOSE_TITLE), description=description, tone="primary")
    order_menu = _order_menu(ui, choice) if choice.show_orders else None
    type_menu = _type_menu(ui, settings, choice) if choice.show_types else None
    proceed = _button(
        ui,
        core.BUTTON_CONTINUE,
        continue_id(choice.nonce, choice.order_sel, choice.type_sel),
        style="primary",
        disabled=not choice.complete,
    )
    return MessageSpec(
        embeds=(main, _attachments_embed(ui)),
        rows=rows_of(order_menu, type_menu, proceed),
        banner=ui.banner("support-request"),
    )


def form_modal(ui: Ui, problem_type: ProblemType, *, nonce: str, order_sel: str) -> ModalSpec:
    """The issue form of ``problem_type``. Input custom_ids are the field ids."""
    inputs = tuple(
        TextInputSpec(
            custom_id=field.id,
            label=_text(ui, k.form_field_key(problem_type.id, field.id, "label")),
            style=_INPUT_STYLES.get(field.style, "short"),
            description=ui.content.get(k.form_field_key(problem_type.id, field.id, "description")),
            placeholder=ui.content.get(k.form_field_key(problem_type.id, field.id, "placeholder")),
            required=field.required,
            min_length=field.min_length or None,
            max_length=field.max_length,
        )
        for field in problem_type.form
    )
    return ModalSpec(
        custom_id=form_id(nonce, order_sel, problem_type.id),
        title=_text(ui, k.type_key(problem_type.id, "form_title")),
        inputs=inputs,
    )


# -- private replies ------------------------------------------------------------------------------------------------


def _reply(ui: Ui, key: str, *, tone: str, values: Values | None = None) -> EmbedSpec:
    return ui.embed(description=_text(ui, key, **(values or {})), tone=tone)


def created_reply(ui: Ui, *, channel_id: int, number: str) -> MessageSpec:
    embed = _reply(
        ui, k.CREATED, tone="success", values={"channel_mention": channel_mention(channel_id), "number": number}
    )
    return MessageSpec(embeds=(embed, _attachments_embed(ui)), banner=ui.banner("ticket-created"))


def limit_reply(ui: Ui, *, limit: int, channel_id: int | None) -> MessageSpec:
    values: Values = {"limit": limit, "channel_mention": channel_mention(channel_id) if channel_id else ""}
    return MessageSpec(
        embeds=(_reply(ui, k.LIMIT_REACHED, tone="warning", values=values),), banner=ui.banner(REFUSED_BANNER)
    )


def no_orders_reply(ui: Ui, *, links: Mapping[str, str] | None = None) -> MessageSpec:
    return MessageSpec(
        embeds=(_reply(ui, k.NO_ORDERS, tone="warning", values=dict(links or {})),), banner=ui.banner(REFUSED_BANNER)
    )


def error_reply(ui: Ui, key: str, **values: str | int) -> MessageSpec:
    """A private error exactly as the router sends it (``Ui.error_message``; used by the preview)."""
    spec = ui.error_message(key, **values)
    if spec is None:
        raise MissingContent(key)
    return spec


# -- the report's ticket -------------------------------------------------------------------------------------------


def channel_topic(ui: Ui, *, number: str, user_name: str) -> str | None:
    """Optional channel topic (None when not configured)."""
    return ui.text(k.CHANNEL_TOPIC, number=number, user_name=escape(user_name))


def panel_rows(ui: Ui, data: PanelData) -> tuple[tuple[Component, ...], ...]:
    """Resolve/Reject while the report is open (staff only, checked when clicked) and the ticket's Close button while
    the ticket is open."""
    buttons: list[ButtonSpec] = []
    if data.status == k.OPEN:
        buttons.append(_button(ui, k.BUTTON_RESOLVE, resolve_id(data.report_id), style="success"))
        buttons.append(_button(ui, k.BUTTON_REJECT, reject_id(data.report_id), style="danger"))
    # Always offered: the ticket's state is checked when clicked, and the panel is not re-rendered on a reopen.
    buttons.append(_button(ui, tk.BUTTON_CLOSE, close_id(data.ticket_id), style="secondary"))
    return rows_of(*buttons)


def ticket_panel(
    ui: Ui,
    settings: ProblemSettings,
    data: PanelData,
    *,
    links: Mapping[str, str] | None = None,
    ping_role_ids: tuple[int, ...] = (),
) -> MessageSpec:
    """The live report panel in the ticket: report summary, status, staff decision and the actions. Its banner follows
    the report's status (the decision edits this same message)."""
    label = type_label(ui, settings, data.type_id)
    values: Values = {
        "number": data.number,
        "type_label": label or "",
        "user_name": escape(data.user_name),
        "user_mention": user_mention(data.user_id),
    }
    title = _text(ui, k.TICKET_TITLE, number=data.number, type_label=label or "", user_name=escape(data.user_name))
    description = _text(ui, k.TICKET_DESCRIPTION, **values, **dict(links or {}))
    decided_by = None
    if data.status != k.OPEN and data.decided_by is not None:
        decided_by = ui.field(k.FIELD_DECIDED_BY, user_mention(data.decided_by), inline=True)
    fields = _fields(
        _type_field(ui, label),
        _order_field(ui, data.order),
        EmbedField(name=_text(ui, k.FIELD_STATUS), value=_text(ui, k.status_key(data.status)), inline=True),
        _reason_field(ui, data.reason if data.status != k.OPEN else None),
        decided_by,
    )
    main = ui.embed(
        title=title,
        description=description,
        tone=_STATUS_TONES.get(data.status, "primary"),
        fields=fields,
        timestamp=True,
    )
    embeds = (main, _attachments_embed(ui)) if data.status == k.OPEN else (main,)
    return MessageSpec(
        embeds=embeds,
        rows=panel_rows(ui, data),
        mention_user_ids=(data.user_id,),
        mention_role_ids=ping_role_ids,
        banner=_status_banner(ui, data.status),
    )


def answer_fields(ui: Ui, problem_type: ProblemType, answers: Mapping[str, str]) -> list[EmbedField]:
    """Form answers as fields named by the form labels (escaped, never cut; empty optional answers are skipped)."""
    fields: list[EmbedField] = []
    for field in problem_type.form:
        answer = (answers.get(field.id) or "").strip()
        if not answer:
            continue
        label = _text(ui, k.form_field_key(problem_type.id, field.id, "label"))
        for piece in _split(answer, ANSWER_CHUNK):
            fields.append(EmbedField(name=label, value=escape(piece)))
    return fields


def _pack(fields: Sequence[EmbedField], base: EmbedSpec) -> list[MessageSpec]:
    """Fields in as many embeds/messages as needed: 25 fields per embed, 10 embeds and 6000 characters per message."""
    messages: list[MessageSpec] = []
    embeds: list[EmbedSpec] = []
    current: list[EmbedField] = []
    used = 0
    for item in fields:
        size = len(item.name) + len(item.value)
        if current and len(current) >= EMBED_FIELDS:
            embeds.append(replace(base, fields=tuple(current)))
            current = []
        if used + size > EMBED_TOTAL_CHARS or (not current and len(embeds) >= EMBEDS_PER_MESSAGE):
            if current:
                embeds.append(replace(base, fields=tuple(current)))
                current = []
            if embeds:
                messages.append(MessageSpec(embeds=tuple(embeds)))
            embeds, used = [], 0
        current.append(item)
        used += size
    if current:
        embeds.append(replace(base, fields=tuple(current)))
    if embeds:
        messages.append(MessageSpec(embeds=tuple(embeds)))
    return messages


def details_messages(ui: Ui, problem_type: ProblemType, answers: Mapping[str, str]) -> list[MessageSpec]:
    """The customer's form answers, posted once below the report panel (none when the type has no form). No banner:
    they continue the panel above."""
    return _pack(answer_fields(ui, problem_type, answers), ui.embed(tone="info", branding_text=False))


# -- decision, notifications, staff alert ----------------------------------------------------------------------------


def decision_notification(
    ui: Ui,
    settings: ProblemSettings,
    *,
    status: str,
    number: str,
    type_id: str,
    order: OrderInfo | None,
    reason: str | None,
    channel_id: int | None,
) -> MessageSpec:
    """The private message telling the customer about the decision (direct message, or posted in the ticket)."""
    label = type_label(ui, settings, type_id)
    values: Values = {
        "number": number,
        "type_label": label or "",
        "channel_mention": channel_mention(channel_id) if channel_id else "",
    }
    embed = ui.embed(
        title=ui.text(k.notify_key(status, "title"), number=number, type_label=label or ""),
        description=_text(ui, k.notify_key(status, "description"), **values),
        tone=_STATUS_TONES.get(status, "info"),
        fields=_fields(_type_field(ui, label), _order_field(ui, order), _reason_field(ui, reason)),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=_status_banner(ui, status))


def staff_alert(
    ui: Ui,
    settings: ProblemSettings,
    *,
    number: str,
    type_id: str,
    user_id: int,
    channel_id: int,
    order: OrderInfo | None,
    ping_role_ids: tuple[int, ...] = (),
) -> MessageSpec:
    """The new-report alert in the staff alert channel (the report details stay in the private ticket)."""
    label = type_label(ui, settings, type_id)
    values: Values = {
        "number": number,
        "type_label": label or "",
        "user_mention": user_mention(user_id),
        "channel_mention": channel_mention(channel_id),
    }
    embed = ui.embed(
        title=_text(ui, k.STAFF_ALERT_TITLE, number=number, type_label=label or ""),
        description=_text(ui, k.STAFF_ALERT_DESCRIPTION, **values),
        tone="warning",
        fields=_fields(_type_field(ui, label), _order_field(ui, order)),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), mention_role_ids=ping_role_ids, banner=ui.banner("staff-notifications"))


# -- staff forms and replies (operator text; shown only after a staff check) -----------------------------------------


def resolve_modal(op: OpText, *, report_id: int, number: str) -> ModalSpec:
    note = TextInputSpec(
        custom_id=REASON_INPUT,
        label=op("problems.form.resolve_note"),
        style="paragraph",
        description=op("problems.form.resolve_note_help"),
        required=False,
        max_length=k.REASON_MAX,
    )
    return ModalSpec(
        custom_id=resolve_form_id(report_id), title=op("problems.form.resolve_title", number=number), inputs=(note,)
    )


def reject_modal(op: OpText, *, report_id: int, number: str, required: bool) -> ModalSpec:
    reason = TextInputSpec(
        custom_id=REASON_INPUT,
        label=op("problems.form.reject_reason"),
        style="paragraph",
        description=op("problems.form.reject_reason_help"),
        required=required,
        max_length=k.REASON_MAX,
    )
    return ModalSpec(
        custom_id=reject_form_id(report_id), title=op("problems.form.reject_title", number=number), inputs=(reason,)
    )


def operator_reply(ui: Ui, text: str, *, tone: str = "info", banner: BannerId = "general-response") -> MessageSpec:
    """A staff-only private reply: ``general-response`` for answers, ``error`` for refused input."""
    return MessageSpec(embeds=(ui.operator_embed(text, tone=tone),), banner=ui.banner(banner))
