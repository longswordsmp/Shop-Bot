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
from shopbot.config.settings import FormField, Settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys
from shopbot.features.checkout.payments import PROVIDERS
from shopbot.features.server_keys import private_channel_problems, reference_problems

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
TICKET_REFERENCE_FIELD = "checkout.ticket.reference_field"
TICKET_ORDER_NUMBER_FIELD = "checkout.ticket.order_number_field"
TICKET_STATUS_FIELD = "checkout.ticket.status_field"
TICKET_STATUS_IN_PROGRESS = "checkout.ticket.status_in_progress"
TICKET_TIMELINE_FIELD = "checkout.ticket.timeline_field"
TICKET_TIMELINE_ENTRY = "checkout.ticket.timeline_entry"

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
FULFILLMENT_CHOSEN_FIELD = "checkout.fulfillment_step.chosen_field"
FULFILLMENT_FEE_FIELD = "checkout.fulfillment_step.fee_field"

# -- workflow: details --------------------------------------------------------------------------------
DETAILS_TITLE = "checkout.details.title"
DETAILS_DESCRIPTION = "checkout.details.description"
DETAILS_MODAL_TITLE = "checkout.details.modal_title"
BUTTON_ENTER_DETAILS = "checkout.button.enter_details"
BUTTON_ENTER_MORE_DETAILS = "checkout.button.enter_more_details"

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
NOTIFIED_STATUSES = (m.PAID, m.PROCESSING, m.FULFILLED, m.CANCELLED, m.REJECTED, m.EXPIRED, m.REFUNDED)

ORDER_PLACEHOLDERS = ("order_number", "total")
NOTIFY_PLACEHOLDERS = ("order_number", "total", "channel_mention")
STAFF_TITLE_PLACEHOLDERS = ("order_number", "total", "status")
STAFF_PLACEHOLDERS = ("order_number", "user_mention", "channel_mention", "total", "status")

