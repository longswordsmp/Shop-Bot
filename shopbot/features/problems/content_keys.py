"""Content keys of the Report a Problem workflow, its shared keys, and its settings checks.

Every word the workflow can show to a customer is a key here: the #purchase button, the private choice step, the
issue types and their forms, the ticket panel, the decision names, the customer notifications and the staff alert.
Keys that only appear under a setting (choosing an order, several issue types, the "not about an order" option,
the staff alert) are required only then. The staff-only decision forms and staff replies use operator text
(``text.OPERATOR_TEXT``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import ProblemType, Settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout.models import ORDER_STATUSES
from shopbot.features.server_keys import channel_placeholders, private_channel_problems, reference_problems
from shopbot.features.tickets import content_keys as tk

if TYPE_CHECKING:
    from shopbot.config.content import ContentStore

FEATURE = "problems"
ROUTE = "pr"
# Problem-report tickets are support tickets of this category id. It contains a dash, so it can never collide with a
# configured ticket category (those ids match KEY_PATTERN).
TICKET_CATEGORY = "problem-report"
# Longest staff reason / note, in characters. Escaped (at most twice as long) it still fits one embed field.
REASON_MAX = 500

OPEN = "open"
RESOLVED = "resolved"
REJECTED = "rejected"
STATUSES = (OPEN, RESOLVED, REJECTED)
DECISIONS = (RESOLVED, REJECTED)

# -- #purchase panel button -----------------------------------------------------------------------------------
BUTTON_REPORT = "problems.button.report"
BUTTON_REPORT_EMOJI = "problems.button.report_emoji"

# -- private choice step ---------------------------------------------------------------------------------------
CHOOSE_TITLE = "problems.choose.title"
CHOOSE_DESCRIPTION = "problems.choose.description"
ORDER_PLACEHOLDER = "problems.choose.order_placeholder"
ORDER_OPTION = "problems.choose.order_option"
ORDER_OPTION_DESCRIPTION = "problems.choose.order_option_description"
NO_ORDER_OPTION = "problems.choose.no_order_option"
NO_ORDER_OPTION_DESCRIPTION = "problems.choose.no_order_option_description"
TYPE_PLACEHOLDER = "problems.choose.type_placeholder"
ATTACHMENTS_NOTE = "problems.attachments_note"

# -- private replies -------------------------------------------------------------------------------------------
CREATED_TITLE = "problems.created.title"
CREATED = "problems.created.description"
LIMIT_REACHED_TITLE = "problems.limit_reached.title"
LIMIT_REACHED = "problems.limit_reached.description"
NO_ORDERS = "problems.no_orders.description"

# -- ticket channel and panel --------------------------------------------------------------------------------------
CHANNEL_PREFIX = "problems.channel_prefix"
CHANNEL_TOPIC = "problems.channel_topic"
TICKET_TITLE = "problems.ticket.title"
TICKET_DESCRIPTION = "problems.ticket.description"
FIELD_TYPE = "problems.ticket.type_field"
FIELD_ORDER = "problems.ticket.order_field"
ORDER_VALUE = "problems.ticket.order_value"
FIELD_STATUS = "problems.ticket.status_field"
FIELD_REASON = "problems.ticket.reason_field"
FIELD_DECIDED_BY = "problems.ticket.decided_by_field"
BUTTON_RESOLVE = "problems.button.resolve"
BUTTON_REJECT = "problems.button.reject"
DETAILS_HEADING = "problems.details.heading"

# -- staff alert -------------------------------------------------------------------------------------------------
STAFF_ALERT_TITLE = "problems.staff_alert.title"
STAFF_ALERT_DESCRIPTION = "problems.staff_alert.description"

ORDER_PLACEHOLDERS = ("order_number", "total", "status")
TICKET_TITLE_PLACEHOLDERS = ("number", "type_label", "user_name")
TICKET_PLACEHOLDERS = ("user_mention", "user_name", "number", "type_label")
NOTIFY_PLACEHOLDERS = ("number", "type_label", "channel_mention")
ALERT_TITLE_PLACEHOLDERS = ("number", "type_label")
ALERT_PLACEHOLDERS = ("user_mention", "channel_mention", "number", "type_label")

_NUMBER_DOC = "{number} is the report's ticket number (zero-padded to tickets.number_padding digits)."
_TYPE_DOC = "{type_label} is the label of the chosen issue type (problems.type.<id>.label)."
_ORDER_DOC = (
    "{order_number} is the order number, {total} the order total written with common.money_format, {status} the "
    "order's status name as the customer's order ticket writes it (checkout.ticket.stage.<status>)."
)
_CHANNEL_LINKS_DOC = "You can link server channels with {channel_<key>} (for example {channel_faq})."


def status_key(status: str) -> str:
    """Name of a report status, e.g. ``problems.status.resolved``."""
    return f"problems.status.{status}"


def notify_key(decision: str, name: str) -> str:
    """Customer notification of a decision, e.g. ``problems.notify.rejected.description``."""
    return f"problems.notify.{decision}.{name}"


def type_key(type_id: str, name: str) -> str:
    """Per-issue-type key, e.g. ``problems.type.general.label``."""
    return f"problems.type.{type_id}.{name}"


def form_field_key(type_id: str, field_id: str, name: str) -> str:
    """Per-form-field key of an issue type, e.g. ``problems.type.general.form.details.label``."""
    return f"problems.type.{type_id}.form.{field_id}.{name}"


def orders_possible(settings: Settings) -> bool:
    """Reports can refer to an order (problems.eligible_statuses lists at least one status)."""
    return bool(settings.problems.eligible_statuses)


def type_choice(settings: Settings) -> bool:
    """The customer picks the issue type (more than one type is configured)."""
    return len(settings.problems.types) > 1


def choice_possible(settings: Settings) -> bool:
    """The private choice step can be shown (an order and/or the issue type may have to be chosen)."""
    return orders_possible(settings) or type_choice(settings)


def alerts_enabled(settings: Settings) -> bool:
    """New reports are announced in the staff alert channel (tickets.staff_alert_channel)."""
    return bool(settings.tickets.staff_alert_channel)


def _type_keys(problem_type: ProblemType) -> list[ContentKey]:
    t = problem_type.id
    group = f"Report a Problem: issue type '{t}'"
    keys = [
        ck(
            type_key(t, "label"),
            Kind.OPTION_LABEL,
            FEATURE,
            group,
            f"Name of the '{t}' issue type: the option in the issue-type menu of the private report step, and "
            "{type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision "
            "message.",
        ),
        ck(
            type_key(t, "description"),
            Kind.OPTION_DESCRIPTION,
            FEATURE,
            group,
            f"Optional small text under the '{t}' option in the issue-type menu (shown when several types exist).",
            required=False,
        ),
        ck(
            type_key(t, "emoji"),
            Kind.EMOJI,
            FEATURE,
            group,
            f"Optional emoji of the '{t}' option in the issue-type menu.",
            required=False,
        ),
    ]
    if problem_type.form:
        keys.append(
            ck(
                type_key(t, "form_title"),
                Kind.MODAL_TITLE,
                FEATURE,
                group,
                f"Title of the form in which the customer describes a '{t}' problem, before the ticket is created "
                "(opened by the choice step's Continue button, or right away when there is nothing to choose).",
            )
        )
        for field in problem_type.form:
            f = field.id
            keys += [
                ck(
                    form_field_key(t, f, "label"),
                    Kind.INPUT_LABEL,
                    FEATURE,
                    group,
                    f"Label of the '{f}' field of the '{t}' form (also the question shown in bold above the answer on "
                    "the report's ticket).",
                ),
                ck(
                    form_field_key(t, f, "description"),
                    Kind.INPUT_DESCRIPTION,
                    FEATURE,
                    group,
                    f"Optional help text under the label of the '{f}' field.",
                    required=False,
                ),
                ck(
                    form_field_key(t, f, "placeholder"),
                    Kind.INPUT_PLACEHOLDER,
                    FEATURE,
                    group,
                    f"Optional greyed-out example text inside the empty '{f}' field.",
                    required=False,
                ),
            ]
    return keys


def content_keys(settings: Settings) -> list[ContentKey]:
    p = settings.problems
    orders = orders_possible(settings)
    choice = choice_possible(settings)
    links = channel_placeholders(settings)
    g_button = "Report a Problem: button on the #purchase panel"
    g_choose = "Report a Problem: private choice step (shown only to the customer)"
    g_replies = "Report a Problem: private replies"
    g_ticket = "Report a Problem: the report's private ticket"
    g_status = "Report a Problem: report statuses and decisions"
    g_notify = "Report a Problem: decision messages to the customer"
    g_alert = "Report a Problem: staff alert"
    keys = [
        ck(
            BUTTON_REPORT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_button,
            "Label of the Report a Problem button on the #purchase panel (next to the Start button). The button "
            "appears once the Report a Problem feature is fully configured (run /setup to update the panel).",
        ),
        ck(
            BUTTON_REPORT_EMOJI,
            Kind.EMOJI,
            FEATURE,
            g_button,
            "Optional emoji on the Report a Problem button.",
            required=False,
        ),
        ck(
            CHOOSE_TITLE,
            Kind.TITLE,
            FEATURE,
            g_choose,
            "Optional title of the private message in which the customer chooses the order the problem is about "
            "and/or the issue type.",
            required=False,
        ),
        ck(
            CHOOSE_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_choose,
            "Text of the private message in which the customer chooses the order the problem is about (their "
            "orders in problems.eligible_statuses, newest first) and/or the issue type, then presses the shared "
            "Continue button to describe the problem. Shown only when there is something to choose. "
            f"{_CHANNEL_LINKS_DOC}",
            required=choice,
            placeholders=links,
        ),
        ck(
            ORDER_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_choose,
            "Greyed-out text of the order menu in the private choice step. Needed when problems.eligible_statuses "
            "is not empty.",
            required=orders,
        ),
        ck(
            ORDER_OPTION,
            Kind.OPTION_LABEL,
            FEATURE,
            g_choose,
            f"Label of each order in the order menu. {_ORDER_DOC} Needed when problems.eligible_statuses is not empty.",
            required=orders,
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            ORDER_OPTION_DESCRIPTION,
            Kind.OPTION_DESCRIPTION,
            FEATURE,
            g_choose,
            f"Optional small text under each order in the order menu. {_ORDER_DOC}",
            required=False,
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            NO_ORDER_OPTION,
            Kind.OPTION_LABEL,
            FEATURE,
            g_choose,
            "Label of the last option of the order menu, for a problem that is not about one of the listed orders. "
            "Needed when problems.allow_without_order = true and problems.eligible_statuses is not empty.",
            required=orders and p.allow_without_order,
        ),
        ck(
            NO_ORDER_OPTION_DESCRIPTION,
            Kind.OPTION_DESCRIPTION,
            FEATURE,
            g_choose,
            "Optional small text under the 'not about an order' option.",
            required=False,
        ),
        ck(
            TYPE_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_choose,
            "Greyed-out text of the issue-type menu in the private choice step. Needed when more than one "
            "[[problems.types]] is configured.",
            required=type_choice(settings),
        ),
        ck(
            ATTACHMENTS_NOTE,
            Kind.DESCRIPTION,
            FEATURE,
            g_choose,
            "Tells the customer that screenshots or files can be uploaded in the private ticket once it is open "
            "(the ticket channel allows attachments). Shown below the confirmation after the report was created "
            "and on the report's ticket panel while the report is open.",
        ),
        ck(
            CREATED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_replies,
            f"Optional title of the private confirmation after the report's ticket was created. {_NUMBER_DOC}",
            required=False,
            placeholders=("number",),
        ),
        ck(
            CREATED,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private confirmation after the report's ticket was created. {channel_mention} links the ticket; "
            f"{_NUMBER_DOC} Also shown when the same report form is submitted again.",
            placeholders=("channel_mention", "number"),
        ),
        ck(
            LIMIT_REACHED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_replies,
            "Optional title of the private reply when the customer already has the maximum number of open reports.",
            required=False,
        ),
        ck(
            LIMIT_REACHED,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply when the customer already has problems.max_open_per_user undecided reports with an open "
            "ticket. {limit} is that number; {channel_mention} links the ticket of their newest open report (a line "
            "with {channel_mention} is left out when that ticket cannot be linked).",
            placeholders=("limit", "channel_mention"),
        ),
        ck(
            NO_ORDERS,
            Kind.DESCRIPTION,
            FEATURE,
            g_replies,
            "Private reply when the customer has no order a report may refer to (statuses in "
            "problems.eligible_statuses). Needed when problems.allow_without_order = false. "
            f"{_CHANNEL_LINKS_DOC}",
            required=not p.allow_without_order,
            placeholders=links,
        ),
        ck(
            CHANNEL_PREFIX,
            Kind.CHANNEL_PREFIX,
            FEATURE,
            g_ticket,
            "Channel-name prefix of problem-report tickets ({prefix} in tickets.channel_name_pattern). Discord "
            "lower-cases channel names and replaces spaces with dashes.",
        ),
        ck(
            CHANNEL_TOPIC,
            Kind.CHANNEL_TOPIC,
            FEATURE,
            g_ticket,
            f"Optional topic of each problem-report ticket channel. {_NUMBER_DOC} {{user_name}} is the customer's "
            "display name.",
            required=False,
            placeholders=("number", "user_name"),
        ),
        ck(
            TICKET_TITLE,
            Kind.TITLE,
            FEATURE,
            g_ticket,
            f"Title of the report panel posted in the new ticket (the live message that shows the report and its "
            f"status). {_NUMBER_DOC} {_TYPE_DOC} {{user_name}} is the customer's display name.",
            placeholders=TICKET_TITLE_PLACEHOLDERS,
        ),
        ck(
            TICKET_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_ticket,
            "Text of the report panel, for example that only the customer and authorized staff can see this "
            "ticket. {user_mention} mentions the customer, {user_name} is their display name. "
            f"{_NUMBER_DOC} {_TYPE_DOC} {_CHANNEL_LINKS_DOC} The form answers are posted below the panel "
            "(problems.details.heading).",
            placeholders=(*TICKET_PLACEHOLDERS, *links),
        ),
        ck(
            DETAILS_HEADING,
            Kind.TITLE,
            FEATURE,
            g_ticket,
            "Optional heading of the card with the customer's form answers, posted below the report panel, shown in "
            "bold (it may start with an icon, e.g. {icon_file}). Each question follows in bold with the answer under "
            "it. If empty, the answers are shown without a heading.",
            required=False,
        ),
        ck(FIELD_TYPE, Kind.FIELD_NAME, FEATURE, g_ticket, "Field name of the issue type on the report panel."),
        ck(
            FIELD_ORDER,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Field name of the order the report is about (report panel, staff alert, decision message; omitted "
            "when the report is not about an order). Needed when problems.eligible_statuses is not empty.",
            required=orders,
        ),
        ck(
            ORDER_VALUE,
            Kind.FIELD_VALUE,
            FEATURE,
            g_ticket,
            f"The order the report is about, shown under problems.ticket.order_field. {_ORDER_DOC} Needed when "
            "problems.eligible_statuses is not empty.",
            required=orders,
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            FIELD_STATUS,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Field name of the report status on the report panel and in the decision message.",
        ),
        ck(
            FIELD_REASON,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Field name of the reason (rejection) or note (resolution) staff gave, shown exactly as staff wrote it "
            "on the report panel and in the decision message (omitted when staff gave none).",
        ),
        ck(
            FIELD_DECIDED_BY,
            Kind.FIELD_NAME,
            FEATURE,
            g_ticket,
            "Optional field name under which the staff member who decided is mentioned on the report panel. If "
            "empty, it is not shown.",
            required=False,
        ),
        ck(
            BUTTON_RESOLVE,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_ticket,
            "Resolve button on the report panel (staff only; opens a form for an optional note to the customer).",
        ),
        ck(
            BUTTON_REJECT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_ticket,
            "Reject button on the report panel (staff only; opens a form for the reason, required when "
            "problems.require_reason_on_reject = true).",
        ),
    ]
    status_docs = {
        OPEN: "waiting for a staff decision",
        RESOLVED: "staff resolved the report",
        REJECTED: "staff rejected the report",
    }
    for status in STATUSES:
        keys.append(
            ck(
                status_key(status),
                Kind.FIELD_VALUE,
                FEATURE,
                g_status,
                f"Name of the report status '{status}' ({status_docs[status]}), shown on the report panel (and in the "
                "decision message). It may start with an icon, e.g. {icon_check}.",
            )
        )
    for decision in DECISIONS:
        keys += [
            ck(
                notify_key(decision, "title"),
                Kind.TITLE,
                FEATURE,
                g_notify,
                f"Optional title of the private message telling the customer their report was {decision}. "
                f"{_NUMBER_DOC} {_TYPE_DOC}",
                required=False,
                placeholders=ALERT_TITLE_PLACEHOLDERS,
            ),
            ck(
                notify_key(decision, "description"),
                Kind.DESCRIPTION,
                FEATURE,
                g_notify,
                f"Text of the private message telling the customer their report was {decision} (staff's "
                "reason/note follows under problems.ticket.reason_field). Sent by direct message when "
                "features.dm_notifications is on; the report panel in the ticket always shows the decision, and "
                "this message is posted in the ticket when the panel cannot be updated. {channel_mention} links the "
                f"ticket (a line with it is left out when the ticket cannot be linked). {_NUMBER_DOC} {_TYPE_DOC}",
                placeholders=NOTIFY_PLACEHOLDERS,
            ),
        ]
    keys += [
        ck(
            STAFF_ALERT_TITLE,
            Kind.TITLE,
            FEATURE,
            g_alert,
            f"Title of the alert posted in the staff alert channel (tickets.staff_alert_channel) for each new "
            f"report. {_NUMBER_DOC} {_TYPE_DOC} Needed when tickets.staff_alert_channel is set.",
            required=alerts_enabled(settings),
            placeholders=ALERT_TITLE_PLACEHOLDERS,
        ),
        ck(
            STAFF_ALERT_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_alert,
            "Text of the new-report staff alert (the report details stay in the private ticket). {user_mention} "
            f"mentions the customer, {{channel_mention}} links the ticket. {_NUMBER_DOC} {_TYPE_DOC} Needed when "
            "tickets.staff_alert_channel is set.",
            required=alerts_enabled(settings),
            placeholders=ALERT_PLACEHOLDERS,
        ),
    ]
    for problem_type in p.types:
        keys += _type_keys(problem_type)
    return keys


def shared_keys(settings: Settings) -> list[str]:
    """Keys of other features the workflow shows: the shared Continue button (choice step), the ticket Close button
    (report panel), the money format and order status names (order menu and order field)."""
    keys = [tk.BUTTON_CLOSE]
    if choice_possible(settings):
        keys.append(core.BUTTON_CONTINUE)
    if orders_possible(settings):
        keys.append(core.MONEY_FORMAT)
        keys += [checkout_keys.stage_key(status) for status in settings.problems.eligible_statuses]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    p = settings.problems
    problems: list[str] = []
    if not p.discord_category:
        problems.append("problems.discord_category: required (the category where problem-report tickets are created)")
    if not p.types:
        problems.append("problems.types: define at least one issue type ([[problems.types]] with an id)")
    unknown = [status for status in p.eligible_statuses if status not in ORDER_STATUSES]
    if unknown:
        problems.append(f"problems.eligible_statuses: unknown order statuses {unknown} (known: {list(ORDER_STATUSES)})")
    if not p.eligible_statuses and not p.allow_without_order:
        problems.append(
            "problems.eligible_statuses is empty and problems.allow_without_order = false: no report could ever be made"
        )
    problems.extend(
        reference_problems(
            settings,
            roles=p.staff_roles,
            categories=(("problems.discord_category", p.discord_category),),
        )
    )
    # Staff alerts name the customer and their order: they must stay in a staff-only channel.
    problems.extend(
        private_channel_problems(settings, (("tickets.staff_alert_channel", settings.tickets.staff_alert_channel),))
    )
    return problems


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    """The longest possible problem-report channel name must fit Discord's limit (names are never cut)."""
    t = settings.tickets
    prefix = len(content.get(CHANNEL_PREFIX) or "")
    worst = tk.channel_name_length(
        t.channel_name_pattern,
        prefix=prefix,
        number=max(t.number_padding, tk.NUMBER_DIGITS_WORST_CASE),
        user=tk.USER_SLUG_MAX if "{user}" in t.channel_name_pattern else 0,
    )
    if worst <= tk.CHANNEL_NAME_MAX:
        return []
    return [
        f"tickets.channel_name_pattern: problem-report channel names can reach {worst} characters with "
        f"{CHANNEL_PREFIX} (Discord allows {tk.CHANNEL_NAME_MAX}; names are never cut). Shorten the pattern, the "
        "channel prefix, or tickets.number_padding."
    ]
