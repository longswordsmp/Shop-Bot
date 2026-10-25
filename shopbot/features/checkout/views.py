"""Pure builders for every checkout message and form.

These functions take the ``Ui`` (content + branding), settings, and plain data, and return ``MessageSpec`` /
``ModalSpec`` objects. They never call Discord or the database, so the runtime, the offline preview and the limits
checker render exactly the same specs. Missing required content raises ``MissingContent`` (nothing incomplete is
sent); optional elements whose content is missing are omitted.

Data in embeds is escaped with ``discord.utils.escape_markdown`` (product names, form answers). Select-menu
options show product names unescaped: Discord does not render markdown there, so escaping would only add visible
backslashes (and could push a 100-character name over the option-label limit). Mentions inside embeds never ping;
nothing here sets ``mention_user_ids``/``mention_role_ids`` (the notifier adds the customer's mention only when it
falls back to the order channel).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from discord.utils import escape_markdown

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.catalog import views as catalog_views
from shopbot.features.catalog.repo import Product
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import PaymentInstructions
from shopbot.features.credits import repo as credits_repo
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELD_VALUE, EMBED_FIELDS, EMBED_TOTAL_CHARS, OPTION_DESCRIPTION
from shopbot.ui.spec import (
    ButtonSpec,
    ButtonStyle,
    Component,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui, chunk_lines

Rows = tuple[tuple[Component, ...], ...]
OperatorText = Callable[..., str]

ROUTE = k.ROUTE
STAFF_ROUTE = k.STAFF_ROUTE
QUANTITY_INPUT = "quantity"
QUANTITY_MAX_DIGITS = 7
REFUND_AMOUNT_INPUT = "amount"
REFUND_REASON_INPUT = "reason"
# Refund reasons are also stored as the store-credit note of a refund to credit.
REFUND_REASON_MAX = credits_repo.NOTE_MAX_LENGTH
MODE_MONEY = "money"
MODE_CREDIT = "credit"
# Raw characters of a form answer per embed field. Escaping at most doubles the length, so every field value
# stays within Discord's 1024-character limit without cutting any character.
ANSWER_CHUNK = 500

_STATUS_TONES = {
    m.PENDING: "info",
    m.PAID: "success",
    m.PROCESSING: "info",
    m.FULFILLED: "success",
    m.CANCELLED: "neutral",
    m.REJECTED: "danger",
    m.EXPIRED: "neutral",
    m.REFUNDED: "neutral",
}


# -- custom ids -------------------------------------------------------------------------------------------


def start_id() -> str:
    return cid(ROUTE, "start")


def pick_id(session_id: int, page: int) -> str:
    return cid(ROUTE, "pick", session_id, page)


def page_id(session_id: int, page: int) -> str:
    return cid(ROUTE, "page", session_id, page)


def qty_id(session_id: int, product_id: int) -> str:
    return cid(ROUTE, "qty", session_id, product_id)


def edit_id(session_id: int) -> str:
    return cid(ROUTE, "edit", session_id)


def go_id(session_id: int, step: str) -> str:
    return cid(ROUTE, "go", session_id, step)


def fulfillment_id(session_id: int) -> str:
    return cid(ROUTE, "ful", session_id)


def details_id(session_id: int, part: int) -> str:
    return cid(ROUTE, "details", session_id, part)


def details_form_id(session_id: int, part: int) -> str:
    return cid(ROUTE, "dform", session_id, part)


def credits_id(session_id: int, apply: bool) -> str:
    return cid(ROUTE, "credits", session_id, int(apply))


def submit_id(session_id: int, total_minor: int) -> str:
    return cid(ROUTE, "submit", session_id, total_minor)


def cancel_id(session_id: int) -> str:
    return cid(ROUTE, "cancel", session_id)


def cancel_yes_id(session_id: int) -> str:
    return cid(ROUTE, "cancel_yes", session_id)


def cancel_no_id(session_id: int) -> str:
    return cid(ROUTE, "cancel_no", session_id)


def order_cancel_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel", order_id)


def order_cancel_yes_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel_yes", order_id)


def order_cancel_no_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel_no", order_id)


STAFF_ACTIONS: dict[str, str] = {
    m.PAID: "paid",
    m.REJECTED: "reject",
    m.CANCELLED: "cancel",
    m.PROCESSING: "process",
    m.FULFILLED: "fulfill",
}
STAFF_ACTION_TARGETS: dict[str, str] = {action: status for status, action in STAFF_ACTIONS.items()}


def staff_action_id(target: str, order_id: int) -> str:
    return cid(STAFF_ROUTE, STAFF_ACTIONS[target], order_id)


def refund_id(order_id: int, mode: str) -> str:
    return cid(STAFF_ROUTE, "refund", order_id, mode)


def refund_form_id(order_id: int, mode: str) -> str:
    return cid(STAFF_ROUTE, "refundform", order_id, mode)


def list_page_id(page: int, status: str, user_id: int) -> str:
    return cid(STAFF_ROUTE, "lpage", page, status or "all", user_id)


# -- small helpers ------------------------------------------------------------------------------------------


def escape(text: str) -> str:
    return escape_markdown(text)


def user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


def channel_mention(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else ""


def _registered(ui: Ui, key: str) -> bool:
    return key in ui.content.registry


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _optional(ui: Ui, key: str | None, /, **values: str | int) -> str | None:
    """Configured text, or None when the key is not configured (or no longer registered)."""
    if key is None or not _registered(ui, key):
        return None
    return ui.text(key, **values)


def money(ui: Ui, minor: int) -> str:
    text = ui.money(minor)
    if text is None:
        raise MissingContent(core.MONEY_FORMAT)
    return text


def _button(
    ui: Ui, key: str, custom_id: str, *, style: ButtonStyle = "secondary", disabled: bool = False
) -> ButtonSpec:
    button = ui.button(key, custom_id, style=style, disabled=disabled)
    if button is None:
        raise MissingContent(key)
    return button


def _field(ui: Ui, name_key: str, value: str, *, inline: bool = False) -> EmbedField:
    name = _text(ui, name_key)
    return EmbedField(name=name, value=value, inline=inline)


def _optional_field(ui: Ui, name_key: str, value: str, *, inline: bool = False) -> EmbedField | None:
    name = _optional(ui, name_key)
    return EmbedField(name=name, value=value, inline=inline) if name else None


def _notice(ui: Ui, text: str, *, tone: str = "warning") -> EmbedSpec:
    """A second card under the main one (no repeated author/footer)."""
    return ui.embed(description=text, tone=tone, branding_text=False)


def _description_message(ui: Ui, key: str, *, tone: str = "info", **values: str | int) -> MessageSpec:
    return MessageSpec(embeds=(ui.embed(description=_text(ui, key, **values), tone=tone),))


def _content_embed(
    ui: Ui,
    title_key: str | None,
    description_key: str | None,
    *,
    required: Sequence[str],
    values: Mapping[str, str | int] | None = None,
    **kwargs: object,
) -> EmbedSpec:
    for key in required:
        if not ui.content.has(key):
            raise MissingContent(key)
    embed = ui.content_embed(title_key, description_key, values=dict(values or {}), require=required, **kwargs)
    if embed is None:
        raise MissingContent(description_key or title_key or "")
    return embed


def error_message(ui: Ui, key: str, /, **values: str | int) -> MessageSpec:
    """A configured error message as a full message (what the router sends; used by the preview)."""
    embed = ui.error_embed(key, **values)
    if embed is None:
        raise MissingContent(key)
    return MessageSpec(embeds=(embed,))


def _fit_fields(base: Sequence[EmbedSpec], fields: Sequence[EmbedField], *, current: int) -> list[EmbedField]:
    """As many of ``fields`` as still fit into embed ``current`` of a message (25 fields, 6000 characters).

    Used only for data the customer typed themselves (form answers): configured text is never dropped.
    """
    used = sum(e.text_length() for e in base)
    count = len(base[current].fields)
    taken: list[EmbedField] = []
    for item in fields:
        size = len(item.name) + len(item.value)
        if count + len(taken) >= EMBED_FIELDS or used + size > EMBED_TOTAL_CHARS:
            break
        taken.append(item)
        used += size
    return taken


def _chunks(text: str, size: int) -> list[str]:
    """Split ``text`` into pieces of at most ``size`` characters (line boundaries preferred); blank pieces are
    dropped because an embed field value cannot be blank."""
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


def answer_fields(name: str, answer: str) -> list[EmbedField]:
    return [EmbedField(name=name, value=escape(piece)) for piece in _chunks(answer, ANSWER_CHUNK)]


# -- shop panel and private replies -------------------------------------------------------------------------


def catalog_browse_button(ui: Ui) -> ButtonSpec | None:
    """The catalog's private-browsing button for the shop panel (None when its label is not configured)."""
    return ui.button(catalog_keys.BUTTON_BROWSE, cid("ct", "browse", 0), style="secondary")


