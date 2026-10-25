"""Operator text of the reviews feature: ``/review`` replies and the /setup report line.

Shown only to staff/admins (ephemeral command replies) and never to customers. Each entry can be overridden in
content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # review states as shown to staff
    "reviews.status.prompted": "requested (no rating yet)",
    "reviews.status.rated": "rated (comment form not submitted yet)",
    "reviews.status.submitted": "submitted (waiting to be published)",
    "reviews.status.published": "published",
    "reviews.status.declined": "declined or removed",
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
}
