"""Payment methods in the order ticket (``[[payments.methods]]``) and the pop-up of a method that is temporarily down.

While an order waits for payment, its card lists the shop's payment methods with their state and shows a button per
method. A method that is ``down`` opens a private pop-up when pressed: it is temporarily unavailable, here are the
methods that work right now, with buttons to use them. What an ``active`` method does depends on its kind:

- ``gift_card``: the gift card form of the ``gift_card`` provider (Submit gift card) and its Buy a gift card link;
- ``staff``: pressing it posts in the order ticket that the customer wants to pay this way and alerts the staff
  (``PAYMENT_METHOD_REQUESTED``; the operations feature posts the alert), who send the payment details in the ticket
  and confirm the payment with Mark paid;
- ``link``: a link button to the method's ``url`` (a hosted payment page); staff confirm the payment with Mark paid.

The bot never asks for card numbers or bank details in Discord. A real card processor is added as a payment provider
(see the ``payments`` package docstring); its method is then a ``link`` (or the provider's own page).

Route: ``sb:co:paym:<order_id>:<method_id>`` (a method button on the order card or in the pop-up; handler in
``handlers.on_payment_method``). Without ``payments.methods`` nothing here is shown and nothing is declared.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from shopbot.config.content import MissingContent
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import PaymentMethod, Settings
from shopbot.features.checkout.payments import gift_card
from shopbot.ui.card import heading
from shopbot.ui.component_emoji import strip_custom_emoji
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, ButtonStyle, MessageSpec, rows_of

if TYPE_CHECKING:
    from shopbot.ui.style import Ui

FEATURE: Final = "checkout"
ROUTE: Final = "co"
ACTION: Final = "paym"

_M: Final = "checkout.payment.method"
_S: Final = "checkout.payment.methods"
SECTION_HEADING: Final = f"{_S}.heading"
LINE_ACTIVE: Final = f"{_S}.line_active"
LINE_DOWN: Final = f"{_S}.line_down"
NOTE: Final = f"{_S}.note"
ERROR_UNAVAILABLE: Final = f"{_S}.error_unavailable"
ERROR_STATE: Final = f"{_S}.error_state"
DOWN_TITLE: Final = "checkout.payment.down.title"
DOWN_DESCRIPTION: Final = "checkout.payment.down.description"
DOWN_ACTIVE_HEADING: Final = "checkout.payment.down.active_heading"
DOWN_ACTIVE_LINE: Final = "checkout.payment.down.active_line"
DOWN_HINT: Final = "checkout.payment.down.hint"
GIFT_CARD_BUTTON: Final = "checkout.payment.down.gift_card_button"
STAFF_TITLE: Final = "checkout.payment.staff_method.title"
STAFF_POSTED: Final = "checkout.payment.staff_method.posted"
STAFF_REPLY: Final = "checkout.payment.staff_method.reply"
STAFF_ALREADY: Final = "checkout.payment.staff_method.already"

# The customer's request to pay with a staff-kind method: posted once per order and method (notification dedupe).
NOTIFY_KIND: Final = "checkout.payment_method"
AUDIT_ACTION: Final = "checkout.payment_method_requested"

_METHOD = ("method",)


def label_key(method_id: str) -> str:
    return f"{_M}.{method_id}.label"


def description_key(method_id: str) -> str:
    return f"{_M}.{method_id}.description"


def method_custom_id(order_id: int, method_id: str) -> str:
    return cid(ROUTE, ACTION, order_id, method_id)


def request_key(order_id: int, method_id: str) -> str:
    """Dedupe key of the ticket post (and, with the operations prefix, of the staff alert) for one request."""
    return f"{NOTIFY_KIND}:{order_id}:{method_id}"


def content_keys(settings: Settings) -> list[ContentKey]:
    methods = settings.payments.methods
    if not methods:
        return []
    g_methods = "Checkout: payment methods"
    g_down = "Checkout: payment method pop-up (a method that is down)"
    g_staff = "Checkout: payment by a method our team handles"
    keys: list[ContentKey] = []
    for method in methods:
        keys += [
            ck(
                label_key(method.id),
                Kind.BUTTON_LABEL,
                FEATURE,
                g_methods,
                f"Name of the payment method '{method.id}' on its button in the order ticket (it may start with an "
                "icon) and in the payment-methods list.",
            ),
            ck(
                description_key(method.id),
                Kind.FIELD_VALUE,
                FEATURE,
                g_methods,
                f"Optional one-line description of the payment method '{method.id}' (e.g. the cards it takes), shown "
                "in the pop-up's list of methods that work right now.",
                required=False,
            ),
        ]
    keys += [
        ck(
            SECTION_HEADING,
            Kind.TITLE,
            FEATURE,
            g_methods,
            "Heading of the payment-methods section on the order card while the order waits for payment (it may "
            "start with an icon).",
        ),
        ck(
            LINE_ACTIVE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_methods,
            "How a method that works right now is listed, e.g. '{icon_dot_blue} **{method}** · available now'.",
            placeholders=_METHOD,
        ),
        ck(
            LINE_DOWN,
            Kind.FIELD_VALUE,
            FEATURE,
            g_methods,
            "How a method that is temporarily down is listed, e.g. '{icon_dot_grey} {method} · temporarily down'.",
            placeholders=_METHOD,
        ),
        ck(
            NOTE,
            Kind.DESCRIPTION,
            FEATURE,
            g_methods,
            "Optional text under the list of payment methods, e.g. why only some work right now.",
            required=False,
        ),
        ck(
            ERROR_UNAVAILABLE,
            Kind.DESCRIPTION,
            FEATURE,
            g_methods,
            "Private reply when a payment-method button is pressed that is no longer offered (the settings changed).",
        ),
        ck(
            ERROR_STATE,
            Kind.DESCRIPTION,
            FEATURE,
            g_methods,
            "Private reply when a payment-method button is pressed for an order that no longer waits for payment.",
        ),
        ck(
            DOWN_TITLE,
            Kind.TITLE,
            FEATURE,
            g_down,
            "Title of the private pop-up shown when a customer presses a payment method that is temporarily down. "
            "{method} is the method's name.",
            placeholders=_METHOD,
        ),
        ck(
            DOWN_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_down,
            "Text of that pop-up: the method is temporarily down (and when it comes back, if known). {method} is the "
            "method's name. The methods that work right now and their buttons follow.",
            placeholders=_METHOD,
        ),
        ck(
            DOWN_ACTIVE_HEADING,
            Kind.TITLE,
            FEATURE,
            g_down,
            "Heading of the pop-up's list of the methods that work right now (it may start with an icon).",
        ),
        ck(
            DOWN_ACTIVE_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_down,
            "How each method that works right now is listed in the pop-up. {method} is its name, {description} its "
            "description (empty when it has none).",
            placeholders=("method", "description"),
        ),
        ck(
            DOWN_HINT,
            Kind.DESCRIPTION,
            FEATURE,
            g_down,
            "Optional last line of the pop-up, e.g. 'Press a button below to use one of them.'",
            required=False,
        ),
    ]
    if any(m.kind == "gift_card" for m in methods):
        keys.append(
            ck(
                GIFT_CARD_BUTTON,
                Kind.BUTTON_LABEL,
                FEATURE,
                g_down,
                "Button in the pop-up that opens the private gift card form (as Submit gift card does).",
            )
        )
    if any(m.kind == "staff" for m in methods):
        keys += [
            ck(
                STAFF_TITLE,
                Kind.TITLE,
                FEATURE,
                g_staff,
                "Title of the message posted in the order ticket when the customer chooses a method our team handles "
                '(kind = "staff"). {method} is the method\'s name.',
                placeholders=_METHOD,
            ),
            ck(
                STAFF_POSTED,
                Kind.DESCRIPTION,
                FEATURE,
                g_staff,
                "Text of that message: our team will send the payment details in this ticket. {method} is the "
                "method's name, {user_mention} mentions the customer.",
                placeholders=("method", "user_mention"),
            ),
            ck(
                STAFF_REPLY,
                Kind.DESCRIPTION,
                FEATURE,
                g_staff,
                "Private reply to the customer after choosing such a method. {method} is the method's name.",
                placeholders=_METHOD,
            ),
            ck(
                STAFF_ALREADY,
                Kind.DESCRIPTION,
                FEATURE,
                g_staff,
                "Private reply when the customer chooses the same method again. {method} is the method's name.",
                placeholders=_METHOD,
            ),
        ]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    """Settings checks beyond the parser's (none today: the parser checks the methods against the provider)."""
    return []