def panel(ui: Ui, *, browse_button: ButtonSpec | None = None, balance_button: ButtonSpec | None = None) -> MessageSpec:
    embed = _content_embed(
        ui, k.PANEL_TITLE, k.PANEL_DESCRIPTION, required=(k.PANEL_TITLE, k.PANEL_DESCRIPTION), panel=True
    )
    start = _button(ui, k.BUTTON_START, start_id(), style="primary")
    return MessageSpec(embeds=(embed,), rows=rows_of(start, browse_button, balance_button))


def started_reply(ui: Ui, channel_id: int) -> MessageSpec:
    return _description_message(ui, k.STARTED, tone="success", channel_mention=channel_mention(channel_id))


def session_exists_reply(ui: Ui, channel_id: int) -> MessageSpec:
    return _description_message(ui, k.SESSION_EXISTS, channel_mention=channel_mention(channel_id))


def limit_reply(ui: Ui, limit: int) -> MessageSpec:
    return _description_message(ui, k.LIMIT_REACHED, tone="warning", limit=limit)


def channel_topic(ui: Ui, user_name: str) -> str | None:
    return ui.text(k.CHANNEL_TOPIC, user_name=user_name)


# -- cart lines and totals ------------------------------------------------------------------------------------


def line_text(ui: Ui, name: str, quantity: int, unit_price_minor: int, line_total_minor: int) -> str:
    return _text(
        ui,
        k.CART_LINE,
        name=escape(name),
        quantity=quantity,
        unit_price=money(ui, unit_price_minor),
        line_total=money(ui, line_total_minor),
    )


