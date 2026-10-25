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

Amounts stored in an order are written in the ORDER's currency (``order_money``): the configured
``checkout.currency_exponent`` applies only while that is still the configured shop currency. Amounts of an
unsubmitted cart use the configured currency (``money``).

Large orders: the order views (``order_messages``) and the staff controls (``staff_views.staff_messages``) budget
Discord's limits (6000 characters and 10 cards per message, 25 fields per card). When the order lines do not fit next
to the configured text, the main message keeps the totals, the payment instructions and the buttons, and the lines
are sent as separate messages; configured text is never cut.

Banners (``shopbot.ui.banners``): every customer message names its banner picture here. The single live workflow
message of an order ticket shows the banner of its current step and, once submitted, the banner of the order's status
(``status_banner``); continuation messages (order lines and payment instructions that did not fit) show none.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.catalog import views as catalog_views
from shopbot.features.catalog.repo import Product
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import PaymentInstructions
from shopbot.features.credits import repo as credits_repo
from shopbot.features.tickets import views as tickets_views
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import (
    EMBED_FIELD_VALUE,
    EMBED_FIELDS,
    EMBED_TOTAL_CHARS,
    EMBEDS_PER_MESSAGE,
    OPTION_DESCRIPTION,
)
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
# Writes an amount in minor units with common.money_format (raises MissingContent when that is not configured).
Money = Callable[[int], str]

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


def status_tone(status: str) -> str:
    """The embed tone (colour) used for an order in ``status`` (customer views)."""
    return _STATUS_TONES.get(status, "info")


_STATUS_BANNERS: dict[str, BannerId] = {
    m.PENDING: "pending-review",
    m.PAID: "successful-confirmation",
    m.PROCESSING: "submission-and-processing",
    m.FULFILLED: "completed-or-dispatched",
    m.CANCELLED: "cancellation",
    m.REJECTED: "rejected-or-unsuccessful-submission",
    m.EXPIRED: "cancellation",
    m.REFUNDED: "resolution",
}


def status_banner(status: str) -> BannerId:
    """The banner of an order in ``status``: its view on the workflow message and its status notification."""
    return _STATUS_BANNERS.get(status, "order-status")


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


def submit_id(session_id: int, total_minor: int, gross_minor: int) -> str:
    """The submit button carries the totals the customer was shown: the total to pay and the gross amount before
    credits (with both, the applied credit is fixed too). Submission refuses anything else."""
    return cid(ROUTE, "submit", session_id, total_minor, gross_minor)


def submission_form_id(session_id: int, total_minor: int, gross_minor: int) -> str:
    """The submission form (``checkout.submission_form``) opened by the submit button; it carries the same shown
    totals as the button, so submitting the form is refused when they changed."""
    return cid(ROUTE, "sform", session_id, total_minor, gross_minor)


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


def staff_action_id(target: str, order_id: int, status: str | None = None) -> str:
    """A staff status button. ``status`` is the order status the controls are rendered for: a click acts only while
    the order is still in it (a stale click changes nothing). Default: the first status ``target`` can be reached
    from."""
    rendered = status if status is not None else m.TRANSITIONS[target][0]
    return cid(STAFF_ROUTE, STAFF_ACTIONS[target], order_id, rendered)


def refund_id(order_id: int, mode: str) -> str:
    return cid(STAFF_ROUTE, "refund", order_id, mode)


def refund_form_id(order_id: int, mode: str) -> str:
    return cid(STAFF_ROUTE, "refundform", order_id, mode)


def credit_return_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "creturn", order_id)


def credit_return_form_id(order_id: int) -> str:
    return cid(STAFF_ROUTE, "creturnform", order_id)


# Management actions on the live order record (``staff_views``: claim/unclaim, note, correction, tracking, details).
MANAGEMENT_ACTIONS = frozenset({"claim", "unclaim", "note", "correct", "track", "details"})
# Staff-channel actions whose custom_id carries the order id as its first argument (the controls of an order).
ORDER_CONTROL_ACTIONS = frozenset({*STAFF_ACTION_TARGETS, "refund", "creturn", *MANAGEMENT_ACTIONS})


def list_page_id(page: int, status: str, user_id: int) -> str:
    return cid(STAFF_ROUTE, "lpage", page, status or "all", user_id)


def list_filter_id(user_id: int) -> str:
    """The order list's status filter (keeps the member filter of the list it is on)."""
    return cid(STAFF_ROUTE, "lfilter", user_id)


def list_view_id() -> str:
    """The order list's menu that shows an order's full record privately."""
    return cid(STAFF_ROUTE, "lview")


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
    """An amount in the configured shop currency (carts, prices, minimums)."""
    text = ui.money(minor)
    if text is None:
        raise MissingContent(core.MONEY_FORMAT)
    return text


