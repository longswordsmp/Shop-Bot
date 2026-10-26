"""Content keys of the staff side of orders (operations package): the live order record in the staff channel
(#orders: customer, ticket, claim, delivery, destination, amounts, tracking, timeline, internal notes and the staff
action buttons) and the customer notifications staff actions send (rejection reason, correction request, tracking
details).

These are business messages (the owner words them), so they are content keys even though staff read most of them.
Staff-only forms and private replies to staff use operator text (``checkout.text``).

Tracking details are optional: the "add tracking details" button appears only when its label
(``checkout.staff.button.tracking``) is configured, and then its other texts are needed too (``content_problems``).
"""

from __future__ import annotations

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings

FEATURE = "checkout"

# -- the live order record (staff channel) ----------------------------------------------------------------------
CUSTOMER_FIELD = "checkout.staff.customer_field"
CHANNEL_FIELD = "checkout.staff.channel_field"
CLAIMED_FIELD = "checkout.staff.claimed_field"
NOT_CLAIMED = "checkout.staff.not_claimed"
DELIVERY_FIELD = "checkout.staff.delivery_field"
SHIP_TO_FIELD = "checkout.staff.ship_to_field"
AMOUNT_FIELD = "checkout.staff.amount_field"
TRACKING_FIELD = "checkout.staff.tracking_field"
TIMELINE_FIELD = "checkout.staff.timeline_field"
TIMELINE_LINE = "checkout.staff.timeline_line"
NOTES_FIELD = "checkout.staff.notes_field"
NOTE_LINE = "checkout.staff.note_line"
STAGE_CLAIMED = "checkout.staff.stage.claimed"
STAGE_UNCLAIMED = "checkout.staff.stage.unclaimed"
STAGE_CORRECTION = "checkout.staff.stage.correction"
STAGE_TRACKING = "checkout.staff.stage.tracking"

BUTTON_CLAIM = "checkout.staff.button.claim"
BUTTON_UNCLAIM = "checkout.staff.button.unclaim"
BUTTON_NOTE = "checkout.staff.button.note"
BUTTON_CORRECTION = "checkout.staff.button.correction"
BUTTON_DETAILS = "checkout.staff.button.details"
BUTTON_TRACKING = "checkout.staff.button.tracking"

# -- customer notifications sent by staff actions --------------------------------------------------------------
NOTIFY_CORRECTION = "correction"
NOTIFY_TRACKING = "tracking"
REJECTION_REASON_FIELD = "checkout.notify.rejected.reason_field"
CORRECTION_TITLE = "checkout.notify.correction.title"
CORRECTION_DESCRIPTION = "checkout.notify.correction.description"
CORRECTION_MESSAGE_FIELD = "checkout.notify.correction.message_field"
TRACKING_TITLE = "checkout.notify.tracking.title"
TRACKING_DESCRIPTION = "checkout.notify.tracking.description"
TRACKING_DETAILS_FIELD = "checkout.notify.tracking.details_field"
SHIPMENT_CARRIER_FIELD = "checkout.notify.shipped.carrier_field"
SHIPMENT_NUMBER_FIELD = "checkout.notify.shipped.number_field"
SHIPMENT_TRACK_BUTTON = "checkout.notify.shipped.track_button"

TIMELINE_PLACEHOLDERS = ("time", "stage", "actor")
NOTE_PLACEHOLDERS = ("time", "actor", "note")
NOTIFY_PLACEHOLDERS = ("order_number", "total", "channel_mention")

# Needed as soon as the tracking button is configured (tracking details are optional).
TRACKING_DEPENDENT_KEYS = (TRACKING_FIELD, STAGE_TRACKING, TRACKING_DETAILS_FIELD, TRACKING_DESCRIPTION)

_G_STAFF = "Checkout: staff order controls (staff channel)"
_G_TIMELINE = "Checkout: staff order controls - timeline and notes"
_G_NOTIFY = "Checkout: customer notifications sent by staff actions"
_NOTIFY_DOC = (
    "{order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent "
    "by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are "
    "closed) posted in the order channel with a mention."
)