def _lines_fields(ui: Ui, lines: Sequence[str]) -> list[EmbedField]:
    name = _text(ui, k.CART_FIELD)
    if not lines:
        return [EmbedField(name=name, value=_text(ui, k.CART_EMPTY))]
    return [EmbedField(name=name, value=block) for block in chunk_lines(lines, EMBED_FIELD_VALUE)]


def _cart_fields(ui: Ui, data: m.WorkflowData) -> list[EmbedField]:
    lines = [
        line_text(ui, line.product.name, line.quantity, line.product.price_minor, line.line_total_minor)
        for line in data.lines
    ]
    fields = _lines_fields(ui, lines)
    if data.lines:
        fields.append(_field(ui, k.CART_SUBTOTAL_FIELD, money(ui, data.totals.subtotal_minor), inline=True))
    return fields


def _option_label(ui: Ui, option_id: str | None) -> str | None:
    if option_id is None:
        return None
    return _optional(ui, k.option_key(option_id, "label"))


def _fee_fields(ui: Ui, fees: Sequence[tuple[str, int]]) -> list[EmbedField]:
    fields: list[EmbedField] = []
    for fee_id, amount in fees:
        item = _optional_field(ui, k.fee_label_key(fee_id), money(ui, amount), inline=True)
        if item is not None:
            fields.append(item)
    return fields


# -- workflow message ------------------------------------------------------------------------------------------


def effective_step(settings: Settings, session: m.Session) -> str:
    """The step to show: a step that no longer applies (settings changed) falls back to the cart."""
    steps = flow.steps_for(settings.checkout, session.fulfillment_option)
    return session.step if session.step in steps else m.CART


def _footer(ui: Ui, settings: Settings, data: m.WorkflowData, step: str) -> str | None:
    parts: list[str | None] = []
    browse = data.browse
    if step == m.BROWSE and browse is not None and browse.pages > 1:
        parts.append(ui.page_indicator(browse.page + 1, browse.pages))
    steps = flow.steps_for(settings.checkout, data.session.fulfillment_option)
    if step in steps:
        parts.append(ui.progress(steps.index(step) + 1, len(steps)))
    return "\n".join(p for p in parts if p) or None


def _step_embed(
    ui: Ui, title_key: str, description_key: str, fields: Sequence[EmbedField], footer: str | None
) -> EmbedSpec:
    return ui.embed(
        title=_text(ui, title_key),
        description=ui.text(description_key),
        fields=fields,
        footer_extra=footer,
    )


def _navigation(ui: Ui, settings: Settings, data: m.WorkflowData, step: str, *, can_continue: bool) -> list[ButtonSpec]:
    session = data.session
    option = session.fulfillment_option
    buttons: list[ButtonSpec] = []
    previous = flow.previous_step(settings.checkout, option, step)
    if previous is not None:
        buttons.append(_button(ui, core.BUTTON_BACK, go_id(session.id, previous)))
    following = flow.next_step(settings.checkout, option, step)
    if following is not None:
        buttons.append(
            _button(ui, core.BUTTON_CONTINUE, go_id(session.id, following), style="primary", disabled=not can_continue)
        )
    buttons.append(_button(ui, core.BUTTON_CANCEL, cancel_id(session.id), style="danger"))
    return buttons


def _product_option(ui: Ui, product: Product) -> SelectOptionSpec:
    price = catalog_views.price_text(ui, product)
    return SelectOptionSpec(
        label=product.name,
        value=str(product.id),
        description=price if len(price) <= OPTION_DESCRIPTION else None,
    )


