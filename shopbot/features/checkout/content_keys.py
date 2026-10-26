"""Content keys, shared keys, and settings checks of the checkout feature.

Every customer-facing string of the purchasing workflow is declared here (plus the payment provider's keys,
declared by the configured provider). The staff order-control message and every customer notification are
business text too, so they are content keys as well. Keys that are only needed when a setting is on (fulfillment
options, forms, fees, minimum order total, credits, timers) are required only under that condition.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import CheckoutSettings, FormField, Settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import discounts, payment_methods, staff_keys
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import PROVIDERS
from shopbot.features.server_keys import channel_placeholder, private_channel_problems, reference_problems

FEATURE = "checkout"
ROUTE = "co"
STAFF_ROUTE = "cs"
PANEL_KEY = "checkout:panel"

# -- shop panel -------------------------------------------------------------------------------------
PANEL_TITLE = "checkout.panel.title"
PANEL_DESCRIPTION = "checkout.panel.description"
BUTTON_START = "checkout.button.start"

# -- order channel ----------------------------------------------------------------------------------
CHANNEL_PREFIX = "checkout.channel_prefix"
CHANNEL_TOPIC = "checkout.channel_topic"

# -- private replies to the start button ------------------------------------------------------------
STARTED = "checkout.started.description"
SESSION_EXISTS = "checkout.session_exists.description"
LIMIT_REACHED = "checkout.limit_reached.description"
BUY_ADDED = "checkout.buy_added.description"
ORDER_EXISTS = "checkout.order_exists.description"
BUTTON_OPEN_TICKET = "checkout.ticket_link.button"

# -- the order ticket: introduction and the parts every state of the live panel shows -------------------
TICKET_INTRO_TITLE = "checkout.ticket.intro_title"
TICKET_INTRO_DESCRIPTION = "checkout.ticket.intro_description"
# The key facts, one line at the top of every card of the ticket: the ticket alone (introduction, closed panel), with
# the status while the order is put together, with the order number and status once it was placed.
TICKET_REFERENCE_LINE = "checkout.ticket.reference_line"
TICKET_DRAFT_LINE = "checkout.ticket.draft_line"
TICKET_ORDER_LINE = "checkout.ticket.order_line"
TICKET_STATUS_IN_PROGRESS = "checkout.ticket.status_in_progress"

# -- receipt sections of the live panel (items, summary, progress tracker) ---------------------------------
ITEMS_HEADING = "checkout.cart.heading"
SUMMARY_HEADING = "checkout.summary.heading"
SUMMARY_LINE = "checkout.summary.line"
SUMMARY_TOTAL_LINE = "checkout.summary.total_line"
SUMMARY_CREDIT_LINE = "checkout.summary.credit_line"
SUMMARY_FREE = "checkout.summary.free"
PROGRESS_HEADING = "checkout.progress.heading"
PROGRESS_START = "checkout.progress.start"
PROGRESS_DONE = "checkout.progress.done"
PROGRESS_CURRENT = "checkout.progress.current"
PROGRESS_NEXT = "checkout.progress.next"
PROGRESS_STOPPED = "checkout.progress.stopped"

# -- submission form (checkout.submission_form, collected when the customer presses Submit) ---------------
SUBMISSION_MODAL_TITLE = "checkout.submission.modal_title"

# -- workflow: browse ---------------------------------------------------------------------------------
BROWSE_TITLE = "checkout.browse.title"
BROWSE_DESCRIPTION = "checkout.browse.description"
BROWSE_EMPTY_CATALOG = "checkout.browse.empty_catalog"
BROWSE_SELECT_PLACEHOLDER = "checkout.browse.select_placeholder"

# -- workflow: cart -----------------------------------------------------------------------------------
CART_TITLE = "checkout.cart.title"
CART_DESCRIPTION = "checkout.cart.description"
CART_FIELD = "checkout.cart.field"
CART_LINE = "checkout.cart.line"
CART_EMPTY = "checkout.cart.empty"
CART_SUBTOTAL_FIELD = "checkout.cart.subtotal_field"
CART_EDIT_PLACEHOLDER = "checkout.cart.edit_placeholder"
CART_EDIT_OPTION = "checkout.cart.edit_option"
CART_MINIMUM_NOT_MET = "checkout.cart.minimum_not_met"

# -- workflow: fulfillment ----------------------------------------------------------------------------
FULFILLMENT_TITLE = "checkout.fulfillment_step.title"
FULFILLMENT_DESCRIPTION = "checkout.fulfillment_step.description"
FULFILLMENT_SELECT_PLACEHOLDER = "checkout.fulfillment_step.select_placeholder"
FULFILLMENT_FEE_FIELD = "checkout.fulfillment_step.fee_field"
EXTRAS_HEADING = "checkout.extras.heading"
EXTRA_LINE = "checkout.extras.line"
EXTRA_STATUS_ON = "checkout.extras.status_on"
EXTRA_STATUS_OFF = "checkout.extras.status_off"
EXTRA_BUTTON_ON = "checkout.extras.button_on"
EXTRA_BUTTON_OFF = "checkout.extras.button_off"

# -- workflow: details --------------------------------------------------------------------------------
DETAILS_TITLE = "checkout.details.title"
DETAILS_DESCRIPTION = "checkout.details.description"
DETAILS_MODAL_TITLE = "checkout.details.modal_title"
BUTTON_ENTER_DETAILS = "checkout.button.enter_details"
BUTTON_ENTER_MORE_DETAILS = "checkout.button.enter_more_details"

# -- workflow: shipping address (checkout.ask_address) -------------------------------------------------
ADDRESS_REGION_PLACEHOLDER = "checkout.address.region_placeholder"
ADDRESS_COUNTRY_PLACEHOLDER = "checkout.address.country_placeholder"
ADDRESS_BUTTON = "checkout.address.button"
ADDRESS_BUTTON_CHANGE = "checkout.address.button_change"
ADDRESS_MODAL_TITLE = "checkout.address.modal_title"
ADDRESS_HEADING = "checkout.address.heading"
ADDRESS_COUNTRY_LINE = "checkout.address.country_line"
ADDRESS_COUNTRY_NONE = "checkout.address.country_none"
ADDRESS_ADDED_LINE = "checkout.address.added_line"
ADDRESS_MISSING_LINE = "checkout.address.missing_line"
ADDRESS_SHIP_TO = "checkout.address.ship_to"
ADDRESS_COUNTRY_LABEL = "checkout.address.country_label"
ERROR_AREA_EXCLUDED = "checkout.error.area_excluded"
ERROR_COUNTRY_FIRST = "checkout.error.country_first"

# -- workflow: review ---------------------------------------------------------------------------------
REVIEW_TITLE = "checkout.review.title"
REVIEW_DESCRIPTION = "checkout.review.description"
REVIEW_TOTAL_FIELD = "checkout.review.total_field"
REVIEW_CREDITS_FIELD = "checkout.review.credits_field"
REVIEW_CREDITS_AVAILABLE_FIELD = "checkout.review.credits_available_field"
REVIEW_PRICE_CHANGED = "checkout.review.price_changed"
BUTTON_APPLY_CREDITS = "checkout.button.apply_credits"
BUTTON_REMOVE_CREDITS = "checkout.button.remove_credits"
BUTTON_SUBMIT = "checkout.button.submit"

# -- workflow: discount codes on the review step (checkout.discount_codes) ---------------------------------
DISCOUNT_BUTTON_ADD = "checkout.discount.button_add"
DISCOUNT_BUTTON_USE = "checkout.discount.button_use"
DISCOUNT_BUTTON_REMOVE = "checkout.discount.button_remove"
DISCOUNT_MODAL_TITLE = "checkout.discount.modal_title"
DISCOUNT_INPUT_LABEL = "checkout.discount.input_label"
DISCOUNT_INPUT_PLACEHOLDER = "checkout.discount.input_placeholder"
SUMMARY_DISCOUNT_LINE = "checkout.summary.discount_line"
SUMMARY_BOGO_LINE = "checkout.summary.bogo_line"
SUMMARY_DISCOUNT_NOTE = "checkout.summary.discount_note"
SUMMARY_DISCOUNT_OFF_LINE = "checkout.summary.discount_off_line"
DISCOUNT_TOO_MANY_TRIES = "checkout.discount.error.too_many_tries"
# -- claimed coupons (the claim button of an announcement; private replies) ------------------------------------
CLAIM_TITLE = "checkout.discount.claim.title"
CLAIM_DESCRIPTION = "checkout.discount.claim.description"
CLAIM_HEADING = "checkout.discount.claim.heading"
CLAIM_HOWTO_HEADING = "checkout.discount.claim.howto_heading"
CLAIM_HOWTO = "checkout.discount.claim.howto"
CLAIM_ENDED_TITLE = "checkout.discount.claim.ended_title"
CLAIM_ENDED = "checkout.discount.claim.ended"
TERMS_PERCENT = "checkout.discount.terms.percent"
TERMS_AMOUNT = "checkout.discount.terms.amount"
TERMS_BOGO = "checkout.discount.terms.bogo"
TERMS_PRODUCTS = "checkout.discount.terms.products"
TERMS_MINIMUM = "checkout.discount.terms.minimum"
TERMS_EXPIRES = "checkout.discount.terms.expires"
TERMS_ONCE = "checkout.discount.terms.once"

# -- quantity form ------------------------------------------------------------------------------------
QUANTITY_TITLE = "checkout.quantity.title"
QUANTITY_LABEL = "checkout.quantity.label"
QUANTITY_DESCRIPTION = "checkout.quantity.description"
QUANTITY_PLACEHOLDER = "checkout.quantity.placeholder"

# -- cancelling ---------------------------------------------------------------------------------------
CANCEL_CONFIRM_TITLE = "checkout.cancel_confirm.title"
CANCEL_CONFIRM_DESCRIPTION = "checkout.cancel_confirm.description"
CANCELLED_TITLE = "checkout.cancelled.title"
CANCELLED_DESCRIPTION = "checkout.cancelled.description"
EXPIRED_TITLE = "checkout.expired.title"
EXPIRED_DESCRIPTION = "checkout.expired.description"
STAFF_CLOSED_TITLE = "checkout.staff_closed.title"
STAFF_CLOSED_DESCRIPTION = "checkout.staff_closed.description"
ORDER_CANCEL_CONFIRM_TITLE = "checkout.order_cancel_confirm.title"
ORDER_CANCEL_CONFIRM_DESCRIPTION = "checkout.order_cancel_confirm.description"
BUTTON_CANCEL_ORDER = "checkout.button.cancel_order"
BUTTON_CANCEL_YES = "checkout.button.cancel_yes"
BUTTON_KEEP_ORDER = "checkout.button.keep_order"

# -- submitted order ----------------------------------------------------------------------------------
SUBMITTED_TITLE = "checkout.submitted.title"
SUBMITTED_DESCRIPTION = "checkout.submitted.description"

# -- staff order controls (business text posted in checkout.staff_channel) ------------------------------
STAFF_TITLE = "checkout.staff.title"
STAFF_DESCRIPTION = "checkout.staff.description"
STAFF_REFUNDED_FIELD = "checkout.staff.refunded_field"
STAFF_DETAILS_TITLE = "checkout.staff.details_title"
STAFF_BUTTON_MARK_PAID = "checkout.staff.button.mark_paid"
STAFF_BUTTON_REJECT = "checkout.staff.button.reject"
STAFF_BUTTON_CANCEL = "checkout.staff.button.cancel"
STAFF_BUTTON_PROCESSING = "checkout.staff.button.processing"
STAFF_BUTTON_FULFILLED = "checkout.staff.button.fulfilled"
STAFF_BUTTON_SHIPPED = "checkout.staff.button.shipped"
STAFF_BUTTON_REFUND = "checkout.staff.button.refund"
STAFF_BUTTON_REFUND_CREDIT = "checkout.staff.button.refund_credit"
STAFF_BUTTON_RETURN_CREDIT = "checkout.staff.button.return_credit"
STAFF_CREDIT_RETURNED_FIELD = "checkout.staff.credit_returned_field"

# -- errors (private) -----------------------------------------------------------------------------------
ERROR_SESSION_CLOSED = "checkout.error.session_closed"
ERROR_OUT_OF_STOCK = "checkout.error.out_of_stock"
ERROR_QUANTITY_INVALID = "checkout.error.quantity_invalid"
ERROR_CART_FULL = "checkout.error.cart_full"
ERROR_PRODUCT_UNAVAILABLE = "checkout.error.product_unavailable"
ERROR_ORDER_NOT_CANCELLABLE = "checkout.error.order_not_cancellable"

# -- notification kinds (besides the statuses) ----------------------------------------------------------
NOTIFY_PAYMENT_REMINDER = "payment_reminder"
NOTIFY_REFUND = "refund"
# "Submission received": the order was submitted and waits for payment.
NOTIFY_SUBMITTED = m.PENDING
# Warning in the order ticket before an unfinished order (cart) expires.
NOTIFY_SESSION_WARNING = "session_expiry_warning"
# Every status change notifies the customer (a partial refund, which changes no status, has its own notification).
NOTIFIED_STATUSES = (
    m.PAYMENT_SUBMITTED,
    m.PAID,
    m.PROCESSING,
    m.SHIPPED,
    m.FULFILLED,
    m.CANCELLED,
    m.REJECTED,
    m.EXPIRED,
    m.REFUNDED,
)

ORDER_PLACEHOLDERS = ("order_number", "total")
NOTIFY_PLACEHOLDERS = ("order_number", "total", "channel_mention")
# Statuses whose view and notification point to Report a problem on the shop panel: their texts may link the
# place-order channel (``purchase_placeholder``).
REPORT_STATUSES = (m.FULFILLED, m.CANCELLED, m.REFUNDED)
# One date per stage of the progress tracker, as Discord timestamps (each reader's own time zone and language).
PROGRESS_PLACEHOLDERS = ("stage", "date", "time", "relative")
_PROGRESS_DOC = (
    "{stage} is the stage name (checkout.ticket.stage.*, or checkout.progress.start for the first one), {date} the "
    "date it was reached, {time} the date and time, {relative} the same as a relative time (e.g. '2 hours ago'); use "
    "one of them."
)
STAFF_TITLE_PLACEHOLDERS = ("order_number", "total", "status")
STAFF_PLACEHOLDERS = ("order_number", "user_mention", "channel_mention", "total", "status")

_ORDER_DOC = "{order_number} is the order number, {total} the order total written with common.money_format."
_STATUS_DOC = {
    m.PENDING: "waiting for payment",
    m.PAYMENT_SUBMITTED: "the customer submitted their payment; staff are verifying it",
    m.PAID: "staff confirmed the payment",
    m.PROCESSING: "staff are preparing the order",
    m.SHIPPED: "the order was shipped (carrier and tracking number entered with Mark shipped)",
    m.FULFILLED: "the order was delivered",
    m.CANCELLED: "the order was cancelled (by the customer before paying, or by staff)",
    m.REJECTED: "staff rejected the order before payment",
    m.EXPIRED: "the order was not paid within checkout.payment_expiry_minutes",
    m.REFUNDED: "the order was fully refunded",
}


def form_field_key(field_id: str, name: str) -> str:
    """Key of a ``checkout.details_form`` field, e.g. ``checkout.form.address.label``."""
    return f"checkout.form.{field_id}.{name}"


def option_key(option_id: str, name: str) -> str:
    """Key of a fulfillment option, e.g. ``checkout.fulfillment.pickup.label``."""
    return f"checkout.fulfillment.{option_id}.{name}"


def option_form_key(option_id: str, field_id: str, name: str) -> str:
    """Key of a fulfillment option's form field, e.g. ``checkout.fulfillment.pickup.form.time.label``."""
    return f"checkout.fulfillment.{option_id}.form.{field_id}.{name}"


