"""Payment methods (``[[payments.methods]]``): how the shop presents the ways to pay, and the pop-up of a method that
cannot be used yet.

Before the customer pays, no text says which methods work right now. The shop panel, #how-to-order and #faq list every
method with its icon (``{payment_methods}``, in the settings' order) and say the customer chooses when they pay. While
an order waits for payment and nothing was paid yet, its card asks the customer to choose how to pay over a button per
method (its icon and name, in the settings' order). What pressing one does depends on the method:

- ``down``: a private pop-up says which methods we accept at the moment (the active ones, by name), with a button for
  each - the only place a customer learns that a method cannot be used yet;
- ``gift_card`` (active): the private gift card how-to: the provider's steps for the amount still to pay, Submit gift
  card (the private form) and Buy a gift card. Once a card counts toward the order, the card shows those steps and
  the cards received in place of the methods;
- ``staff`` (active): pressing it posts in the order ticket that the customer wants to pay this way and alerts the
  staff (``PAYMENT_METHOD_REQUESTED``; the operations feature posts the alert), who send the payment details in the
  ticket and confirm the payment with Mark paid;
- ``link`` (active): a link button to the method's ``url`` (a hosted payment page); staff confirm with Mark paid.

So a method that comes back needs a settings change only (``status = "active"``): the lists, the buttons and the
pop-up follow. Each method's ``icon`` is one of the bot's icons (the brand marks ``brand_*``, ``card``, ``gift``);
the /setup report names a method whose picture is missing (``missing_icons``).

The bot never asks for card numbers or bank details in Discord. A real card processor is added as a payment provider
(see the ``payments`` package docstring); its method is then a ``link`` (or the provider's own page).

Route: ``sb:co:paym:<order_id>:<method_id>`` (a method button on the order card or in the pop-up; handler in
``handlers.on_payment_method``). Without ``payments.methods`` nothing here is shown and nothing is declared.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Final

from shopbot.config.content import MissingContent
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import PaymentMethod, Settings
from shopbot.features.checkout.payments import gift_card
from shopbot.ui.card import heading
from shopbot.ui.component_emoji import strip_custom_emoji
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import ACTION_ROWS
from shopbot.ui.spec import ButtonSpec, Component, MessageSpec, rows_of

if TYPE_CHECKING:
    from shopbot.ui.style import Ui

FEATURE: Final = "checkout"
ROUTE: Final = "co"
ACTION: Final = "paym"

_M: Final = "checkout.payment.method"
_S: Final = "checkout.payment.methods"
SECTION_HEADING: Final = f"{_S}.heading"
LEAD: Final = f"{_S}.lead"
LINK_LINE: Final = f"{_S}.link_line"
ERROR_UNAVAILABLE: Final = f"{_S}.error_unavailable"
ERROR_STATE: Final = f"{_S}.error_state"
DOWN_TITLE: Final = "checkout.payment.down.title"
DOWN_DESCRIPTION: Final = "checkout.payment.down.description"
DOWN_HINT: Final = "checkout.payment.down.hint"
GIFT_CARD_BUTTON: Final = "checkout.payment.down.gift_card_button"
STAFF_TITLE: Final = "checkout.payment.staff_method.title"
STAFF_POSTED: Final = "checkout.payment.staff_method.posted"
STAFF_REPLY: Final = "checkout.payment.staff_method.reply"
STAFF_ALREADY: Final = "checkout.payment.staff_method.already"

# The list of every method with its icon, for the texts that present the ways to pay (shop panel, guides, FAQ).
PLACEHOLDER: Final = "payment_methods"
PLACEHOLDER_DOC: Final = (
    " {payment_methods} lists the shop's payment methods, one per line starting with its icon, in the order of "
    "[[payments.methods]] (put it on lines of its own)."
)
# Method buttons per row on the order card: two or three per row read better than one long row; five per row when
# that many rows would not fit next to the other buttons.
METHODS_PER_ROW: Final = 3

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


def placeholders(settings: Settings) -> tuple[str, ...]:
    """``{payment_methods}`` for the texts that may list the methods (none without ``payments.methods``)."""
    return (PLACEHOLDER,) if settings.payments.methods else ()


def placeholder_doc(settings: Settings) -> str:
    return PLACEHOLDER_DOC if settings.payments.methods else ""


def content_keys(settings: Settings) -> list[ContentKey]:
    methods = settings.payments.methods
    if not methods:
        return []
    g_methods = "Checkout: payment methods"
    g_down = "Checkout: payment method pop-up (a method that cannot be used yet)"
    g_staff = "Checkout: payment by a method our team handles"
    keys: list[ContentKey] = []
    for method in methods:
        keys += [
            ck(
                label_key(method.id),
                Kind.BUTTON_LABEL,
                FEATURE,
                g_methods,
                f"Name of the payment method '{method.id}': on its button in the order ticket and in the list of "
                "payment methods ({payment_methods}). Its icon is the method's icon in [[payments.methods]], shown "
                "before the name.",
            ),
            ck(
                description_key(method.id),
                Kind.FIELD_VALUE,
                FEATURE,
                g_methods,
                f"Optional one-line description of the payment method '{method.id}' (e.g. the cards it takes), shown "
                'under its name when it is a payment page (kind = "link") offered again privately.',
                required=False,
            ),
        ]
    keys += [
        ck(
            SECTION_HEADING,
            Kind.TITLE,
            FEATURE,
            g_methods,
            "Heading of the payment section on the order card while the order waits for the customer to choose how "
            "to pay (it may start with an icon), e.g. '{icon_wallet} CHOOSE HOW TO PAY'.",
        ),
        ck(
            LEAD,
            Kind.DESCRIPTION,
            FEATURE,
            g_methods,
            "Text under that heading; a button per payment method follows, e.g. 'Pick a payment method below.' "
            "Never say here which methods work right now: pressing a method that cannot be used yet explains it.",
        ),
        ck(
            LINK_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_methods,
            'Private reply to an older button of a method that is now a payment page (kind = "link"), above the link '
            "button. {method} is its name, {description} its description (empty when it has none).",
            placeholders=("method", "description"),
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
            'Title of the private pop-up shown when a customer presses a payment method that is status = "down", '
            "e.g. 'We're sorry!'. {method} is the method's name.",
            placeholders=_METHOD,
        ),
        ck(
            DOWN_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_down,
            "Text of that pop-up: which methods we accept at the moment. {available} is the names of the methods "
            'that are status = "active" (in bold, separated by commas), {method} the name of the method pressed. '
            "A button for each method in {available} follows.",
            placeholders=("method", "available"),
        ),
        ck(
            DOWN_HINT,
            Kind.DESCRIPTION,
            FEATURE,
            g_down,
            "Optional last line of the pop-up, e.g. 'Press a button below to pay.'",
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
                "Button in the pop-up that shows how to pay with a gift card (as pressing the gift card method on "
                "the order card does), next to Buy a gift card.",
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


def missing_icons(settings: Settings, icon_names: Collection[str] | None) -> list[PaymentMethod]:
    """The methods whose icon is not a picture in the icons folder (none while icons are off: ``icon_names`` None).
    Such a method shows without its mark, like any icon that is missing: the /setup report names it
    (``panel.publish``), and it never keeps checkout from being ready."""
    if icon_names is None:
        return []
    return [method for method in settings.payments.methods if method.icon and method.icon not in icon_names]


# -- what the customer sees ------------------------------------------------------------------------------------------


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def method_name(ui: Ui, method_id: str) -> str:
    """The method's name for sentences and titles: its label without an icon it may start with."""
    return strip_custom_emoji(_text(ui, label_key(method_id))) or method_id