def _browse(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    embeds = [
        _step_embed(
            ui, k.BROWSE_TITLE, k.BROWSE_DESCRIPTION, _cart_fields(ui, data), _footer(ui, settings, data, m.BROWSE)
        )
    ]
    rows: list[tuple[Component, ...]] = []
    browse = data.browse
    if browse is None or browse.total == 0 or not browse.products:
        embeds.append(_notice(ui, _text(ui, k.BROWSE_EMPTY_CATALOG), tone="neutral"))
    else:
        select = SelectSpec(
            custom_id=pick_id(session.id, browse.page),
            options=tuple(_product_option(ui, p) for p in browse.products),
            placeholder=ui.text(k.BROWSE_SELECT_PLACEHOLDER),
        )
        rows.append((select,))
        if browse.pages > 1:
            rows.append(
                (
                    _button(
                        ui, core.BUTTON_PREV, page_id(session.id, max(0, browse.page - 1)), disabled=browse.page <= 0
                    ),
                    _button(
                        ui,
                        core.BUTTON_NEXT,
                        page_id(session.id, min(browse.pages - 1, browse.page + 1)),
                        disabled=browse.page >= browse.pages - 1,
                    ),
                )
            )
    rows.extend(rows_of(*_navigation(ui, settings, data, m.BROWSE, can_continue=bool(data.lines))))
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows))


def _edit_option(ui: Ui, line: m.CartLine) -> SelectOptionSpec:
    description = _optional(ui, k.CART_EDIT_OPTION, quantity=line.quantity, line_total=money(ui, line.line_total_minor))
    if description is not None and len(description) > OPTION_DESCRIPTION:
        description = None  # optional text that does not fit is omitted, never cut
    return SelectOptionSpec(label=line.product.name, value=str(line.product.id), description=description)


def minimum_met(settings: Settings, data: m.WorkflowData) -> bool:
    return data.totals.subtotal_minor >= settings.checkout.min_order_total_minor


def _cart(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    embeds = [
        _step_embed(ui, k.CART_TITLE, k.CART_DESCRIPTION, _cart_fields(ui, data), _footer(ui, settings, data, m.CART))
    ]
    met = minimum_met(settings, data)
    if data.lines and not met:
        minimum = money(ui, settings.checkout.min_order_total_minor)
        embeds.append(_notice(ui, _text(ui, k.CART_MINIMUM_NOT_MET, minimum=minimum)))
    rows: list[tuple[Component, ...]] = []
    if data.lines:
        rows.append(
            (
                SelectSpec(
                    custom_id=edit_id(session.id),
                    options=tuple(_edit_option(ui, line) for line in data.lines),
                    placeholder=ui.text(k.CART_EDIT_PLACEHOLDER),
                ),
            )
        )
    rows.extend(rows_of(*_navigation(ui, settings, data, m.CART, can_continue=bool(data.lines) and met)))
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows))