def fee_label_key(fee_id: str) -> str:
    """The label of an order fee: a configured fee's, or (fee id ``x.<extra>``) the chosen extra's."""
    if fee_id.startswith("x."):
        return extra_key(fee_id.removeprefix("x."), "label")
    return f"checkout.fee.{fee_id}.label"


def address_region_key(group_id: str) -> str:
    """Label of a region (``[[shipping.groups]]``) in the address step's region menu."""
    return f"checkout.address.menu.{group_id}"


def address_field_key(field_id: str, name: str) -> str:
    """Key of a shipping address form field, e.g. ``checkout.address.street.label``."""
    return f"checkout.address.{field_id}.{name}"


def extra_key(extra_id: str, name: str) -> str:
    """Key of an extra, e.g. ``checkout.extra.protection.label``."""
    return f"checkout.extra.{extra_id}.{name}"


def status_title_key(status: str) -> str:
    return f"checkout.status.{status}.title"


def status_description_key(status: str) -> str:
    return f"checkout.status.{status}.description"


def status_label_key(status: str) -> str:
    return f"checkout.status_label.{status}"


def notify_title_key(kind: str) -> str:
    return f"checkout.notify.{kind}.title"


def notify_description_key(kind: str) -> str:
    return f"checkout.notify.{kind}.description"


def stage_key(status: str) -> str:
    """Customer-facing name of an order status: the live panel's status field, the order timeline, /myorders."""
    return f"checkout.ticket.stage.{status}"


def submission_field_key(field_id: str, name: str) -> str:
    """Key of a ``checkout.submission_form`` field, e.g. ``checkout.submission.form.reference.label``."""
    return f"checkout.submission.form.{field_id}.{name}"


def discount_error_key(reason: str) -> str:
    """The customer text for a discount code that does not apply (``discounts.REASONS``), e.g.
    ``checkout.discount.error.expired``."""
    return f"checkout.discount.error.{reason}"


