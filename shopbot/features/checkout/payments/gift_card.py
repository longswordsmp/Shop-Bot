"""The ``gift_card`` payment provider: customers pay the exact total with gift cards (e.g. DoorDash eGift cards).

Under the payment instructions the customer finds **Submit gift card** (a private form: the code, the PIN if the card
has one, and the card's amount) and, when ``payments.gift_card.buy_url`` is set, a link to buy a card. Several cards
may pay one order (``max_cards``); once the cards cover the total the order is placed ("payment submitted") and staff
with the payment roles (``staff_roles``; owners and admins always) reveal the codes, redeem them in the shop's
account and confirm the payment, or refuse a card with a reason - the customer is told and submits another card.
Value beyond the total becomes store credit.

Codes are never posted in a channel: they are stored encrypted (``vault``), shown only in a private reply to the
payment staff, and deleted once staff decide on the card or after ``keep_days``. A customer message in their order
ticket that looks like a code is deleted (``chat_guard``). Refunds are recorded (store credit or outside the bot).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from discord.utils import escape_markdown

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.checkout import gift_card_store as store
from shopbot.features.checkout.models import Order
from shopbot.features.checkout.order_money import order_money
from shopbot.features.checkout.payments.base import PaymentInstructions, PaymentStart, RefundResult
from shopbot.features.checkout.payments.vault import normalize_code
from shopbot.ui.spec import EmbedField

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.ui.style import Ui

FEATURE: Final = "checkout"
PROVIDER_ID: Final = "gift_card"
_P: Final = "checkout.payment." + PROVIDER_ID

TITLE: Final = f"{_P}.title"
INSTRUCTIONS: Final = f"{_P}.instructions"
SUBMIT_BUTTON: Final = f"{_P}.submit_button"
BUY_BUTTON: Final = f"{_P}.buy_button"
FORM_TITLE: Final = f"{_P}.form.title"
CODE_LABEL: Final = f"{_P}.form.code_label"
CODE_HINT: Final = f"{_P}.form.code_placeholder"
PIN_LABEL: Final = f"{_P}.form.pin_label"
PIN_HINT: Final = f"{_P}.form.pin_placeholder"
AMOUNT_LABEL: Final = f"{_P}.form.amount_label"
AMOUNT_HINT: Final = f"{_P}.form.amount_placeholder"
CARDS_FIELD: Final = f"{_P}.cards_field"
CARD_LINE: Final = f"{_P}.card_line"
CARD_WAITING: Final = f"{_P}.card_status.waiting"
CARD_REDEEMED: Final = f"{_P}.card_status.redeemed"
CARD_REFUSED: Final = f"{_P}.card_status.refused"
REMAINING: Final = f"{_P}.remaining"
REPLY_PARTIAL: Final = f"{_P}.reply.partial"
REPLY_COMPLETE: Final = f"{_P}.reply.complete"
ERROR_CODE: Final = f"{_P}.error.code"
ERROR_AMOUNT: Final = f"{_P}.error.amount"
ERROR_DUPLICATE: Final = f"{_P}.error.duplicate"
ERROR_TOO_MANY: Final = f"{_P}.error.too_many"
ERROR_STATE: Final = f"{_P}.error.state"
ERROR_UNAVAILABLE: Final = f"{_P}.error.unavailable"
CHAT_GUARD: Final = f"{_P}.chat_guard"
# The customer notification when staff refused a card (the order waits for payment again).
NOTIFY_CARD_PROBLEM: Final = "card_problem"
NOTIFY_PROBLEM_TITLE: Final = "checkout.notify.card_problem.title"
NOTIFY_PROBLEM_DESCRIPTION: Final = "checkout.notify.card_problem.description"
# Staff controls on the order record.
STAFF_REVEAL: Final = "checkout.staff.button.reveal_gift_card"
STAFF_CONFIRM: Final = "checkout.staff.button.payment_confirmed"
STAFF_PROBLEM: Final = "checkout.staff.button.card_problem"
# The payment field on the order record.
_S: Final = "checkout.staff." + PROVIDER_ID
RECORD_FIELD: Final = f"{_S}.field"
RECORD_LINE: Final = f"{_S}.line"
RECORD_WAITING: Final = f"{_S}.state.waiting"
RECORD_REDEEMED: Final = f"{_S}.state.redeemed"
RECORD_REFUSED: Final = f"{_S}.state.refused"
RECORD_WITHDRAWN: Final = f"{_S}.state.withdrawn"
RECORD_ERASED: Final = f"{_S}.state.erased"
RECORD_REVEALED: Final = f"{_S}.revealed"
RECORD_TOTAL: Final = f"{_S}.total"

CODE_INPUT: Final = "code"
PIN_INPUT: Final = "pin"
AMOUNT_INPUT: Final = "amount"
AMOUNT_MAX_LENGTH: Final = 12
# Characters the code input takes: the code's letters and digits plus the spaces and dashes it may be typed with.
CODE_INPUT_MAX: Final = store.CODE_MAX + 16

_ORDER = ("order_number", "total")
_CODE_CHARS = re.compile(r"[A-Za-z0-9 -]+")


def enabled(settings: Settings) -> bool:
    return settings.payments.provider == PROVIDER_ID


def valid_code(code: str) -> bool:
    """Letters, digits, spaces and dashes; ``CODE_MIN``-``CODE_MAX`` letters and digits."""
    return bool(_CODE_CHARS.fullmatch(code)) and store.CODE_MIN <= len(normalize_code(code)) <= store.CODE_MAX


def last_chars(code: str) -> str:
    return normalize_code(code)[-store.LAST_CHARS :]


class GiftCardProvider:
    id = PROVIDER_ID
    requires_staff_confirmation = True
    supports_refunds = False

    def content_keys(self, settings: Settings) -> list[ContentKey]:
        pay = "Checkout: gift card payment"
        form = "Checkout: gift card payment form"
        replies = "Checkout: gift card payment replies"
        staff = "Checkout: staff gift card controls"
        return [
            ck(
                TITLE,
                Kind.TITLE,
                FEATURE,
                pay,
                "Optional title of the payment instructions shown in the order "
                "ticket while the order waits for payment.",
                required=False,
                placeholders=_ORDER,
            ),
            ck(
                INSTRUCTIONS,
                Kind.DESCRIPTION,
                FEATURE,
                pay,
                "How to pay with gift cards, shown in the order "
                "ticket and in the payment reminder. {total} is the amount to pay.",
                placeholders=_ORDER,
            ),
            ck(
                SUBMIT_BUTTON,
                Kind.BUTTON_LABEL,
                FEATURE,
                pay,
                "Button under the instructions that opens the private gift card form.",
            ),
            ck(
                BUY_BUTTON,
                Kind.BUTTON_LABEL,
                FEATURE,
                pay,
                "Link button to payments.gift_card.buy_url (where customers buy a card).",
                required=bool(settings.payments.gift_card.buy_url),
            ),
            ck(
                CARDS_FIELD,
                Kind.FIELD_NAME,
                FEATURE,
                pay,
                "Heading of the section listing the cards the customer submitted for the order (under the "
                "instructions on the order card and in the card problem notification), shown in bold, e.g. "
                "'{icon_gift} GIFT CARDS'.",
            ),
            ck(
                CARD_LINE,
                Kind.FORMAT,
                FEATURE,
                pay,
                "One submitted card: {number} (1, 2...), {amount}, {last4} (the "
                "code's last characters) and {status} (one of the card_status texts).",
                placeholders=("number", "amount", "last4", "status"),
            ),
            ck(CARD_WAITING, Kind.FORMAT, FEATURE, pay, "Status of a card our team has not checked yet."),
            ck(CARD_REDEEMED, Kind.FORMAT, FEATURE, pay, "Status of a card our team redeemed."),
            ck(
                CARD_REFUSED,
                Kind.FORMAT,
                FEATURE,
                pay,
                "Status of a card our team could not accept; {reason} is what staff wrote.",
                placeholders=("reason",),
            ),
            ck(
                REMAINING,
                Kind.FORMAT,
                FEATURE,
                pay,
                "Last line of the cards section while part of the total is still to pay: "
                "{remaining} (still to pay) and {received} (the cards that count).",
                placeholders=("remaining", "received"),
            ),
            ck(
                FORM_TITLE,
                Kind.MODAL_TITLE,
                FEATURE,
                form,
                "Title of the private gift card form.",
                placeholders=("order_number",),
            ),
            ck(CODE_LABEL, Kind.INPUT_LABEL, FEATURE, form, "Label of the gift card code input."),
            ck(CODE_HINT, Kind.INPUT_PLACEHOLDER, FEATURE, form, "Optional example in the code input.", required=False),
            ck(PIN_LABEL, Kind.INPUT_LABEL, FEATURE, form, "Label of the optional PIN input."),
            ck(PIN_HINT, Kind.INPUT_PLACEHOLDER, FEATURE, form, "Optional hint in the PIN input.", required=False),
            ck(AMOUNT_LABEL, Kind.INPUT_LABEL, FEATURE, form, "Label of the card amount input."),
            ck(
                AMOUNT_HINT,
                Kind.INPUT_PLACEHOLDER,
                FEATURE,
                form,
                "Optional hint in the amount input; {remaining} is what is still to pay.",
                required=False,
                placeholders=("remaining",),
            ),
            ck(
                REPLY_PARTIAL,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "Private reply after a card that does not cover the "
                "total yet: {amount} (this card), {received} (all cards), {remaining} (still to pay), {total}.",
                placeholders=("amount", "received", "remaining", "total"),
            ),
            ck(
                REPLY_COMPLETE,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "Private reply after the card that completes the "
                "payment (the order is placed): {amount} (this card), {received} (all cards), {order_number}.",
                placeholders=("amount", "received", "order_number"),
            ),
            ck(
                ERROR_CODE,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "The code is not valid: {min}-{max} letters and digits.",
                placeholders=("min", "max"),
            ),
            ck(
                ERROR_AMOUNT,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "The amount is not a valid amount; {example} shows one.",
                placeholders=("example",),
            ),
            ck(
                ERROR_DUPLICATE,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "This card was already submitted (it is waiting or was redeemed).",
            ),
            ck(
                ERROR_TOO_MANY,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "The order already has {max} cards (payments.gift_card.max_cards).",
                placeholders=("max",),
            ),
            ck(
                ERROR_STATE,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "The order no longer waits for a payment (placed, paid or closed).",
            ),
            ck(
                ERROR_UNAVAILABLE,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "Cards cannot be accepted right now (the bot "
                "cannot store them safely); the customer should try again later or ask our team.",
            ),
            ck(
                CHAT_GUARD,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "Sent to the customer (by DM, else in the ticket) when "
                "their message was deleted because it looked like a gift card code (payments.gift_card.chat_guard). "
                "{channel_mention} is their order ticket.",
                placeholders=("channel_mention",),
            ),
            ck(
                NOTIFY_PROBLEM_TITLE,
                Kind.TITLE,
                FEATURE,
                replies,
                "Title of the notification when our team could not accept a card (the order waits for payment again).",
                placeholders=(*_ORDER, "remaining", "channel_mention"),
            ),
            ck(
                NOTIFY_PROBLEM_DESCRIPTION,
                Kind.DESCRIPTION,
                FEATURE,
                replies,
                "Text of that notification; the "
                "cards with the reasons follow. {remaining} is still to pay, {channel_mention} the order ticket.",
                placeholders=(*_ORDER, "remaining", "channel_mention"),
            ),
            ck(
                STAFF_REVEAL,
                Kind.BUTTON_LABEL,
                FEATURE,
                staff,
                "Staff button: show the submitted codes privately (payment roles only; every reveal is logged).",
            ),
            ck(
                STAFF_CONFIRM,
                Kind.BUTTON_LABEL,
                FEATURE,
                staff,
                "Staff button: every waiting card was redeemed (the order is paid).",
            ),
            ck(
                STAFF_PROBLEM,
                Kind.BUTTON_LABEL,
                FEATURE,
                staff,
                "Staff button: the waiting cards could not be redeemed (asks for the reason the customer is shown).",
            ),
            ck(
                RECORD_FIELD,
                Kind.FIELD_NAME,
                FEATURE,
                staff,
                "Field on the order record listing the submitted cards (never their codes).",
            ),
            ck(
                RECORD_LINE,
                Kind.FORMAT,
                FEATURE,
                staff,
                "One card on the order record: {index}, {amount}, {last4} "
                "and {state} (one of the state texts, followed by the revealed text once someone revealed it).",
                placeholders=("index", "amount", "last4", "state"),
            ),
            ck(RECORD_WAITING, Kind.FORMAT, FEATURE, staff, "State of a card no one decided on yet."),
            ck(RECORD_REDEEMED, Kind.FORMAT, FEATURE, staff, "State of a redeemed card."),
            ck(
                RECORD_REFUSED,
                Kind.FORMAT,
                FEATURE,
                staff,
                "State of a refused card; {reason} is what the customer was told.",
                placeholders=("reason",),
            ),
            ck(
                RECORD_WITHDRAWN,
                Kind.FORMAT,
                FEATURE,
                staff,
                "State of a card given up because the order ended before anyone decided.",
            ),
            ck(
                RECORD_ERASED,
                Kind.FORMAT,
                FEATURE,
                staff,
                "State of a card no one decided on whose code was deleted after payments.gift_card.keep_days (shown "
                "instead of the waiting state).",
            ),
            ck(
                RECORD_REVEALED,
                Kind.FORMAT,
                FEATURE,
                staff,
                "Added to a card once its code was revealed: {count} (times) and {staff} (who revealed it last).",
                placeholders=("count", "staff"),
            ),
            ck(
                RECORD_TOTAL,
                Kind.FORMAT,
                FEATURE,
                staff,
                "Last line of the payment field: {covered} (cards waiting or redeemed) of {total}.",
                placeholders=("covered", "total"),
            ),
        ]

    def instructions(self, settings: Settings, order: Order, external_ref: str | None) -> PaymentInstructions:
        del order, external_ref
        buy_url = settings.payments.gift_card.buy_url or None
        return PaymentInstructions(
            description_key=INSTRUCTIONS,
            title_key=TITLE,
            link_url=buy_url,
            link_label_key=BUY_BUTTON if buy_url else None,
            form_label_key=SUBMIT_BUTTON,
        )

    async def progress(self, app: App, order: Order) -> tuple[EmbedField, ...]:
        cards = await app.db.run(store.cards, order.id)
        return cards_fields(app.ui, app.settings, order, cards)

    async def start(self, app: App, order: Order) -> PaymentStart:
        return PaymentStart(external_ref=None, instructions=self.instructions(app.settings, order, None))

    async def refund(self, app: App, order: Order, amount_minor: int) -> RefundResult:
        del app, order, amount_minor
        return RefundResult(method="manual")


# -- rendering (customer side) ----------------------------------------------------------------------------------


def card_status(ui: Ui, card: store.GiftCard) -> str | None:
    if card.status == store.SUBMITTED:
        return ui.text(CARD_WAITING)
    if card.status == store.REDEEMED:
        return ui.text(CARD_REDEEMED)
    if card.status == store.REFUSED:
        return ui.text(CARD_REFUSED, reason=escape_markdown(card.reason or ""))
    return None  # withdrawn cards are not listed


def counted_minor(cards: list[store.GiftCard]) -> int:
    """The value of the cards that count toward the total (submitted or redeemed; refused ones do not)."""
    return sum(card.amount_minor for card in cards if card.status in store.ACTIVE)


def cards_fields(
    ui: Ui, settings: Settings, order: Order, cards: list[store.GiftCard], *, only: tuple[int, ...] | None = None
) -> tuple[EmbedField, ...]:
    """The submitted cards (numbered in submission order) and what is still to pay. ``only``: list just these cards
    (still numbered among all of them)."""
    fmt = order_money(ui, settings, order.currency)
    lines: list[str] = []
    for number, card in enumerate(cards, start=1):
        status = card_status(ui, card)
        if status is None or (only is not None and card.id not in only):
            continue
        line = ui.text(
            CARD_LINE, number=number, amount=fmt(card.amount_minor), last4=escape_markdown(card.last4), status=status
        )
        if line:
            lines.append(line)
    counted = counted_minor(cards)
    remaining = max(0, order.total_minor - counted)
    if remaining and counted:
        line = ui.text(REMAINING, remaining=fmt(remaining), received=fmt(counted))
        if line:
            lines.append(line)
    name = ui.text(CARDS_FIELD)
    if not lines or not name:
        return ()
    return tuple(EmbedField(name, chunk) for chunk in _chunks(lines))


def _chunks(lines: list[str], limit: int = 1024) -> list[str]:
    """``lines`` joined into texts of at most ``limit`` characters (a field value); a single longer line is split
    too, never inside a markdown escape, so nothing is cut."""
    chunks: list[str] = []
    current = ""
    pieces = [piece for line in lines for piece in _split_line(line, limit)]
    for piece in pieces:
        candidate = f"{current}\n{piece}" if current else piece
        if len(candidate) > limit and current:
            chunks.append(current)
            candidate = piece
        current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_line(line: str, limit: int) -> list[str]:
    pieces: list[str] = []
    while len(line) > limit:
        cut = limit
        backslashes = len(line[:cut]) - len(line[:cut].rstrip("\\"))
        if backslashes % 2:  # the cut would separate an escape from the character it escapes
            cut -= 1
        pieces.append(line[:cut])
        line = line[cut:]
    return [*pieces, line]


# -- rendering (staff side: the order record; never a code) -------------------------------------------------------


def record_fields(ui: Ui, settings: Settings, order: Order, cards: tuple[store.GiftCard, ...]) -> list[EmbedField]:
    """The cards on the order record, split into fields of at most 1024 characters (refusal reasons can be long);
    empty while there is none, or when the gift card texts are not registered."""
    name = ui.text(RECORD_FIELD) if RECORD_FIELD in ui.content.registry else None
    if not cards or not name:
        return []
    fmt = order_money(ui, settings, order.currency)
    lines = []
    for index, card in enumerate(cards, start=1):
        state = _record_state(ui, card)
        line = ui.text(
            RECORD_LINE, index=index, amount=fmt(card.amount_minor), last4=escape_markdown(card.last4), state=state
        )
        if line:
            lines.append(line)
    covered = sum(card.amount_minor for card in cards if card.status in store.ACTIVE)
    total = ui.text(RECORD_TOTAL, covered=fmt(covered), total=fmt(order.total_minor))
    if total:
        lines.append(total)
    return [EmbedField(name, chunk) for chunk in _chunks(lines)]


def _record_state(ui: Ui, card: store.GiftCard) -> str:
    parts: list[str | None] = []
    if card.status == store.SUBMITTED:
        # A waiting card whose code was deleted unread no longer waits for anything: only that is said.
        parts.append(ui.text(RECORD_WAITING if card.stored else RECORD_ERASED))
    elif card.status == store.REDEEMED:
        parts.append(ui.text(RECORD_REDEEMED))
    elif card.status == store.REFUSED:
        parts.append(ui.text(RECORD_REFUSED, reason=escape_markdown(card.reason or "")))
    else:
        parts.append(ui.text(RECORD_WITHDRAWN))
    if card.reveal_count and card.revealed_by is not None:
        parts.append(ui.text(RECORD_REVEALED, count=card.reveal_count, staff=f"<@{card.revealed_by}>"))
    return " · ".join(p for p in parts if p)