def currency_digits(settings: Settings, currency: str) -> int:
    """Minor-unit digits of ``currency``: the configured override only applies to the configured shop currency."""
    c = settings.checkout
    return currency_exponent(currency, c.currency_exponent if currency == c.currency else -1)


def order_money(ui: Ui, settings: Settings, currency: str) -> Money:
    """Writes amounts stored in an order with the order's own currency (common.money_format)."""
    c = settings.checkout
    exponent = currency_digits(settings, currency)

    def write(minor: int) -> str:
        amount = format_amount(minor, exponent, c.decimal_separator, c.thousands_separator)
        text = ui.text(core.MONEY_FORMAT, amount=amount, currency=currency)
        if text is None:
            raise MissingContent(core.MONEY_FORMAT)
        return text

    return write


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


def _description_message(
    ui: Ui, key: str, banner: BannerId, /, *, tone: str = "info", **values: str | int
) -> MessageSpec:
    return MessageSpec(embeds=(ui.embed(description=_text(ui, key, **values), tone=tone),), banner=ui.banner(banner))


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
    return MessageSpec(embeds=(embed,), banner=ui.banner("error"))


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


def _spread(head: EmbedSpec, fields: Sequence[EmbedField]) -> list[EmbedSpec]:
    """``head`` with ``fields``; fields beyond the 25 of one card continue on further cards of the same colour
    (without the author line and footer)."""
    embeds = [replace(head, fields=tuple(fields[:EMBED_FIELDS]))]
    for start in range(EMBED_FIELDS, len(fields), EMBED_FIELDS):
        embeds.append(EmbedSpec(color=head.color, fields=tuple(fields[start : start + EMBED_FIELDS])))
    return embeds


def _fits(embeds: Sequence[EmbedSpec]) -> bool:
    """Whether the cards fit one message (10 cards, 6000 characters; each card holds at most 25 fields)."""
    return (
        len(embeds) <= EMBEDS_PER_MESSAGE
        and sum(e.text_length() for e in embeds) <= EMBED_TOTAL_CHARS
        and all(len(e.fields) <= EMBED_FIELDS for e in embeds)
    )


def _pack_fields(fields: Sequence[EmbedField], color: int | None) -> list[MessageSpec]:
    """Data fields (each within the field limits) in as many messages as needed: 25 fields per card, 10 cards and
    6000 characters per message. Nothing is cut."""
    messages: list[MessageSpec] = []
    embeds: list[EmbedSpec] = []
    current: list[EmbedField] = []
    used = 0
    for item in fields:
        size = len(item.name) + len(item.value)
        if current and len(current) >= EMBED_FIELDS:
            embeds.append(EmbedSpec(color=color, fields=tuple(current)))
            current = []
        if used + size > EMBED_TOTAL_CHARS or (not current and len(embeds) >= EMBEDS_PER_MESSAGE):
            if current:
                embeds.append(EmbedSpec(color=color, fields=tuple(current)))
                current = []
            if embeds:
                messages.append(MessageSpec(embeds=tuple(embeds)))
            embeds, used = [], 0
        current.append(item)
        used += size
    if current:
        embeds.append(EmbedSpec(color=color, fields=tuple(current)))
    if embeds:
        messages.append(MessageSpec(embeds=tuple(embeds)))
    return messages


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


def panel(
    ui: Ui,
    *,
    browse_button: ButtonSpec | None = None,
    balance_button: ButtonSpec | None = None,
    report_button: ButtonSpec | None = None,
) -> MessageSpec:
    embed = _content_embed(
        ui, k.PANEL_TITLE, k.PANEL_DESCRIPTION, required=(k.PANEL_TITLE, k.PANEL_DESCRIPTION), panel=True
    )
    start = _button(ui, k.BUTTON_START, start_id(), style="primary")
    return MessageSpec(
        embeds=(embed,),
        rows=rows_of(start, report_button, browse_button, balance_button),
        banner=ui.banner("purchase-panel"),
    )


def _open_ticket_rows(ui: Ui, url: str | None) -> Rows:
    """The optional link button that opens the customer's order ticket (omitted when its label is not set)."""
    label = _optional(ui, k.BUTTON_OPEN_TICKET)
    if not label or not url:
        return ()
    return rows_of(ButtonSpec(label=label, style="link", url=url))


def started_reply(ui: Ui, channel_id: int, url: str | None = None) -> MessageSpec:
    spec = _description_message(
        ui, k.STARTED, "ticket-created", tone="success", channel_mention=channel_mention(channel_id)
    )
    return replace(spec, rows=_open_ticket_rows(ui, url))