def _icon(ui: Ui, method: PaymentMethod, *, label: bool) -> str:
    """The method's icon followed by a space, or nothing (no icon, or one that is not uploaded yet)."""
    if not method.icon:
        return ""
    return ui.content.render_icons(f"{{icon_{method.icon}}} ", label=label)


def button_label(ui: Ui, method: PaymentMethod) -> str:
    """The method's button label: its icon (which Discord shows as the button's emoji) and its name."""
    return _icon(ui, method, label=True) + method_name(ui, method.id)


def methods_list(ui: Ui, settings: Settings) -> str:
    """The ``{payment_methods}`` value: every method in the settings' order, one per line, its icon and name (on one
    line a narrow screen would wrap between an icon and its name)."""
    return "\n".join(_icon(ui, m, label=False) + method_name(ui, m.id) for m in settings.payments.methods)


def section_text(ui: Ui, settings: Settings) -> str | None:
    """The payment section of the order card while the customer chooses how to pay (None without
    ``payments.methods``): the heading and its lead. It never says which methods work: the buttons follow."""
    if not settings.payments.methods:
        return None
    return f"{heading(_text(ui, SECTION_HEADING))}\n{_text(ui, LEAD)}"


def active_methods(settings: Settings) -> list[PaymentMethod]:
    return [m for m in settings.payments.methods if m.active]


