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

The order ticket reads like a shop receipt: every card starts with one line of key facts (the ticket, the order
number, the status), then the lead text, then sections - a bold heading starting with an icon over short lines:
ITEMS, SUMMARY (the total in bold), SHIP TO on the address step and the review, the payment methods and how to pay
while the order waits for payment, and PROGRESS (the stages reached with their date, then the ones to come). Fields
are left for what people typed (form answers) and the shipment's carrier and tracking number.

Large orders: the order views (``order_messages``) and the staff controls (``staff_views.staff_messages``) budget
Discord's limits (4096 characters per card description, 6000 characters and 10 cards per message, 25 fields per
card). A card whose text is longer than one description continues in a second card of the same colour; when the
order lines do not fit next to the configured text, the main message keeps the summary, the payment instructions and
the buttons, and the lines are sent as separate messages; configured text is never cut.

Banners (``shopbot.ui.banners``): every customer message names its banner picture here. The single live workflow
message of an order ticket shows the banner of its current step and, once submitted, the banner of the order's status
(``status_banner``); continuation messages (order lines and payment instructions that did not fit) show none.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.catalog import views as catalog_views
from shopbot.features.catalog.repo import Product
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discounts, flow, gift_card_store, payment_methods, tips
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys as sk
from shopbot.features.checkout.order_money import Money, order_money
from shopbot.features.checkout.payments import PaymentInstructions, gift_card
from shopbot.features.credits import repo as credits_repo
from shopbot.features.server_keys import channel_name_key
from shopbot.features.tickets import views as tickets_views
from shopbot.ui.card import heading, with_paragraph
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import (
    EMBED_DESCRIPTION,
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
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    from shopbot.features.checkout.handling_repo import Shipment

Rows = tuple[tuple[Component, ...], ...]
OperatorText = Callable[..., str]

ROUTE = k.ROUTE
STAFF_ROUTE = k.STAFF_ROUTE
QUANTITY_INPUT = "quantity"
QUANTITY_MAX_DIGITS = 7
DISCOUNT_INPUT = "code"
# The code field takes a little more than the longest code, so surrounding spaces do not block the form.
DISCOUNT_INPUT_MAX = 40
# The tip menu's Other amount option (the other options' values are amounts in minor units) and the form's field.
TIP_OTHER = "other"
TIP_INPUT = "amount"
TIP_INPUT_MAX = 20
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
    m.PAYMENT_SUBMITTED: "warning",
    m.PAID: "success",
    m.PROCESSING: "info",
    m.SHIPPED: "primary",
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
    m.PENDING: "order-status",  # waiting for the payment, not under review yet
    m.PAYMENT_SUBMITTED: "pending-review",
    m.PAID: "successful-confirmation",
    m.PROCESSING: "submission-and-processing",
    m.SHIPPED: "completed-or-dispatched",
    m.FULFILLED: "successful-confirmation",  # delivered (the dispatched van is for shipped)
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


def extra_id(session_id: int, extra: str) -> str:
    return cid(ROUTE, "extra", session_id, extra)


def address_group_id(session_id: int) -> str:
    return cid(ROUTE, "agroup", session_id)


def address_country_id(session_id: int, menu: int) -> str:
    return cid(ROUTE, "acountry", session_id, menu)


def address_open_id(session_id: int) -> str:
    return cid(ROUTE, "aopen", session_id)


def address_form_id(session_id: int) -> str:
    return cid(ROUTE, "aform", session_id)


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


def discount_open_id(session_id: int) -> str:
    """Add discount code: opens the private code form (review step)."""
    return cid(ROUTE, "dcode", session_id)


def discount_form_id(session_id: int) -> str:
    return cid(ROUTE, "dcform", session_id)


def discount_use_id(session_id: int, code_id: int) -> str:
    """Use coupon: applies a coupon the customer claimed (the handler checks the claim)."""
    return cid(ROUTE, "dcuse", session_id, code_id)


def discount_remove_id(session_id: int) -> str:
    return cid(ROUTE, "dcremove", session_id)


def tip_id(session_id: int) -> str:
    """The review step's tip menu (an amount in minor units, 0 = no tip, or ``TIP_OTHER``)."""
    return cid(ROUTE, "tip", session_id)


def tip_form_id(session_id: int) -> str:
    return cid(ROUTE, "tipform", session_id)


def cancel_yes_id(session_id: int) -> str:
    return cid(ROUTE, "cancel_yes", session_id)


def cancel_no_id(session_id: int) -> str:
    return cid(ROUTE, "cancel_no", session_id)


def order_cancel_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel", order_id)


def gift_card_reveal_id(order_id: int) -> str:
    """Staff (payment roles): show the order's waiting gift card codes privately."""
    return cid(STAFF_ROUTE, "gcreveal", order_id)


def gift_card_problem_id(order_id: int, card_id: int = 0) -> str:
    """Staff (payment roles): refuse the order's waiting cards (``card_id`` 0) or one card, with a reason."""
    return cid(STAFF_ROUTE, "gcproblem", order_id, card_id)


def gift_card_problem_form_id(order_id: int, card_id: int = 0) -> str:
    return cid(STAFF_ROUTE, "gcproblemform", order_id, card_id)


def gift_card_redeemed_id(order_id: int, card_id: int) -> str:
    """Staff (payment roles): one card of the reveal reply was redeemed."""
    return cid(STAFF_ROUTE, "gcok", order_id, card_id)


def payment_form_id(order_id: int) -> str:
    """The button that opens the payment provider's private form (Submit gift card)."""
    return cid(ROUTE, "payform", order_id)


def payment_form_submit_id(order_id: int) -> str:
    return cid(ROUTE, "paysubmit", order_id)


def order_cancel_yes_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel_yes", order_id)


def order_cancel_no_id(order_id: int) -> str:
    return cid(ROUTE, "ocancel_no", order_id)


STAFF_ACTIONS: dict[str, str] = {
    m.PAID: "paid",
    m.REJECTED: "reject",
    m.CANCELLED: "cancel",
    m.PROCESSING: "process",
    m.SHIPPED: "ship",
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


def _button(
    ui: Ui, key: str, custom_id: str, *, style: ButtonStyle = "secondary", disabled: bool = False
) -> ButtonSpec:
    button = ui.button(key, custom_id, style=style, disabled=disabled)
    if button is None:
        raise MissingContent(key)
    return button


def _require(ui: Ui, *keys: str) -> None:
    """Refuse to build a message whose required text is not configured (even a part that is not shown this time)."""
    for key in keys:
        if not ui.content.has(key):
            raise MissingContent(key)


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
    message = ui.error_message(key, **values)
    if message is None:
        raise MissingContent(key)
    return message


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
    """Whether the cards fit one message (10 cards, 6000 characters; each card holds at most 25 fields and a
    description of at most 4096 characters)."""
    return (
        len(embeds) <= EMBEDS_PER_MESSAGE
        and sum(e.text_length() for e in embeds) <= EMBED_TOTAL_CHARS
        and all(len(e.fields) <= EMBED_FIELDS for e in embeds)
        and all(len(e.description or "") <= EMBED_DESCRIPTION for e in embeds)
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
    put in the cart after all (e.g. sold out a moment earlier): its text is the card's last paragraph, in the warning
    colour, so the reply stays one card."""
    started = started_reply(ui, channel_id)  # its banner too: the ticket opened even when the product was not added
    if notice is None:
        return started
    card = replace(started.embeds[0], color=ui.color("warning"))
    return replace(started, embeds=with_paragraph(card, notice.description) + started.embeds[1:])


def limit_reply(ui: Ui, limit: int) -> MessageSpec:
    return _description_message(ui, k.LIMIT_REACHED, "rejected-or-unsuccessful-submission", tone="warning", limit=limit)


def ticket_reference(settings: Settings, number: int) -> str:
    """The ticket's unique reference as shown everywhere (the ticket number, as in the channel name)."""
    return tickets_views.number_text(settings.tickets, number)


def ticket_intro(ui: Ui, settings: Settings, ticket_number: int, user_id: int) -> MessageSpec:
    """The first message of an order ticket: which ticket this is and who can see it (configured text), under the
    ticket's reference line so it is always shown."""
    reference = ticket_reference(settings, ticket_number)
    embed = _content_embed(
        ui,
        k.TICKET_INTRO_TITLE,
        k.TICKET_INTRO_DESCRIPTION,
        required=(k.TICKET_INTRO_DESCRIPTION, k.TICKET_REFERENCE_LINE),
        values={"ticket_number": reference, "user_mention": user_mention(user_id)},
        tone="info",
        timestamp=True,
    )
    embeds = _card_embeds(embed, [reference_line(ui, reference), embed.description])
    return MessageSpec(embeds=tuple(embeds), banner=ui.banner("ticket-created"))


def channel_topic(ui: Ui, user_name: str) -> str | None:
    return ui.text(k.CHANNEL_TOPIC, user_name=user_name)


# -- the card layout of the order ticket -------------------------------------------------------------------------
# Every card of the ticket reads like a receipt: the key facts in one line at the top (ticket, order number,
# status), the lead text, then sections - a bold heading starting with an icon over short lines (items, summary,
# ship to, payment, progress). Fields are left for the customer's own answers and the shipment.


def section(title: str | None, lines: Sequence[str]) -> str | None:
    """A card section: its heading in bold over its lines (None without lines)."""
    if not lines:
        return None
    return "\n".join([heading(title), *lines] if title else lines)


def _card_embeds(head: EmbedSpec, parts: Sequence[str | None]) -> list[EmbedSpec]:
    """``head`` (title, colour, footer, fields) with ``parts`` (paragraphs: the key facts, the lead, the sections) as
    its description. What does not fit into one card's description continues in further cards of the same colour: a
    paragraph moves whole to the next card when it fits there, a longer one is split between its lines. Nothing is
    cut. The cards read as one: the title and author line on the first, ``head``'s fields, footer and time on the
    last (where the banner goes too)."""
    texts: list[str] = []
    current = ""
    for part in (p for p in parts if p):
        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= EMBED_DESCRIPTION:
            current = candidate
            continue
        if len(part) <= EMBED_DESCRIPTION:
            texts.append(current)  # not empty: the part alone fits
            current = part
            continue
        for index, line in enumerate(part.split("\n")):
            separator = ("\n\n" if index == 0 else "\n") if current else ""
            if len(current) + len(separator) + len(line) > EMBED_DESCRIPTION and current:
                texts.append(current)
                current, separator = "", ""
            current = f"{current}{separator}{line}"
    if current:
        texts.append(current)
    if len(texts) <= 1:
        return [replace(head, description=texts[0] if texts else None)]
    first = replace(head, description=texts[0], fields=(), footer_text=None, footer_icon_url=None, timestamp=None)
    middle = [EmbedSpec(color=head.color, description=text) for text in texts[1:-1]]
    last = EmbedSpec(
        color=head.color,
        description=texts[-1],
        fields=head.fields,
        footer_text=head.footer_text,
        footer_icon_url=head.footer_icon_url,
        timestamp=head.timestamp,
    )
    return [first, *middle, last]


def _with_last_fields(embeds: Sequence[EmbedSpec], fields: Sequence[EmbedField]) -> list[EmbedSpec]:
    """``embeds`` with ``fields`` added to the last card (under the text that continues there)."""
    if not fields:
        return list(embeds)
    last = embeds[-1]
    return [*embeds[:-1], replace(last, fields=last.fields + tuple(fields))]


def reference_line(ui: Ui, reference: str) -> str:
    """The ticket's key facts line before an order exists: its reference (``ticket_reference``)."""
    return _text(ui, k.TICKET_REFERENCE_LINE, ticket_number=reference)


def draft_line(ui: Ui, settings: Settings, ticket_number: int | None) -> str | None:
    """The key facts line while the customer puts the order together: the ticket and that it is not placed yet
    (None when, exceptionally, the ticket's number is not known)."""
    _require(ui, k.TICKET_DRAFT_LINE)  # required even when (exceptionally) no number is known
    status = _text(ui, k.TICKET_STATUS_IN_PROGRESS)
    if ticket_number is None:
        return None
    return _text(ui, k.TICKET_DRAFT_LINE, ticket_number=ticket_reference(settings, ticket_number), status=status)


def order_line(ui: Ui, settings: Settings, view: m.OrderView) -> str | None:
    """The key facts line of a placed order: the ticket, the order number and the current stage (None when the
    ticket's number or the stage's name is not known)."""
    _require(ui, k.TICKET_ORDER_LINE)  # required even when it cannot be shown
    stage = stage_name(ui, view.order.status)
    if view.ticket_number is None or stage is None:
        return None
    return _text(
        ui,
        k.TICKET_ORDER_LINE,
        ticket_number=ticket_reference(settings, view.ticket_number),
        order_number=str(view.order.number),
        status=stage,
    )


# -- items and amounts ----------------------------------------------------------------------------------------------


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


def items_section(ui: Ui, lines: Sequence[str]) -> str:
    """The items section: its heading over one line per item (the empty-cart text while there is none)."""
    text = section(_text(ui, k.ITEMS_HEADING), list(lines) or [_text(ui, k.CART_EMPTY)])
    assert text is not None
    return text


def _cart_lines(ui: Ui, data: m.WorkflowData) -> list[str]:
    return [
        line_text(ui, line.product.name, line.quantity, line.product.price_minor, line.line_total_minor)
        for line in data.lines
    ]


def _option_label(ui: Ui, option_id: str | None) -> str | None:
    if option_id is None:
        return None
    return _optional(ui, k.option_key(option_id, "label"))


def _amount_line(ui: Ui, label: str, amount: str) -> str:
    return _text(ui, k.SUMMARY_LINE, label=label, amount=amount)


def _delivery_line(ui: Ui, label: str | None, fee: str | None) -> str | None:
    """The delivery option with its fee ("Free" without one; the name alone when that text is not set). An option
    whose name is not configured any more shows its fee as checkout.fulfillment_step.fee_field."""
    if fee is None:
        free = _optional(ui, k.SUMMARY_FREE)
        return (_amount_line(ui, label, free) if free else label) if label else None
    name = label or _optional(ui, k.FULFILLMENT_FEE_FIELD)
    return _amount_line(ui, name, fee) if name else None


def discount_line(ui: Ui, kind: str | None, code: str, amount: str) -> str:
    """The discount in a summary, right under the subtotal: "Discount (SAVE10) · -$2.00", or for buy one get one free
    "Buy one, get one free (BOGO) · -$5.99". While those texts are not set (discount codes switched off since the
    order was placed) the code is shown as the line's label, so the amounts still add up."""
    key = k.SUMMARY_BOGO_LINE if kind == discounts.BOGO else k.SUMMARY_DISCOUNT_LINE
    text = _optional(ui, key, code=escape(code), amount=amount)
    return text or _amount_line(ui, escape(code), f"-{amount}")


def discount_reason(ui: Ui, reason: str, code: str, minimum: str = "") -> str:
    """Why a discount code does not apply, in the customer's words (``content_keys.discount_error_key``): the private
    reply, and the note in the summary of an order whose code stopped applying."""
    values: dict[str, str | int] = {"code": escape(code)}
    if reason == discounts.MINIMUM:
        values["minimum"] = minimum
    return _text(ui, k.discount_error_key(reason), **values)


def _discount_row(ui: Ui, discount: m.AppliedDiscount, write: Money) -> str | None:
    """The applied code's line in the summary, or (a code that stopped applying) a one-line "not applied" in its
    place; the note under the total says why (``_discount_note``)."""
    if discount.applies:
        return discount_line(ui, discount.kind, discount.code, write(discount.amount_minor))
    return _optional(ui, k.SUMMARY_DISCOUNT_OFF_LINE, code=escape(discount.code))


def _discount_note(ui: Ui, discount: m.AppliedDiscount, write: Money) -> str | None:
    """Under the total, for a code that stopped applying: why, and that Remove discount takes it off."""
    if discount.applies:
        return None
    reason = discount_reason(ui, discount.reason or discounts.UNKNOWN, discount.code, write(discount.minimum_minor))
    return _optional(ui, k.SUMMARY_DISCOUNT_NOTE, reason=reason)


def tip_line(ui: Ui, amount: str) -> str | None:
    """The tip for our team in a summary ("Tip for our team · $5.00"), after the extras and fees. Its text is required
    also while tips are off (orders placed with a tip keep showing it); None only while checkout is not ready."""
    return _optional(ui, k.SUMMARY_TIP_LINE, amount=amount)


def _credit_line(ui: Ui, amount: str) -> str | None:
    """The store credit applied (taken off the total); None when its texts are not set (credits switched off since
    the order was placed)."""
    label = _optional(ui, k.REVIEW_CREDITS_FIELD)
    return _optional(ui, k.SUMMARY_CREDIT_LINE, label=label, amount=amount) if label else None


def summary_section(ui: Ui, subtotal: str, rows: Sequence[str], total: str, *, final: bool) -> str:
    """The summary: the subtotal, ``rows`` (delivery, extras, fees, store credit) and the total in bold. With no
    rows the total is the subtotal, shown once: as the total when it is what the customer pays (``final``), as the
    subtotal while delivery is not chosen yet."""
    total_label = _text(ui, k.REVIEW_TOTAL_FIELD)
    subtotal_label = _text(ui, k.CART_SUBTOTAL_FIELD)
    if rows:
        lines = [_amount_line(ui, subtotal_label, subtotal), *rows]
        lines.append(_text(ui, k.SUMMARY_TOTAL_LINE, label=total_label, amount=total))
    else:
        label = total_label if final else subtotal_label
        lines = [_text(ui, k.SUMMARY_TOTAL_LINE, label=label, amount=total)]
    text = section(_text(ui, k.SUMMARY_HEADING), lines)
    assert text is not None
    return text


def _workflow_summary(
    ui: Ui, settings: Settings, data: m.WorkflowData, *, final: bool = False, available: bool = False
) -> str | None:
    """What the order costs so far (None while the cart is empty): the subtotal, the discount code applied (or that
    it is not applied, with the reason under the total), the chosen delivery option and extras with their prices, fees,
    the tip for our team, the store credit applied and the total. ``available`` (review step): also the store credit
    the customer could still apply."""
    if not data.lines:
        return None
    totals = data.totals
    c = settings.checkout
    rows: list[str] = []
    discount = _discount_row(ui, data.discount, lambda minor: money(ui, minor)) if data.discount else None
    if discount:
        rows.append(discount)
    chosen = c.fulfillment(data.session.fulfillment_option) if c.fulfillment_options else None
    if chosen is not None:
        fee = money(ui, totals.fulfillment_fee_minor) if totals.fulfillment_fee_minor > 0 else None
        delivery = _delivery_line(ui, _option_label(ui, chosen.id), fee)
        if delivery:
            rows.append(delivery)
    for fee_item in totals.fees:
        label = _optional(ui, k.fee_label_key(fee_item.fee_id))
        if label:
            rows.append(_amount_line(ui, label, money(ui, fee_item.amount_minor)))
    tip = tip_line(ui, money(ui, totals.tip_minor)) if totals.tip_minor > 0 else None
    if tip:
        rows.append(tip)
    if totals.credits_applied_minor > 0:
        credit = _credit_line(ui, money(ui, totals.credits_applied_minor))
        if credit:
            rows.append(credit)
    text = summary_section(ui, money(ui, totals.subtotal_minor), rows, money(ui, totals.total_minor), final=final)
    if available and totals.credits_applied_minor == 0 and data.credits_enabled and data.credits_available_minor > 0:
        label = _optional(ui, k.REVIEW_CREDITS_AVAILABLE_FIELD)
        if label:
            text += "\n" + _amount_line(ui, label, money(ui, data.credits_available_minor))
    note = _discount_note(ui, data.discount, lambda minor: money(ui, minor)) if data.discount else None
    if note:
        text += "\n" + note
    return text


def _order_summary(ui: Ui, view: m.OrderView, fmt: Money) -> str:
    """The placed order's amounts as stored in the order (in its currency): subtotal, the discount (when a code took
    something off), delivery, extras and fees, the tip for our team, store credit applied, total."""
    order = view.order
    rows: list[str] = []
    if order.discount_minor > 0 and order.discount_code:
        rows.append(discount_line(ui, order.discount_kind, order.discount_code, fmt(order.discount_minor)))
    fee = fmt(order.fulfillment_fee_minor) if order.fulfillment_fee_minor > 0 else None
    delivery = _delivery_line(ui, _option_label(ui, order.fulfillment_option), fee)
    if delivery:
        rows.append(delivery)
    for fee_item in view.fees:
        label = _optional(ui, k.fee_label_key(fee_item.fee_id))
        if label:  # a fee whose name is no longer configured is left out (the total includes it)
            rows.append(_amount_line(ui, label, fmt(fee_item.amount_minor)))
    tip = tip_line(ui, fmt(order.tip_minor)) if order.tip_minor > 0 else None
    if tip:
        rows.append(tip)
    if order.credits_applied_minor > 0:
        credit = _credit_line(ui, fmt(order.credits_applied_minor))
        if credit:
            rows.append(credit)
    return summary_section(ui, fmt(order.subtotal_minor), rows, fmt(order.total_minor), final=True)


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
    current, total = step_number(settings, data.session.fulfillment_option, step)
    if current:
        parts.append(ui.progress(current, total))
    return "\n".join(p for p in parts if p) or None


def step_number(settings: Settings, option: str | None, step: str | None) -> tuple[int, int]:
    """(step, number of steps) as the customer sees them: choosing items and the cart are one step, the payment after
    the review is the last one (``step`` None). (0, total) for a step that is not shown."""
    shown = [s for s in flow.steps_for(settings.checkout, option) if s != m.CART]
    total = len(shown) + 1
    if step is None:
        return total, total
    step = m.BROWSE if step == m.CART else step
    return (shown.index(step) + 1 if step in shown else 0), total


def _step_card(
    ui: Ui,
    settings: Settings,
    data: m.WorkflowData,
    step: str,
    title_key: str,
    description_key: str,
    sections: Sequence[str | None],
    *,
    notice: str | None = None,
) -> list[EmbedSpec]:
    """A step of the live panel: the key facts line, the step's text and its notice (an empty catalog, a minimum not
    met, a changed total), then ``sections``. Very long text continues in a second card (see ``_card_embeds``)."""
    head = ui.embed(title=_text(ui, title_key), footer_extra=_footer(ui, settings, data, step))
    parts = [draft_line(ui, settings, data.ticket_number), ui.text(description_key), notice, *sections]
    return _card_embeds(head, parts)


def _fits_one(embeds: Sequence[EmbedSpec]) -> bool:
    """One card within Discord's limits (a payment reminder keeps the instructions in its card only when they fit)."""
    return len(embeds) == 1 and _fits(embeds)


def _navigation(ui: Ui, settings: Settings, data: m.WorkflowData, step: str, *, can_continue: bool) -> list[ButtonSpec]:
    session = data.session
    option = session.fulfillment_option
    buttons: list[ButtonSpec] = []
    previous = flow.previous_step(settings.checkout, option, step)
    if previous is not None:
        buttons.append(_button(ui, core.BUTTON_BACK, go_id(session.id, previous)))
    following = flow.next_step(settings.checkout, option, step)
    if following is not None:
        # The main action only once it can be pressed: until then it is grey, so the step's own action (choose,
        # enter the address) is the one coloured button of its row.
        style: ButtonStyle = "primary" if can_continue else "secondary"
        buttons.append(
            _button(ui, core.BUTTON_CONTINUE, go_id(session.id, following), style=style, disabled=not can_continue)
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


def _cart_sections(ui: Ui, settings: Settings, data: m.WorkflowData) -> list[str | None]:
    """The items and what they cost so far."""
    return [items_section(ui, _cart_lines(ui, data)), _workflow_summary(ui, settings, data)]


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
    return _with_last_fields(embeds, taken), len(taken) == len(answers)


def _browse(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    browse = data.browse
    empty = browse is None or browse.total == 0 or not browse.products
    embeds = _step_card(
        ui,
        settings,
        data,
        m.BROWSE,
        k.BROWSE_TITLE,
        k.BROWSE_DESCRIPTION,
        _cart_sections(ui, settings, data),
        notice=_text(ui, k.BROWSE_EMPTY_CATALOG) if empty else None,
    )
    rows: list[tuple[Component, ...]] = []
    if browse is not None and not empty:
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
    met = minimum_met(settings, data)
    notice = None
    if data.lines and not met:
        notice = _text(ui, k.CART_MINIMUM_NOT_MET, minimum=money(ui, settings.checkout.min_order_total_minor))
    embeds = _step_card(
        ui, settings, data, m.CART, k.CART_TITLE, k.CART_DESCRIPTION, _cart_sections(ui, settings, data), notice=notice
    )
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
    extras = extras_text(ui, settings, session) if c.extras else None
    items, summary = _cart_sections(ui, settings, data)

    def card(with_items: bool) -> list[EmbedSpec]:
        sections = [extras, items if with_items else None, summary]
        return _step_card(ui, settings, data, m.FULFILLMENT, k.FULFILLMENT_TITLE, k.FULFILLMENT_DESCRIPTION, sections)

    # What was selected so far: the items, or only the summary when the items do not fit with the rest (a long card
    # continues in a second one of the same colour, as on the review step).
    embeds = card(True)
    if not _fits(embeds):
        embeds = card(False)
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
    rows.extend(rows_of(*extra_buttons(ui, settings, session)))
    rows.extend(rows_of(*_navigation(ui, settings, data, m.FULFILLMENT, can_continue=chosen is not None)))
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows), banner=ui.banner("details-and-options"))


def extras_text(ui: Ui, settings: Settings, session: m.Session) -> str:
    """The extras section of the delivery step: its heading, then each extra with its price and whether it is added."""
    added = {extra.id for extra in flow.chosen_extras(settings.checkout, session.details)}
    lines = [heading(_text(ui, k.EXTRAS_HEADING))]
    for extra in settings.checkout.extras:
        status = _text(ui, k.EXTRA_STATUS_ON if extra.id in added else k.EXTRA_STATUS_OFF)
        lines.append(
            _text(
                ui,
                k.EXTRA_LINE,
                status=status,
                label=_text(ui, k.extra_key(extra.id, "label")),
                price=money(ui, extra.amount_minor),
                description=_optional(ui, k.extra_key(extra.id, "description")) or "",
            ).strip()
        )
    return "\n".join(lines)


def extra_buttons(ui: Ui, settings: Settings, session: m.Session) -> list[ButtonSpec]:
    """One toggle per extra: green while added (pressing removes it), grey otherwise."""
    added = {extra.id for extra in flow.chosen_extras(settings.checkout, session.details)}
    buttons: list[ButtonSpec] = []
    for extra in settings.checkout.extras:
        on = extra.id in added
        label = _text(
            ui,
            k.EXTRA_BUTTON_ON if on else k.EXTRA_BUTTON_OFF,
            label=_text(ui, k.extra_key(extra.id, "label")),
            price=money(ui, extra.amount_minor),
        )
        buttons.append(
            ButtonSpec(label=label, custom_id=extra_id(session.id, extra.id), style="success" if on else "secondary")
        )
    return buttons


def address_text(ui: Ui, settings: Settings, details: Mapping[str, str]) -> str:
    """The address section of the details step: the chosen country and whether the address was entered (never the
    street: the channel only shows the name and city)."""
    country = details.get(flow.ADDRESS_COUNTRY)
    country_name = settings.shipping.country_name(country) if country else _text(ui, k.ADDRESS_COUNTRY_NONE)
    lines = [heading(_text(ui, k.ADDRESS_HEADING)), _text(ui, k.ADDRESS_COUNTRY_LINE, country=escape(country_name))]
    if country and all(details.get(f.answer_key) for f in flow.ADDRESS_FIELDS if f.required):
        lines.append(_text(ui, k.ADDRESS_ADDED_LINE, **_address_values(details)))
    else:
        lines.append(_text(ui, k.ADDRESS_MISSING_LINE))
    return "\n".join(lines)


def _address_values(details: Mapping[str, str]) -> dict[str, str | int]:
    return {f.id: escape(details.get(f.answer_key, "")) for f in flow.ADDRESS_FIELDS if f.id != "street"}


def ship_to(ui: Ui, settings: Settings, details: Mapping[str, str]) -> str | None:
    """The destination as shown on the review, the order and the staff record (no street), or None without one."""
    country = details.get(flow.ADDRESS_COUNTRY)
    if not country:
        return None
    return _text(
        ui, k.ADDRESS_SHIP_TO, **_address_values(details), country=escape(settings.shipping.country_name(country))
    )


def address_rows(ui: Ui, settings: Settings, session: m.Session) -> list[tuple[Component, ...]]:
    """The region menu, then the chosen region's country menus (at most 25 countries each)."""
    shipping = settings.shipping
    group_id = session.details.get(flow.ADDRESS_GROUP)
    country = session.details.get(flow.ADDRESS_COUNTRY)
    if group_id is None and country is not None:
        group_id = next((g.id for g in shipping.groups if country in g.countries), None)
    regions = SelectSpec(
        custom_id=address_group_id(session.id),
        options=tuple(
            SelectOptionSpec(label=_text(ui, k.address_region_key(g.id)), value=g.id, default=g.id == group_id)
            for g in shipping.groups
        ),
        placeholder=_text(ui, k.ADDRESS_REGION_PLACEHOLDER),
    )
    rows: list[tuple[Component, ...]] = [(regions,)]
    for index, codes in enumerate(flow.country_menus(shipping, group_id)):
        names = [shipping.country_name(code) for code in codes]
        menu = SelectSpec(
            custom_id=address_country_id(session.id, index),
            options=tuple(
                SelectOptionSpec(label=name, value=code, default=code == country)
                for code, name in zip(codes, names, strict=True)
            ),
            placeholder=_text(ui, k.ADDRESS_COUNTRY_PLACEHOLDER, first=names[0], last=names[-1]),
        )
        rows.append((menu,))
    return rows


def address_modal(ui: Ui, settings: Settings, session: m.Session) -> ModalSpec:
    """The private address form; earlier answers are filled in again (e.g. after an area we do not ship to)."""
    country = session.details.get(flow.ADDRESS_COUNTRY)
    inputs = tuple(
        TextInputSpec(
            custom_id=field.answer_key,
            label=_text(ui, k.address_field_key(field.id, "label")),
            style="paragraph" if field.style == "paragraph" else "short",
            placeholder=ui.text(k.address_field_key(field.id, "placeholder")),
            default=session.details.get(field.answer_key) or None,
            required=flow.address_field_required(field, settings.shipping, country),
            min_length=field.min_length or None,
            max_length=field.max_length,
        )
        for field in flow.ADDRESS_FIELDS
    )
    return ModalSpec(custom_id=address_form_id(session.id), title=_text(ui, k.ADDRESS_MODAL_TITLE), inputs=inputs)


def _details(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    session = data.session
    if settings.checkout.ask_address:
        return _address_step(ui, settings, data)
    inputs = flow.form_inputs(settings.checkout, session.fulfillment_option)
    answers = _detail_answer_fields(ui, settings, session)
    items, summary = _cart_sections(ui, settings, data)

    def card(with_items: bool) -> list[EmbedSpec]:
        sections = [items if with_items else None, summary]
        return _step_card(ui, settings, data, m.DETAILS, k.DETAILS_TITLE, k.DETAILS_DESCRIPTION, sections)

    # The selections so far and the answers entered on this step. When not everything fits, the items give way to
    # the summary first; answers that still do not fit are not shown (they are stored and shown to staff).
    embeds, complete = _with_answers(card(True), answers)
    if not complete or not _fits(embeds):
        embeds, _ = _with_answers(card(False), answers)
    parts = flow.form_parts(inputs)
    keys = (k.BUTTON_ENTER_DETAILS, k.BUTTON_ENTER_MORE_DETAILS)
    form_buttons = [
        _button(ui, keys[min(index, 1)], details_id(session.id, index), style="primary") for index in range(len(parts))
    ]
    complete = flow.details_complete(inputs, session.details)
    rows = rows_of(*form_buttons) + rows_of(*_navigation(ui, settings, data, m.DETAILS, can_continue=complete))
    return MessageSpec(embeds=tuple(embeds), rows=rows, banner=ui.banner("details-and-options"))


def _address_step(ui: Ui, settings: Settings, data: m.WorkflowData) -> MessageSpec:
    """The shipping address step: the address section, the items so far, the region and country menus, the address
    form button (plus the details form buttons, if any) and the navigation."""
    session = data.session
    address = address_text(ui, settings, session.details)
    items, summary = _cart_sections(ui, settings, data)

    def card(with_items: bool) -> list[EmbedSpec]:
        sections = [address, items if with_items else None, summary]
        return _step_card(ui, settings, data, m.DETAILS, k.DETAILS_TITLE, k.DETAILS_DESCRIPTION, sections)

    embeds = card(True)
    if not _fits(embeds):
        embeds = card(False)
    entered = bool(session.details.get(flow.ADDRESS_COUNTRY)) and any(
        session.details.get(f.answer_key) for f in flow.ADDRESS_FIELDS
    )
    open_form = _button(
        ui,
        k.ADDRESS_BUTTON_CHANGE if entered else k.ADDRESS_BUTTON,
        address_open_id(session.id),
        style="secondary" if entered else "primary",
        disabled=not session.details.get(flow.ADDRESS_COUNTRY),
    )
    inputs = flow.form_inputs(settings.checkout, None)
    keys = (k.BUTTON_ENTER_DETAILS, k.BUTTON_ENTER_MORE_DETAILS)
    form_buttons = [
        _button(ui, keys[min(index, 1)], details_id(session.id, index)) for index in range(len(flow.form_parts(inputs)))
    ]
    complete = flow.details_step_complete(settings.checkout, settings.shipping, None, session.details)
    navigation = _navigation(ui, settings, data, m.DETAILS, can_continue=complete)
    rows = (*address_rows(ui, settings, session), *rows_of(open_form, *form_buttons, *navigation))
    return MessageSpec(embeds=tuple(embeds), rows=tuple(rows), banner=ui.banner("details-and-options"))


def review_sections(ui: Ui, settings: Settings, data: m.WorkflowData, *, with_items: bool = True) -> list[str | None]:
    """The review: the items, the summary with the total to pay (and the store credit that could still be applied),
    where the order goes (name, city and country; never the street) and, last, just above the menus, the tip note while
    tips are on."""
    destination = ship_to(ui, settings, data.session.details) if settings.checkout.ask_address else None
    return [
        items_section(ui, _cart_lines(ui, data)) if with_items else None,
        _workflow_summary(ui, settings, data, final=True, available=True),
        section(_text(ui, k.ADDRESS_HEADING), [destination]) if destination else None,
        tip_note(ui, settings, data),
    ]


def tip_note(ui: Ui, settings: Settings, data: m.WorkflowData) -> str | None:
    """The last line of the review step while tips are on: who a tip is for (100% goes to our team), or thanks once a
    tip is chosen."""
    if not settings.checkout.tips.enabled or not data.lines:
        return None
    return _text(ui, k.TIP_NOTE_THANKS if data.totals.tip_minor > 0 else k.TIP_NOTE)


def tip_menu(ui: Ui, settings: Settings, data: m.WorkflowData) -> SelectSpec | None:
    """The review step's tip menu while tips are on (None otherwise): No tip, the listed amounts, the customer's own
    amount while one is chosen, and Other amount (``allow_custom``; it opens the private amount form). The tip that
    counts is shown as selected; with no tip the menu shows its placeholder. Picking the selected option again sends
    nothing in Discord, so the customer's own amount is its own option and Other amount is never the selected one."""
    c = settings.checkout
    if not c.tips.enabled:
        return None
    current = data.totals.tip_minor
    options = [SelectOptionSpec(label=_text(ui, k.TIP_OPTION_NONE), value="0")]
    for amount in c.tips.amounts:
        label = _text(ui, k.TIP_OPTION_AMOUNT, amount=money(ui, amount))
        options.append(SelectOptionSpec(label=label, value=str(amount), default=amount == current))
    if current > 0 and current not in c.tips.amounts:
        label = _text(ui, k.TIP_OPTION_AMOUNT, amount=money(ui, current))
        description = _optional(ui, k.TIP_OPTION_YOURS)
        options.append(SelectOptionSpec(label=label, value=str(current), description=description, default=True))
    if c.tips.allow_custom:
        description = _optional(ui, k.TIP_OPTION_CUSTOM_DESCRIPTION, **_tip_limits(ui, settings))
        if description is not None and len(description) > OPTION_DESCRIPTION:
            description = None  # optional text that does not fit is omitted, never cut
        options.append(SelectOptionSpec(label=_text(ui, k.TIP_OPTION_CUSTOM), value=TIP_OTHER, description=description))
    return SelectSpec(custom_id=tip_id(data.session.id), options=tuple(options), placeholder=ui.text(k.TIP_PLACEHOLDER))


def _tip_limits(ui: Ui, settings: Settings) -> dict[str, str | int]:
    """The smallest and largest amount a customer may type as a tip ({minimum}, {maximum})."""
    c = settings.checkout
    return {"minimum": money(ui, tips.custom_minimum(c)), "maximum": money(ui, c.tips.max_minor)}


def tip_modal(ui: Ui, settings: Settings, session_id: int) -> ModalSpec:
    """The private form of Other amount: one amount field (checked server-side, ``tips.parse``)."""
    return ModalSpec(
        custom_id=tip_form_id(session_id),
        title=_text(ui, k.TIP_MODAL_TITLE),
        inputs=(
            TextInputSpec(
                custom_id=TIP_INPUT,
                label=_text(ui, k.TIP_INPUT_LABEL),
                placeholder=_optional(ui, k.TIP_INPUT_PLACEHOLDER, **_tip_limits(ui, settings)),
                required=True,
                max_length=TIP_INPUT_MAX,
            ),
        ),
    )


def tip_error(ui: Ui, settings: Settings, reason: str) -> tuple[str, dict[str, str | int]]:
    """The customer text (key and values) for a typed tip that cannot be used (``tips.INVALID`` / ``tips.TOO_HIGH``)."""
    limits = _tip_limits(ui, settings)
    if reason == tips.TOO_HIGH:
        return k.TIP_ERROR_TOO_HIGH, {"maximum": limits["maximum"]}
    return k.TIP_ERROR_INVALID, limits


def _review(ui: Ui, settings: Settings, data: m.WorkflowData, notice: str | None) -> MessageSpec:
    session = data.session
    answers = _detail_answer_fields(ui, settings, session)

    def card(with_items: bool) -> list[EmbedSpec]:
        sections = review_sections(ui, settings, data, with_items=with_items)
        return _step_card(ui, settings, data, m.REVIEW, k.REVIEW_TITLE, k.REVIEW_DESCRIPTION, sections, notice=notice)

    # The summary also lists the details the customer entered (as many as fit next to the configured text). The
    # items give way to the summary only when they cannot be shown in one message.
    embeds, _ = _with_answers(card(True), answers)
    if not _fits(embeds):
        embeds, _ = _with_answers(card(False), answers)
    credit_buttons: list[ButtonSpec] = []
    if data.credits_enabled:
        if data.totals.credits_applied_minor > 0:
            credit_buttons.append(_button(ui, k.BUTTON_REMOVE_CREDITS, credits_id(session.id, False)))
        elif data.credits_available_minor > 0:
            credit_buttons.append(_button(ui, k.BUTTON_APPLY_CREDITS, credits_id(session.id, True), style="success"))
    discount_buttons = review_discount_buttons(ui, settings, data)
    tip = tip_menu(ui, settings, data)
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
        # The store credit and discount buttons that take money off (one row, at most 4), the tip menu (its own row),
        # then Back, Confirm and Cancel.
        rows=rows_of(*credit_buttons, *discount_buttons, tip) + rows_of(*navigation),
        banner=ui.banner("review-and-summary"),
    )


def review_discount_buttons(ui: Ui, settings: Settings, data: m.WorkflowData) -> list[ButtonSpec]:
    """The review step's discount buttons (secondary, in the store credit's row): Remove discount while a code is
    applied (also one that stopped applying); otherwise one Use coupon button per claimed coupon (newest first) and
    Add discount code while the shop has a code that can be used. None while discount codes are off."""
    if not settings.checkout.discount_codes:
        return []
    session = data.session
    if session.discount_code_id is not None:
        return [_button(ui, k.DISCOUNT_BUTTON_REMOVE, discount_remove_id(session.id))]
    buttons = [
        _button_with(ui, k.DISCOUNT_BUTTON_USE, discount_use_id(session.id, code_id), code=code)
        for code_id, code in data.offer.coupons
    ]
    if data.offer.codes:
        buttons.append(_button(ui, k.DISCOUNT_BUTTON_ADD, discount_open_id(session.id)))
    return buttons


def _button_with(ui: Ui, key: str, custom_id: str, *, code: str) -> ButtonSpec:
    button = ui.button(key, custom_id, style="secondary", values={"code": code})
    if button is None:
        raise MissingContent(key)
    return button


def discount_modal(ui: Ui, session_id: int) -> ModalSpec:
    """The private discount code form of the review step."""
    return ModalSpec(
        custom_id=discount_form_id(session_id),
        title=_text(ui, k.DISCOUNT_MODAL_TITLE),
        inputs=(
            TextInputSpec(
                custom_id=DISCOUNT_INPUT,
                label=_text(ui, k.DISCOUNT_INPUT_LABEL),
                placeholder=ui.text(k.DISCOUNT_INPUT_PLACEHOLDER),
                required=True,
                min_length=discounts.CODE_MIN,
                max_length=DISCOUNT_INPUT_MAX,
            ),
        ),
    )


def price_changed_notice(ui: Ui, total_minor: int) -> str:
    return _text(ui, k.REVIEW_PRICE_CHANGED, total=money(ui, total_minor))


def workflow_message(ui: Ui, settings: Settings, data: m.WorkflowData, *, notice: str | None = None) -> MessageSpec:
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
        _button(ui, k.BUTTON_CANCEL_YES, cancel_yes_id(session_id), style="danger"),
        _button(ui, k.BUTTON_KEEP_ORDER, cancel_no_id(session_id)),
    )
    return MessageSpec(embeds=(embed,), rows=rows, banner=ui.banner("general-response"))


def session_ended(ui: Ui, status: str, *, reference: str | None = None, by_staff: bool = False) -> MessageSpec:
    """The workflow message after the customer cancelled (or the session expired, or staff closed the ticket of the
    unfinished order: ``by_staff``, whose text falls back to the cancelled text); no controls. ``reference``: the
    ticket reference (``ticket_reference``), still shown in the key facts line of the ended panel."""
    if by_staff:
        title_key, description_key = k.STAFF_CLOSED_TITLE, k.STAFF_CLOSED_DESCRIPTION
    elif status == m.SESSION_EXPIRED:
        title_key, description_key = k.EXPIRED_TITLE, k.EXPIRED_DESCRIPTION
    else:
        title_key, description_key = k.CANCELLED_TITLE, k.CANCELLED_DESCRIPTION
    embed = _content_embed(ui, title_key, description_key, required=(description_key,), tone="neutral", timestamp=True)
    facts = reference_line(ui, reference) if reference else None
    return MessageSpec(embeds=tuple(_card_embeds(embed, [facts, embed.description])), banner=ui.banner("cancellation"))


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


def purchase_values(ui: Ui, settings: Settings, links: Mapping[str, str] | None) -> dict[str, str | int]:
    """The value of ``{channel_purchase}`` (``content_keys.purchase_placeholder``): the place-order channel as a
    clickable link (``links``: the channel links the bot knows, ``server_keys.channel_mentions``), or its name while
    it is not known (before /setup created it)."""
    names = k.purchase_placeholder(settings)
    if not names:
        return {}
    link = (links or {}).get(names[0])
    if not link:
        name = _optional(ui, channel_name_key(settings.checkout.panel_channel))
        link = f"#{name}" if name else ""
    return {names[0]: link}


def stage_name(ui: Ui, status: str) -> str | None:
    """The customer-facing name of an order status (``checkout.ticket.stage.<status>``); None for a status that has
    no stage name (a status added later without one is left out rather than shown with invented text)."""
    key = k.stage_key(status)
    if not _registered(ui, key):
        return None
    return _text(ui, key)


# The stages of an order that goes well, in order, and the stages that end an order early. Payment submitted and
# processing happen only on some orders: they are listed once reached, never as a stage still to come.
_PATH = (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING, m.SHIPPED, m.FULFILLED)
_STOPS = (m.CANCELLED, m.REJECTED, m.EXPIRED, m.REFUNDED)


def _upcoming(order: m.Order) -> tuple[str, ...]:
    """The stages still to come for an open order: confirmed payment, shipped (an order with a shipping address)
    and delivered."""
    ships = flow.ADDRESS_COUNTRY in order.details
    position = _PATH.index(order.status)
    expected = (m.PAID, m.SHIPPED, m.FULFILLED) if ships else (m.PAID, m.FULFILLED)
    return tuple(s for s in expected if _PATH.index(s) > position)


def _reached(order: m.Order, events: Sequence[m.OrderEvent]) -> tuple[dict[str, int], list[tuple[str, int]]]:
    """(the stages of ``_PATH`` the order is through, with when it got there; the stages that ended it, in order).
    A stage that was left backwards (a refused gift card puts the order back to waiting for payment) no longer
    counts; the order was placed when it first waited for payment."""
    reached: dict[str, int] = {}
    stops: list[tuple[str, int]] = []
    for event in events:
        status = event.to_status
        if status in _STOPS:
            stops.append((status, event.created_at))
        elif status in _PATH:
            position = _PATH.index(status)
            first = reached.get(m.PENDING) if status == m.PENDING else None
            reached = {s: t for s, t in reached.items() if _PATH.index(s) < position}
            reached[status] = first or event.created_at
    if order.status in _PATH and order.status not in reached:  # a history without its latest change
        position = _PATH.index(order.status)
        reached = {s: t for s, t in reached.items() if _PATH.index(s) < position}
        reached[order.status] = order.created_at if order.status == m.PENDING else order.updated_at
    elif order.status in _STOPS and not stops:
        stops.append((order.status, order.updated_at))
    return reached, stops


def progress_lines(ui: Ui, order: m.Order, events: Sequence[m.OrderEvent]) -> list[str]:
    """The progress tracker of a placed order: every stage it went through with the date (the current one in bold),
    then the stages still to come; an order that was cancelled, rejected, expired or refunded ends with that stage.
    Dates are Discord timestamps (each reader sees them in their own time zone and language)."""
    reached, stops = _reached(order, events)

    def name(status: str) -> str | None:
        return _text(ui, k.PROGRESS_START) if status == m.PENDING else stage_name(ui, status)

    def when(at: int) -> dict[str, str | int]:
        return {"date": f"<t:{at}:d>", "time": f"<t:{at}:f>", "relative": f"<t:{at}:R>"}

    open_order = order.status in _PATH
    done = [s for s in _PATH if s in reached]
    lines: list[str] = []
    for status in done:
        stage = name(status)
        if stage is not None:
            key = k.PROGRESS_CURRENT if open_order and status == done[-1] else k.PROGRESS_DONE
            lines.append(_text(ui, key, stage=stage, **when(reached[status])))
    for status, at in stops:
        stage = name(status)
        if stage is not None:
            lines.append(_text(ui, k.PROGRESS_STOPPED, stage=stage, **when(at)))
    if open_order:
        lines += [_text(ui, k.PROGRESS_NEXT, stage=stage) for s in _upcoming(order) if (stage := name(s)) is not None]
    return lines


def progress_section(ui: Ui, order: m.Order, events: Sequence[m.OrderEvent]) -> str | None:
    return section(_text(ui, k.PROGRESS_HEADING), progress_lines(ui, order, events))


def _order_lines(ui: Ui, view: m.OrderView, fmt: Money) -> list[str]:
    return [line_text(ui, i.name, i.quantity, i.unit_price_minor, i.line_total_minor, fmt=fmt) for i in view.items]


def _instruction_values(instructions: PaymentInstructions, values: Mapping[str, str | int]) -> dict[str, str | int]:
    """The order's values for the payment instructions: their {total} is what is still to pay."""
    return {**values, "total": instructions.amount_due} if instructions.amount_due else dict(values)


def field_sections(fields: Sequence[EmbedField]) -> list[str]:
    """Fields as card sections (the name as the heading over the value); fields of one name that follow each other
    (a long list split into several) are one section."""
    sections: list[tuple[str, list[str]]] = []
    for item in fields:
        if sections and sections[-1][0] == item.name:
            sections[-1][1].append(item.value)
        else:
            sections.append((item.name, [item.value]))
    return [text for name, values in sections if (text := section(name, values))]


def payment_parts(
    ui: Ui, settings: Settings, instructions: PaymentInstructions, values: Mapping[str, str | int], *, methods: bool
) -> list[str]:
    """The payment sections of an order that waits for payment: the payment methods (``methods``), how to pay (the
    provider's instructions under their title) and what was paid so far (the provider's progress, e.g. the gift
    cards received and what is still to pay)."""
    values = _instruction_values(instructions, values)
    title = _optional(ui, instructions.title_key, **values)
    text = _text(ui, instructions.description_key, **values)
    how_to_pay = f"{heading(title)}\n{text}" if title else text
    listed = payment_methods.section_text(ui, settings) if methods else None
    return [*([listed] if listed else []), how_to_pay, *field_sections(instructions.fields)]


def instructions_embeds(
    ui: Ui, settings: Settings, instructions: PaymentInstructions, values: Mapping[str, str | int], *, methods: bool
) -> list[EmbedSpec]:
    """The payment sections as a card of their own (only when they do not fit into the order card or the reminder;
    very long instructions continue in a second card)."""
    head = ui.embed(tone="info", branding_text=False)
    return _card_embeds(head, payment_parts(ui, settings, instructions, values, methods=methods))


def _payment_buttons(ui: Ui, instructions: PaymentInstructions | None, order_id: int) -> tuple[ButtonSpec, ...]:
    """The provider's link button and the button that opens its private payment form (both optional)."""
    form = None
    if instructions is not None and instructions.form_label_key:
        form = _button(ui, instructions.form_label_key, payment_form_id(order_id), style="success")
    return tuple(b for b in (form, _instruction_link(ui, instructions)) if b is not None)


def _instruction_link(ui: Ui, instructions: PaymentInstructions | None) -> ButtonSpec | None:
    if instructions is None or not instructions.link_url or not instructions.link_label_key:
        return None
    label = _optional(ui, instructions.link_label_key)
    if not label:
        return None
    return ButtonSpec(label=label, style="link", url=instructions.link_url)


def shipment_fields(ui: Ui, shipment: Shipment) -> list[EmbedField]:
    """The carrier and tracking number of a shipped order (staff-entered data, escaped), side by side, named as in
    the shipped notification."""
    return [
        EmbedField(_text(ui, sk.SHIPMENT_CARRIER_FIELD), escape(shipment.carrier), inline=True),
        EmbedField(_text(ui, sk.SHIPMENT_NUMBER_FIELD), escape(shipment.tracking_number), inline=True),
    ]


def track_rows(ui: Ui, shipment: Shipment | None) -> Rows:
    """Track package (the carrier's page) when staff entered a tracking link."""
    if shipment is None or not shipment.tracking_url:
        return ()
    return rows_of(ButtonSpec(label=_text(ui, sk.SHIPMENT_TRACK_BUTTON), style="link", url=shipment.tracking_url))


@dataclass(frozen=True)
class OrderMessages:
    """The workflow message of a submitted order, plus what did not fit into it (posted as separate messages in the
    order channel, each at most once): the payment instructions (only when the configured texts alone fill the
    message) and the order lines."""

    main: MessageSpec
    instructions: tuple[MessageSpec, ...] = ()
    items: tuple[MessageSpec, ...] = ()


def _item_messages(ui: Ui, lines: Sequence[str], color: int | None) -> tuple[MessageSpec, ...]:
    """The items section on cards of their own (one per message, split between lines): the order lines that did not
    fit into the order card."""
    embeds = _card_embeds(EmbedSpec(color=color), [items_section(ui, lines)])
    return tuple(MessageSpec(embeds=(embed,)) for embed in embeds)


def order_messages(
    ui: Ui,
    settings: Settings,
    view: m.OrderView,
    *,
    instructions: PaymentInstructions | None,
    shipment: Shipment | None = None,
    links: Mapping[str, str] | None = None,
) -> OrderMessages:
    """The workflow message of a submitted order, laid out as a receipt: the key facts line (ticket, order number,
    status), the text of its current status, then - while it waits for payment - the payment methods and
    instructions (with the customer's cancel button), the items, the summary and the progress tracker. A shipped
    order shows its ``shipment`` (carrier and tracking number, and Track package with a tracking link). ``links``:
    the channel links for ``{channel_purchase}`` (``purchase_values``).

    Budgeted for Discord's limits: everything in one message when it fits; otherwise the order lines move to
    separate messages, and if the configured texts alone fill the message, the payment instructions too. The
    payment instructions and the cancel button are always delivered; nothing is cut."""
    order = view.order
    fmt = order_money(ui, settings, order.currency)
    values = order_values(ui, settings, order)
    pending = order.status == m.PENDING
    if pending:
        # Paying is the last step of ordering (the steps before it are on the workflow message).
        current, total = step_number(settings, order.fulfillment_option, None)
        head = _content_embed(
            ui,
            k.SUBMITTED_TITLE,
            k.SUBMITTED_DESCRIPTION,
            required=(k.SUBMITTED_TITLE,),
            values=values,
            tone="primary",
            footer_extra=ui.progress(current, total),
        )
    else:
        title_key = k.status_title_key(order.status)
        head = _content_embed(
            ui,
            title_key,
            k.status_description_key(order.status),
            required=(title_key,),
            values={**values, **purchase_values(ui, settings, links)},
            tone=status_tone(order.status),
        )
    shown = shipment if order.status == m.SHIPPED else None
    if shown is not None:
        head = replace(head, fields=tuple(shipment_fields(ui, shown)))
    facts = order_line(ui, settings, view)
    lines = _order_lines(ui, view, fmt)
    items = items_section(ui, lines)
    summary = _order_summary(ui, view, fmt)
    progress = progress_section(ui, order, view.timeline)
    paying = instructions if pending else None
    payment = payment_parts(ui, settings, paying, values, methods=True) if paying is not None else []
    pay = _payment_buttons(ui, instructions, order.id) if pending else ()
    cancel = _button(ui, k.BUTTON_CANCEL_ORDER, order_cancel_id(order.id), style="danger") if pending else None

    def embeds_for(with_lines: bool, with_instructions: bool) -> list[EmbedSpec]:
        middle = payment if with_instructions else []
        return _card_embeds(head, [facts, head.description, *middle, items if with_lines else None, summary, progress])

    # Most complete first: everything; the lines separately; the instructions separately; both separately.
    layouts = ((True, True), (False, True), (True, False), (False, False))
    with_lines, with_instructions = next((lo for lo in layouts if _fits(embeds_for(*lo))), layouts[-1])
    if not pending:
        rows = track_rows(ui, shown)
    elif with_instructions:
        rows = payment_methods.payment_rows(ui, settings, order.id, pay, cancel)
    else:
        rows = rows_of(cancel)
    tail = (
        ()
        if paying is None or with_instructions
        else (
            MessageSpec(
                embeds=tuple(instructions_embeds(ui, settings, paying, values, methods=True)),
                rows=payment_methods.payment_rows(ui, settings, order.id, pay, None),
            ),
        )
    )
    return OrderMessages(
        main=MessageSpec(
            embeds=tuple(embeds_for(with_lines, with_instructions)),
            rows=rows,
            banner=ui.banner(status_banner(order.status)),
        ),
        # Continuation messages: only the workflow message (the first part) shows the banner.
        instructions=tail,
        items=() if with_lines else _item_messages(ui, lines, head.color),
    )


def order_message(
    ui: Ui,
    settings: Settings,
    view: m.OrderView,
    *,
    instructions: PaymentInstructions | None,
    shipment: Shipment | None = None,
    links: Mapping[str, str] | None = None,
) -> MessageSpec:
    """The workflow message of a submitted order (see ``order_messages``)."""
    return order_messages(ui, settings, view, instructions=instructions, shipment=shipment, links=links).main


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
        _button(ui, k.BUTTON_CANCEL_YES, order_cancel_yes_id(order.id), style="danger"),
        _button(ui, k.BUTTON_KEEP_ORDER, order_cancel_no_id(order.id)),
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

    if status == m.PAYMENT_SUBMITTED and order.payment_provider == gift_card.PROVIDER_ID:
        # The customer's gift cards cover the total: the payment staff reveal and redeem them, or refuse one. One main
        # action per row: Payment confirmed (revealing the codes comes first, but is not the decision).
        buttons += [
            b
            for b in (
                _optional_button(ui, gift_card.STAFF_REVEAL, gift_card_reveal_id(order.id), "secondary"),
                _optional_button(ui, gift_card.STAFF_CONFIRM, staff_action_id(m.PAID, order.id, status), "success"),
                _optional_button(ui, gift_card.STAFF_PROBLEM, gift_card_problem_id(order.id), "danger"),
            )
            if b is not None
        ]
    elif status in m.UNPAID_STATUSES and staff_confirms_payment:
        buttons.append(action(m.PAID, k.STAFF_BUTTON_MARK_PAID, "success"))
    if status in m.TRANSITIONS[m.REJECTED]:
        buttons.append(action(m.REJECTED, k.STAFF_BUTTON_REJECT, "danger"))
    if status == m.PAID:
        buttons.append(action(m.PROCESSING, k.STAFF_BUTTON_PROCESSING, "secondary"))  # Mark shipped is the main one
    if status in (m.PAID, m.PROCESSING):
        # An order with a shipping address ships first (Mark shipped asks for the tracking number); one without (a
        # shop whose checkout asks for no address) is completed directly.
        if flow.ADDRESS_COUNTRY in order.details:
            buttons.append(action(m.SHIPPED, k.STAFF_BUTTON_SHIPPED, "success"))
        else:
            buttons.append(action(m.FULFILLED, k.STAFF_BUTTON_FULFILLED, "success"))
    if status == m.SHIPPED:
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
        buttons.append(action(m.CANCELLED, k.STAFF_BUTTON_CANCEL, "danger"))
    return buttons


def _optional_button(ui: Ui, key: str, custom_id: str, style: ButtonStyle) -> ButtonSpec | None:
    """A button whose label may not be registered (the gift card texts of an order placed before the shop changed its
    payment provider): left out then, so the record still renders."""
    return ui.button(key, custom_id, style=style) if _registered(ui, key) else None


def ordered_answers(settings: Settings, order: m.Order) -> list[tuple[str, str]]:
    """(answer key, answer as shown) in form order: the shipping address first (the country by name), then the
    forms' answers; fields no longer configured last, by key."""
    c = settings.checkout
    address = [flow.ADDRESS_COUNTRY, *(f.answer_key for f in flow.ADDRESS_FIELDS)]
    known = address + [i.answer_key for i in flow.form_inputs(c, order.fulfillment_option) + flow.submission_inputs(c)]
    keys = [key for key in known if key in order.details]
    keys += sorted(key for key in order.details if key not in known)
    return [(key, answer_text(settings, key, order.details[key])) for key in keys]


def answer_text(settings: Settings, answer_key: str, answer: str) -> str:
    """An answer as staff read it: the address country by its name, everything else as entered."""
    return settings.shipping.country_name(answer) if answer_key == flow.ADDRESS_COUNTRY else answer


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


def _notify_values(
    ui: Ui, settings: Settings, view: m.OrderView, links: Mapping[str, str] | None = None
) -> dict[str, str | int]:
    """The order's values for a notification: its number and total, its ticket and (for the texts that point to
    Report a problem) the place-order channel."""
    values = {**order_values(ui, settings, view.order), "channel_mention": channel_mention(view.channel_id)}
    return {**values, **purchase_values(ui, settings, links)}


def status_notification(
    ui: Ui, settings: Settings, view: m.OrderView, status: str, *, links: Mapping[str, str] | None = None
) -> MessageSpec:
    """The customer's notification of a status change (``links``: see ``purchase_values``)."""
    embed = _content_embed(
        ui,
        k.notify_title_key(status),
        k.notify_description_key(status),
        required=(k.notify_description_key(status),),
        values=_notify_values(ui, settings, view, links),
        tone=_STATUS_TONES.get(status, "info"),
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner(status_banner(status)))


def gift_card_modal(ui: Ui, settings: Settings, order: m.Order, *, remaining_minor: int) -> ModalSpec:
    """The private gift card form: the code, the PIN (optional) and the card's full value. The value is never
    prefilled: a customer with a larger card would declare less than it holds (the hint shows what is still to
    pay)."""
    remaining = order_money(ui, settings, order.currency)(remaining_minor)
    return ModalSpec(
        custom_id=payment_form_submit_id(order.id),
        title=_text(ui, gift_card.FORM_TITLE, order_number=order.number),
        inputs=(
            TextInputSpec(
                custom_id=gift_card.CODE_INPUT,
                label=_text(ui, gift_card.CODE_LABEL),
                placeholder=_optional(ui, gift_card.CODE_HINT),
                min_length=gift_card_store.CODE_MIN,
                max_length=gift_card.CODE_INPUT_MAX,
            ),
            TextInputSpec(
                custom_id=gift_card.PIN_INPUT,
                label=_text(ui, gift_card.PIN_LABEL),
                placeholder=_optional(ui, gift_card.PIN_HINT),
                required=False,
                max_length=gift_card_store.PIN_MAX,
            ),
            TextInputSpec(
                custom_id=gift_card.AMOUNT_INPUT,
                label=_text(ui, gift_card.AMOUNT_LABEL),
                placeholder=_optional(ui, gift_card.AMOUNT_HINT, remaining=remaining),
                max_length=gift_card.AMOUNT_MAX_LENGTH,
            ),
        ),
    )


def payment_refused_notification(
    ui: Ui, settings: Settings, view: m.OrderView, *, remaining: str, fields: Sequence[EmbedField]
) -> MessageSpec:
    """Our team could not accept a gift card: the order waits for payment again (the refused cards with the reasons
    follow as a section, ``fields`` from ``gift_card.cards_fields``; the order ticket shows the Submit gift card
    button again)."""
    embed = _content_embed(
        ui,
        gift_card.NOTIFY_PROBLEM_TITLE,
        gift_card.NOTIFY_PROBLEM_DESCRIPTION,
        required=(gift_card.NOTIFY_PROBLEM_DESCRIPTION,),
        values={**_notify_values(ui, settings, view), "remaining": remaining},
        tone="danger",
        timestamp=True,
    )
    embeds = _card_embeds(embed, [embed.description, *field_sections(fields)])
    return MessageSpec(embeds=tuple(embeds), banner=ui.banner("rejected-or-unsuccessful-submission"))


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
    """The payment reminder, with how to pay (and what was paid so far) as its last sections; they follow in a card
    of their own when they do not fit next to the reminder text."""
    kind = k.NOTIFY_PAYMENT_REMINDER
    embed = _content_embed(
        ui,
        k.notify_title_key(kind),
        k.notify_description_key(kind),
        required=(k.notify_description_key(kind),),
        values=_notify_values(ui, settings, view),
        tone="warning",
        timestamp=True,
    )
    embeds = [embed]
    if instructions is not None:
        order_text = order_values(ui, settings, view.order)
        parts = payment_parts(ui, settings, instructions, order_text, methods=False)
        merged = _card_embeds(embed, [embed.description, *parts])
        embeds = (
            merged
            if _fits_one(merged)
            else [embed, *instructions_embeds(ui, settings, instructions, order_text, methods=False)]
        )
    link = _instruction_link(ui, instructions)
    return MessageSpec(embeds=tuple(embeds), rows=rows_of(link), banner=ui.banner("reminder"))


def refund_notification(
    ui: Ui, settings: Settings, view: m.OrderView, amount_minor: int, *, links: Mapping[str, str] | None = None
) -> MessageSpec:
    """A partial refund was recorded (``links``: see ``purchase_values``)."""
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
            **purchase_values(ui, settings, links),
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
    previous/next buttons when there is more than one page. Statuses show their names (as on the order record), or
    their ids while those names are not configured, so the list works with operator text alone."""

    def name(status: str) -> str:
        return _optional(ui, k.status_label_key(status)) or status

    lines = [
        op(
            "checkout.list.line",
            number=o.number,
            status=name(o.status),
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
        *(SelectOptionSpec(label=name(s), value=s, default=s == status) for s in m.ORDER_STATUSES),
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
            SelectOptionSpec(
                label=op("checkout.list.view_option", number=o.number, status=name(o.status)), value=str(o.id)
            )
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
    return MessageSpec(embeds=(embed,), rows=tuple(rows), banner=ui.banner("staff-dashboard"))
