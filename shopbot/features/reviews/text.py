"""Operator text of the reviews feature: ``/review`` replies, the staff reject form, replies to the staff approval
buttons and the /setup report lines.

Shown only to staff/admins (ephemeral replies after a staff check, the staff-only reject form) and never to
customers. Each entry can be overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # review states as shown to staff
    "reviews.status.prompted": "requested (no rating yet)",
    "reviews.status.rated": "rated (comment form not submitted yet)",
    "reviews.status.submitted": "submitted (waiting to be published)",
    "reviews.status.pending_approval": "waiting for staff approval",
    "reviews.status.published": "published",
    "reviews.status.rejected": "rejected by staff (not published)",
    "reviews.status.declined": "declined or removed",
    # staff approval: the reject form (staff only)
    "reviews.form.reject_title": "Reject the review of order #{order_number}",
    "reviews.form.reject_reason": "Reason (optional)",
    "reviews.form.reject_reason_help": "Shown to the customer only if the rejection notice has a reason heading.",
    # staff approval: replies to the Approve / Reject buttons and /review approve|reject
    "reviews.decision.title": "Review approval",
    "reviews.decision.approved": "The review of order #{order_number} was approved.",
    "reviews.decision.published": "It is published in the reviews channel.",
    "reviews.decision.publish_pending": "It could not be published yet; publishing is retried automatically.",
    "reviews.decision.rejected": "The review of order #{order_number} was rejected and will not be published.",
    "reviews.decision.notice.dm": "The customer was told by direct message.",
    "reviews.decision.notice.channel": "The customer was told in the order channel.",
    "reviews.decision.notice.none": "The customer could not be told (direct messages and the order channel are not "
    "available).",
    "reviews.decision.notice.duplicate": "The customer was already told.",
    "reviews.decision.notice.off": "No notice is sent to the customer (its text is not configured).",
    "reviews.decision.not_pending": "The review of order #{order_number} no longer waits for approval (it is "
    "{status}). Nothing was changed.",
    "reviews.decision.expired": "Order #{order_number} is '{status}' now, so its review cannot be published as a "
    "verified review. Reject it instead.",
    "reviews.decision.reason_too_long": "The reason is too long ({length} characters, at most {max}).",
    "reviews.decision.not_found": "No review is waiting for approval for order #{order_number}.",
    "reviews.decision.message_failed": "The approval message could not be updated; it is updated by the next /setup.",
    # /review list
    "reviews.list.title": "Reviews ({count})",
    "reviews.list.empty": "No reviews found.",
    "reviews.list.line": "Order #{order_number} | {status} | rating {rating} | {member} | {message}",
    "reviews.list.none": "-",
    # /review remove and /review resend
    "reviews.cmd.remove_title": "Remove review",
    "reviews.cmd.resend_title": "Send review request again",
    "reviews.cmd.order_not_found": "No order #{order_number} was found.",
    "reviews.cmd.no_review": "Order #{order_number} has no review request.",
    "reviews.cmd.prompt_cancelled": "The pending review request of order #{order_number} was cancelled.",
    "reviews.cmd.already_removed": "The review of order #{order_number} was already declined or removed.",
    "reviews.cmd.removed": "The review of order #{order_number} was removed.",
    "reviews.cmd.message_deleted": "The published review message was deleted.",
    "reviews.cmd.message_missing": "The published review message was already gone.",
    "reviews.cmd.remove_failed": "The published review message of order #{order_number} could not be deleted "
    "(Discord error {status}). Nothing was changed.",
    "reviews.cmd.not_fulfilled": "Order #{order_number} is '{status}'. Review requests are only sent for fulfilled "
    "orders.",
    "reviews.cmd.resend_wrong_state": "The review of order #{order_number} is {status}; only an unanswered request "
    "can be sent again.",
    "reviews.cmd.resent_dm": "The review request of order #{order_number} was sent by direct message.",
    "reviews.cmd.resent_channel": "The review request of order #{order_number} was posted in the order channel.",
    "reviews.cmd.resend_failed": "The review request of order #{order_number} could not be delivered (direct "
    "messages are closed or switched off, and the order channel is not available).",
    "reviews.cmd.missing_content": "The review request cannot be built: content key {content_key} is not configured.",
    # /setup report
    "reviews.setup.requeued": "Reviews: {count} submitted review(s) queued for publishing.",
    "reviews.setup.approvals_queued": "Reviews: {count} approval request(s) queued for posting in the approval "
    "channel.",
    "reviews.setup.approvals_refreshed": "Reviews: {count} approval message(s) of pending reviews updated.",
}
