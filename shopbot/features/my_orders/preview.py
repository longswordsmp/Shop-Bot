"""Preview: every state of the ``/myorders`` reply, built by the runtime view functions from synthetic orders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.checkout import models as m
from shopbot.features.checkout import preview as checkout_preview
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders import views
from shopbot.features.my_orders.repo import CustomerOrder
from shopbot.ui.spec import MessageSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.config.settings import Settings
    from shopbot.context import App

SAMPLE_USER_ID = checkout_preview.SAMPLE_USER_ID
SAMPLE_CHANNEL_ID = checkout_preview.SAMPLE_CHANNEL_ID
# Tracking details are typed by staff: the longest sample uses a long synthetic text.
SAMPLE_TRACKING = "[sample tracking details]"
LONG_TRACKING = ("[sample tracking details] " * 40).strip()


def sample_orders(settings: Settings, statuses: list[str], *, tracking: str | None = None) -> list[CustomerOrder]:
    """Synthetic orders (newest first) in ``statuses``, each with the timeline leading to its status; open orders
    link their ticket."""
    products = checkout_preview._products(2)
    lines = (m.CartLine(products[0], 2), m.CartLine(products[1], 1))
    items: list[CustomerOrder] = []
    for index, status in enumerate(statuses):
        view = checkout_preview._order(settings, status, lines)
        order_id = 100 + len(statuses) - index
        order = replace(view.order, id=order_id, number=checkout_preview.SAMPLE_ORDER_NUMBER + order_id)
        items.append(
            CustomerOrder(
                order=order,
                timeline=checkout_preview.sample_timeline(status, order_id),
                channel_id=SAMPLE_CHANNEL_ID if status in m.OPEN_STATUSES else None,
                tracking=tracking if status in (m.FULFILLED, m.REFUNDED) else None,
            )
        )
    return items


def preview(app: App) -> list[PreviewState]:
    ui, settings = app.ui, app.settings
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=k.FEATURE, name=name, note=note, spec=spec))

    def message(items: list[CustomerOrder], page: int = 0) -> Callable[[], MessageSpec]:
        return lambda: views.my_orders_message(ui, settings, items, page=page, user_id=SAMPLE_USER_ID)

    add("my_orders_empty", "/myorders: the member has no order yet.", lambda: views.empty_message(ui))
    few = sample_orders(settings, [m.PENDING, m.FULFILLED], tracking=SAMPLE_TRACKING)
    add(
        "my_orders_single_page",
        "/myorders: a member with an order waiting for payment (its ticket linked) and a fulfilled order with "
        "tracking details (one page, no paging buttons).",
        message(few),
    )
    per_page = settings.my_orders.orders_per_page
    many = sample_orders(settings, [list(m.ORDER_STATUSES)[i % len(m.ORDER_STATUSES)] for i in range(per_page * 2 + 1)])
    add("my_orders_first_page", "/myorders: first of several pages (previous disabled).", message(many, 0))
    add("my_orders_middle_page", "/myorders: a middle page (both paging buttons).", message(many, 1))
    add("my_orders_last_page", "/myorders: last page (next disabled).", message(many, 99))
    largest = sample_orders(settings, [m.REFUNDED] * per_page, tracking=LONG_TRACKING)
    add(
        "my_orders_largest_page",
        "/myorders: a full page of orders with the longest timeline and long tracking details (orders that do not "
        "fit one message next to the configured text move to the next page).",
        message(largest),
    )
    return states