# -- what the customer sees ------------------------------------------------------------------------------------------


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def method_name(ui: Ui, method_id: str) -> str:
    """The method's name for sentences and titles: its label without the icon it may start with."""
    return strip_custom_emoji(_text(ui, label_key(method_id))) or method_id


def section_text(ui: Ui, settings: Settings) -> str | None:
    """The payment-methods section of the order card (None without ``payments.methods``)."""
    methods = settings.payments.methods
    if not methods:
        return None
    lines = [heading(_text(ui, SECTION_HEADING))]
    for method in methods:
        line = LINE_ACTIVE if method.active else LINE_DOWN
        lines.append(_text(ui, line, method=method_name(ui, method.id)).strip())
    note = ui.text(NOTE)
    if note:
        lines.append(note)
    return "\n".join(lines)


def method_buttons(ui: Ui, settings: Settings, order_id: int) -> list[ButtonSpec]:
    """A button per method that is not the gift card (whose own buttons are Submit gift card and Buy a gift card):
    the methods that are down (secondary), an active method our team handles (primary), an active payment page
    (link)."""
    buttons: list[ButtonSpec] = []
    for method in settings.payments.methods:
        if method.kind == "gift_card":
            continue
        buttons.append(_method_button(ui, order_id, method))
    return buttons