def _fulfillment(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    c = settings.checkout
    chosen = c.fulfillment(session.fulfillment_option)
    fields: list[EmbedField] = []
    if chosen is not None:
        fields.append(_field(ui, k.FULFILLMENT_CHOSEN_FIELD, _text(ui, k.option_key(chosen.id, "label")), inline=True))
        if chosen.fee_minor > 0:
            fields.append(_field(ui, k.FULFILLMENT_FEE_FIELD, money(ui, chosen.fee_minor), inline=True))
    embed = _step_embed(
        ui, k.FULFILLMENT_TITLE, k.FULFILLMENT_DESCRIPTION, fields, _footer(ui, settings, data, m.FULFILLMENT)
    )
    options = tuple(
        SelectOptionSpec(
            label=_text(ui, k.option_key(option.id, "label")),
            value=option.id,
            description=ui.text(k.option_key(option.id, "description")),
            default=chosen is not None and option.id == chosen.id,
        )
        for option in c.fulfillment_options
    )
    select = SelectSpec(
        custom_id=fulfillment_id(session.id), options=options, placeholder=ui.text(k.FULFILLMENT_SELECT_PLACEHOLDER)
    )
    rows: list[tuple[Component, ...]] = [(select,)]
    rows.extend(rows_of(*_navigation(ui, settings, data, m.FULFILLMENT, can_continue=chosen is not None)))
    return MessageSpec(embeds=(embed,), rows=tuple(rows))


def _details(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    inputs = flow.form_inputs(settings.checkout, session.fulfillment_option)
    embed = _step_embed(ui, k.DETAILS_TITLE, k.DETAILS_DESCRIPTION, (), _footer(ui, settings, data, m.DETAILS))
    answers: list[EmbedField] = []
    for item in inputs:
        answer = session.details.get(item.answer_key, "")
        if answer.strip():
            answers += answer_fields(_text(ui, item.label_key), answer)
    embed = replace(embed, fields=tuple(_fit_fields([embed], answers, current=0)))
    parts = flow.form_parts(inputs)
    keys = (k.BUTTON_ENTER_DETAILS, k.BUTTON_ENTER_MORE_DETAILS)
    form_buttons = [
        _button(ui, keys[min(index, 1)], details_id(session.id, index), style="primary") for index in range(len(parts))
    ]
    complete = flow.details_complete(inputs, session.details)
    rows = rows_of(*form_buttons) + rows_of(*_navigation(ui, settings, data, m.DETAILS, can_continue=complete))
    return MessageSpec(embeds=(embed,), rows=rows)


def review_fields(ui: Ui, settings: Settings, data: m.WorkflowData) -> list[EmbedField]:
    session = data.session
    totals = data.totals
    fields = _cart_fields(ui, data)
    chosen = (
        settings.checkout.fulfillment(session.fulfillment_option) if settings.checkout.fulfillment_options else None
    )
    if chosen is not None:
        fields.append(_field(ui, k.FULFILLMENT_CHOSEN_FIELD, _text(ui, k.option_key(chosen.id, "label")), inline=True))
    if totals.fulfillment_fee_minor > 0:
        fields.append(_field(ui, k.FULFILLMENT_FEE_FIELD, money(ui, totals.fulfillment_fee_minor), inline=True))
    for fee in totals.fees:
        fields.append(_field(ui, k.fee_label_key(fee.fee_id), money(ui, fee.amount_minor), inline=True))
    if totals.credits_applied_minor > 0:
        fields.append(_field(ui, k.REVIEW_CREDITS_FIELD, money(ui, totals.credits_applied_minor), inline=True))
    elif data.credits_enabled and data.credits_available_minor > 0:
        available = _optional_field(ui, k.REVIEW_CREDITS_AVAILABLE_FIELD, money(ui, data.credits_available_minor))
        if available is not None:
            fields.append(available)
    fields.append(_field(ui, k.REVIEW_TOTAL_FIELD, money(ui, totals.total_minor), inline=True))
    return fields


def _review(ui: Ui, settings: Settings, data: m.WorkflowData, notice: EmbedSpec | None) -> MessageSpec:
    session = data.session
    embed = _step_embed(
        ui,
        k.REVIEW_TITLE,
        k.REVIEW_DESCRIPTION,
        review_fields(ui, settings, data),
        _footer(ui, settings, data, m.REVIEW),
    )
    embeds = [embed] if notice is None else [embed, notice]
    credit_buttons: list[ButtonSpec] = []
    if data.credits_enabled:
        if data.totals.credits_applied_minor > 0:
            credit_buttons.append(_button(ui, k.BUTTON_REMOVE_CREDITS, credits_id(session.id, False)))
        elif data.credits_available_minor > 0:
            credit_buttons.append(_button(ui, k.BUTTON_APPLY_CREDITS, credits_id(session.id, True), style="success"))
    navigation: list[ButtonSpec] = []
    previous = flow.previous_step(settings.checkout, session.fulfillment_option, m.REVIEW)
    if previous is not None:
        navigation.append(_button(ui, core.BUTTON_BACK, go_id(session.id, previous)))
    navigation.append(_button(ui, k.BUTTON_SUBMIT, submit_id(session.id, data.totals.total_minor), style="success"))
    navigation.append(_button(ui, core.BUTTON_CANCEL, cancel_id(session.id), style="danger"))
    return MessageSpec(embeds=tuple(embeds), rows=rows_of(*credit_buttons) + rows_of(*navigation))


def price_changed_notice(ui: Ui, total_minor: int) -> EmbedSpec:
    return _notice(ui, _text(ui, k.REVIEW_PRICE_CHANGED, total=money(ui, total_minor)))


def workflow_message(
    ui: Ui, settings: Settings, data: m.WorkflowData, *, notice: EmbedSpec | None = None
) -> MessageSpec:
    """The single editable workflow message of an active session, at its current step."""
    step = effective_step(settings, data.session)
    if step == m.BROWSE:
        return _browse(ui, settings, data)
    if step == m.FULFILLMENT:
        return _fulfillment(ui, settings, data)
    if step == m.DETAILS:
        return _details(ui, settings, data)
    if step == m.REVIEW:
        return _review(ui, settings, data, notice)
    return _cart(ui, settings, data)


def cancel_confirm(ui: Ui, session_id: int) -> MessageSpec:
    embed = _content_embed(
        ui,
        k.CANCEL_CONFIRM_TITLE,
        k.CANCEL_CONFIRM_DESCRIPTION,
        required=(k.CANCEL_CONFIRM_DESCRIPTION,),
        tone="warning",
    )
    rows = rows_of(
        _button(ui, core.BUTTON_CONFIRM, cancel_yes_id(session_id), style="danger"),
        _button(ui, core.BUTTON_CANCEL, cancel_no_id(session_id)),
    )
    return MessageSpec(embeds=(embed,), rows=rows)


def session_ended(ui: Ui, status: str) -> MessageSpec:
    """The workflow message after the customer cancelled (or the session expired); no controls."""
    if status == m.SESSION_EXPIRED:
        title_key, description_key = k.EXPIRED_TITLE, k.EXPIRED_DESCRIPTION
    else:
        title_key, description_key = k.CANCELLED_TITLE, k.CANCELLED_DESCRIPTION
    embed = _content_embed(ui, title_key, description_key, required=(description_key,), tone="neutral")
    return MessageSpec(embeds=(embed,))


# -- modals -------------------------------------------------------------------------------------------------------


def quantity_modal(ui: Ui, session_id: int, product_id: int, current: int | None) -> ModalSpec:
    return ModalSpec(
        custom_id=qty_id(session_id, product_id),
        title=_text(ui, k.QUANTITY_TITLE),
        inputs=(
            TextInputSpec(
                custom_id=QUANTITY_INPUT,
                label=_text(ui, k.QUANTITY_LABEL),
                description=ui.text(k.QUANTITY_DESCRIPTION),
                placeholder=ui.text(k.QUANTITY_PLACEHOLDER),
                default=str(current) if current else None,
                required=True,
                min_length=1,
                max_length=QUANTITY_MAX_DIGITS,
            ),
        ),
    )


def details_modal(ui: Ui, settings: Settings, session: m.Session, part: int) -> ModalSpec:
    parts = flow.form_parts(flow.form_inputs(settings.checkout, session.fulfillment_option))
    if not 0 <= part < len(parts):
        raise IndexError(part)
    inputs = tuple(
        TextInputSpec(
            custom_id=item.answer_key,
            label=_text(ui, item.label_key),
            style="paragraph" if item.field.style == "paragraph" else "short",
            description=ui.text(item.description_key),
            placeholder=ui.text(item.placeholder_key),
            default=session.details.get(item.answer_key) or None,
            required=item.field.required,
            min_length=item.field.min_length or None,
            max_length=item.field.max_length,
        )
        for item in parts[part]
    )
    return ModalSpec(custom_id=details_form_id(session.id, part), title=_text(ui, k.DETAILS_MODAL_TITLE), inputs=inputs)


# -- submitted order and status views ------------------------------------------------------------------------------


def order_values(ui: Ui, order: m.Order) -> dict[str, str | int]:
    return {"order_number": str(order.number), "total": money(ui, order.total_minor)}


def order_fields(ui: Ui, view: m.OrderView) -> list[EmbedField]:
    """Items, subtotal, fulfillment option and fee, fees, credits, total (as stored in the order)."""
    order = view.order
    lines = [line_text(ui, i.name, i.quantity, i.unit_price_minor, i.line_total_minor) for i in view.items]
    fields = _lines_fields(ui, lines)
    fields.append(_field(ui, k.CART_SUBTOTAL_FIELD, money(ui, order.subtotal_minor), inline=True))
    label = _option_label(ui, order.fulfillment_option)
    if label:
        item = _optional_field(ui, k.FULFILLMENT_CHOSEN_FIELD, label, inline=True)
        if item is not None:
            fields.append(item)
    if order.fulfillment_fee_minor > 0:
        item = _optional_field(ui, k.FULFILLMENT_FEE_FIELD, money(ui, order.fulfillment_fee_minor), inline=True)
        if item is not None:
            fields.append(item)
    fields += _fee_fields(ui, [(f.fee_id, f.amount_minor) for f in view.fees])
    if order.credits_applied_minor > 0:
        item = _optional_field(ui, k.REVIEW_CREDITS_FIELD, money(ui, order.credits_applied_minor), inline=True)
        if item is not None:
            fields.append(item)
    fields.append(_field(ui, k.REVIEW_TOTAL_FIELD, money(ui, order.total_minor), inline=True))
    return fields


def instructions_embed(ui: Ui, instructions: PaymentInstructions, values: Mapping[str, str | int]) -> EmbedSpec:
    title = _optional(ui, instructions.title_key, **values)
    description = _text(ui, instructions.description_key, **values)
    return ui.embed(title=title, description=description, tone="info", branding_text=False)


def _instruction_link(ui: Ui, instructions: PaymentInstructions | None) -> ButtonSpec | None:
    if instructions is None or not instructions.link_url or not instructions.link_label_key:
        return None
    label = _optional(ui, instructions.link_label_key)
    if not label:
        return None
    return ButtonSpec(label=label, style="link", url=instructions.link_url)


def order_message(ui: Ui, view: m.OrderView, *, instructions: PaymentInstructions | None) -> MessageSpec:
    """The workflow message of a submitted order: waiting for payment (with the payment instructions and the
    customer's cancel button), or the view configured for its current status."""
    order = view.order
    values = order_values(ui, order)
    fields = order_fields(ui, view)
    if order.status == m.PENDING:
        embed = _content_embed(
            ui,
            k.SUBMITTED_TITLE,
            k.SUBMITTED_DESCRIPTION,
            required=(k.SUBMITTED_TITLE,),
            values=values,
            fields=fields,
            tone="success",
        )
        embeds = [embed]
        if instructions is not None:
            embeds.append(instructions_embed(ui, instructions, values))
        rows = rows_of(
            _instruction_link(ui, instructions),
            _button(ui, k.BUTTON_CANCEL_ORDER, order_cancel_id(order.id), style="danger"),
        )
        return MessageSpec(embeds=tuple(embeds), rows=rows)
    title_key = k.status_title_key(order.status)
    embed = _content_embed(
        ui,
        title_key,
        k.status_description_key(order.status),
        required=(title_key,),
        values=values,
        fields=fields,
        tone=_STATUS_TONES.get(order.status, "info"),
    )
    return MessageSpec(embeds=(embed,))


def order_cancel_confirm(ui: Ui, order: m.Order) -> MessageSpec:
    embed = _content_embed(
        ui,
        k.ORDER_CANCEL_CONFIRM_TITLE,
        k.ORDER_CANCEL_CONFIRM_DESCRIPTION,
        required=(k.ORDER_CANCEL_CONFIRM_DESCRIPTION,),
        values={"order_number": str(order.number)},
        tone="warning",
    )
    rows = rows_of(
        _button(ui, core.BUTTON_CONFIRM, order_cancel_yes_id(order.id), style="danger"),
        _button(ui, core.BUTTON_BACK, order_cancel_no_id(order.id)),
    )
    return MessageSpec(embeds=(embed,), rows=rows)


# -- staff order controls -----------------------------------------------------------------------------------------


def staff_buttons(ui: Ui, order: m.Order, *, staff_confirms_payment: bool, credit_refunds: bool) -> list[ButtonSpec]:
    buttons: list[ButtonSpec] = []
    status = order.status
    if status == m.PENDING:
        if staff_confirms_payment:
            buttons.append(_button(ui, k.STAFF_BUTTON_MARK_PAID, staff_action_id(m.PAID, order.id), style="success"))
        buttons.append(_button(ui, k.STAFF_BUTTON_REJECT, staff_action_id(m.REJECTED, order.id), style="danger"))
    if status == m.PAID:
        buttons.append(_button(ui, k.STAFF_BUTTON_PROCESSING, staff_action_id(m.PROCESSING, order.id), style="primary"))
    if status in (m.PAID, m.PROCESSING):
        buttons.append(_button(ui, k.STAFF_BUTTON_FULFILLED, staff_action_id(m.FULFILLED, order.id), style="success"))
    if order.can_refund:
        buttons.append(_button(ui, k.STAFF_BUTTON_REFUND, refund_id(order.id, MODE_MONEY)))
        if credit_refunds:
            buttons.append(_button(ui, k.STAFF_BUTTON_REFUND_CREDIT, refund_id(order.id, MODE_CREDIT)))
    if status in m.TRANSITIONS[m.CANCELLED]:
        style: ButtonStyle = "secondary" if status == m.PENDING else "danger"
        buttons.append(_button(ui, k.STAFF_BUTTON_CANCEL, staff_action_id(m.CANCELLED, order.id), style=style))
    return buttons


def staff_message(ui: Ui, view: m.OrderView, *, staff_confirms_payment: bool, credit_refunds: bool) -> MessageSpec:
    """The order controls in the staff channel (edited in place on every status change)."""
    order = view.order
    status = _text(ui, k.status_label_key(order.status))
    total = money(ui, order.total_minor)
    title = _text(ui, k.STAFF_TITLE, order_number=str(order.number), total=total, status=status)
    description = _text(
        ui,
        k.STAFF_DESCRIPTION,
        order_number=str(order.number),
        user_mention=user_mention(order.user_id),
        channel_mention=channel_mention(view.channel_id),
        total=total,
        status=status,
    )
    fields = order_fields(ui, view)
    if order.refunded_minor > 0:
        fields.append(_field(ui, k.STAFF_REFUNDED_FIELD, money(ui, order.refunded_minor), inline=True))
    embed = ui.embed(
        title=title,
        description=description,
        fields=fields,
        tone=_STATUS_TONES.get(order.status, "info"),
        timestamp=True,
    )
    buttons = staff_buttons(ui, order, staff_confirms_payment=staff_confirms_payment, credit_refunds=credit_refunds)
    return MessageSpec(embeds=(embed,), rows=rows_of(*buttons))


def ordered_answers(settings: Settings, order: m.Order) -> list[tuple[str, str]]:
    """(answer key, answer) in form order (fields no longer configured last, by key)."""
    known = [i.answer_key for i in flow.form_inputs(settings.checkout, order.fulfillment_option)]
    keys = [key for key in known if key in order.details]
    keys += sorted(key for key in order.details if key not in known)
    return [(key, order.details[key]) for key in keys]


def staff_details_messages(ui: Ui, settings: Settings, order: m.Order) -> list[MessageSpec]:
    """The customer's form answers, posted under the staff order controls (split so every message fits).

    Answers whose label is no longer configured are left out here (staff see every answer in /order lookup).
    """
    fields: list[EmbedField] = []
    for answer_key, answer in ordered_answers(settings, order):
        label_key = flow.label_key_for_answer(answer_key)
        label = _optional(ui, label_key) if label_key else None
        if label and answer.strip():
            fields += answer_fields(label, answer)
    if not fields:
        return []
    title = _optional(ui, k.STAFF_DETAILS_TITLE, order_number=str(order.number))
    messages: list[MessageSpec] = []
    current: list[EmbedField] = []
    base = len(title or "")
    used = base
    for item in fields:
        size = len(item.name) + len(item.value)
        if current and (len(current) >= EMBED_FIELDS or used + size > EMBED_TOTAL_CHARS):
            messages.append(
                MessageSpec(embeds=(ui.embed(title=title, fields=current, tone="neutral", branding_text=False),))
            )
            current, used = [], base
        current.append(item)
        used += size
    if current:
        messages.append(
            MessageSpec(embeds=(ui.embed(title=title, fields=current, tone="neutral", branding_text=False),))
        )
    return messages


# -- customer notifications ---------------------------------------------------------------------------------------


def _notify_values(ui: Ui, view: m.OrderView) -> dict[str, str | int]:
    return {**order_values(ui, view.order), "channel_mention": channel_mention(view.channel_id)}


def status_notification(ui: Ui, view: m.OrderView, status: str) -> MessageSpec:
    embed = _content_embed(
        ui,
        k.notify_title_key(status),
        k.notify_description_key(status),
        required=(k.notify_description_key(status),),
        values=_notify_values(ui, view),
        tone=_STATUS_TONES.get(status, "info"),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,))


def reminder_notification(ui: Ui, view: m.OrderView, instructions: PaymentInstructions | None) -> MessageSpec:
    kind = k.NOTIFY_PAYMENT_REMINDER
    values = _notify_values(ui, view)
    embed = _content_embed(
        ui,
        k.notify_title_key(kind),
        k.notify_description_key(kind),
        required=(k.notify_description_key(kind),),
        values=values,
        tone="warning",
        timestamp=True,
    )
    embeds = [embed]
    if instructions is not None:
        embeds.append(instructions_embed(ui, instructions, order_values(ui, view.order)))
    link = _instruction_link(ui, instructions)
    return MessageSpec(embeds=tuple(embeds), rows=rows_of(link))


def refund_notification(ui: Ui, view: m.OrderView, amount_minor: int) -> MessageSpec:
    kind = k.NOTIFY_REFUND
    embed = _content_embed(
        ui,
        k.notify_title_key(kind),
        k.notify_description_key(kind),
        required=(k.notify_description_key(kind),),
        values={
            "order_number": str(view.order.number),
            "amount": money(ui, amount_minor),
            "channel_mention": channel_mention(view.channel_id),
        },
        tone="info",
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,))


