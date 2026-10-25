"""Content keys of ``/myorders``: the customer's private list of their own orders.

Stage names and the timeline line format are the checkout feature's (``checkout.ticket.stage.*``,
``checkout.ticket.timeline_entry``), so an order's stages read the same in its ticket and here. Prices use
``common.money_format``; paging uses the shared previous/next buttons and the optional page indicator.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features import core_keys as core

FEATURE = "my_orders"
ROUTE = "mo"
COMMAND_NAME = "myorders"

COMMAND_DESCRIPTION = "my_orders.command.description"
LIST_TITLE = "my_orders.list.title"
LIST_DESCRIPTION = "my_orders.list.description"
EMPTY = "my_orders.empty.description"
ORDER_TITLE = "my_orders.order.title"
ORDER_NUMBER_FIELD = "my_orders.order.number_field"
ORDER_STATUS_FIELD = "my_orders.order.status_field"
ORDER_TOTAL_FIELD = "my_orders.order.total_field"
ORDER_TIMELINE_FIELD = "my_orders.order.timeline_field"
ORDER_TICKET_FIELD = "my_orders.order.ticket_field"
ORDER_TRACKING_FIELD = "my_orders.order.tracking_field"

ORDER_PLACEHOLDERS = ("order_number", "status", "total", "date")


def content_keys(settings: Settings) -> list[ContentKey]:
    del settings
    group = "My orders (/myorders, private)"
    order_doc = (
        "{order_number} is the order number, {status} its current stage name (checkout.ticket.stage.*), {total} the "
        "total (common.money_format), {date} the date it was submitted (shown by Discord in the reader's time zone)."
    )
    return [
        ck(
            COMMAND_DESCRIPTION,
            Kind.COMMAND_DESCRIPTION,
            FEATURE,
            group,
            "Description of the /myorders command shown in Discord's command list (at most 100 characters). The "
            "command is registered only when features.my_orders is on and this text is set; Discord receives it when "
            "the bot starts (a change needs a restart: /reload does not update it).",
        ),
        ck(
            LIST_TITLE,
            Kind.TITLE,
            FEATURE,
            group,
            "Title of the private /myorders reply (also when the member has no order yet). {count} is the number of "
            "the member's orders.",
            placeholders=("count",),
        ),
        ck(
            LIST_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            group,
            "Optional text under the /myorders title when the member has orders. {count} is their number. One card "
            "per order follows (newest first), with previous/next buttons when there is more than one page.",
            required=False,
            placeholders=("count",),
        ),
        ck(
            EMPTY,
            Kind.DESCRIPTION,
            FEATURE,
            group,
            "Text of the /myorders reply when the member has no order yet.",
        ),
        ck(
            ORDER_TITLE,
            Kind.TITLE,
            FEATURE,
            group,
            f"Title of each order's card in /myorders. {order_doc}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            ORDER_NUMBER_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Name of the field showing the order number (the order's reference) on each order card.",
        ),
        ck(
            ORDER_STATUS_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Name of the field showing the order's current stage (checkout.ticket.stage.*) on each order card.",
        ),
        ck(
            ORDER_TOTAL_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Name of the field showing the order total (common.money_format) on each order card.",
        ),
        ck(
            ORDER_TIMELINE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Name of the field listing the order's stages so far on each order card (each written with "
            "checkout.ticket.timeline_entry).",
        ),
        ck(
            ORDER_TICKET_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Optional name of a field linking the order's ticket while it is still open (omitted when empty).",
            required=False,
        ),
        ck(
            ORDER_TRACKING_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Optional name of the field showing the tracking details staff recorded for the order (shown only when "
            "there are tracking details; omitted when empty).",
            required=False,
        ),
    ]


def shared_keys(settings: Settings) -> list[str]:
    """Prices and the paging buttons."""
    del settings
    return [core.MONEY_FORMAT, core.BUTTON_PREV, core.BUTTON_NEXT]