def content_keys(settings: Settings) -> list[ContentKey]:
    del settings
    return [
        ck(
            CUSTOMER_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record that mentions the customer (no ping).",
        ),
        ck(
            CHANNEL_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Optional name of the field on the staff order record that links the customer's order channel.",
            required=False,
        ),
        ck(
            CLAIMED_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record showing which staff member claimed the order and since "
            "when (while nobody has claimed an open order, it shows checkout.staff.not_claimed).",
        ),
        ck(
            NOT_CLAIMED,
            Kind.FIELD_VALUE,
            FEATURE,
            _G_STAFF,
            "Shown under checkout.staff.claimed_field while nobody has claimed an open order.",
        ),
        ck(
            DELIVERY_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record showing the delivery option and the extras the customer "
            "chose (their labels; shown when the order has one).",
        ),
        ck(
            SHIP_TO_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record showing where the order goes: the country (with "
            "operations.answers_in_orders_channel: checkout.address.ship_to, never the street). The record's "
            "details button shows the full address privately.",
        ),
        ck(
            AMOUNT_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record with the amounts, one per line as in the customer's summary "
            "(checkout.summary.line, .total_line): items, delivery, extras and other fees, store credit, the total, "
            "and refunds.",
        ),
        ck(
            TRACKING_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_STAFF,
            "Name of the field on the staff order record showing the tracking details staff entered. Needed when "
            "checkout.staff.button.tracking is configured.",
            required=False,
        ),
        ck(
            TIMELINE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_TIMELINE,
            "Name of the field on the staff order record listing the order's stages with their times (submitted, "
            "status changes, claims, correction requests, tracking details; the latest ones when there are many - "
            "the details button shows the full history).",
        ),
        ck(
            TIMELINE_LINE,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "How one stage is written in the timeline, e.g. '{time} {stage} {actor}'. {time} is the time (shown in "
            "each reader's time zone), {stage} the stage name (checkout.status_label.* for statuses, "
            "checkout.staff.stage.* for the other stages), {actor} mentions who did it (empty for automatic "
            "changes).",
            placeholders=TIMELINE_PLACEHOLDERS,
        ),
        ck(
            STAGE_CLAIMED,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "Stage name in the timeline when a staff member claimed the order.",
        ),
        ck(
            STAGE_UNCLAIMED,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "Stage name in the timeline when the claim on the order was released.",
        ),
        ck(
            STAGE_CORRECTION,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "Stage name in the timeline when staff asked the customer to correct information.",
        ),
        ck(
            STAGE_TRACKING,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "Stage name in the timeline when staff added or changed the tracking details. Needed when "
            "checkout.staff.button.tracking is configured.",
            required=False,
        ),
        ck(
            NOTES_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_TIMELINE,
            "Name of the field on the staff order record listing the internal staff notes (the latest ones; never "
            "shown to the customer).",
        ),
        ck(
            NOTE_LINE,
            Kind.FORMAT,
            FEATURE,
            _G_TIMELINE,
            "How one internal note is written, e.g. '{time} {actor}: {note}'. {time} is when it was added, {actor} "
            "mentions who added it, {note} is the note.",
            placeholders=NOTE_PLACEHOLDERS,
        ),
        ck(
            BUTTON_CLAIM,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Staff button: claim an open order (records who handles it; a second claim is refused).",
        ),
        ck(
            BUTTON_UNCLAIM,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Staff button: release the claim (only the member holding it, or an admin).",
        ),
        ck(
            BUTTON_NOTE,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Staff button: add an internal note (opens a staff-only form; notes are never shown to the customer).",
        ),
        ck(
            BUTTON_CORRECTION,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Staff button on an order waiting for payment: ask the customer to correct information (opens a form for "
            "the message; the customer is notified privately and the order keeps waiting).",
        ),
        ck(
            BUTTON_DETAILS,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Staff button: show the full order record privately to the staff member who clicks it.",
        ),
        ck(
            BUTTON_TRACKING,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_STAFF,
            "Optional staff button on a paid, in-progress or completed order: add or change tracking details "
            "(included in the customer's completion notification, or sent to them when the order is already "
            "completed). Leave empty if your workflow does not use tracking details.",
            required=False,
        ),
        ck(
            REJECTION_REASON_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_NOTIFY,
            "Name of the field in the customer's rejection notification (checkout.notify.rejected.*) that shows the "
            "reason staff entered when rejecting the order.",
        ),
        ck(
            CORRECTION_TITLE,
            Kind.TITLE,
            FEATURE,
            _G_NOTIFY,
            f"Optional title of the notification asking the customer to correct information. {_NOTIFY_DOC}",
            required=False,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            CORRECTION_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            _G_NOTIFY,
            "Notification sent to the customer when staff ask them to correct information on an order waiting for "
            f"payment (the staff message follows in a field). {_NOTIFY_DOC}",
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            CORRECTION_MESSAGE_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_NOTIFY,
            "Name of the field in the correction notification that shows the staff member's message.",
        ),
        ck(
            TRACKING_TITLE,
            Kind.TITLE,
            FEATURE,
            _G_NOTIFY,
            f"Optional title of the notification sent when staff add or change tracking details of a completed "
            f"order. {_NOTIFY_DOC}",
            required=False,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            TRACKING_DESCRIPTION,
            Kind.DESCRIPTION,
            FEATURE,
            _G_NOTIFY,
            "Notification sent to the customer when staff add or change tracking details of an order that is already "
            f"completed (the details follow in a field). {_NOTIFY_DOC} Needed when checkout.staff.button.tracking is "
            "configured.",
            required=False,
            placeholders=NOTIFY_PLACEHOLDERS,
        ),
        ck(
            SHIPMENT_CARRIER_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_NOTIFY,
            "Name of the field showing the carrier in the shipped notification (Mark shipped) and on the customer's "
            "order ticket card while the order is shipped, e.g. '{icon_truck} Carrier'.",
        ),
        ck(
            SHIPMENT_NUMBER_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_NOTIFY,
            "Name of the field showing the tracking number in the shipped notification and on the customer's order "
            "ticket card while the order is shipped, e.g. '{icon_map} Tracking number'.",
        ),
        ck(
            SHIPMENT_TRACK_BUTTON,
            Kind.BUTTON_LABEL,
            FEATURE,
            _G_NOTIFY,
            "Label of the link button under the shipped notification and on the customer's order ticket card while "
            "the order is shipped, that opens the carrier's tracking page (shown when staff entered a tracking link), "
            "e.g. 'Track package'.",
        ),
        ck(
            TRACKING_DETAILS_FIELD,
            Kind.FIELD_NAME,
            FEATURE,
            _G_NOTIFY,
            "Name of the field showing the tracking details in the customer's completion notification and in the "
            "tracking notification. Needed when checkout.staff.button.tracking is configured.",
            required=False,
        ),
    ]


def tracking_enabled(content: ContentStore) -> bool:
    """Tracking details are used: the tracking button's label is configured."""
    return BUTTON_TRACKING in content.registry and content.has(BUTTON_TRACKING)


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    del settings
    if not tracking_enabled(content):
        return []
    return [
        f"{key} is not configured (needed because {BUTTON_TRACKING} is configured)"
        for key in TRACKING_DEPENDENT_KEYS
        if not content.has(key)
    ]
