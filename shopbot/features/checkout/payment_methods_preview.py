"""Preview states of the payment methods (only with ``payments.methods``): the pop-up of a method that is down, the
private gift card how-to, the ticket post and replies of a method our team handles, the reply for a payment page, the
errors, and the order card once methods are back (an example configuration). Built by the runtime view functions from
synthetic sample data. Called by ``preview.preview``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from shopbot.features.checkout import models as m
from shopbot.features.checkout import payment_methods as pm
from shopbot.features.checkout import views
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.config.settings import PaymentMethod, Settings
    from shopbot.context import App
    from shopbot.features.checkout.payments import PaymentInstructions

SAMPLE_PAYMENT_PAGE = "https://example.com/pay"

Add = Callable[[str, str, Callable[[], MessageSpec | ModalSpec | None]], None]


def _back(settings: Settings) -> Settings:
    """The shipped methods with the first one back as a payment page and the second handled by our team."""
    methods: list[PaymentMethod] = []
    others = [method for method in settings.payments.methods if method.kind != "gift_card"]
    for method in settings.payments.methods:
        if others and method is others[0]:
            method = replace(method, status="active", kind="link", url=SAMPLE_PAYMENT_PAGE)
        elif len(others) > 1 and method is others[1]:
            method = replace(method, status="active", kind="staff")
        methods.append(method)
    return replace(settings, payments=replace(settings.payments, methods=tuple(methods)))


def add_states(app: App, add: Add, pending: m.OrderView, instructions: PaymentInstructions | None) -> None:
    """``pending``: a sample order waiting for payment."""
    ui, settings = app.ui, app.settings
    methods = settings.payments.methods
    if not methods:
        return
    order = pending.order
    down = next((method for method in methods if not method.active), None)
    if down is not None:
        add(
            "payment_method_down",
            f"Private pop-up when a customer presses a payment method that is down ('{down.id}'): the methods we "
            "accept at the moment, each with its button. The only place a customer learns a method cannot be used.",
            lambda: pm.down_message(ui, settings, order.id, down),
        )
    if instructions is not None and pm.gift_card_method(settings) is not None:
        add(
            "payment_gift_card_howto",
            "Private how-to when the customer presses the gift card method (or Pay with gift card in the pop-up): "
            "the steps for the exact amount, Submit gift card and Buy a gift card.",
            lambda: views.payment_howto(ui, settings, order, instructions),
        )
    staff = next((method for method in methods if method.kind == "staff"), None)
    if staff is not None:
        add(
            "payment_method_staff_post",
            "Posted in the order ticket when the customer chooses a method our team handles (kind 'staff', once it "
            "is active); the staff get an alert in the staff channel.",
            lambda: pm.staff_method_post(ui, staff, order.user_id),
        )
        add(
            "payment_method_staff_reply",
            "Private reply after choosing a method our team handles.",
            lambda: pm.reply(ui, pm.STAFF_REPLY, staff),
        )
        add(
            "payment_method_staff_already",
            "Private reply when the customer chooses that method again.",
            lambda: pm.reply(ui, pm.STAFF_ALREADY, staff),
        )
    back = _back(settings)
    link = next((method for method in back.payments.methods if method.kind == "link"), None)
    if link is not None:
        add(
            "payment_method_link_reply",
            "Private reply to an older button of a method that is now a payment page (kind 'link').",
            lambda: pm.link_message(ui, link),
        )
    if instructions is not None and back != settings:
        add(
            "order_submitted_methods_back",
            "Example: the order card once methods are back (the first as a payment page, the second handled by our "
            "team).",
            lambda: views.order_message(ui, back, pending, instructions=instructions),
        )
    add(
        "error_payment_method_unavailable",
        "Error: a payment-method button that is no longer offered (the settings changed).",
        lambda: views.error_message(ui, pm.ERROR_UNAVAILABLE),
    )
    add(
        "error_payment_method_state",
        "Error: a payment-method button of an order that no longer waits for payment.",
        lambda: views.error_message(ui, pm.ERROR_STATE),
    )
