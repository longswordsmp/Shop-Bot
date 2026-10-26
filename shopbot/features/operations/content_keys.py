"""Content keys and settings checks of the operations feature (staff alerts in ``operations.staff_channel``, staff
reminders, failure alerts, and the Owner's private alerts).

These are business messages read by staff and the Owner, so the owner words them. Every alert and reminder has an
optional title and a required text; reminder texts are needed only while their threshold is above 0. The Owner's
private versions are optional: when empty, the Owner receives the staff wording.

Alerts carry identifiers only (order number, mentions, totals, channel links, Discord timestamps, internal names of
failed tasks or notifications) - never form answers, payment details or error texts.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.operations import repo
from shopbot.features.server_keys import private_channel_problems, reference_problems

FEATURE = "operations"

NEW_ORDER = "new_order"
PAYMENT_SUBMITTED = "payment_submitted"
PAID_ORDER = "paid_order"
ORDER_ALERTS = (NEW_ORDER, PAYMENT_SUBMITTED, PAID_ORDER)
# A customer chose a payment method our team handles (payments.methods kind "staff"): declared only while one exists.
PAYMENT_METHOD = "payment_method"
# An order left an item low or sold it out (operations.stock_alerts; see operations.stock).
LOW_STOCK = "low_stock"
SOLD_OUT = "sold_out"
STOCK_ALERTS = (LOW_STOCK, SOLD_OUT)
STOCK_PLACEHOLDERS = ("name", "count")

FAILURE_NOTIFICATION = "notification"
FAILURE_TASK = "task"
FAILURE_ERROR = "error"
FAILURE_ROLE = "role"
FAILURES = (FAILURE_NOTIFICATION, FAILURE_TASK, FAILURE_ERROR, FAILURE_ROLE)
# Failures the Owner also receives privately (when operations.owner_dm_alerts is on).
CRITICAL_FAILURES = (FAILURE_TASK,)

BUTTON_OPEN_RECORD = "operations.button.open_record"
BUTTON_OPEN_TICKET = "operations.button.open_ticket"

ORDER_PLACEHOLDERS = ("order_number", "customer_mention", "total", "status", "order_channel")
PAYMENT_METHOD_PLACEHOLDERS = (*ORDER_PLACEHOLDERS, "method")
REMINDER_ORDER_PLACEHOLDERS = (*ORDER_PLACEHOLDERS, "waiting_since", "minutes")
REMINDER_TICKET_PLACEHOLDERS = ("ticket_number", "customer_mention", "ticket_channel", "waiting_since", "minutes")
FAILURE_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    FAILURE_NOTIFICATION: ("user_mention", "notification"),
    FAILURE_TASK: ("task", "task_id", "attempts"),
    FAILURE_ERROR: ("source", "error_id", "error"),
    FAILURE_ROLE: ("user_mention", "role_mention"),
}


def order_alerts(settings: Settings) -> tuple[str, ...]:
    """The order alerts this configuration sends (the payment-method alert only with a method our team handles)."""
    if any(method.kind == "staff" for method in settings.payments.methods):
        return (*ORDER_ALERTS, PAYMENT_METHOD)
    return ORDER_ALERTS


def alert_placeholders(alert: str) -> tuple[str, ...]:
    return PAYMENT_METHOD_PLACEHOLDERS if alert == PAYMENT_METHOD else ORDER_PLACEHOLDERS


def alert_key(alert: str, part: str) -> str:
    return f"operations.alert.{alert}.{part}"


def owner_key(alert: str, part: str) -> str:
    return f"operations.owner.{alert}.{part}"


def reminder_key(kind: str, part: str) -> str:
    return f"operations.reminder.{kind}.{part}"


def failure_key(kind: str, part: str) -> str:
    return f"operations.failure.{kind}.{part}"


def owner_failure_key(kind: str, part: str) -> str:
    return f"operations.owner.failure_{kind}.{part}"


_ORDER_DOC = (
    "{order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order "
    "total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel."
)
_ALERT_DOC = {
    NEW_ORDER: "a customer submitted a new order",
    PAYMENT_SUBMITTED: "a customer submitted their payment (gift cards covering the total) and it waits to be verified",
    PAID_ORDER: "an order's payment was confirmed and it waits to be shipped",
    PAYMENT_METHOD: (
        'a customer chose a payment method our team handles (payments.methods kind "staff") and waits for the '
        "payment details in their order ticket ({method} is the method's name)"
    ),
}
_REMINDER_DOC = {
    repo.UNCLAIMED: ("an open order nobody claimed", "operations.unclaimed_order_minutes"),
    repo.UNPAID: ("an order still waiting for payment", "operations.unpaid_order_minutes"),
    repo.UNFULFILLED: ("a paid order not shipped yet", "operations.unfulfilled_order_minutes"),
    repo.UNANSWERED: (
        "a support ticket whose customer waits for a staff reply",
        "operations.unanswered_ticket_minutes",
    ),
}
_FAILURE_DOC = {
    FAILURE_NOTIFICATION: (
        "a notification could not be delivered: a private notification to a member, neither by direct message nor in "
        "the fallback channel, or a message the bot posts in a channel (e.g. a ticket's staff alert or an order's "
        "details). {user_mention} mentions the member it was for (no ping; '-' when it was for staff), "
        "{notification} is the notification's internal name (e.g. checkout.paid)"
    ),
    FAILURE_TASK: (
        "a scheduled task failed permanently after its retries (details are in the audit log). {task} is the task's "
        "internal name, {task_id} its number, {attempts} the attempts made"
    ),
    FAILURE_ERROR: (
        "a button, form or command failed unexpectedly (the member got the configured error message; details are in "
        "the bot logs). {source} is the internal name of the control or command, {error_id} the reference the member "
        "saw, {error} the error type"
    ),
    FAILURE_ROLE: (
        "the customer role could not be given or removed (missing role, role above the bot's highest role, or missing "
        "permission). {user_mention} mentions the member (no ping), {role_mention} the role"
    ),
}


def content_keys(settings: Settings) -> list[ContentKey]:
    o = settings.operations
    g_alerts = "Operations: staff alerts (staff channel)"
    g_owner = "Operations: the Owner's private alerts"
    g_reminders = "Operations: staff reminders (staff channel)"
    g_failures = "Operations: failure alerts (staff channel)"
    keys: list[ContentKey] = []
    for alert in order_alerts(settings):
        doc = f"Staff alert when {_ALERT_DOC[alert]} (operations.ping_roles are mentioned). {_ORDER_DOC}"
        alert_values = alert_placeholders(alert)
        keys += [
            ck(
                alert_key(alert, "title"),
                Kind.TITLE,
                FEATURE,
                g_alerts,
                f"Optional title of the {doc}",
                required=False,
                placeholders=alert_values,
            ),
            ck(
                alert_key(alert, "description"),
                Kind.DESCRIPTION,
                FEATURE,
                g_alerts,
                doc,
                placeholders=alert_values,
            ),
            ck(
                owner_key(alert, "title"),
                Kind.TITLE,
                FEATURE,
                g_owner,
                f"Optional title of the Owner's private alert when {_ALERT_DOC[alert]} (empty = the staff alert's "
                "title). Placeholders as in the staff alert.",
                placeholders=alert_values,
                fallback=alert_key(alert, "title"),
            ),
            ck(
                owner_key(alert, "description"),
                Kind.DESCRIPTION,
                FEATURE,
                g_owner,
                f"Optional text of the Owner's private alert when {_ALERT_DOC[alert]} (empty = the staff alert's "
                "text). Placeholders as in the staff alert.",
                placeholders=alert_values,
                fallback=alert_key(alert, "description"),
            ),
        ]
    if o.stock_alerts:
        stock_doc = {
            LOW_STOCK: "an order left an item with catalog.low_stock_threshold units or fewer",
            SOLD_OUT: "an order sold an item out",
        }
        for alert in STOCK_ALERTS:
            doc = (
                f"Staff alert when {stock_doc[alert]} (once per drop; operations.ping_roles are mentioned). {{name}} "
                "is the item's name, {count} the units that can still be ordered."
            )
            keys += [
                ck(
                    alert_key(alert, "title"),
                    Kind.TITLE,
                    FEATURE,
                    g_alerts,
                    f"Optional title of the {doc}",
                    required=False,
                    placeholders=STOCK_PLACEHOLDERS,
                ),
                ck(
                    alert_key(alert, "description"),
                    Kind.DESCRIPTION,
                    FEATURE,
                    g_alerts,
                    doc,
                    placeholders=STOCK_PLACEHOLDERS,
                ),
            ]
    keys += [
        ck(
            BUTTON_OPEN_RECORD,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_alerts,
            "Optional link button under order alerts and order reminders that jumps to the order's live record in "
            "checkout.staff_channel.",
            required=False,
        ),
        ck(
            BUTTON_OPEN_TICKET,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_reminders,
            "Optional link button that jumps to the ticket: under unanswered-ticket reminders and under the order "
            "alerts (next to operations.button.open_record).",
            required=False,
        ),
    ]
    placeholders: tuple[str, ...]
    for kind in repo.REMINDER_KINDS:
        what, setting = _REMINDER_DOC[kind]
        minutes = getattr(o, setting.split(".", 1)[1])
        if kind == repo.UNANSWERED:
            placeholders = REMINDER_TICKET_PLACEHOLDERS
            values = (
                "{ticket_number} is the ticket number, {customer_mention} mentions the customer (no ping), "
                "{ticket_channel} links the ticket"
            )
        else:
            placeholders = REMINDER_ORDER_PLACEHOLDERS
            values = _ORDER_DOC.rstrip(".")
        doc = (
            f"Staff reminder about {what}, posted once it waited {setting} minutes (again every "
            f"operations.reminder_repeat_minutes while it still waits, when set). {values}; {{waiting_since}} is "
            "when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes."
        )
        keys += [
            ck(
                reminder_key(kind, "title"),
                Kind.TITLE,
                FEATURE,
                g_reminders,
                f"Optional title of the {doc}",
                required=False,
                placeholders=placeholders,
            ),
            ck(
                reminder_key(kind, "description"),
                Kind.DESCRIPTION,
                FEATURE,
                g_reminders,
                f"{doc} Needed when {setting} is above 0.",
                required=minutes > 0,
                placeholders=placeholders,
            ),
        ]
    for kind in FAILURES:
        doc = f"Staff alert when {_FAILURE_DOC[kind]}."
        placeholders = FAILURE_PLACEHOLDERS[kind]
        keys += [
            ck(
                failure_key(kind, "title"),
                Kind.TITLE,
                FEATURE,
                g_failures,
                f"Optional title of the {doc}",
                required=False,
                placeholders=placeholders,
            ),
            ck(failure_key(kind, "description"), Kind.DESCRIPTION, FEATURE, g_failures, doc, placeholders=placeholders),
        ]
        if kind in CRITICAL_FAILURES:
            keys += [
                ck(
                    owner_failure_key(kind, "title"),
                    Kind.TITLE,
                    FEATURE,
                    g_owner,
                    f"Optional title of the Owner's private alert when {_FAILURE_DOC[kind].split('.')[0]} (empty = "
                    "the staff alert's title).",
                    placeholders=placeholders,
                    fallback=failure_key(kind, "title"),
                ),
                ck(
                    owner_failure_key(kind, "description"),
                    Kind.DESCRIPTION,
                    FEATURE,
                    g_owner,
                    "Optional text of the Owner's private alert about the same failure (empty = the staff alert's "
                    "text). Placeholders as in the staff alert.",
                    placeholders=placeholders,
                    fallback=failure_key(kind, "description"),
                ),
            ]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    o = settings.operations
    problems: list[str] = []
    if not o.staff_channel:
        problems.append("operations.staff_channel: required (staff alerts and reminders are posted there)")
    problems.extend(
        reference_problems(settings, roles=o.ping_roles, channels=(("operations.staff_channel", o.staff_channel),))
    )
    # Alerts show order numbers, totals and member mentions: the channel must be staff-only.
    problems.extend(private_channel_problems(settings, (("operations.staff_channel", o.staff_channel),)))
    role = settings.checkout.customer_role
    if role and settings.server.role(role) is None:
        problems.append(f"checkout.customer_role: role '{role}' is not defined in server.roles")
    return problems