# -- staff-only forms and lists (operator text) ----------------------------------------------------------------------


def refund_modal(op: OperatorText, order: m.Order, *, mode: str, max_text: str, example: str) -> ModalSpec:
    title_key = "checkout.refund.title_credit" if mode == MODE_CREDIT else "checkout.refund.title"
    return ModalSpec(
        custom_id=refund_form_id(order.id, mode),
        title=op(title_key, number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=REFUND_AMOUNT_INPUT,
                label=op("checkout.refund.amount", currency=order.currency),
                description=op("checkout.refund.amount_hint", max=max_text, example=example),
                default=max_text,
                required=True,
                min_length=1,
                max_length=24,
            ),
            TextInputSpec(
                custom_id=REFUND_REASON_INPUT,
                label=op("checkout.refund.reason"),
                style="paragraph",
                description=op("checkout.refund.reason_hint"),
                required=False,
                max_length=REFUND_REASON_MAX,
            ),
        ),
    )


def order_list_page(
    ui: Ui,
    op: OperatorText,
    amount: Callable[[int, str], str],
    orders: Sequence[m.Order],
    *,
    page: int,
    pages: int,
    total: int,
    status: str,
    user_id: int,
) -> MessageSpec:
    """Staff order list (operator text) with previous/next buttons when there is more than one page."""
    lines = [
        op(
            "checkout.list.line",
            number=o.number,
            status=o.status,
            customer=user_mention(o.user_id),
            total=amount(o.total_minor, o.currency),
            created=f"<t:{o.created_at}:d>",
        )
        for o in orders
    ]
    description = "\n".join(lines) if lines else op("checkout.list.empty")
    embed = ui.operator_embed(op("checkout.list.title", total=total), description)
    if pages <= 1:
        return MessageSpec(embeds=(embed,))
    embed = replace(embed, footer_text=op("checkout.list.page", page=page + 1, pages=pages))
    rows = rows_of(
        ButtonSpec(
            label=op("checkout.list.previous"),
            custom_id=list_page_id(max(0, page - 1), status, user_id),
            disabled=page <= 0,
        ),
        ButtonSpec(
            label=op("checkout.list.next"),
            custom_id=list_page_id(min(pages - 1, page + 1), status, user_id),
            disabled=page >= pages - 1,
        ),
    )
    return MessageSpec(embeds=(embed,), rows=rows)
