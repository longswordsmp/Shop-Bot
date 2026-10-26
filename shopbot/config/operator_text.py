"""Operator text: wording for admin-only command replies, setup reports, and the audit channel.

This text is technical tooling output shown only to authorized administrators (ephemeral replies to
admin-gated commands, and the admin-only audit channel). It is never shown to customers. Every entry can
be overridden in content.toml under ``operator.<key>``. Feature packages contribute their own entries
through ``FeatureModule.operator_text``.
"""

from __future__ import annotations

import re

# The title of each #bot-log entry, by audit action (an action without one shows its identifier).
AUDIT_TITLES: dict[str, str] = {
    "admin.backup": "Database backup made",
    "admin.content_clear": "Text reset to its file value",
    "admin.content_set": "Text changed",
    "admin.maintenance": "Daily maintenance",
    "admin.reload": "Settings and texts reloaded",
    "announcements.failed": "Announcement not posted",
    "announcements.interrupted": "Announcement interrupted",
    "announcements.published": "Announcement posted",
    "catalog.listing_refresh_failed": "Catalog item not updated",
    "catalog.category_create": "Catalog category created",
    "catalog.category_edit": "Catalog category changed",
    "catalog.category_delete": "Catalog category deleted",
    "catalog.product_categories": "Item's categories changed",
    "checkout.customer_role_added": "Customer role given",
    "checkout.customer_role_failed": "Customer role not changed",
    "checkout.customer_role_removed": "Customer role removed",
    "checkout.gift_card_erased": "Gift card code deleted (kept too long)",
    "checkout.gift_card_message_removed": "Gift card code removed from the chat",
    "checkout.gift_card_redeemed": "Gift card redeemed",
    "checkout.gift_card_refused": "Gift card refused",
    "checkout.gift_card_revealed": "Gift card code revealed",
    "checkout.gift_card_submitted": "Gift card submitted",
    "checkout.order_submitted": "Order placed",
    "checkout.payment_method_requested": "Payment method chosen",
    "checkout.order_pending_payment": "Waiting for payment again",
    "checkout.order_payment_submitted": "Payment submitted",
    "checkout.order_paid": "Payment confirmed",
    "checkout.order_processing": "Order processing",
    "checkout.order_shipped": "Order shipped",
    "checkout.order_fulfilled": "Order delivered",
    "checkout.order_rejected": "Order rejected",
    "checkout.order_cancelled": "Order cancelled",
    "checkout.order_expired": "Order expired",
    "checkout.order_refunded": "Order refunded",
    "checkout.order_refund": "Refund recorded",
    "checkout.order_credit_returned": "Store credit given back",
    "checkout.order_claimed": "Order claimed",
    "checkout.order_unclaimed": "Order claim released",
    "checkout.order_note": "Internal note added",
    "checkout.order_correction_requested": "Correction requested",
    "checkout.order_tracking_set": "Tracking details set",
    "checkout.session_started": "Order ticket opened",
    "checkout.session_cancelled": "Unfinished order cancelled",
    "checkout.session_expired": "Unfinished order expired",
    "checkout.session_closed_by_staff": "Unfinished order closed by staff",
    "checkout.ticket_closed_by_staff": "Order ticket closed by staff",
    "credits.adjust": "Store credit adjusted",
    "new_arrivals.announced": "New arrival announced",
    "new_arrivals.subscribed": "Restock alerts switched on",
    "new_arrivals.unsubscribed": "Restock alerts switched off",
    "new_arrivals.toggle_failed": "Restock alerts not changed",
    "notifications.dm_failed": "DM not delivered",
    "notifications.failed": "Notification not delivered",
    "operations.alert_undelivered": "Staff alert not delivered",
    "operations.error": "Error",
    "operations.owner_dm_failed": "Owner DM not delivered",
    "panels.withdrawn": "Panels withdrawn",
    "problems.reported": "Problem reported",
    "problems.resolved": "Problem resolved",
    "problems.rejected": "Problem report rejected",
    "reviews.prompted": "Review requested",
    "reviews.prompt_cancelled": "Review request cancelled",
    "reviews.declined": "Review declined by the customer",
    "reviews.published": "Review published",
    "reviews.approved": "Review approved",
    "reviews.rejected": "Review rejected",
    "reviews.removed": "Review removed",
    "reviews.resent": "Review request sent again",
    "server.reset": "Server reset",
    "setup.run": "Server setup (/setup)",
    "task.failed": "Task failed",
    "tickets.created": "Ticket opened",
    "tickets.create_failed": "Ticket not opened",
    "tickets.claimed": "Ticket claimed",
    "tickets.unclaimed": "Ticket claim released",
    "tickets.closed": "Ticket closed",
    "tickets.reopened": "Ticket reopened",
    "tickets.deleted": "Ticket deleted",
    "tickets.channel_deleted": "Ticket channel deleted",
    "tickets.recovered": "Ticket recovered after a restart",
    "tickets.participant_added": "Member added to a ticket",
    "tickets.participant_removed": "Member removed from a ticket",
    "tickets.transcript_posted": "Transcript saved",
    "tickets.transcript_failed": "Transcript not saved",
    "verification.accepted": "Rules accepted",
    "verification.failed": "Verification failed",
    "verification.dm_failed": "Verification DM not delivered",
}

CORE_OPERATOR_TEXT: dict[str, str] = {
    # #bot-log entries: the title (AUDIT_TITLES), one line "Order #12 · by @staff · 20:41", then the details
    **{f"audit.action.{action}": title for action, title in AUDIT_TITLES.items()},
    "audit.target.order": "Order #{number}",
    "audit.target.ticket": "Ticket #{number}",
    "audit.target.other": "{type} {id}",
    "audit.by": "by {actor}",
    "audit.automatic": "automatic",
    "audit.details": "Details",
    "command.failed": "The command failed. Reference: {error_id}. Details are in the bot logs.",
    "command.not_in_guild": "This command can only be used in the configured server.",
    "command.done": "Done.",
    "command.not_found": "No matching record was found.",
    "command.invalid": "Invalid input: {reason}",
    "command.feature_disabled": "The '{feature}' feature is not enabled or not fully configured. Run /status.",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def render_operator(template: str, values: dict[str, object]) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER_RE.sub(substitute, template)