def session_exists_reply(ui: Ui, channel_id: int, url: str | None = None) -> MessageSpec:
    spec = _description_message(ui, k.SESSION_EXISTS, "ticket-created", channel_mention=channel_mention(channel_id))
    return replace(spec, rows=_open_ticket_rows(ui, url))


def order_exists_reply(ui: Ui, channel_id: int, order_number: int, limit: int, url: str | None = None) -> MessageSpec:
    """The customer already has the maximum number of open orders: a private link to the open order's ticket."""
    spec = _description_message(
        ui,
        k.ORDER_EXISTS,
        "order-status",
        tone="warning",
        channel_mention=channel_mention(channel_id),
        order_number=str(order_number),
        limit=limit,
    )
    return replace(spec, rows=_open_ticket_rows(ui, url))


def buy_added_reply(ui: Ui, name: str, channel_id: int) -> MessageSpec:
    """Reply to a catalog buy button that continued the customer's existing order channel."""
    return _description_message(
        ui, k.BUY_ADDED, "selection-and-cart", name=escape(name), channel_mention=channel_mention(channel_id)
    )


def buy_started_reply(ui: Ui, channel_id: int, notice: EmbedSpec | None = None) -> MessageSpec:
    """Reply to a catalog buy button that opened a new order channel; ``notice`` explains a product that could not be
    put in the cart after all (e.g. sold out a moment earlier)."""
    started = started_reply(ui, channel_id)  # its banner too: the ticket opened even when the product was not added
    return replace(started, embeds=started.embeds + ((notice,) if notice is not None else ()))


def limit_reply(ui: Ui, limit: int) -> MessageSpec:
    return _description_message(ui, k.LIMIT_REACHED, "rejected-or-unsuccessful-submission", tone="warning", limit=limit)


def ticket_reference(settings: Settings, number: int) -> str:
    """The ticket's unique reference as shown everywhere (the ticket number, as in the channel name)."""
    return tickets_views.number_text(settings.tickets, number)


def _reference_field(ui: Ui, settings: Settings, ticket_number: int | None) -> list[EmbedField]:
    name = _text(ui, k.TICKET_REFERENCE_FIELD)  # required even when (exceptionally) no number is known
    if ticket_number is None:
        return []
    return [EmbedField(name=name, value=ticket_reference(settings, ticket_number), inline=True)]


def ticket_intro(ui: Ui, settings: Settings, ticket_number: int, user_id: int) -> MessageSpec:
    """The first message of an order ticket: which ticket this is and who can see it (configured text), with the
    ticket's reference as a field so it is always shown."""
    reference = ticket_reference(settings, ticket_number)
    embed = _content_embed(
        ui,
        k.TICKET_INTRO_TITLE,
        k.TICKET_INTRO_DESCRIPTION,
        required=(k.TICKET_INTRO_DESCRIPTION,),
        values={"ticket_number": reference, "user_mention": user_mention(user_id)},
        tone="info",
        fields=_reference_field(ui, settings, ticket_number),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("ticket-created"))


def channel_topic(ui: Ui, user_name: str) -> str | None:
    return ui.text(k.CHANNEL_TOPIC, user_name=user_name)


# -- cart lines and totals ------------------------------------------------------------------------------------