_ORDER_DOC = "{order_number} is the order number, {total} the order total written with common.money_format."
_STATUS_DOC = {
    m.PENDING: "waiting for payment",
    m.PAID: "staff confirmed the payment",
    m.PROCESSING: "staff are preparing the order",
    m.FULFILLED: "the order was delivered/completed",
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
    return f"checkout.fee.{fee_id}.label"


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
    g_ticket = "Checkout: order ticket (introduction, reference, status and timeline on the live panel)"
    g_stages = "Checkout: order stage names (live panel, timeline, /myorders)"
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
            "shown in the reference field under the text), {user_mention} mentions the customer (no ping).",
            placeholders=("ticket_number", "user_mention"),
        ),
        ck(
            TICKET_REFERENCE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Name of the field showing the ticket's unique reference (the ticket number, as in the channel name) on "
            "the ticket introduction and on every state of the live panel.",
        ),
        ck(
            TICKET_ORDER_NUMBER_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Name of the field showing the order number on the live panel once the order was submitted.",
        ),
        ck(
            TICKET_STATUS_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Name of the field showing the current status on every state of the live panel (the value is "
            "checkout.ticket.status_in_progress before submission, then the order's stage name).",
        ),
        ck(
            TICKET_STATUS_IN_PROGRESS,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            "Status shown on the live panel while the customer is still putting the order together (not submitted "
            "yet).",
        ),
        ck(
            TICKET_TIMELINE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Name of the field listing the order's stages so far (the timeline) on the live panel after submission "
            "(also in /myorders). Each stage is written with checkout.ticket.timeline_entry.",
        ),
        ck(
            TICKET_TIMELINE_ENTRY,
            Kind.FORMAT,
            FEATURE,
            g_ticket,
            "How one stage of the timeline is written, e.g. '{time} {stage}'. {stage} is the stage name "
            "(checkout.ticket.stage.*), {time} the date and time it was reached and {relative} the same as a "
            "relative time (both shown by Discord in each reader's own time zone and language).",
            placeholders=("stage", "time", "relative"),
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
            "Name of the field listing the cart lines (product steps, cart, review, the submitted order and the "
            "staff order controls). Each line is written with checkout.cart.line.",
        ),
        ck(
            CART_LINE,
            Kind.FORMAT,
            FEATURE,
            g_cart,
            "How one cart/order line is written, e.g. '{quantity} x {name}: {line_total}'. {name} is the product "
            "name, {quantity} the quantity, {unit_price} the price of one unit and {line_total} the line price "
            "(both written with common.money_format).",
            placeholders=("name", "quantity", "unit_price", "line_total"),
        ),
        ck(
            CART_EMPTY,
            Kind.FIELD_VALUE,
            FEATURE,
            g_cart,
            "Shown in the cart field while the cart is empty.",
        ),
        ck(
            CART_SUBTOTAL_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_cart,
            "Name of the subtotal field (sum of the cart lines before fees and credits).",
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
            FULFILLMENT_CHOSEN_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ful,
            "Name of the field showing the chosen fulfillment option (its label). Needed when "
            "checkout.fulfillment_options are defined.",
            required=options,
        ),
        ck(
            FULFILLMENT_FEE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_ful,
            "Name of the field showing the chosen option's fee (fee_minor, written with common.money_format; "
            "shown only when that fee is above 0). Needed when an option has a fee.",
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
            "Name of the field showing the total to pay (review step, submitted order, staff order controls).",
        ),
        ck(
            REVIEW_CREDITS_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_review,
            "Name of the field showing the store credit applied to the order (written with "
            "common.money_format). Needed when credits are enabled and credits.allow_at_checkout = true.",
            required=credits,
        ),
        ck(
            REVIEW_CREDITS_AVAILABLE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            g_review,
            "Optional name of a field showing how much store credit the customer could apply (shown while "
            "credits are not applied).",
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
    for status in m.STATUS_VIEWS:
        required = status != m.EXPIRED or c.payment_expiry_minutes > 0
        condition = " Needed when checkout.payment_expiry_minutes is above 0." if status == m.EXPIRED else ""
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
                f"Optional text of the order workflow message once the order is '{status}'. {_ORDER_DOC}",
                required=False,
                placeholders=ORDER_PLACEHOLDERS,
            ),
        ]
    for status in m.ORDER_STATUSES:
        keys.append(
            ck(
                stage_key(status),
                Kind.FORMAT,
                FEATURE,
                g_stages,
                f"Name of the stage '{status}' ({_STATUS_DOC[status]}) shown to the customer: the status field of "
                "the live panel, the order timeline and /myorders.",
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
                f"({_STATUS_DOC[status]}). {notify_doc}{condition}",
                required=required,
                placeholders=NOTIFY_PLACEHOLDERS,
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
            "the refunded amount (common.money_format), {channel_mention} links the order channel.",
            placeholders=("order_number", "amount", "channel_mention"),
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
            STAFF_BUTTON_FULFILLED,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_staff,
            "Staff button: the order was delivered/completed (the order channel is closed).",
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
    provider = PROVIDERS.get(settings.payments.provider)
    if provider is not None:
        keys += provider.content_keys(settings)
    keys += staff_keys.content_keys(settings)  # staff side of orders (operations package)
    return keys


def shared_keys(settings: Settings) -> list[str]:
    """Shared keys the workflow displays: prices, the step indicator (the live panel always shows the progress),
    step navigation, cancel confirmation and paging."""
    del settings
    return [
        core.MONEY_FORMAT,
        core.PROGRESS_FORMAT,
        core.BUTTON_BACK,
        core.BUTTON_CONTINUE,
        core.BUTTON_CANCEL,
        core.BUTTON_CONFIRM,
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
    # Staff order controls show customer details and payment actions: the channel must not be public.
    problems.extend(private_channel_problems(settings, (("checkout.staff_channel", c.staff_channel),)))
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
