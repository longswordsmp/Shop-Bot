"""Pure builders for every Report a Problem message and form.

They take the ``Ui`` (content + branding), settings and plain data and return ``MessageSpec``/``ModalSpec`` objects;
they never call Discord, so the runtime, the offline preview and the limits checker render exactly the same specs.
Missing required content raises ``MissingContent``; optional elements whose content is missing are omitted.
Customer data (display names, form answers, staff reasons) is escaped with ``discord.utils.escape_markdown`` and never
pings: embeds do not mention, only the explicit ``mention_user_ids``/``mention_role_ids`` of a spec do.

Every message carries the banner of its kind (``Ui.banner``). The private step and the report panel are each ONE
message whose banner follows its state (``STATUS_BANNERS``, ``REFUSED_BANNER``); the answers card posted under the panel
has none (it continues the panel).

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
from dataclasses import dataclass

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import ProblemSettings, ProblemType
from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.tickets import content_keys as tk
from shopbot.ui.card import PARAGRAPH, heading, pack_messages, with_paragraph
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_DESCRIPTION
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
# The answers card continues in its next embed only where one is full; with less room than this left, the next answer
# (or the rest of a long one) starts in the next embed instead of a few words at the bottom of this one.
MIN_PIECE = 200
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


def type_label(ui: Ui, settings: ProblemSettings, type_id: str) -> str | None:
    """The issue type's label; None when the type is no longer configured (its field is then omitted)."""
    if settings.type(type_id) is None:
        return None
    return _text(ui, k.type_key(type_id, "label"))


def _order_field(ui: Ui, order: OrderInfo | None) -> EmbedField | None:
    """The order the report is about, full width under the short facts (number, total and status never squeezed into
    a third of the card)."""
    if order is None:
        return None
    return EmbedField(name=_text(ui, k.FIELD_ORDER), value=_text(ui, k.ORDER_VALUE, **order.values()))


def _type_field(ui: Ui, label: str | None) -> EmbedField | None:
    if label is None:
        return None
    return EmbedField(name=_text(ui, k.FIELD_TYPE), value=label, inline=True)


def _status_field(ui: Ui, status: str) -> EmbedField:
    return EmbedField(name=_text(ui, k.FIELD_STATUS), value=_text(ui, k.status_key(status)), inline=True)


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
            emoji=ui.content.render(k.type_key(t.id, "emoji")),
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
        embeds=(main,),
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


def _reply(ui: Ui, key: str, *, tone: str, values: Values | None = None, title_key: str | None = None) -> EmbedSpec:
    values = values or {}
    title = ui.text(title_key, **values) if title_key else None
    return ui.embed(title=title, description=_text(ui, key, **values), tone=tone)


def created_reply(ui: Ui, *, channel_id: int, number: str) -> MessageSpec:
    """The private confirmation once the report's ticket exists: an optional title, the link to the ticket, then the
    attachments note as the card's last paragraph."""
    values: Values = {"channel_mention": channel_mention(channel_id), "number": number}
    embed = _reply(ui, k.CREATED, tone="success", values=values, title_key=k.CREATED_TITLE)
    return MessageSpec(embeds=with_paragraph(embed, _text(ui, k.ATTACHMENTS_NOTE)), banner=ui.banner("ticket-created"))