def _method_button(ui: Ui, order_id: int, method: PaymentMethod) -> ButtonSpec:
    label = _text(ui, label_key(method.id))
    if method.active and method.kind == "link":
        return ButtonSpec(label=label, style="link", url=method.url)
    style: ButtonStyle = "primary" if method.active else "secondary"
    return ButtonSpec(label=label, custom_id=method_custom_id(order_id, method.id), style=style)


def active_methods(settings: Settings) -> list[PaymentMethod]:
    return [m for m in settings.payments.methods if m.active]


def _gift_card_buttons(ui: Ui, settings: Settings, payment_form_id: str) -> list[ButtonSpec]:
    buttons = [ButtonSpec(label=_text(ui, GIFT_CARD_BUTTON), custom_id=payment_form_id, style="success")]
    buy_url = settings.payments.gift_card.buy_url
    buy = ui.text(gift_card.BUY_BUTTON) if buy_url else None
    if buy_url and buy:
        buttons.append(ButtonSpec(label=buy, style="link", url=buy_url))
    return buttons


def down_message(
    ui: Ui, settings: Settings, order_id: int, method: PaymentMethod, *, payment_form_id: str
) -> MessageSpec:
    """The private pop-up of a method that is down: it is temporarily unavailable; the methods that work right now
    with their descriptions, and a button for each."""
    name = method_name(ui, method.id)
    lines = [_text(ui, DOWN_DESCRIPTION, method=name), "", heading(_text(ui, DOWN_ACTIVE_HEADING))]
    buttons: list[ButtonSpec] = []
    for active in active_methods(settings):
        description = ui.text(description_key(active.id)) or ""
        line = _text(ui, DOWN_ACTIVE_LINE, method=method_name(ui, active.id), description=description)
        lines.append(line.strip())
        if active.kind == "gift_card":
            buttons += _gift_card_buttons(ui, settings, payment_form_id)
        else:
            buttons.append(_method_button(ui, order_id, active))
    hint = ui.text(DOWN_HINT)
    if hint:
        lines += ["", hint]
    embed = ui.embed(title=_text(ui, DOWN_TITLE, method=name), description="\n".join(lines), tone="warning")
    return MessageSpec(embeds=(embed,), rows=rows_of(*buttons), banner=ui.banner("general-response"))


def staff_method_post(ui: Ui, method: PaymentMethod, user_id: int) -> MessageSpec:
    """Posted in the order ticket when the customer chooses a method our team handles."""
    name = method_name(ui, method.id)
    embed = ui.embed(
        title=_text(ui, STAFF_TITLE, method=name),
        description=_text(ui, STAFF_POSTED, method=name, user_mention=f"<@{user_id}>"),
        tone="info",
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("submission-and-processing"))


def reply(ui: Ui, key: str, method: PaymentMethod, *, tone: str = "info") -> MessageSpec:
    embed = ui.embed(description=_text(ui, key, method=method_name(ui, method.id)), tone=tone)
    return MessageSpec(embeds=(embed,), banner=ui.banner("general-response"))


def link_message(ui: Ui, method: PaymentMethod) -> MessageSpec:
    """A method that is a payment page (kind "link"), offered again privately (its button on the order card is a
    link and sends nothing; this answers an older button of the method)."""
    description = ui.text(description_key(method.id)) or ""
    line = _text(ui, DOWN_ACTIVE_LINE, method=method_name(ui, method.id), description=description).strip()
    embed = ui.embed(description=line, tone="info")
    button = ButtonSpec(label=_text(ui, label_key(method.id)), style="link", url=method.url)
    return MessageSpec(embeds=(embed,), rows=rows_of(button), banner=ui.banner("general-response"))


def payment_rows(
    ui: Ui, settings: Settings, order_id: int, provider_buttons: Sequence[ButtonSpec], cancel: ButtonSpec | None
) -> tuple[tuple[ButtonSpec, ...], ...]:
    """The buttons of an order that waits for payment: the method buttons first (up to five per row), then the
    provider's buttons (Submit gift card, Buy a gift card) and Cancel order."""
    methods = method_buttons(ui, settings, order_id)
    method_rows = rows_of(*methods)
    action_rows = rows_of(*provider_buttons, cancel)
    return tuple(tuple(b for b in row if isinstance(b, ButtonSpec)) for row in (*method_rows, *action_rows))
