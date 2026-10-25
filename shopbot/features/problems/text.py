"""Operator text of the Report a Problem workflow: the staff-only decision forms, staff replies, order-history notes
and /setup diagnostics.

Shown only to staff (after a staff check, privately) or in staff-only records; never to customers. Each entry can be
overridden in content.toml under ``operator.<key>``. Modal titles and labels must stay within Discord's 45
characters.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # decision forms (staff only)
    "problems.form.resolve_title": "Resolve problem report {number}",
    "problems.form.resolve_note": "Note for the customer (optional)",
    "problems.form.resolve_note_help": "Shown to the customer on the ticket and in the decision message.",
    "problems.form.reject_title": "Reject problem report {number}",
    "problems.form.reject_reason": "Reason for the customer",
    "problems.form.reject_reason_help": "Shown to the customer on the ticket and in the decision message.",
    # staff replies
    "problems.op.decided": "Problem report {number} was {status}. Customer notification: {delivery}.",
    "problems.op.already_decided": "Problem report {number} was already {status}; nothing was changed.",
    "problems.op.reason_required": "A reason is required to reject a problem report.",
    "problems.op.reason_too_long": "The reason may be at most {max} characters (it was {length}).",
    "problems.op.status.resolved": "resolved",
    "problems.op.status.rejected": "rejected",
    "problems.op.delivery.dm": "sent by direct message",
    "problems.op.delivery.channel": "posted in the ticket",
    "problems.op.delivery.panel": "shown on the ticket panel (direct messages are off)",
    "problems.op.delivery.none": "could not be delivered (logged); the ticket panel shows the decision",
    "problems.op.delivery.duplicate": "already sent",
    "problems.op.delivery.failed": "not delivered: the ticket panel could not be updated either (logged)",
    # order history (staff record of the order)
    "problems.order_note.reported": "Problem report {number} opened ({type}).",
    "problems.order_note.resolved": "Problem report {number} resolved. Note: {reason}",
    "problems.order_note.rejected": "Problem report {number} rejected. Reason: {reason}",
    "problems.order_note.none": "-",
    # /setup diagnostics
    "problems.publish.no_guild": "Problem reports: the configured server is not available.",
    "problems.publish.category_missing": (
        "Problem reports: category '{name}' (problems.discord_category) does not exist yet; run /setup to create it."
    ),
    "problems.publish.category_permissions": (
        "Problem reports: the bot lacks {permissions} in the category where report tickets are created."
    ),
    "problems.publish.no_alert_channel": (
        "Problem reports: tickets.staff_alert_channel is empty, so staff are not alerted about new reports "
        "(they appear only in the ticket category)."
    ),
    "problems.publish.alert_channel_missing": "Problem reports: staff alert channel '{name}' does not exist yet.",
    "problems.publish.ping_not_mentionable": (
        "Problem reports: problems.ping_staff is on, but Discord will not notify {roles} (make the role mentionable "
        "or give the bot 'Mention @everyone, @here and All Roles')."
    ),
    "problems.publish.ready": "Problem reports: the Report a Problem button is shown on the #purchase panel.",
}