def catalog_placeholder(settings: Settings) -> tuple[str, ...]:
    """The placeholder that links the catalog channel (``catalog.listing_channel``, e.g. ``channel_catalog``) as a
    clickable #channel; none while no listing channel is set."""
    channel = settings.catalog.listing_channel
    return (channel_placeholder(channel),) if channel else ()


def purchase_placeholder(settings: Settings) -> tuple[str, ...]:
    """The placeholder that links the place-order channel (the shop panel's, e.g. ``channel_purchase``), as a
    clickable #channel in descriptions; none while ``checkout.panel_channel`` is not set."""
    channel = settings.checkout.panel_channel
    return (channel_placeholder(channel),) if channel else ()


def session_warning_enabled(settings: Settings) -> bool:
    """The expiry warning is on and can be sent before the unfinished order expires."""
    c = settings.checkout
    return 0 < c.session_expiry_warning_minutes < c.session_expiry_minutes


def credits_possible(settings: Settings) -> bool:
    """Whether checkout can offer to apply credits (the credits feature is switched on and allows it)."""
    return settings.features.credits and settings.credits.allow_at_checkout


def any_form(settings: Settings) -> bool:
    c = settings.checkout
    return bool(c.details_form) or any(o.form for o in c.fulfillment_options)


def needs_second_details_form(settings: Settings) -> bool:
    """More than 5 inputs (details form + an option's form) need a second form."""
    c = settings.checkout
    base = len(c.details_form)
    return any(base + len(o.form) > 5 for o in c.fulfillment_options)


def _form_keys(
    group: str, fields: tuple[FormField, ...], key: Callable[[str, str], str], *, form: str = "order details form"
) -> list[ContentKey]:
    keys: list[ContentKey] = []
    for field in fields:
        f = field.id
        keys += [
            ck(
                key(f, "label"),
                Kind.INPUT_LABEL,
                FEATURE,
                group,
                f"Label of the '{f}' field in the {form} (also the field name under which the "
                "answer is shown to the customer and to staff).",
            ),
            ck(
                key(f, "description"),
                Kind.INPUT_DESCRIPTION,
                FEATURE,
                group,
                f"Optional help text under the '{f}' field label.",
                required=False,
            ),
            ck(
                key(f, "placeholder"),
                Kind.INPUT_PLACEHOLDER,
                FEATURE,
                group,
                f"Optional greyed-out example text inside the empty '{f}' field.",
                required=False,
            ),
        ]
    return keys