def line_text(
    ui: Ui, name: str, quantity: int, unit_price_minor: int, line_total_minor: int, *, fmt: Money | None = None
) -> str:
    """One cart/order line; ``fmt`` writes the amounts (default: the configured shop currency)."""
    write: Money = fmt if fmt is not None else (lambda minor: money(ui, minor))
    return _text(
        ui,
        k.CART_LINE,
        name=escape(name),
        quantity=quantity,
        unit_price=write(unit_price_minor),
        line_total=write(line_total_minor),
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


def _fee_fields(ui: Ui, fees: Sequence[tuple[str, int]], fmt: Money) -> list[EmbedField]:
    fields: list[EmbedField] = []
    for fee_id, amount in fees:
        item = _optional_field(ui, k.fee_label_key(fee_id), fmt(amount), inline=True)
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


def _step_embeds(
    ui: Ui, title_key: str, description_key: str, fields: Sequence[EmbedField], footer: str | None
) -> list[EmbedSpec]:
    """A step card whose fields continue on further cards beyond Discord's 25 fields per card (many fees)."""
    return _spread(_step_embed(ui, title_key, description_key, (), footer), fields)


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


def _session_header(ui: Ui, settings: Settings, data: m.WorkflowData) -> list[EmbedField]:
    """The ticket reference and the current status: the first fields of every step of the live panel."""
    status = EmbedField(
        name=_text(ui, k.TICKET_STATUS_FIELD), value=_text(ui, k.TICKET_STATUS_IN_PROGRESS), inline=True
    )
    return [*_reference_field(ui, settings, data.ticket_number), status]


def _compact_cart_fields(ui: Ui, data: m.WorkflowData) -> list[EmbedField]:
    """The cart as its subtotal only (used on later steps when the lines do not fit next to the other data)."""
    if not data.lines:
        return _lines_fields(ui, [])
    return [_field(ui, k.CART_SUBTOTAL_FIELD, money(ui, data.totals.subtotal_minor), inline=True)]


def _chosen_option_fields(ui: Ui, settings: Settings, session: m.Session) -> list[EmbedField]:
    """The chosen fulfillment option and its fee (when it has one)."""
    chosen = (
        settings.checkout.fulfillment(session.fulfillment_option) if settings.checkout.fulfillment_options else None
    )
    if chosen is None:
        return []
    fields = [_field(ui, k.FULFILLMENT_CHOSEN_FIELD, _text(ui, k.option_key(chosen.id, "label")), inline=True)]
    if chosen.fee_minor > 0:
        fields.append(_field(ui, k.FULFILLMENT_FEE_FIELD, money(ui, chosen.fee_minor), inline=True))
    return fields


def _detail_answer_fields(ui: Ui, settings: Settings, session: m.Session) -> list[EmbedField]:
    """The details-form answers the customer entered so far, named by their field labels, in form order."""
    answers: list[EmbedField] = []
    for item in flow.form_inputs(settings.checkout, session.fulfillment_option):
        answer = session.details.get(item.answer_key, "")
        if answer.strip():
            answers += answer_fields(_text(ui, item.label_key), answer)
    return answers


def _with_answers(
    embeds: list[EmbedSpec], answers: Sequence[EmbedField], *, budget: Sequence[EmbedSpec] = ()
) -> tuple[list[EmbedSpec], bool]:
    """``embeds`` with as many of the customer's ``answers`` as still fit on the last card (``budget``: further cards
    of the same message). Returns the cards and whether every answer fit."""
    taken = _fit_fields([*embeds, *budget], answers, current=len(embeds) - 1)
    last = embeds[-1]
    return [*embeds[:-1], replace(last, fields=last.fields + tuple(taken))], len(taken) == len(answers)


def _browse(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    fields = _session_header(ui, settings, data) + _cart_fields(ui, data)
    embeds = _step_embeds(ui, k.BROWSE_TITLE, k.BROWSE_DESCRIPTION, fields, _footer(ui, settings, data, m.BROWSE))
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
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows), banner=ui.banner("selection-and-cart"))


def _edit_option(ui: Ui, line: m.CartLine) -> SelectOptionSpec:
    description = _optional(ui, k.CART_EDIT_OPTION, quantity=line.quantity, line_total=money(ui, line.line_total_minor))
    if description is not None and len(description) > OPTION_DESCRIPTION:
        description = None  # optional text that does not fit is omitted, never cut
    return SelectOptionSpec(label=line.product.name, value=str(line.product.id), description=description)


def minimum_met(settings: Settings, data: m.WorkflowData) -> bool:
    return data.totals.subtotal_minor >= settings.checkout.min_order_total_minor


def _cart(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    fields = _session_header(ui, settings, data) + _cart_fields(ui, data)
    embeds = _step_embeds(ui, k.CART_TITLE, k.CART_DESCRIPTION, fields, _footer(ui, settings, data, m.CART))
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
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows), banner=ui.banner("selection-and-cart"))