def limit_reply(ui: Ui, *, limit: int, channel_id: int | None) -> MessageSpec:
    """The open-report limit is reached. The line linking the newest open report's ticket is left out when that ticket
    cannot be linked (``render_lines``), so no sentence ends in an empty link."""
    values: Values = {"limit": limit, "channel_mention": channel_mention(channel_id) if channel_id else ""}
    description = ui.content.render_lines(k.LIMIT_REACHED, **values)
    if description is None:
        raise MissingContent(k.LIMIT_REACHED)
    embed = ui.embed(title=ui.text(k.LIMIT_REACHED_TITLE), description=description, tone="warning")
    return MessageSpec(embeds=(embed,), banner=ui.banner(REFUSED_BANNER))


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
    # Two short facts side by side, then the order full width, then the decision (never three squeezed columns).
    fields = _fields(
        _type_field(ui, label),
        _status_field(ui, data.status),
        _order_field(ui, data.order),
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
    embeds = with_paragraph(main, _text(ui, k.ATTACHMENTS_NOTE)) if data.status == k.OPEN else (main,)
    return MessageSpec(
        embeds=embeds,
        rows=panel_rows(ui, data),
        mention_user_ids=(data.user_id,),
        mention_role_ids=ping_role_ids,
        banner=_status_banner(ui, data.status),
    )


def answer_lines(ui: Ui, problem_type: ProblemType, answers: Mapping[str, str]) -> list[str]:
    """The answers card's lines: each answered question in bold (its form label) with the escaped answer under it, in
    one line, so a question always opens its answer; questions are separated by ``PARAGRAPH`` and empty optional answers
    are skipped."""
    lines: list[str] = []
    for field in problem_type.form:
        answer = (answers.get(field.id) or "").strip()
        if not answer:
            continue
        label = _text(ui, k.form_field_key(problem_type.id, field.id, "label"))
        if lines:
            lines.append(PARAGRAPH)
        lines.append(f"{heading(label)}\n{escape(answer)}")
    return lines


def _split_at(text: str, room: int) -> tuple[str, str]:
    """``text`` (longer than ``room``) as a first part of at most ``room`` characters and the rest: split at the last
    line break, else the last space, in the second half of the room (that one whitespace character is the break);
    without either, exactly at ``room``, but never between a backslash and the character it escapes."""
    for separator in ("\n", " "):
        at = text.rfind(separator, room // 2, room + 1)
        if at > 0:
            return text[:at], text[at + 1 :]
    cut = room
    while cut > 1 and text[cut - 1] == "\\":
        cut -= 1
    if cut <= 1:
        cut = room
    return text[:cut], text[cut:]


def _descriptions(lines: Sequence[str]) -> list[str]:
    """``lines`` joined into embed descriptions of at most 4096 characters (``PARAGRAPH`` starts a new paragraph). A
    line that does not fit continues in the next description, split by ``_split_at``; nothing is cut."""
    texts: list[str] = []
    current = ""
    paragraph = False
    for line in lines:
        if line == PARAGRAPH:
            paragraph = True
            continue
        rest = line
        while rest:
            separator = ("\n\n" if paragraph else "\n") if current else ""
            room = EMBED_DESCRIPTION - len(current) - len(separator)
            if len(rest) <= room:
                current += separator + rest
                break
            if current and room < MIN_PIECE:
                texts.append(current)
                current = ""
                continue
            first, rest = _split_at(rest, room)
            texts.append(current + separator + first)
            current = ""
        paragraph = False
    if current:
        texts.append(current)
    return texts


def details_messages(ui: Ui, problem_type: ProblemType, answers: Mapping[str, str]) -> list[MessageSpec]:
    """The customer's form answers as one card posted once below the report panel: the optional heading
    (``problems.details.heading``), then each question in bold with the answer under it. A card longer than one embed
    continues in the next embed of the same colour, and in further messages within Discord's limits (6000 characters
    per message); nothing is cut. None when the type has no form or no answer. No banner, author or footer: the card
    continues the panel above."""
    lines = answer_lines(ui, problem_type, answers)
    if not lines:
        return []
    title = ui.text(k.DETAILS_HEADING)
    if title:
        lines = [heading(title), *lines]
    embeds = [ui.embed(description=text, tone="info", branding_text=False) for text in _descriptions(lines)]
    return [MessageSpec(embeds=group) for group in pack_messages(embeds)]


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
    """The private message telling the customer about the decision (direct message, or posted in the ticket): the same
    key facts as the report panel (issue type, order, status), then staff's note or reason. A line linking the ticket is
    left out when the ticket cannot be linked (``render_lines``)."""
    label = type_label(ui, settings, type_id)
    values: Values = {
        "number": number,
        "type_label": label or "",
        "channel_mention": channel_mention(channel_id) if channel_id else "",
    }
    description_key = k.notify_key(status, "description")
    if not ui.content.has(description_key):
        raise MissingContent(description_key)
    embed = ui.embed(
        title=ui.text(k.notify_key(status, "title"), number=number, type_label=label or ""),
        description=ui.content.render_lines(description_key, **values),
        tone=_STATUS_TONES.get(status, "info"),
        fields=_fields(
            _type_field(ui, label), _status_field(ui, status), _order_field(ui, order), _reason_field(ui, reason)
        ),
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


def operator_reply(
    ui: Ui, title: str, text: str | None = None, *, tone: str = "info", banner: BannerId = "general-response"
) -> MessageSpec:
    """A staff-only private reply: a short title (what happened) over the details; ``general-response`` for answers,
    ``error`` for refused input."""
    return MessageSpec(embeds=(ui.operator_embed(title, text, tone=tone),), banner=ui.banner(banner))