def content_keys(settings: Settings) -> list[ContentKey]:
    c = settings.checkout
    options = bool(c.fulfillment_options)
    forms = any_form(settings)
    credits = credits_possible(settings)
    g_panel = "Checkout: shop panel"
    g_channel = "Checkout: private order channel"
    g_replies = "Checkout: private replies to the start button"
    g_browse = "Checkout: order workflow - choosing products"
    g_cart = "Checkout: order workflow - cart"
    g_qty = "Checkout: quantity form"
    g_ful = "Checkout: order workflow - fulfillment option"
    g_details = "Checkout: order workflow - order details form"
    g_review = "Checkout: order workflow - review and submit"
    g_cancel = "Checkout: cancelling"
    g_submitted = "Checkout: submitted order (order channel)"
    g_status = "Checkout: order status views (order channel)"
    g_labels = "Checkout: order status names"
    g_notify = "Checkout: customer notifications (direct message, or the order channel)"
    g_staff = "Checkout: staff order controls (staff channel)"
    g_errors = "Checkout: error messages (private)"
    g_ticket = "Checkout: order ticket (introduction, and the ticket, order number and status on the live panel)"
    g_receipt = "Checkout: order ticket - items, summary and progress (live panel)"
    g_stages = "Checkout: order stage names (live panel, progress tracker, /myorders)"
    keys: list[ContentKey] = [
        ck(PANEL_TITLE, Kind.TITLE, FEATURE, g_panel, "Title of the shop panel (the message with the start button)."),
        ck(PANEL_DESCRIPTION, Kind.DESCRIPTION, FEATURE, g_panel, "Text of the shop panel."),
        ck(
            BUTTON_START,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_panel,
            "Button on the shop panel that opens a private order channel for the customer. The panel also "
            "shows the catalog's browse button (catalog.button.browse) and the credits balance button when "
            "those are configured.",
        ),
        ck(
            CHANNEL_PREFIX,
            Kind.CHANNEL_PREFIX,
            FEATURE,
            g_channel,
            "Channel-name prefix of private order channels ({prefix} in tickets.channel_name_pattern; the "
            "number is the shared ticket/order-channel counter). Discord lower-cases channel names.",
        ),
        ck(
            CHANNEL_TOPIC,
            Kind.CHANNEL_TOPIC,
            FEATURE,
            g_channel,
            "Optional topic of each order channel. {user_name} is the customer's display name.",
            required=False,
            placeholders=("user_name",),
        ),
        ck(
            STARTED,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply after the start button created the customer's order channel. {channel_mention} "
            "links the channel.",
            placeholders=("channel_mention",),
        ),
        ck(
            SESSION_EXISTS,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply when the customer clicks start while they already have an unfinished order "
            "channel; no second channel is created. {channel_mention} links the existing channel.",
            placeholders=("channel_mention",),
        ),
        ck(
            LIMIT_REACHED,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply when the customer already has the maximum number of open orders (waiting for "
            "payment, paid, or in progress). {limit} is checkout.max_open_orders_per_user.",
            placeholders=("limit",),
        ),
        ck(
            BUY_ADDED,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Optional: private reply when the customer clicks a product's buy button (catalog.button.buy) while "
            "they already have an unfinished order channel; the product is now in its cart (one unit, or the "
            "quantity already chosen). {name} is the product name, {channel_mention} links the channel. If empty, "
            "checkout.session_exists.description is shown. A buy button that opens a new order channel answers "
            "with checkout.started.description.",
            placeholders=("name", "channel_mention"),
            fallback=SESSION_EXISTS,
        ),
        ck(
            ORDER_EXISTS,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply when the customer clicks start while they already have the maximum number of open orders "
            "(checkout.max_open_orders_per_user) and one of them still has its order ticket open: no new ticket is "
            "created, the reply links the existing one. {channel_mention} links that ticket, {order_number} is its "
            "order number, {limit} the maximum (checkout.limit_reached.description is used when no open ticket "
            "can be linked).",
            placeholders=("channel_mention", "order_number", "limit"),
        ),
        ck(
            BUTTON_OPEN_TICKET,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_replies,
            "Optional link button under the private replies to the start button (new ticket, unfinished ticket, "
            "existing open order) that opens the customer's order ticket. Omitted when empty.",
            required=False,
        ),
        ck(
            TICKET_INTRO_TITLE,
            Kind.TITLE,
            FEATURE,
            g_ticket,
            "Optional title of the first message in every order ticket. {ticket_number} is the ticket number.",
            required=False,
            placeholders=("ticket_number",),
        ),
        ck(
            TICKET_INTRO_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_ticket,
            "First message in every order ticket, posted before the live panel: identifies the ticket and explains "
            "that only the customer and authorized staff can see it. {ticket_number} is the ticket number (it is also "
            "shown in checkout.ticket.reference_line above the text), {user_mention} mentions the customer (no ping).",
            placeholders=("ticket_number", "user_mention"),
        ),
        ck(
            TICKET_REFERENCE_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            "First line of the ticket introduction and of the closed panel of an order that was never placed: the "
            "ticket's unique reference, e.g. '{icon_ticket} Ticket **#{ticket_number}**'. {ticket_number} is the "
            "ticket number (as in the channel name).",
            placeholders=("ticket_number",),
        ),
        ck(
            TICKET_DRAFT_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            "First line of every step of the live panel while the customer puts the order together, e.g. "
            "'{icon_ticket} Ticket **#{ticket_number}** · {status}'. {ticket_number} is the ticket number, {status} "
            "checkout.ticket.status_in_progress.",
            placeholders=("ticket_number", "status"),
        ),
        ck(
            TICKET_ORDER_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            "First line of the live panel once the order was placed (every status), e.g. '{icon_ticket} Ticket "
            "**#{ticket_number}** · Order **#{order_number}** · {status}'. {ticket_number} is the ticket number, "
            "{order_number} the order number, {status} the current stage name (checkout.ticket.stage.*).",
            placeholders=("ticket_number", "order_number", "status"),
        ),
        ck(
            TICKET_STATUS_IN_PROGRESS,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            "Status shown on the live panel while the customer is still putting the order together (not submitted "
            "yet), as {status} in checkout.ticket.draft_line.",
        ),
        ck(
            ITEMS_HEADING,
            Kind.TITLE,
            FEATURE,
            g_receipt,
            "Heading of the items section of the live panel (every step and every status), shown in bold, e.g. "
            "'{icon_cart} ITEMS'. Each item is written with checkout.cart.line.",
        ),
        ck(
            SUMMARY_HEADING,
            Kind.TITLE,
            FEATURE,
            g_receipt,
            "Heading of the amounts section of the live panel, shown in bold, e.g. '{icon_receipt} SUMMARY': the "
            "subtotal, the delivery option and extras with their prices, fees, store credit and the total (what is "
            "known so far while the order is put together).",
        ),
        ck(
            SUMMARY_LINE,
            Kind.FORMAT,
            FEATURE,
            g_receipt,
            "How one amount of the summary is written, e.g. '{label} · {amount}'. {label} is its name (the subtotal, "
            "the delivery option, an extra or fee), {amount} the amount (common.money_format).",
            placeholders=("label", "amount"),
        ),
        ck(
            SUMMARY_TOTAL_LINE,
            Kind.FORMAT,
            FEATURE,
            g_receipt,
            "Last line of the summary: the total (or the subtotal while nothing else is known), e.g. "
            "'**{label} · {amount}**'. {label} is checkout.review.total_field (or checkout.cart.subtotal_field), "
            "{amount} the amount.",
            placeholders=("label", "amount"),
        ),
        ck(
            SUMMARY_CREDIT_LINE,
            Kind.FORMAT,
            FEATURE,
            g_receipt,
            "How the store credit applied to the order is written in the summary (it is taken off the total), e.g. "
            "'{label} · -{amount}'. {label} is checkout.review.credits_field, {amount} the credit applied. Needed when "
            "credits are enabled and credits.allow_at_checkout = true.",
            required=credits,
            placeholders=("label", "amount"),
        ),
        ck(
            SUMMARY_FREE,
            Kind.FORMAT,
            FEATURE,
            g_receipt,
            "Optional: shown as the amount of a delivery option without a fee, e.g. 'Free' (when empty, the option is "
            "listed without an amount).",
            required=False,
        ),
        ck(
            PROGRESS_HEADING,
            Kind.TITLE,
            FEATURE,
            g_receipt,
            "Heading of the progress tracker on the live panel once the order was placed, shown in bold, e.g. "
            "'{icon_clock} PROGRESS'. It lists the stages reached (with their date), then the ones still to come.",
        ),
        ck(
            PROGRESS_START,
            Kind.FORMAT,
            FEATURE,
            g_receipt,
            "Name of the first stage in the progress tracker, when the customer pressed Confirm order and the "
            "items were reserved for them, e.g. 'Items reserved'. The later stages use their stage names "
            "(checkout.ticket.stage.*).",
        ),
        ck(
            PROGRESS_DONE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_receipt,
            f"A stage the order went through, e.g. '{{icon_check}} {{stage}} · {{date}}'. {_PROGRESS_DOC}",
            placeholders=PROGRESS_PLACEHOLDERS,
        ),
        ck(
            PROGRESS_CURRENT,
            Kind.FIELD_VALUE,
            FEATURE,
            g_receipt,
            f"The stage the order is in now, e.g. '{{icon_check}} **{{stage}}** · {{date}}'. {_PROGRESS_DOC}",
            placeholders=PROGRESS_PLACEHOLDERS,
        ),
        ck(
            PROGRESS_NEXT,
            Kind.FIELD_VALUE,
            FEATURE,
            g_receipt,
            "A stage still to come, e.g. '{icon_dot_grey} {stage}'. {stage} is the stage name.",
            placeholders=("stage",),
        ),
        ck(
            PROGRESS_STOPPED,
            Kind.FIELD_VALUE,
            FEATURE,
            g_receipt,
            "The stage that ended the order (cancelled, rejected, expired or refunded), e.g. "
            f"'{{icon_cross}} **{{stage}}** · {{date}}'. {_PROGRESS_DOC}",
            placeholders=PROGRESS_PLACEHOLDERS,
        ),
        ck(
            BROWSE_TITLE,
            Kind.TITLE,
            FEATURE,
            g_browse,
            "Title of the order workflow message while the customer chooses products (first step).",
        ),
        ck(
            BROWSE_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_browse,
            "Optional text of the product-choosing step (e.g. how to pick products and quantities).",
            required=False,
        ),
        ck(
            BROWSE_EMPTY_CATALOG,
            Kind.DESCRIPTION,
            FEATURE,
            g_browse,
            "Shown in the product-choosing step when no product is offered right now.",
        ),
        ck(
            BROWSE_SELECT_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_browse,
            "Greyed-out text of the product selection menu (if empty, Discord would show its own default "
            "text). The options are the product names; the small text under each is its price.",
        ),
        ck(
            CART_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cart,
            "Title of the cart step (the customer checks and edits quantities).",
        ),
        ck(CART_DESCRIPTION, Kind.DESCRIPTION, FEATURE, g_cart, "Optional text of the cart step.", required=False),
        ck(
            CART_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_cart,
            "Name of the field listing the order lines on the staff order record (the customer's live panel lists "
            "them under checkout.cart.heading). Each line is written with checkout.cart.line.",
        ),
        ck(
            CART_LINE,
            Kind.FORMAT,
            FEATURE,
            g_cart,
            "How one cart/order line is written (the customer's items and the staff order record), e.g. "
            "'`{quantity}×` **{name}** · {line_total}'. {name} is the product name, {quantity} the quantity, "
            "{unit_price} the price of one unit and {line_total} the line price (both written with "
            "common.money_format).",
            placeholders=("name", "quantity", "unit_price", "line_total"),
        ),
        ck(
            CART_EMPTY,
            Kind.FIELD_VALUE,
            FEATURE,
            g_cart,
            "Shown under the items heading while the cart is empty.",
        ),
        ck(
            CART_SUBTOTAL_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_cart,
            "Name of the subtotal (sum of the cart lines before fees and credits) in the summary and on the staff "
            "order record.",
        ),
        ck(
            CART_EDIT_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_cart,
            "Greyed-out text of the menu that picks a cart line to change its quantity (if empty, Discord would "
            "show its own default text).",
        ),
        ck(
            CART_EDIT_OPTION,
            Kind.OPTION_DESCRIPTION,
            FEATURE,
            g_cart,
            "Optional small text under each product in the change-quantity menu. {quantity} is the quantity in "
            "the cart, {line_total} the line price.",
            required=False,
            placeholders=("quantity", "line_total"),
        ),
        ck(
            CART_MINIMUM_NOT_MET,
            Kind.DESCRIPTION,
            FEATURE,
            g_cart,
            "Shown in the cart step while the subtotal is below checkout.min_order_total_minor (the continue "
            "button stays disabled). {minimum} is that minimum written with common.money_format. Needed when "
            "checkout.min_order_total_minor is above 0.",
            required=c.min_order_total_minor > 0,
            placeholders=("minimum",),
        ),
        ck(QUANTITY_TITLE, Kind.MODAL_TITLE, FEATURE, g_qty, "Title of the form that asks for a product's quantity."),
        ck(
            QUANTITY_LABEL,
            Kind.INPUT_LABEL,
            FEATURE,
            g_qty,
            "Label of the quantity field (a whole number; 0 removes the product from the cart).",
        ),
        ck(
            QUANTITY_DESCRIPTION,
            Kind.INPUT_DESCRIPTION,
            FEATURE,
            g_qty,
            "Optional help text under the quantity label (e.g. that 0 removes the product).",
            required=False,
        ),
        ck(
            QUANTITY_PLACEHOLDER,
            Kind.INPUT_PLACEHOLDER,
            FEATURE,
            g_qty,
            "Optional greyed-out example inside the empty quantity field.",
            required=False,
        ),
        ck(
            FULFILLMENT_TITLE,
            Kind.TITLE,
            FEATURE,
            g_ful,
            "Title of the step where the customer chooses a fulfillment option (delivery, pickup, ...). Needed "
            "when checkout.fulfillment_options are defined.",
            required=options,
        ),
        ck(
            FULFILLMENT_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_ful,
            "Optional text of the fulfillment step.",
            required=False,
        ),
        ck(
            FULFILLMENT_SELECT_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_ful,
            "Greyed-out text of the fulfillment option menu (if empty, Discord would show its own default text). "
            "Needed when checkout.fulfillment_options are defined.",
            required=options,
        ),
        ck(
            FULFILLMENT_FEE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ful,
            "Name of the delivery fee in the summary and on the staff order record when the chosen option's name "
            "is not configured any more (an order placed before the option was removed); otherwise the option's "
            "name is shown with its fee. Needed when an option has a fee.",
            required=any(o.fee_minor > 0 for o in c.fulfillment_options),
        ),
        ck(
            DETAILS_TITLE,
            Kind.TITLE,
            FEATURE,
            g_details,
            "Title of the step where the customer enters order details (checkout.details_form and the chosen "
            "option's form). The answers are shown below as fields named by the field labels. Needed when a "
            "form is defined.",
            required=forms,
        ),
        ck(
            DETAILS_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_details,
            "Optional text of the details step.",
            required=False,
        ),
        ck(
            DETAILS_MODAL_TITLE,
            Kind.MODAL_TITLE,
            FEATURE,
            g_details,
            "Title of the order details form. Needed when a form is defined.",
            required=forms,
        ),
        ck(
            BUTTON_ENTER_DETAILS,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_details,
            "Button that opens the order details form. Needed when a form is defined.",
            required=forms,
        ),
        ck(
            BUTTON_ENTER_MORE_DETAILS,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_details,
            "Button that opens the second part of the details form (Discord forms hold at most 5 fields; used "
            "when checkout.details_form plus an option's form have more than 5 fields together).",
            required=needs_second_details_form(settings),
        ),
        ck(
            REVIEW_TITLE,
            Kind.TITLE,
            FEATURE,
            g_review,
            "Title of the final step where the customer reviews the order and submits it.",
        ),
        ck(
            REVIEW_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_review,
            "Optional text of the review step.",
            required=False,
        ),
        ck(
            REVIEW_TOTAL_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_review,
            "Name of the total to pay: the last line of the summary (review step and the placed order) and on the "
            "staff order record.",
        ),
        ck(
            REVIEW_CREDITS_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_review,
            "Name of the store credit applied to the order, in the summary (checkout.summary.credit_line) and on the "
            "staff order record. Needed when credits are enabled and credits.allow_at_checkout = true.",
            required=credits,
        ),
        ck(
            REVIEW_CREDITS_AVAILABLE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_review,
            "Optional name of the line under the review step's total showing how much store credit the customer "
            "could apply (shown while it is not applied).",
            required=False,
        ),
        ck(
            REVIEW_PRICE_CHANGED,
            Kind.DESCRIPTION,
            FEATURE,
            g_review,
            "Shown in the review step when the total changed since the customer looked at it (prices, fees or "
            "their credit balance changed); nothing was submitted yet. {total} is the new total.",
            placeholders=("total",),
        ),
        ck(
            BUTTON_APPLY_CREDITS,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_review,
            "Button in the review step that applies the customer's store credit. Needed when credits are "
            "enabled and credits.allow_at_checkout = true.",
            required=credits,
        ),
        ck(
            BUTTON_REMOVE_CREDITS,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_review,
            "Button in the review step that removes the applied store credit. Needed when credits are enabled "
            "and credits.allow_at_checkout = true.",
            required=credits,
        ),
        ck(BUTTON_SUBMIT, Kind.BUTTON_LABEL, FEATURE, g_review, "Button in the review step that submits the order."),
        ck(
            CANCEL_CONFIRM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cancel,
            "Optional title of the question asking the customer to confirm cancelling an unfinished order.",
            required=False,
        ),
        ck(
            CANCEL_CONFIRM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_cancel,
            "Question asking the customer to confirm cancelling an unfinished order (shared confirm/cancel buttons).",
        ),
        ck(
            CANCELLED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cancel,
            "Optional title of the workflow message after the customer cancelled an unfinished order (the "
            "channel is then closed). Also used after staff closed the ticket of an unfinished order when "
            "checkout.staff_closed.title is empty.",
            required=False,
        ),
        ck(
            CANCELLED_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_cancel,
            "Text of the workflow message after the customer cancelled an unfinished order. Also used after staff "
            "closed the ticket of an unfinished order (/ticket close) when checkout.staff_closed.description is "
            "empty.",
        ),
        ck(
            STAFF_CLOSED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cancel,
            "Optional title of the workflow message after staff closed the ticket of an unfinished (never "
            "submitted) order with /ticket close. Empty = checkout.cancelled.title.",
            fallback=CANCELLED_TITLE,
        ),
        ck(
            STAFF_CLOSED_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_cancel,
            "Optional text of the workflow message after staff closed the ticket of an unfinished (never "
            "submitted) order with /ticket close (the order is cancelled, the channel closed). Empty = "
            "checkout.cancelled.description.",
            fallback=CANCELLED_DESCRIPTION,
        ),
        ck(
            EXPIRED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cancel,
            "Optional title of the workflow message when an unfinished order expired after "
            "checkout.session_expiry_minutes without activity.",
            required=False,
        ),
        ck(
            EXPIRED_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_cancel,
            "Text of the workflow message when an unfinished order expired. Needed when "
            "checkout.session_expiry_minutes is above 0.",
            required=c.session_expiry_minutes > 0,
        ),
        ck(
            ORDER_CANCEL_CONFIRM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_cancel,
            "Optional title of the question asking the customer to confirm cancelling a submitted, unpaid "
            "order. {order_number} is the order number.",
            required=False,
            placeholders=("order_number",),
        ),
        ck(
            ORDER_CANCEL_CONFIRM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_cancel,
            "Question asking the customer to confirm cancelling a submitted, unpaid order (shared "
            "confirm/back buttons). {order_number} is the order number.",
            placeholders=("order_number",),
        ),
        ck(
            BUTTON_CANCEL_ORDER,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_cancel,
            "Button under a submitted order that lets the customer cancel it while it is not paid yet.",
        ),
        ck(
            BUTTON_CANCEL_YES,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_cancel,
            "Button of the cancel question (an unfinished order or an unpaid one) that cancels it.",
        ),
        ck(
            BUTTON_KEEP_ORDER,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_cancel,
            "Button of the cancel question that keeps the order and goes back to it.",
        ),
        ck(
            SUBMITTED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_submitted,
            f"Title of the workflow message after the order was submitted (waiting for payment). {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            SUBMITTED_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_submitted,
            f"Optional text of the submitted order. The payment instructions follow below it. {_ORDER_DOC}",
            required=False,
            placeholders=ORDER_PLACEHOLDERS,
        ),
    ]
    purchase = purchase_placeholder(settings)
    purchase_doc = f" {{{purchase[0]}}} links the place-order channel (where Report a problem is)." if purchase else ""
    for status in m.STATUS_VIEWS:
        required = status != m.EXPIRED or c.payment_expiry_minutes > 0
        condition = " Needed when checkout.payment_expiry_minutes is above 0." if status == m.EXPIRED else ""
        links = purchase if status in REPORT_STATUSES else ()
        keys += [
            ck(
                status_title_key(status),
                Kind.TITLE,
                FEATURE,
                g_status,
                f"Title of the order workflow message once the order is '{status}' ({_STATUS_DOC[status]}). "
                f"{_ORDER_DOC}{condition}",
                required=required,
                placeholders=ORDER_PLACEHOLDERS,
            ),
            ck(
                status_description_key(status),
                Kind.DESCRIPTION,
                FEATURE,
                g_status,
                f"Optional text of the order workflow message once the order is '{status}' (under the ticket, order "
                f"number and status line; the items, summary and progress follow). {_ORDER_DOC}"
                f"{purchase_doc if links else ''}",
                required=False,
                placeholders=(*ORDER_PLACEHOLDERS, *links),
            ),
        ]
    for status in m.ORDER_STATUSES:
        keys.append(
            ck(
                stage_key(status),
                Kind.FORMAT,
                FEATURE,
                g_stages,
                f"Name of the stage '{status}' ({_STATUS_DOC[status]}) shown to the customer: the status on the "
                "live panel, the progress tracker and /myorders.",
            )
        )
    for status in m.ORDER_STATUSES:
        keys.append(
            ck(
                status_label_key(status),
                Kind.FORMAT,
                FEATURE,
                g_labels,
                f"Name of the order status '{status}' ({_STATUS_DOC[status]}), used as {{status}} in the staff "
                "order controls.",
            )
        )
    notify_doc = (
        "{order_number} is the order number, {total} the order total, {channel_mention} links the order channel. "
        "Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct "
        "messages are closed) posted in the order channel with a mention."
    )
    for status in NOTIFIED_STATUSES:
        required = status != m.EXPIRED or c.payment_expiry_minutes > 0
        condition = " Needed when checkout.payment_expiry_minutes is above 0." if status == m.EXPIRED else ""
        links = purchase if status in REPORT_STATUSES else ()
        keys += [
            ck(
                notify_title_key(status),
                Kind.TITLE,
                FEATURE,
                g_notify,
                f"Optional title of the notification sent when an order becomes '{status}'. {notify_doc}",
                required=False,
                placeholders=NOTIFY_PLACEHOLDERS,
            ),
            ck(
                notify_description_key(status),
                Kind.DESCRIPTION,
                FEATURE,
                g_notify,
                f"Notification sent to the customer when their order becomes '{status}' "
                f"({_STATUS_DOC[status]}). {notify_doc}{condition}{purchase_doc if links else ''}",
                required=required,
                placeholders=(*NOTIFY_PLACEHOLDERS, *links),
            ),
        ]
    keys += [
        ck(
            notify_title_key(NOTIFY_SUBMITTED),
            Kind.TITLE,
            FEATURE,
            g_notify,
            f"Optional title of the 'submission received' notification. {notify_doc}",
            required=False,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            notify_description_key(NOTIFY_SUBMITTED),
            Kind.DESCRIPTION,
            FEATURE,
            g_notify,
            "'Submission received' notification, sent once when the customer submitted the order (it now waits for "
            f"payment). {notify_doc}",
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            notify_title_key(NOTIFY_SESSION_WARNING),
            Kind.TITLE,
            FEATURE,
            g_notify,
            "Optional title of the warning posted in the order ticket before an unfinished order expires. "
            "{minutes} and {expires} as in the text.",
            required=False,
            placeholders=("minutes", "expires"),
        ),
        ck(
            notify_description_key(NOTIFY_SESSION_WARNING),
            Kind.DESCRIPTION,
            FEATURE,
            g_notify,
            "Warning posted in the order ticket (mentioning the customer) checkout.session_expiry_warning_minutes "
            "before an unfinished order expires for inactivity (any change to the order resets the timer; after a "
            "later idle period the warning is sent again). {minutes} is that number of minutes, {expires} when the "
            "order expires (relative time shown by Discord, e.g. 'in 10 minutes'). Needed when "
            "checkout.session_expiry_warning_minutes is above 0.",
            required=session_warning_enabled(settings),
            placeholders=("minutes", "expires"),
        ),
    ]
    reminder = c.payment_reminder_minutes > 0
    keys += [
        ck(
            notify_title_key(NOTIFY_PAYMENT_REMINDER),
            Kind.TITLE,
            FEATURE,
            g_notify,
            f"Optional title of the payment reminder. {notify_doc}",
            required=False,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            notify_description_key(NOTIFY_PAYMENT_REMINDER),
            Kind.DESCRIPTION,
            FEATURE,
            g_notify,
            "Reminder sent once when an order is still unpaid checkout.payment_reminder_minutes after it was "
            f"submitted (the payment instructions are attached below it). {notify_doc} Needed when "
            "checkout.payment_reminder_minutes is above 0.",
            required=reminder,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            notify_title_key(NOTIFY_REFUND),
            Kind.TITLE,
            FEATURE,
            g_notify,
            "Optional title of the notification sent when staff record a partial refund. {order_number} is the "
            "order number, {amount} the refunded amount, {channel_mention} links the order channel.",
            required=False,
            placeholders=("order_number", "amount", "channel_mention"),
        ),
        ck(
            notify_description_key(NOTIFY_REFUND),
            Kind.DESCRIPTION,
            FEATURE,
            g_notify,
            "Notification sent to the customer when staff record a partial refund (a refund that completes the "
            "full refund sends the 'refunded' notification instead). {order_number} is the order number, {amount} "
            f"the refunded amount (common.money_format), {{channel_mention}} links the order channel.{purchase_doc}",
            placeholders=("order_number", "amount", "channel_mention", *purchase),
        ),
        ck(
            STAFF_TITLE,
            Kind.TITLE,
            FEATURE,
            g_staff,
            "Title of the order controls posted for staff in checkout.staff_channel for every submitted order "
            "(edited on every status change). {order_number} is the order number, {total} the total, {status} "
            "the status name (checkout.status_label.*).",
            placeholders=STAFF_TITLE_PLACEHOLDERS,
        ),
        ck(
            STAFF_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_staff,
            "Text of the staff order controls. {user_mention} mentions the customer (no ping), "
            "{channel_mention} links the order channel, {order_number}/{total}/{status} as in the title. The "
            "order lines, totals and buttons follow.",
            placeholders=STAFF_PLACEHOLDERS,
        ),
        ck(
            STAFF_REFUNDED_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_staff,
            "Name of the field on the staff order controls showing how much was refunded so far.",
        ),
        ck(
            STAFF_DETAILS_TITLE,
            Kind.TITLE,
            FEATURE,
            g_staff,
            "Optional title of the message posted under the staff order controls with the customer's form "
            "answers (only when a form is defined and operations.answers_in_orders_channel = true; otherwise the "
            "answers are shown only privately in the record's details). {order_number} is the order number.",
            required=False,
            placeholders=("order_number",),
        ),
        ck(
            STAFF_BUTTON_MARK_PAID,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: the payment was received (unpaid -> paid; stock is then taken).",
        ),
        ck(
            STAFF_BUTTON_REJECT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: reject an unpaid order (reserved stock and applied credit are returned).",
        ),
        ck(
            STAFF_BUTTON_CANCEL,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: cancel the order (unpaid: stock and credit are returned; paid: nothing is restocked, "
            "refunded or returned automatically - use the refund buttons and the return-credit button).",
        ),
        ck(
            STAFF_BUTTON_PROCESSING,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: mark a paid order as being prepared (optional stage).",
        ),
        ck(
            STAFF_BUTTON_SHIPPED,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: the order was shipped. Opens a form for the carrier, the tracking number and an optional "
            "tracking link, which the customer receives in the ticket and by direct message.",
        ),
        ck(
            STAFF_BUTTON_FULFILLED,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: a shipped order was delivered (the order ticket is closed).",
        ),
        ck(
            STAFF_BUTTON_REFUND,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: record a (partial) refund. With the manual payment method the money is returned "
            "outside the bot.",
        ),
        ck(
            STAFF_BUTTON_REFUND_CREDIT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: refund (part of) the order as store credit. Needed when the credits feature is on.",
            required=settings.features.credits,
        ),
        ck(
            STAFF_BUTTON_RETURN_CREDIT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: give back the store credit the customer applied to a paid order (all of it at once; "
            "shown until it was given back, e.g. after cancelling a paid order). Cancelling a paid order returns "
            "neither money nor credit automatically. Needed when credits are enabled and "
            "credits.allow_at_checkout = true.",
            required=credits,
        ),
        ck(
            STAFF_CREDIT_RETURNED_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_staff,
            "Optional name of the field on the staff order controls showing the applied store credit that was "
            "given back to the customer (written with common.money_format).",
            required=False,
        ),
        ck(
            ERROR_SESSION_CLOSED,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when the customer uses a control of an order workflow that was already submitted, cancelled "
            "or expired.",
        ),
        ck(
            ERROR_OUT_OF_STOCK,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when the customer asks for more units than are available (when adding to the cart, or when "
            "submitting if someone else bought them first). {name} is the product name, {available} the units "
            "still available.",
            placeholders=("name", "available"),
        ),
        ck(
            ERROR_QUANTITY_INVALID,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when a quantity is above the allowed maximum (checkout.max_line_quantity or the product's "
            "per-order limit). {max} is that maximum.",
            placeholders=("max",),
        ),
        ck(
            ERROR_CART_FULL,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when the customer adds a product but the cart already has checkout.max_cart_lines different "
            "products. {max} is that number.",
            placeholders=("max",),
        ),
        ck(
            ERROR_PRODUCT_UNAVAILABLE,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when a product in the cart is no longer offered (deactivated or removed). {name} is the "
            "product name.",
            placeholders=("name",),
        ),
        ck(
            ERROR_ORDER_NOT_CANCELLABLE,
            Kind.DESCRIPTION,
            FEATURE,
            g_errors,
            "Shown when the customer tries to cancel an order that can no longer be cancelled by them (it was "
            "already paid, cancelled or handled).",
        ),
    ]
    for option in c.fulfillment_options:
        o = option.id
        group = f"Checkout: fulfillment option '{o}'"
        keys += [
            ck(
                option_key(o, "label"),
                Kind.OPTION_LABEL,
                FEATURE,
                group,
                f"Name of the '{o}' fulfillment option (menu option, and the chosen-option field).",
            ),
            ck(
                option_key(o, "description"),
                Kind.OPTION_DESCRIPTION,
                FEATURE,
                group,
                f"Optional small text under the '{o}' option in the menu (e.g. its fee or delivery time).",
                required=False,
            ),
        ]
        keys += _form_keys(group, option.form, partial(option_form_key, o))
    if c.details_form:
        keys += _form_keys("Checkout: order details form", c.details_form, form_field_key)
    if c.submission_form:
        g_submission = "Checkout: submission form (asked when the customer presses Submit)"
        keys.append(
            ck(
                SUBMISSION_MODAL_TITLE,
                Kind.MODAL_TITLE,
                FEATURE,
                g_submission,
                "Title of the form that opens when the customer presses Submit (checkout.submission_form). The answers "
                "are stored with the order and shown to staff.",
            )
        )
        keys += _form_keys(g_submission, c.submission_form, submission_field_key, form="submission form")
    for fee in c.fees:
        keys.append(
            ck(
                fee_label_key(fee.id),
                Kind.FIELD_NAME,
                FEATURE,
                "Checkout: fees",
                f"Name of the field showing the '{fee.id}' fee in the review step, the submitted order and the "
                "staff controls (the value is the fee written with common.money_format).",
            )
        )
    keys += _extra_keys(c)
    keys += _address_keys(settings)
    keys += _discount_keys(settings)
    provider = PROVIDERS.get(settings.payments.provider)
    if provider is not None:
        keys += provider.content_keys(settings)
    keys += payment_methods.content_keys(settings)
    keys += staff_keys.content_keys(settings)  # staff side of orders (operations package)
    return keys


ADDRESS_FIELD_DOCS = {
    "name": "the recipient's full name",
    "street": "the street and house number (and apartment, if any)",
    "city": "the city or town",
    "region": "the state, province or region (required for a country with excluded areas, e.g. US states)",
    "postal": "the postal or ZIP code",
}


def _address_keys(settings: Settings) -> list[ContentKey]:
    c = settings.checkout
    if not c.ask_address:
        return []
    group = "Checkout: shipping address (checkout.ask_address)"
    where = "the shipping address step"
    keys = [
        ck(
            ADDRESS_REGION_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            group,
            f"Greyed-out text of the region menu on {where} (e.g. 'Choose your region').",
        ),
        ck(
            ADDRESS_COUNTRY_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            group,
            f"Greyed-out text of a country menu on {where}. {{first}} and {{last}} are the first and last country in "
            "it (a region with more than 25 countries has several menus), e.g. 'Choose your country ({first} to "
            "{last})'.",
            placeholders=("first", "last"),
        ),
        ck(ADDRESS_BUTTON, Kind.BUTTON_LABEL, FEATURE, group, f"Button on {where} that opens the address form."),
        ck(ADDRESS_BUTTON_CHANGE, Kind.BUTTON_LABEL, FEATURE, group, "The same button once an address was entered."),
        ck(ADDRESS_MODAL_TITLE, Kind.MODAL_TITLE, FEATURE, group, "Title of the private address form."),
        ck(
            ADDRESS_HEADING,
            Kind.TITLE,
            FEATURE,
            group,
            f"Heading of the address section of {where}, shown in bold (it may start with an icon, e.g. "
            "{icon_location}).",
        ),
        ck(
            ADDRESS_COUNTRY_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            f"Line on {where} showing the chosen country, e.g. '**Country:** {{country}}'.",
            placeholders=("country",),
        ),
        ck(
            ADDRESS_COUNTRY_NONE,
            Kind.FIELD_VALUE,
            FEATURE,
            group,
            "Shown as {country} before a country is chosen, e.g. 'not chosen yet'.",
        ),
        ck(
            ADDRESS_ADDED_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            f"Line on {where} once the address is entered, e.g. '**Address:** {{name}}, {{city}}'. The street is "
            "never shown in the channel.",
            placeholders=("name", "city", "region", "postal"),
        ),
        ck(
            ADDRESS_MISSING_LINE,
            Kind.FIELD_VALUE,
            FEATURE,
            group,
            f"Line on {where} before the address is entered, e.g. '**Address:** not added yet'.",
        ),
        ck(
            ADDRESS_SHIP_TO,
            Kind.FORMAT,
            FEATURE,
            group,
            "How the destination is written on the review, the order and the staff record, e.g. '{name}, {city}, "
            "{country}' (the full address is shown to staff only).",
            placeholders=("name", "city", "region", "postal", "country"),
        ),
        ck(
            ADDRESS_COUNTRY_LABEL,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Label of the country in the address shown to staff (e.g. 'Country').",
        ),
        ck(
            ERROR_AREA_EXCLUDED,
            Kind.DESCRIPTION,
            FEATURE,
            group,
            "Private reply when the address is in an area we do not ship to (shipping.excluded_areas). {area} is its "
            "name (e.g. Alaska), {country} the country. The answers are kept so the customer can correct them.",
            placeholders=("area", "country"),
        ),
        ck(
            ERROR_COUNTRY_FIRST,
            Kind.DESCRIPTION,
            FEATURE,
            group,
            "Private reply when the address form is opened before a country is chosen.",
        ),
    ]
    for group_def in settings.shipping.groups:
        keys.append(
            ck(
                address_region_key(group_def.id),
                Kind.OPTION_LABEL,
                FEATURE,
                group,
                f"Name of the region '{group_def.id}' ([[shipping.groups]]) in the address step's region menu (e.g. "
                "North America).",
            )
        )
    for field_id, doc in ADDRESS_FIELD_DOCS.items():
        keys += [
            ck(
                address_field_key(field_id, "label"),
                Kind.INPUT_LABEL,
                FEATURE,
                group,
                f"Label of the address form field for {doc}; also its name in the address shown to staff.",
            ),
            ck(
                address_field_key(field_id, "placeholder"),
                Kind.INPUT_PLACEHOLDER,
                FEATURE,
                group,
                f"Optional greyed-out example in the field for {doc}.",
                required=False,
            ),
        ]
    return keys


# The customer text of each reason a discount code does not apply: (reason, what happened, placeholders besides {code}).
_DISCOUNT_REASON_DOCS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (discounts.UNKNOWN, "the shop has no code with this name (or it uses characters codes never have)", ()),
    (discounts.ENDED, "staff ended the code (/discount end)", ()),
    (discounts.EXPIRED, "the code's end date has passed", ()),
    (discounts.USED_UP, "the code was used as many times as it allows", ()),
    (discounts.ALREADY_USED, "the code is once per customer and this customer already placed an order with it", ()),
    (discounts.NO_ELIGIBLE_ITEMS, "the code is limited to some items and none of them is in the cart", ()),
    (discounts.MINIMUM, "the items in the cart are worth less than the code's minimum ({minimum})", ("minimum",)),
    (
        discounts.NEEDS_TWO_ITEMS,
        "the code is buy one, get one free and fewer than two eligible items are in the cart",
        (),
    ),
)


def _discount_keys(settings: Settings) -> list[ContentKey]:
    """Discount codes on the review step and claimed coupons. Registered always (an order placed with a code keeps
    showing its discount); required while ``checkout.discount_codes`` is on."""
    on = settings.checkout.discount_codes
    group = "Checkout: discount codes (review step)"
    g_claim = "Checkout: claimed coupons (private replies to an announcement's claim button)"
    keys: list[ContentKey] = [
        ck(
            DISCOUNT_BUTTON_ADD,
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            "Button on the review step that opens the private discount code form, e.g. '{icon_discount} Add discount "
            "code'. Shown while the shop has at least one discount code that can be used (/discount).",
            required=on,
        ),
        ck(
            DISCOUNT_BUTTON_USE,
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            "Button on the review step for a coupon the customer claimed (at most two, newest first): pressing it "
            "applies the code, e.g. '{icon_gift} Use coupon {code}'. {code} is the code.",
            required=on,
            placeholders=("code",),
        ),
        ck(
            DISCOUNT_BUTTON_REMOVE,
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            "Button on the review step that takes the applied discount code off the order (it replaces the add and "
            "use buttons while a code is applied).",
            required=on,
        ),
        ck(DISCOUNT_MODAL_TITLE, Kind.MODAL_TITLE, FEATURE, group, "Title of the discount code form.", required=on),
        ck(DISCOUNT_INPUT_LABEL, Kind.INPUT_LABEL, FEATURE, group, "Label of the code field in the form.", required=on),
        ck(
            DISCOUNT_INPUT_PLACEHOLDER,
            Kind.INPUT_PLACEHOLDER,
            FEATURE,
            group,
            "Optional greyed-out example inside the empty code field.",
            required=False,
        ),
        ck(
            SUMMARY_DISCOUNT_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            "How a percent or amount discount is written in the summary, right under the subtotal (review step, order "
            "ticket, staff record), e.g. 'Discount ({code}) · -{amount}'. {code} is the code, {amount} the amount it "
            "takes off.",
            required=on,
            placeholders=("code", "amount"),
        ),
        ck(
            SUMMARY_BOGO_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            "How a buy one, get one free discount is written in the summary, e.g. 'Buy one, get one free ({code}) · "
            "-{amount}'. {code} is the code, {amount} the price of the free items.",
            required=on,
            placeholders=("code", "amount"),
        ),
        ck(
            SUMMARY_DISCOUNT_OFF_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            "Shown in the summary in the discount's place when a code the customer applied stops applying (the cart "
            "changed, the code expired, was used up or ended), e.g. 'Discount ({code}) · not applied'. The total is "
            "shown without the discount; the note under it says why (checkout.summary.discount_note).",
            required=on,
            placeholders=("code",),
        ),
        ck(
            SUMMARY_DISCOUNT_NOTE,
            Kind.FIELD_VALUE,
            FEATURE,
            group,
            "Shown under the total of the review step when a code the customer applied stops applying, e.g. "
            "'{icon_alert} {reason} You can also press **Remove discount**.'. {reason} is the reason's text "
            "(checkout.discount.error.*).",
            required=on,
            placeholders=("reason",),
        ),
    ]
    for reason, what, extra in _DISCOUNT_REASON_DOCS:
        keys.append(
            ck(
                discount_error_key(reason),
                Kind.DESCRIPTION,
                FEATURE,
                group,
                f"Private reply when the customer's discount code cannot be used because {what}; also shown in the "
                "summary of the review step (checkout.summary.discount_note) when a code the customer applied stops "
                "applying. {code} is the code.",
                required=on,
                placeholders=("code", *extra),
            )
        )
    keys += [
        ck(
            DISCOUNT_TOO_MANY_TRIES,
            Kind.DESCRIPTION,
            FEATURE,
            group,
            "Private reply after 5 codes that could not be used within 10 minutes (protection against guessing codes): "
            "the customer waits a few minutes before trying again.",
            required=on,
        ),
        ck(
            CLAIM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_claim,
            "Title of the private reply after a coupon was claimed.",
            required=on,
        ),
        ck(
            CLAIM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_claim,
            "First text of the private reply after a coupon was claimed (claiming it again shows the same reply). "
            "{code} is the code.",
            required=on,
            placeholders=("code",),
        ),
        ck(
            CLAIM_HEADING,
            Kind.TITLE,
            FEATURE,
            g_claim,
            "Heading of the coupon's terms in the claim reply, shown in bold, e.g. '{icon_gift} YOUR COUPON'. The "
            "terms follow, one per line (checkout.discount.terms.*).",
            required=on,
        ),
        ck(
            CLAIM_HOWTO_HEADING,
            Kind.TITLE,
            FEATURE,
            g_claim,
            "Heading of how to use the coupon in the claim reply, shown in bold, e.g. '{icon_info} HOW TO USE IT'.",
            required=on,
        ),
        ck(
            CLAIM_HOWTO,
            Kind.DESCRIPTION,
            FEATURE,
            g_claim,
            "How to use the coupon (under checkout.discount.claim.howto_heading). {code} is the code; "
            + (
                f"{{{catalog_placeholder(settings)[0]}}} links the catalog channel."
                if catalog_placeholder(settings)
                else "a link to the catalog channel is available once catalog.listing_channel is set."
            ),
            required=on,
            placeholders=("code", *catalog_placeholder(settings)),
        ),
        ck(
            CLAIM_ENDED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_claim,
            "Title of the private reply to a claim button whose offer has ended (the code ended or expired, or "
            "discount codes are switched off).",
            required=on,
        ),
        ck(CLAIM_ENDED, Kind.DESCRIPTION, FEATURE, g_claim, "Text of that reply.", required=on),
        ck(
            TERMS_PERCENT,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "The terms of a percent coupon, e.g. '**{percent}% off** your items'. {percent} is the percentage.",
            required=on,
            placeholders=("percent",),
        ),
        ck(
            TERMS_AMOUNT,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "The terms of an amount coupon, e.g. '**{amount} off** your items'. {amount} is the amount.",
            required=on,
            placeholders=("amount",),
        ),
        ck(
            TERMS_BOGO,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "The terms of a buy one, get one free coupon, e.g. '**Buy one, get one free:** the cheaper item of every "
            "pair is free'.",
            required=on,
        ),
        ck(
            TERMS_PRODUCTS,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "Shown when the coupon counts only some items: {products} lists their names.",
            required=on,
            placeholders=("products",),
        ),
        ck(
            TERMS_MINIMUM,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "Shown when the coupon needs a minimum: {minimum} is the items subtotal the cart needs.",
            required=on,
            placeholders=("minimum",),
        ),
        ck(
            TERMS_EXPIRES,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "Shown when the coupon has an end date: {date} is that date (each reader sees it in their own time zone).",
            required=on,
            placeholders=("date",),
        ),
        ck(
            TERMS_ONCE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_claim,
            "Shown when each customer can use the coupon once.",
            required=on,
        ),
    ]
    return keys


def _extra_keys(c: CheckoutSettings) -> list[ContentKey]:
    if not c.extras:
        return []
    group = "Checkout: extras (delivery step)"
    keys = [
        ck(
            EXTRAS_HEADING,
            Kind.TITLE,
            FEATURE,
            group,
            "Heading of the extras on the delivery step, shown in bold (it may start with an icon, e.g. "
            "{icon_shield}).",
        ),
        ck(
            EXTRA_LINE,
            Kind.FORMAT,
            FEATURE,
            group,
            "How one extra is listed on the delivery step, e.g. '{status} **{label}** · {price}'. {status} is "
            "checkout.extras.status_on or status_off, {label} the extra's name, {price} its price, {description} its "
            "description (empty when it has none).",
            placeholders=("status", "label", "price", "description"),
        ),
        ck(
            EXTRA_STATUS_ON,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Marker of an extra the customer added (as {status} in checkout.extras.line), e.g. {icon_check}.",
        ),
        ck(
            EXTRA_STATUS_OFF,
            Kind.FIELD_NAME,
            FEATURE,
            group,
            "Marker of an extra not added (as {status} in checkout.extras.line), e.g. {icon_dot_grey}.",
        ),
        ck(
            EXTRA_BUTTON_ON,
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            "Label of an added extra's button (pressing it removes the extra), e.g. '{label}: On'.",
            placeholders=("label", "price"),
        ),
        ck(
            EXTRA_BUTTON_OFF,
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            "Label of an extra's button while it is not added (pressing it adds the extra), e.g. '{label}: Off'.",
            placeholders=("label", "price"),
        ),
    ]
    for extra in c.extras:
        keys += [
            ck(
                extra_key(extra.id, "label"),
                Kind.FIELD_NAME,
                FEATURE,
                group,
                f"Name of the extra '{extra.id}' (e.g. Shipping protection), on the delivery step, the review, the "
                "order and the staff record.",
            ),
            ck(
                extra_key(extra.id, "description"),
                Kind.FIELD_VALUE,
                FEATURE,
                group,
                f"Optional short explanation of the extra '{extra.id}' on the delivery step (what it covers).",
                required=False,
            ),
        ]
    return keys


def shared_keys(settings: Settings) -> list[str]:
    """Shared keys the workflow displays: prices, the step indicator (the live panel always shows the progress),
    step navigation (Back, Continue, Cancel) and paging."""
    del settings
    return [
        core.MONEY_FORMAT,
        core.PROGRESS_FORMAT,
        core.BUTTON_BACK,
        core.BUTTON_CONTINUE,
        core.BUTTON_CANCEL,
        core.BUTTON_PREV,
        core.BUTTON_NEXT,
    ]


def settings_problems(settings: Settings) -> list[str]:
    c = settings.checkout
    problems: list[str] = []
    if not c.currency:
        problems.append("settings.checkout.currency is not set (business decision)")
    provider = settings.payments.provider
    if not provider:
        problems.append("settings.payments.provider is not set (business decision)")
    elif provider not in PROVIDERS:
        problems.append(
            f"settings.payments.provider '{provider}' is not a supported payment provider "
            f"(supported: {', '.join(sorted(PROVIDERS))})"
        )
    if not c.panel_channel:
        problems.append("checkout.panel_channel: required (the channel that shows the shop panel)")
    if not c.staff_channel:
        problems.append("checkout.staff_channel: required (staff order controls are posted there)")
    if not c.order_category:
        problems.append("checkout.order_category: required (private order channels are created there)")
    problems.extend(
        reference_problems(
            settings,
            roles=c.staff_roles,
            channels=(("checkout.panel_channel", c.panel_channel), ("checkout.staff_channel", c.staff_channel)),
            categories=(("checkout.order_category", c.order_category),),
        )
    )
    if provider == "gift_card":
        # Who may read the codes: owners and admins always; these roles must exist.
        problems.extend(reference_problems(settings, roles=settings.payments.gift_card.staff_roles))
    # Staff order controls show customer details and payment actions: the channel must not be public.
    problems.extend(private_channel_problems(settings, (("checkout.staff_channel", c.staff_channel),)))
    if c.ask_address:
        if not settings.shipping.groups:
            problems.append("checkout.ask_address: list the countries we ship to in [[shipping.groups]]")
        if len(settings.shipping.groups) > 25:
            problems.append("checkout.ask_address: at most 25 [[shipping.groups]] fit in the region menu")
        if any(option.form for option in c.fulfillment_options):
            problems.append(
                "checkout.ask_address: delivery options with their own form are not supported with the address step "
                "(the address is asked before the delivery option)"
            )
    for fee in c.fees:
        if fee.kind == "percent" and fee.basis_points <= 0:
            problems.append(f"checkout.fees '{fee.id}': a percent fee needs basis_points > 0 (100 = 1%)")
        if fee.kind == "flat" and fee.amount_minor <= 0:
            problems.append(f"checkout.fees '{fee.id}': a flat fee needs amount_minor > 0")
    if c.session_expiry_warning_minutes > 0 and c.session_expiry_minutes <= 0:
        problems.append(
            "checkout.session_expiry_warning_minutes: needs checkout.session_expiry_minutes above 0 (the warning is "
            "sent before an unfinished order expires)"
        )
    elif 0 < c.session_expiry_minutes <= c.session_expiry_warning_minutes:
        problems.append("checkout.session_expiry_warning_minutes: must be less than checkout.session_expiry_minutes")
    if (
        c.payment_reminder_minutes > 0
        and c.payment_expiry_minutes > 0
        and c.payment_reminder_minutes >= c.payment_expiry_minutes
    ):
        problems.append(
            "checkout.payment_reminder_minutes: must be less than checkout.payment_expiry_minutes (the reminder "
            "is sent before an unpaid order expires)"
        )
    return problems