def gift_card_method(settings: Settings) -> PaymentMethod | None:
    """The method whose payment is the gift card provider's (its instructions and buttons belong to it)."""
    return next((m for m in settings.payments.methods if m.kind == "gift_card"), None)


def method_button(ui: Ui, order_id: int, method: PaymentMethod) -> ButtonSpec:
    """A method's choice button: a link to its page when it is an active payment page, otherwise a button that does
    what the method does (secondary: no method is pushed over another)."""
    label = button_label(ui, method)
    if method.active and method.kind == "link":
        return ButtonSpec(label=label, style="link", url=method.url)
    return ButtonSpec(label=label, custom_id=method_custom_id(order_id, method.id), style="secondary")


def _gift_card_buttons(ui: Ui, settings: Settings, order_id: int, method: PaymentMethod) -> list[ButtonSpec]:
    """Pay with gift card (the gift card how-to, as the method's button does) and Buy a gift card."""
    buttons = [
        ButtonSpec(label=_text(ui, GIFT_CARD_BUTTON), custom_id=method_custom_id(order_id, method.id), style="success")
    ]
    buy_url = settings.payments.gift_card.buy_url
    buy = ui.text(gift_card.BUY_BUTTON) if buy_url else None
    if buy_url and buy:
        buttons.append(ButtonSpec(label=buy, style="link", url=buy_url))
    return buttons


def down_message(ui: Ui, settings: Settings, order_id: int, method: PaymentMethod) -> MessageSpec:
    """The private pop-up of a method that is down: we're sorry, at the moment we accept the methods that are active
    (by name), with a button for each."""
    working = active_methods(settings)
    available = ", ".join(f"**{method_name(ui, active.id)}**" for active in working)
    name = method_name(ui, method.id)
    lines = [_text(ui, DOWN_DESCRIPTION, method=name, available=available)]
    hint = ui.text(DOWN_HINT)
    if hint:
        lines.append(hint)
    buttons: list[ButtonSpec] = []
    for active in working:
        if active.kind == "gift_card":
            buttons += _gift_card_buttons(ui, settings, order_id, active)
        else:
            buttons.append(method_button(ui, order_id, active))
    embed = ui.embed(title=_text(ui, DOWN_TITLE, method=name), description="\n".join(lines), tone="info")
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
    line = _text(ui, LINK_LINE, method=method_name(ui, method.id), description=description).strip()
    embed = ui.embed(description=line, tone="info")
    button = ButtonSpec(label=button_label(ui, method), style="link", url=method.url)
    return MessageSpec(embeds=(embed,), rows=rows_of(button), banner=ui.banner("general-response"))


def _method_rows(buttons: Sequence[ButtonSpec], free_rows: int) -> tuple[tuple[Component, ...], ...]:
    """The method buttons in rows of about the same length (at most ``METHODS_PER_ROW``), or five per row when that
    many rows do not fit into ``free_rows``."""
    if not buttons:
        return ()
    count = -(-len(buttons) // METHODS_PER_ROW)
    return rows_of(*buttons, per_row=-(-len(buttons) // count) if count <= free_rows else 5)


def payment_rows(
    ui: Ui,
    settings: Settings,
    order_id: int,
    provider_buttons: Sequence[ButtonSpec],
    cancel: ButtonSpec | None,
    *,
    choosing: bool,
) -> tuple[tuple[ButtonSpec, ...], ...]:
    """The buttons of an order that waits for payment, Cancel order alone last. While the customer chooses how to pay
    (``choosing``): a button per method in the settings' order, after the provider's own buttons unless those belong to
    a method (the gift card's Submit gift card and Buy a gift card are in its how-to). Otherwise (no methods, or the
    customer is paying with the provider, e.g. a gift card was submitted): the provider's buttons."""
    picker = choosing and bool(settings.payments.methods)
    provider = () if picker and gift_card_method(settings) is not None else tuple(provider_buttons)
    provider_rows = rows_of(*provider)
    cancel_rows = rows_of(cancel)
    methods = [method_button(ui, order_id, method) for method in settings.payments.methods] if picker else []
    rows = (
        *provider_rows,
        *_method_rows(methods, ACTION_ROWS - len(provider_rows) - len(cancel_rows)),
        *cancel_rows,
    )
    return tuple(tuple(b for b in row if isinstance(b, ButtonSpec)) for row in rows)
