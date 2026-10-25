"""Content keys, shared keys, settings checks, and cross-key content checks of the ticket system.

Every customer-facing string the ticket system can show is declared here. Keys that are only needed
when a setting is on (select-style panel, close confirmation, reopening, transcripts, staff alerts,
automatic deletion/closing) are required only under that condition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings, TicketCategory
from shopbot.features import core_keys as core
from shopbot.features.server_keys import reference_problems

if TYPE_CHECKING:
    from shopbot.config.content import ContentStore

FEATURE = "tickets"
ROUTE = "tk"

# -- panel ------------------------------------------------------------------------------------------
PANEL_TITLE = "tickets.panel.title"
PANEL_DESCRIPTION = "tickets.panel.description"
PANEL_SELECT_PLACEHOLDER = "tickets.panel.select_placeholder"

# -- controls ---------------------------------------------------------------------------------------
BUTTON_CLOSE = "tickets.button.close"
BUTTON_CLAIM = "tickets.button.claim"
BUTTON_UNCLAIM = "tickets.button.unclaim"
BUTTON_DELETE = "tickets.button.delete"
BUTTON_TRANSCRIPT = "tickets.button.transcript"
BUTTON_REOPEN = "tickets.button.reopen"

# -- confirmations ----------------------------------------------------------------------------------
CLOSE_CONFIRM_TITLE = "tickets.close_confirm.title"
CLOSE_CONFIRM_DESCRIPTION = "tickets.close_confirm.description"
DELETE_CONFIRM_TITLE = "tickets.delete_confirm.title"
DELETE_CONFIRM_DESCRIPTION = "tickets.delete_confirm.description"

# -- in-channel status messages ---------------------------------------------------------------------
CLAIMED = "tickets.claimed.description"
UNCLAIMED = "tickets.unclaimed.description"
CLOSED_TITLE = "tickets.closed.title"
CLOSED_DESCRIPTION = "tickets.closed.description"
CLOSED_REASON_LABEL = "tickets.closed.reason_label"
REOPENED = "tickets.reopened.description"
DELETION_SCHEDULED = "tickets.deletion_scheduled.description"
AUTO_CLOSE_WARNING = "tickets.auto_close_warning.description"
AUTO_CLOSED = "tickets.auto_closed.description"
USER_ADDED = "tickets.user_added.description"
USER_REMOVED = "tickets.user_removed.description"

# -- private replies --------------------------------------------------------------------------------
CREATED = "tickets.created.description"
LIMIT_REACHED = "tickets.limit_reached.description"
NOT_OPEN = "tickets.error.not_open"

# -- staff alert ------------------------------------------------------------------------------------
STAFF_ALERT_TITLE = "tickets.staff_alert.title"
STAFF_ALERT_DESCRIPTION = "tickets.staff_alert.description"

# -- transcripts ------------------------------------------------------------------------------------
TRANSCRIPT_TITLE = "tickets.transcript.title"
TRANSCRIPT_DESCRIPTION = "tickets.transcript.description"
TRANSCRIPT_DM_TITLE = "tickets.transcript_dm.title"
TRANSCRIPT_DM_DESCRIPTION = "tickets.transcript_dm.description"

WELCOME_PLACEHOLDERS = ("user_mention", "user_name", "number")
CLOSED_PLACEHOLDERS = ("closer_mention", "number")
STAFF_ALERT_PLACEHOLDERS = ("user_mention", "channel_mention", "number", "category_label")
TRANSCRIPT_PLACEHOLDERS = ("number", "user_mention", "closer_mention")

_NUMBER_DOC = "{number} is the ticket number, zero-padded to tickets.number_padding digits."


def category_key(category_id: str, name: str) -> str:
    """Per-category key, e.g. ``tickets.category.general.button_label``."""
    return f"tickets.category.{category_id}.{name}"


def form_field_key(category_id: str, field_id: str, name: str) -> str:
    """Per-form-field key, e.g. ``tickets.category.general.form.details.label``."""
    return f"tickets.category.{category_id}.form.{field_id}.{name}"


# Discord's limit for channel names. Ticket channel names are never cut: a longer name aborts the creation.
CHANNEL_NAME_MAX = 100
# Worst-case lengths used to check tickets.channel_name_pattern: {number} (ticket numbers stay below 10
# million) and {user} (usernames are cut to 32 characters, see service.channel_user_slug).
NUMBER_DIGITS_WORST_CASE = 7
USER_SLUG_MAX = 32
DEFAULT_MAX_CREATED_PER_DAY = 10


def daily_creation_cap(settings: Settings) -> int:
    """tickets.max_created_per_user_per_day: support tickets one user may create per 24 hours (0 = unlimited)."""
    return int(getattr(settings.tickets, "max_created_per_user_per_day", DEFAULT_MAX_CREATED_PER_DAY))


def channel_name_length(pattern: str, *, prefix: int, number: int, user: int) -> int:
    """Length of tickets.channel_name_pattern once {prefix}, {number} and {user} are replaced by values of
    these lengths."""
    literal = pattern.replace("{prefix}", "").replace("{number}", "").replace("{user}", "")
    return (
        len(literal)
        + pattern.count("{prefix}") * prefix
        + pattern.count("{number}") * number
        + pattern.count("{user}") * user
    )


def auto_close_enabled(settings: Settings) -> bool:
    return settings.tickets.auto_close_inactive_minutes > 0


def auto_close_warning_enabled(settings: Settings) -> bool:
    t = settings.tickets
    return t.auto_close_inactive_minutes > 0 and 0 < t.auto_close_warning_minutes < t.auto_close_inactive_minutes


def _category_keys(category: TicketCategory) -> list[ContentKey]:
    c = category.id
    group = f"Ticket category '{c}'"
    keys = [
        ck(
            category_key(c, "button_label"),
            Kind.BUTTON_LABEL,
            FEATURE,
            group,
            f"Label of the '{c}' button on the ticket panel (also the option label when the panel uses a "
            "selection menu, and {category_label} in the staff alert).",
        ),
        ck(
            category_key(c, "button_emoji"),
            Kind.EMOJI,
            FEATURE,
            group,
            f"Optional emoji on the '{c}' panel button / selection option.",
            required=False,
        ),
        ck(
            category_key(c, "select_description"),
            Kind.OPTION_DESCRIPTION,
            FEATURE,
            group,
            f"Optional small text under the '{c}' option when the panel uses a selection menu.",
            required=False,
        ),
        ck(
            category_key(c, "channel_prefix"),
            Kind.CHANNEL_PREFIX,
            FEATURE,
            group,
            f"Channel-name prefix for '{c}' tickets ({{prefix}} in tickets.channel_name_pattern). Discord "
            "lower-cases channel names and replaces spaces with dashes.",
        ),
        ck(
            category_key(c, "channel_topic"),
            Kind.CHANNEL_TOPIC,
            FEATURE,
            group,
            f"Optional topic of each '{c}' ticket channel. {_NUMBER_DOC} {{user_name}} is the customer's display name.",
            required=False,
            placeholders=("number", "user_name"),
        ),
        ck(
            category_key(c, "welcome_title"),
            Kind.TITLE,
            FEATURE,
            group,
            f"Title of the welcome message posted in a new '{c}' ticket (the message that carries the "
            f"close/claim buttons). {_NUMBER_DOC} Mentions do not render in titles; use {{user_name}}.",
            placeholders=WELCOME_PLACEHOLDERS,
        ),
        ck(
            category_key(c, "welcome_description"),
            Kind.DESCRIPTION,
            FEATURE,
            group,
            f"Text of the welcome message posted in a new '{c}' ticket. {{user_mention}} mentions the "
            f"customer, {{user_name}} is their display name. {_NUMBER_DOC} Form answers (if the category "
            "has a form) are added below as fields named by the form labels.",
            placeholders=WELCOME_PLACEHOLDERS,
        ),
    ]
    if category.form:
        keys.append(
            ck(
                category_key(c, "form_title"),
                Kind.MODAL_TITLE,
                FEATURE,
                group,
                f"Title of the form shown before a '{c}' ticket is created.",
            )
        )
        for field in category.form:
            f = field.id
            keys += [
                ck(
                    form_field_key(c, f, "label"),
                    Kind.INPUT_LABEL,
                    FEATURE,
                    group,
                    f"Label of the '{f}' form field (also the field name of the answer in the welcome message).",
                ),
                ck(
                    form_field_key(c, f, "description"),
                    Kind.INPUT_DESCRIPTION,
                    FEATURE,
                    group,
                    f"Optional help text under the '{f}' form field label.",
                    required=False,
                ),
                ck(
                    form_field_key(c, f, "placeholder"),
                    Kind.INPUT_PLACEHOLDER,
                    FEATURE,
                    group,
                    f"Optional greyed-out example text inside the empty '{f}' form field.",
                    required=False,
                ),
            ]
    return keys


def content_keys(settings: Settings) -> list[ContentKey]:
    t = settings.tickets
    transcripts = bool(t.transcript_channel)
    alerts = bool(t.staff_alert_channel)
    g_panel = "Ticket panel"
    g_buttons = "Ticket buttons"
    g_confirm = "Ticket confirmation prompts (private)"
    g_status = "Ticket status messages (posted in the ticket channel)"
    g_private = "Ticket private replies"
    g_alert = "New-ticket staff alert"
    g_transcript = "Ticket transcripts"
    keys = [
        ck(PANEL_TITLE, Kind.TITLE, FEATURE, g_panel, "Title of the ticket panel (the message with the open buttons)."),
        ck(PANEL_DESCRIPTION, Kind.DESCRIPTION, FEATURE, g_panel, "Text of the ticket panel."),
        ck(
            PANEL_SELECT_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            FEATURE,
            g_panel,
            'Greyed-out text of the category selection menu (used when tickets.panel_style = "select").',
            required=t.panel_style == "select",
        ),
        ck(BUTTON_CLOSE, Kind.BUTTON_LABEL, FEATURE, g_buttons, "Close button on the ticket welcome message."),
        ck(
            BUTTON_CLAIM,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_buttons,
            "Claim button on the ticket welcome message (staff take ownership of the ticket).",
        ),
        ck(
            BUTTON_UNCLAIM,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_buttons,
            "Unclaim button that replaces the claim button while a staff member has claimed the ticket.",
        ),
        ck(
            BUTTON_DELETE,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_buttons,
            "Delete button on the ticket-closed message (staff only; asks for confirmation).",
        ),
        ck(
            BUTTON_TRANSCRIPT,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_buttons,
            "Transcript button on the ticket-closed message (staff only; posts a transcript to the "
            "transcript channel). Needed when tickets.transcript_channel is set.",
            required=transcripts,
        ),
        ck(
            BUTTON_REOPEN,
            Kind.BUTTON_LABEL,
            FEATURE,
            g_buttons,
            "Reopen button on the ticket-closed message. Needed when tickets.allow_reopen = true.",
            required=t.allow_reopen,
        ),
        ck(
            CLOSE_CONFIRM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_confirm,
            "Optional title of the private prompt asking to confirm closing a ticket.",
            required=False,
        ),
        ck(
            CLOSE_CONFIRM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_confirm,
            "Text of the private prompt asking to confirm closing a ticket (uses the shared confirm/cancel "
            "buttons). Needed when tickets.close_confirmation = true.",
            required=t.close_confirmation,
        ),
        ck(
            DELETE_CONFIRM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_confirm,
            "Optional title of the private prompt asking staff to confirm deleting a ticket channel.",
            required=False,
        ),
        ck(
            DELETE_CONFIRM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_confirm,
            "Text of the private prompt asking staff to confirm deleting a ticket channel (uses the shared "
            "confirm/cancel buttons).",
        ),
        ck(
            CLAIMED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in the ticket when a staff member claims it. {staff_mention} mentions that staff member.",
            placeholders=("staff_mention",),
        ),
        ck(UNCLAIMED, Kind.DESCRIPTION, FEATURE, g_status, "Posted in the ticket when it is unclaimed."),
        ck(
            CLOSED_TITLE,
            Kind.TITLE,
            FEATURE,
            g_status,
            "Title of the message posted in the ticket when it is closed (manually or automatically); "
            f"this message carries the reopen/transcript/delete buttons. {_NUMBER_DOC}",
            placeholders=("number",),
        ),
        ck(
            CLOSED_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Text of the ticket-closed message when someone closed it. {closer_mention} mentions who "
            f"closed it. {_NUMBER_DOC}",
            placeholders=CLOSED_PLACEHOLDERS,
        ),
        ck(
            CLOSED_REASON_LABEL,
            Kind.FIELD_NAME,
            FEATURE,
            g_status,
            "Optional field name under which the close reason given by staff (/ticket close reason) is "
            "shown on the ticket-closed message. If empty, the reason is not shown to the customer.",
            required=False,
        ),
        ck(
            REOPENED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in the ticket when it is reopened. {actor_mention} mentions who reopened it. Needed "
            "when tickets.allow_reopen = true.",
            required=t.allow_reopen,
            placeholders=("actor_mention",),
        ),
        ck(
            DELETION_SCHEDULED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in the ticket after closing when the channel will be deleted automatically. {minutes} "
            "is tickets.delete_after_close_minutes. Needed when that setting is above 0.",
            required=t.delete_after_close_minutes > 0,
            placeholders=("minutes",),
        ),
        ck(
            AUTO_CLOSE_WARNING,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in an inactive ticket (and pings the customer) tickets.auto_close_warning_minutes before "
            "it closes automatically. {minutes} is that number of minutes, {user_mention} mentions the "
            "customer. Needed when both auto-close settings are above 0.",
            required=auto_close_warning_enabled(settings),
            placeholders=("minutes", "user_mention"),
        ),
        ck(
            AUTO_CLOSED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Text of the ticket-closed message when the ticket was closed automatically for inactivity "
            f"(used instead of tickets.closed.description). {_NUMBER_DOC} Needed when "
            "tickets.auto_close_inactive_minutes is above 0.",
            required=auto_close_enabled(settings),
            placeholders=("number",),
        ),
        ck(
            USER_ADDED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in the ticket when staff add someone to it (/ticket add). {user_mention} mentions "
            "(and pings) the added member.",
            placeholders=("user_mention",),
        ),
        ck(
            USER_REMOVED,
            Kind.DESCRIPTION,
            FEATURE,
            g_status,
            "Posted in the ticket when staff remove someone from it (/ticket remove). {user_mention} "
            "mentions the removed member.",
            placeholders=("user_mention",),
        ),
        ck(
            CREATED,
            Kind.DESCRIPTION,
            FEATURE,
            g_private,
            "Private reply after a ticket was created. {channel_mention} links the new ticket channel. "
            f"{_NUMBER_DOC}",
            placeholders=("channel_mention", "number"),
        ),
        ck(
            LIMIT_REACHED,
            Kind.DESCRIPTION,
            FEATURE,
            g_private,
            "Private error when the customer reached a ticket limit: the maximum number of open tickets in "
            "that category (also checked when they reopen their own closed ticket), or the maximum number of "
            "tickets created in 24 hours (tickets.max_created_per_user_per_day). {limit} is that maximum.",
            placeholders=("limit",),
        ),
        ck(
            NOT_OPEN,
            Kind.DESCRIPTION,
            FEATURE,
            g_private,
            "Private error when a ticket button cannot be used in the ticket's current state (e.g. closing "
            "an already closed ticket, claiming an already claimed ticket, deleting an open ticket).",
        ),
        ck(
            STAFF_ALERT_TITLE,
            Kind.TITLE,
            FEATURE,
            g_alert,
            f"Title of the alert posted in the staff alert channel for each new ticket. {_NUMBER_DOC} "
            "Needed when tickets.staff_alert_channel is set.",
            required=alerts,
            placeholders=("number", "category_label"),
        ),
        ck(
            STAFF_ALERT_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_alert,
            "Text of the new-ticket staff alert. {user_mention} mentions the customer, {channel_mention} "
            "links the ticket, {category_label} is the category's button label. "
            f"{_NUMBER_DOC} Needed when tickets.staff_alert_channel is set.",
            required=alerts,
            placeholders=STAFF_ALERT_PLACEHOLDERS,
        ),
        ck(
            TRANSCRIPT_TITLE,
            Kind.TITLE,
            FEATURE,
            g_transcript,
            f"Title of the message (with the transcript file) posted in the transcript channel. {_NUMBER_DOC} "
            "Needed when tickets.transcript_channel is set.",
            required=transcripts,
            placeholders=("number",),
        ),
        ck(
            TRANSCRIPT_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_transcript,
            "Text of the transcript message in the transcript channel. {user_mention} mentions the customer; "
            "{closer_mention} mentions who closed the ticket (the bot for automatic closes, or the staff "
            f"member who requested the transcript while the ticket was open). {_NUMBER_DOC} Needed when "
            "tickets.transcript_channel is set.",
            required=transcripts,
            placeholders=TRANSCRIPT_PLACEHOLDERS,
        ),
        ck(
            TRANSCRIPT_DM_TITLE,
            Kind.TITLE,
            FEATURE,
            g_transcript,
            f"Title of the direct message that sends the customer their transcript file. {_NUMBER_DOC} Needed "
            "when tickets.transcript_dm_user = true and a transcript channel is set.",
            required=transcripts and t.transcript_dm_user,
            placeholders=("number",),
        ),
        ck(
            TRANSCRIPT_DM_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            g_transcript,
            f"Text of the direct message that sends the customer their transcript file. {_NUMBER_DOC} Needed "
            "when tickets.transcript_dm_user = true and a transcript channel is set.",
            required=transcripts and t.transcript_dm_user,
            placeholders=("number",),
        ),
    ]
    for category in t.categories:
        keys += _category_keys(category)
    return keys


def shared_keys(settings: Settings) -> list[str]:
    """Shared confirm/cancel buttons: the delete confirmation always uses them."""
    del settings
    return [core.BUTTON_CONFIRM, core.BUTTON_CANCEL]


def settings_problems(settings: Settings) -> list[str]:
    t = settings.tickets
    problems: list[str] = []
    if not t.panel_channel:
        problems.append("tickets.panel_channel: required (the channel that shows the ticket panel)")
    if not t.categories:
        problems.append("tickets.categories: define at least one ticket category")
    channels = (
        ("tickets.panel_channel", t.panel_channel),
        ("tickets.transcript_channel", t.transcript_channel),
        ("tickets.staff_alert_channel", t.staff_alert_channel),
    )
    categories = [("tickets.closed_category", t.closed_category)]
    categories += [(f"tickets.categories '{c.id}'.discord_category", c.discord_category) for c in t.categories]
    problems.extend(reference_problems(settings, channels=channels, categories=tuple(categories)))
    role_keys = list(t.staff_roles)
    for category in t.categories:
        role_keys += [key for key in category.staff_roles if key not in role_keys]
    problems.extend(reference_problems(settings, roles=tuple(role_keys)))
    if (
        t.auto_close_warning_minutes > 0
        and t.auto_close_inactive_minutes > 0
        and t.auto_close_warning_minutes >= t.auto_close_inactive_minutes
    ):
        problems.append(
            "tickets.auto_close_warning_minutes: must be less than tickets.auto_close_inactive_minutes "
            "(the warning is sent that many minutes before the automatic close)"
        )
    return problems


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    """Cross-key rules: the longest possible ticket channel name must fit Discord's limit."""
    t = settings.tickets
    prefixes = [(len(content.get(category_key(c.id, "channel_prefix")) or ""), c.id) for c in t.categories]
    longest, category_id = max(prefixes, default=(0, ""))
    worst = channel_name_length(
        t.channel_name_pattern,
        prefix=longest,
        number=max(t.number_padding, NUMBER_DIGITS_WORST_CASE),
        user=USER_SLUG_MAX if "{user}" in t.channel_name_pattern else 0,
    )
    if worst <= CHANNEL_NAME_MAX:
        return []
    where = f" with {category_key(category_id, 'channel_prefix')}" if category_id and longest else ""
    return [
        f"tickets.channel_name_pattern: ticket channel names can reach {worst} characters{where} "
        f"(Discord allows {CHANNEL_NAME_MAX}; names are never cut). Shorten the pattern, the channel prefix, "
        "or tickets.number_padding."
    ]