def _fulfillment(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    c = settings.checkout
    chosen = c.fulfillment(session.fulfillment_option)
    header = _session_header(ui, settings, data)
    option_fields = _chosen_option_fields(ui, settings, session)
    footer = _footer(ui, settings, data, m.FULFILLMENT)
    # What was selected so far: the cart lines, or only the subtotal when the lines do not fit with the rest.
    embed = _step_embed(
        ui, k.FULFILLMENT_TITLE, k.FULFILLMENT_DESCRIPTION, header + _cart_fields(ui, data) + option_fields, footer
    )
    if not _fits([embed]):
        compact = header + _compact_cart_fields(ui, data) + option_fields
        embed = _step_embed(ui, k.FULFILLMENT_TITLE, k.FULFILLMENT_DESCRIPTION, compact, footer)
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
    return MessageSpec(embeds=(embed,), rows=tuple(rows), banner=ui.banner("details-and-options"))


def _details(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    inputs = flow.form_inputs(settings.checkout, session.fulfillment_option)
    header = _session_header(ui, settings, data)
    option_fields = _chosen_option_fields(ui, settings, session)
    footer = _footer(ui, settings, data, m.DETAILS)
    answers = _detail_answer_fields(ui, settings, session)
    # The selections so far and the answers entered on this step. When not everything fits, the cart lines give way
    # to the subtotal first; answers that still do not fit are not shown (they are stored and shown to staff).
    full = _step_embed(
        ui, k.DETAILS_TITLE, k.DETAILS_DESCRIPTION, header + _cart_fields(ui, data) + option_fields, footer
    )
    embeds, complete = _with_answers([full], answers)
    if not complete or not _fits(embeds):
        compact = header + _compact_cart_fields(ui, data) + option_fields
        embeds, _ = _with_answers([_step_embed(ui, k.DETAILS_TITLE, k.DETAILS_DESCRIPTION, compact, footer)], answers)
    embed = embeds[0]
    parts = flow.form_parts(inputs)
    keys = (k.BUTTON_ENTER_DETAILS, k.BUTTON_ENTER_MORE_DETAILS)
    form_buttons = [
        _button(ui, keys[min(index, 1)], details_id(session.id, index), style="primary") for index in range(len(parts))
    ]
    complete = flow.details_complete(inputs, session.details)
    rows = rows_of(*form_buttons) + rows_of(*_navigation(ui, settings, data, m.DETAILS, can_continue=complete))
    return MessageSpec(embeds=(embed,), rows=rows, banner=ui.banner("details-and-options"))


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
    embeds = _step_embeds(
        ui,
        k.REVIEW_TITLE,
        k.REVIEW_DESCRIPTION,
        _session_header(ui, settings, data) + review_fields(ui, settings, data),
        _footer(ui, settings, data, m.REVIEW),
    )
    # The summary also lists the details the customer entered (as many as fit next to the configured text).
    embeds, _ = _with_answers(
        embeds, _detail_answer_fields(ui, settings, session), budget=[notice] if notice is not None else []
    )
    if notice is not None:
        embeds.append(notice)
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
    totals = data.totals
    navigation.append(
        _button(ui, k.BUTTON_SUBMIT, submit_id(session.id, totals.total_minor, totals.gross_minor), style="success")
    )
    navigation.append(_button(ui, core.BUTTON_CANCEL, cancel_id(session.id), style="danger"))
    return MessageSpec(
        embeds=tuple(embeds),
        rows=rows_of(*credit_buttons) + rows_of(*navigation),
        banner=ui.banner("review-and-summary"),
    )


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
    return MessageSpec(embeds=(embed,), rows=rows, banner=ui.banner("general-response"))


def session_ended(ui: Ui, status: str, *, reference: str | None = None, by_staff: bool = False) -> MessageSpec:
    """The workflow message after the customer cancelled (or the session expired, or staff closed the ticket of the
    unfinished order: ``by_staff``, whose text falls back to the cancelled text); no controls. ``reference``: the
    ticket reference (``ticket_reference``), still shown on the ended panel."""
    if by_staff:
        title_key, description_key = k.STAFF_CLOSED_TITLE, k.STAFF_CLOSED_DESCRIPTION
    elif status == m.SESSION_EXPIRED:
        title_key, description_key = k.EXPIRED_TITLE, k.EXPIRED_DESCRIPTION
    else:
        title_key, description_key = k.CANCELLED_TITLE, k.CANCELLED_DESCRIPTION
    fields = [_field(ui, k.TICKET_REFERENCE_FIELD, reference, inline=True)] if reference else []
    embed = _content_embed(
        ui, title_key, description_key, required=(description_key,), tone="neutral", fields=fields, timestamp=True
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("cancellation"))


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


def submission_modal(ui: Ui, settings: Settings, session: m.Session, total_minor: int, gross_minor: int) -> ModalSpec:
    """The submission form (``checkout.submission_form``, at most 5 fields) opened by the submit button; answers of an
    earlier attempt (e.g. the total changed meanwhile) are filled in again, unless the field's limits changed so the
    old answer no longer fits (Discord would refuse the whole form)."""

    def earlier(item: flow.FormInput) -> str | None:
        answer = session.details.get(item.answer_key) or None
        if answer is None or not item.field.min_length <= len(answer) <= item.field.max_length:
            return None
        return answer

    inputs = tuple(
        TextInputSpec(
            custom_id=item.answer_key,
            label=_text(ui, item.label_key),
            style="paragraph" if item.field.style == "paragraph" else "short",
            description=ui.text(item.description_key),
            placeholder=ui.text(item.placeholder_key),
            default=earlier(item),
            required=item.field.required,
            min_length=item.field.min_length or None,
            max_length=item.field.max_length,
        )
        for item in flow.submission_inputs(settings.checkout)
    )
    if not inputs:
        raise IndexError("no submission form")
    return ModalSpec(
        custom_id=submission_form_id(session.id, total_minor, gross_minor),
        title=_text(ui, k.SUBMISSION_MODAL_TITLE),
        inputs=inputs,
    )


# -- submitted order and status views ------------------------------------------------------------------------------


def order_values(ui: Ui, settings: Settings, order: m.Order) -> dict[str, str | int]:
    return {"order_number": str(order.number), "total": order_money(ui, settings, order.currency)(order.total_minor)}


def _order_line_fields(ui: Ui, view: m.OrderView, fmt: Money) -> list[EmbedField]:
    lines = [line_text(ui, i.name, i.quantity, i.unit_price_minor, i.line_total_minor, fmt=fmt) for i in view.items]
    return _lines_fields(ui, lines)


def _order_total_fields(ui: Ui, view: m.OrderView, fmt: Money) -> list[EmbedField]:
    """Subtotal, fulfillment option and fee, fees, credits, total (as stored in the order)."""
    order = view.order
    fields = [_field(ui, k.CART_SUBTOTAL_FIELD, fmt(order.subtotal_minor), inline=True)]
    label = _option_label(ui, order.fulfillment_option)
    if label:
        item = _optional_field(ui, k.FULFILLMENT_CHOSEN_FIELD, label, inline=True)
        if item is not None:
            fields.append(item)
    if order.fulfillment_fee_minor > 0:
        item = _optional_field(ui, k.FULFILLMENT_FEE_FIELD, fmt(order.fulfillment_fee_minor), inline=True)
        if item is not None:
            fields.append(item)
    fields += _fee_fields(ui, [(f.fee_id, f.amount_minor) for f in view.fees], fmt)
    if order.credits_applied_minor > 0:
        item = _optional_field(ui, k.REVIEW_CREDITS_FIELD, fmt(order.credits_applied_minor), inline=True)
        if item is not None:
            fields.append(item)
    fields.append(_field(ui, k.REVIEW_TOTAL_FIELD, fmt(order.total_minor), inline=True))
    return fields


def order_fields(ui: Ui, settings: Settings, view: m.OrderView) -> list[EmbedField]:
    """Items, subtotal, fulfillment option and fee, fees, credits, total (as stored in the order, in its currency)."""
    fmt = order_money(ui, settings, view.order.currency)
    return _order_line_fields(ui, view, fmt) + _order_total_fields(ui, view, fmt)


def stage_name(ui: Ui, status: str) -> str | None:
    """The customer-facing name of an order status (``checkout.ticket.stage.<status>``); None for a status that has
    no stage name (a status added later without one is left out rather than shown with invented text)."""
    key = k.stage_key(status)
    if not _registered(ui, key):
        return None
    return _text(ui, key)


def timeline_lines(ui: Ui, events: Sequence[m.OrderEvent]) -> list[str]:
    """One configured line per stage the order reached, oldest first. Times are Discord timestamps (each reader sees
    them in their own time zone and language)."""
    lines: list[str] = []
    for event in events:
        stage = stage_name(ui, event.to_status)
        if stage is None:
            continue
        lines.append(
            _text(
                ui,
                k.TICKET_TIMELINE_ENTRY,
                stage=stage,
                time=f"<t:{event.created_at}:f>",
                relative=f"<t:{event.created_at}:R>",
            )
        )
    return lines


def timeline_fields(ui: Ui, events: Sequence[m.OrderEvent]) -> list[EmbedField]:
    """The order timeline as field(s) named ``checkout.ticket.timeline_field`` (none when there is no stage)."""
    lines = timeline_lines(ui, events)
    if not lines:
        return []
    name = _text(ui, k.TICKET_TIMELINE_FIELD)
    return [EmbedField(name=name, value=block) for block in chunk_lines(lines, EMBED_FIELD_VALUE)]


def order_header_fields(ui: Ui, settings: Settings, view: m.OrderView) -> list[EmbedField]:
    """Ticket reference, order number and current status: the first fields of a submitted order's live panel."""
    fields = _reference_field(ui, settings, view.ticket_number)
    fields.append(_field(ui, k.TICKET_ORDER_NUMBER_FIELD, str(view.order.number), inline=True))
    stage = stage_name(ui, view.order.status)
    if stage is not None:
        fields.append(_field(ui, k.TICKET_STATUS_FIELD, stage, inline=True))
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


@dataclass(frozen=True)
class OrderMessages:
    """The workflow message of a submitted order, plus what did not fit into it (posted as separate messages in the
    order channel, each at most once): the payment instructions (only when the configured texts alone fill the
    message) and the order lines."""

    main: MessageSpec
    instructions: tuple[MessageSpec, ...] = ()
    items: tuple[MessageSpec, ...] = ()


def order_messages(
    ui: Ui, settings: Settings, view: m.OrderView, *, instructions: PaymentInstructions | None
) -> OrderMessages:
    """The workflow message of a submitted order: waiting for payment (with the payment instructions and the
    customer's cancel button), or the view configured for its current status. It always shows the ticket reference,
    the order number, the current status and the timeline of the stages reached so far.

    Budgeted for Discord's limits: everything in one message when it fits; otherwise the order lines move to
    separate messages, and if the configured texts alone fill the message, the payment instructions too. The
    payment instructions and the cancel button are always delivered; nothing is cut."""
    order = view.order
    fmt = order_money(ui, settings, order.currency)
    values = order_values(ui, settings, order)
    header_fields = order_header_fields(ui, settings, view)
    line_fields = _order_line_fields(ui, view, fmt)
    total_fields = _order_total_fields(ui, view, fmt) + timeline_fields(ui, view.timeline)
    pending = order.status == m.PENDING
    if pending:
        head = _content_embed(
            ui, k.SUBMITTED_TITLE, k.SUBMITTED_DESCRIPTION, required=(k.SUBMITTED_TITLE,), values=values, tone="success"
        )
    else:
        title_key = k.status_title_key(order.status)
        head = _content_embed(
            ui,
            title_key,
            k.status_description_key(order.status),
            required=(title_key,),
            values=values,
            tone=_STATUS_TONES.get(order.status, "info"),
        )
    tail = [instructions_embed(ui, instructions, values)] if pending and instructions is not None else []
    link = _instruction_link(ui, instructions) if pending else None
    cancel = _button(ui, k.BUTTON_CANCEL_ORDER, order_cancel_id(order.id), style="danger") if pending else None

    def embeds_for(with_lines: bool, with_instructions: bool) -> list[EmbedSpec]:
        fields = header_fields + (line_fields if with_lines else []) + total_fields
        return _spread(head, fields) + (tail if with_instructions else [])

    # Most complete first: everything; the lines separately; the instructions separately; both separately.
    layouts = ((True, True), (False, True), (True, False), (False, False))
    with_lines, with_instructions = next((lo for lo in layouts if _fits(embeds_for(*lo))), layouts[-1])
    return OrderMessages(
        main=MessageSpec(
            embeds=tuple(embeds_for(with_lines, with_instructions)),
            rows=rows_of(link, cancel) if with_instructions else rows_of(cancel),
            banner=ui.banner(status_banner(order.status)),
        ),
        # Continuation messages: only the workflow message (the first part) shows the banner.
        instructions=() if with_instructions else tuple(MessageSpec(embeds=(e,), rows=rows_of(link)) for e in tail),
        items=() if with_lines else tuple(_pack_fields(line_fields, head.color)),
    )


def order_message(
    ui: Ui, settings: Settings, view: m.OrderView, *, instructions: PaymentInstructions | None
) -> MessageSpec:
    """The workflow message of a submitted order (see ``order_messages``)."""
    return order_messages(ui, settings, view, instructions=instructions).main


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
    return MessageSpec(embeds=(embed,), rows=rows, banner=ui.banner("general-response"))


# -- staff order controls -----------------------------------------------------------------------------------------


def staff_buttons(ui: Ui, order: m.Order, *, staff_confirms_payment: bool, credit_refunds: bool) -> list[ButtonSpec]:
    """The status and refund controls for the order's current status. Status buttons carry that status (a click after
    the order changed acts on nothing). ``credit_refunds``: refunds to store credit are possible for this order now.
    The live record (``staff_views.staff_messages``) adds the management buttons after these."""
    buttons: list[ButtonSpec] = []
    status = order.status

    def action(target: str, key: str, style: ButtonStyle) -> ButtonSpec:
        return _button(ui, key, staff_action_id(target, order.id, status), style=style)

    if status == m.PENDING:
        if staff_confirms_payment:
            buttons.append(action(m.PAID, k.STAFF_BUTTON_MARK_PAID, "success"))
        buttons.append(action(m.REJECTED, k.STAFF_BUTTON_REJECT, "danger"))
    if status == m.PAID:
        buttons.append(action(m.PROCESSING, k.STAFF_BUTTON_PROCESSING, "primary"))
    if status in (m.PAID, m.PROCESSING):
        buttons.append(action(m.FULFILLED, k.STAFF_BUTTON_FULFILLED, "success"))
    if order.can_refund:
        buttons.append(_button(ui, k.STAFF_BUTTON_REFUND, refund_id(order.id, MODE_MONEY)))
        if credit_refunds:
            buttons.append(_button(ui, k.STAFF_BUTTON_REFUND_CREDIT, refund_id(order.id, MODE_CREDIT)))
    if order.can_return_credit:
        # Optional while the credits feature is off (an order from before); omitted when not configured.
        returning = ui.button(k.STAFF_BUTTON_RETURN_CREDIT, credit_return_id(order.id))
        if returning is not None:
            buttons.append(returning)
    if status in m.TRANSITIONS[m.CANCELLED]:
        buttons.append(action(m.CANCELLED, k.STAFF_BUTTON_CANCEL, "secondary" if status == m.PENDING else "danger"))
    return buttons


def ordered_answers(settings: Settings, order: m.Order) -> list[tuple[str, str]]:
    """(answer key, answer) in form order (fields no longer configured last, by key)."""
    c = settings.checkout
    known = [i.answer_key for i in flow.form_inputs(c, order.fulfillment_option) + flow.submission_inputs(c)]
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


def _notify_values(ui: Ui, settings: Settings, view: m.OrderView) -> dict[str, str | int]:
    return {**order_values(ui, settings, view.order), "channel_mention": channel_mention(view.channel_id)}


def status_notification(ui: Ui, settings: Settings, view: m.OrderView, status: str) -> MessageSpec:
    embed = _content_embed(
        ui,
        k.notify_title_key(status),
        k.notify_description_key(status),
        required=(k.notify_description_key(status),),
        values=_notify_values(ui, settings, view),
        tone=_STATUS_TONES.get(status, "info"),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner(status_banner(status)))


def session_warning_notification(ui: Ui, *, minutes: int, expires_at: int) -> MessageSpec:
    """Posted in the order ticket before an unfinished order expires for inactivity."""
    kind = k.NOTIFY_SESSION_WARNING
    embed = _content_embed(
        ui,
        k.notify_title_key(kind),
        k.notify_description_key(kind),
        required=(k.notify_description_key(kind),),
        values={"minutes": minutes, "expires": f"<t:{expires_at}:R>"},
        tone="warning",
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("reminder"))


def reminder_notification(
    ui: Ui, settings: Settings, view: m.OrderView, instructions: PaymentInstructions | None
) -> MessageSpec:
    kind = k.NOTIFY_PAYMENT_REMINDER
    values = _notify_values(ui, settings, view)
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
        embeds.append(instructions_embed(ui, instructions, order_values(ui, settings, view.order)))
    link = _instruction_link(ui, instructions)
    return MessageSpec(embeds=tuple(embeds), rows=rows_of(link), banner=ui.banner("reminder"))


def refund_notification(ui: Ui, settings: Settings, view: m.OrderView, amount_minor: int) -> MessageSpec:
    kind = k.NOTIFY_REFUND
    embed = _content_embed(
        ui,
        k.notify_title_key(kind),
        k.notify_description_key(kind),
        required=(k.notify_description_key(kind),),
        values={
            "order_number": str(view.order.number),
            "amount": order_money(ui, settings, view.order.currency)(amount_minor),
            "channel_mention": channel_mention(view.channel_id),
        },
        tone="info",
        timestamp=True,
    )
    # A partial refund updates an order that is not necessarily settled (a full refund sends the 'refunded' status
    # notification instead).
    return MessageSpec(embeds=(embed,), banner=ui.banner("order-status"))


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


def credit_return_modal(op: OperatorText, order: m.Order, *, amount_text: str) -> ModalSpec:
    """Staff only: confirm giving back the store credit applied to an order (all of it), with an optional reason."""
    return ModalSpec(
        custom_id=credit_return_form_id(order.id),
        title=op("checkout.refund.title_return", number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=REFUND_REASON_INPUT,
                label=op("checkout.refund.reason"),
                style="paragraph",
                description=op("checkout.refund.return_hint", amount=amount_text),
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
    """Staff order list (operator text): a status filter, a menu that opens an order's full record, and
    previous/next buttons when there is more than one page."""
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
    if pages > 1:
        embed = replace(embed, footer_text=op("checkout.list.page", page=page + 1, pages=pages))
    filter_options = (
        SelectOptionSpec(label=op("checkout.list.filter_all"), value="all", default=not status),
        *(SelectOptionSpec(label=s, value=s, default=s == status) for s in m.ORDER_STATUSES),
    )
    rows: list[tuple[Component, ...]] = [
        (
            SelectSpec(
                custom_id=list_filter_id(user_id),
                options=filter_options,
                placeholder=op("checkout.list.filter_placeholder"),
            ),
        )
    ]
    if orders:
        view_options = tuple(
            SelectOptionSpec(label=op("checkout.list.view_option", number=o.number, status=o.status), value=str(o.id))
            for o in orders
        )
        rows.append(
            (
                SelectSpec(
                    custom_id=list_view_id(), options=view_options, placeholder=op("checkout.list.view_placeholder")
                ),
            )
        )
    if pages > 1:
        rows.extend(
            rows_of(
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
        )
    return MessageSpec(embeds=(embed,), rows=tuple(rows))
