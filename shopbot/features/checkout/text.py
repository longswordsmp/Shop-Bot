"""Operator text of the checkout feature: private replies to staff (order buttons, ``/order`` and ``/orders``), the
staff-only forms (refund, note, correction request, tracking details, rejection reason), the staff order list and
lookup, and /setup report lines.

Shown only after a staff/admin check passed, always privately. Never shown to customers. The staff order-control
message and every customer notification are business text (content keys), not operator text. Each entry can be
overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    "checkout.amount": "{amount} {currency}",
    # status changes (buttons and /order status|cancel)
    "checkout.op.status_changed": "Order #{number} is now {status}.",
    "checkout.op.already_handled": "Order #{number} was already handled; it is {status} now. Nothing was changed.",
    "checkout.op.invalid_transition": "Order #{number} is {status}; it cannot be changed to {target}.",
    "checkout.op.provider_confirms": (
        "Order #{number} is paid through the '{provider}' payment provider, which confirms payments itself."
    ),
    "checkout.op.not_found": "Order #{number} does not exist.",
    "checkout.op.order_missing": "This order no longer exists.",
    "checkout.op.note_added": "Note added to the history of order #{number}.",
    # refunds
    "checkout.op.refund_manual": (
        "Refund of {amount} recorded for order #{number}. Return the money to the customer yourself (payments are "
        "handled outside the bot)."
    ),
    "checkout.op.refund_provider": "Refund of {amount} for order #{number} was sent through the payment provider.",
    "checkout.op.refund_credit": "Refund of {amount} for order #{number} was added to the customer's store credit.",
    "checkout.op.refund_remaining": "Still refundable: {remaining}.",
    "checkout.op.fully_refunded": "Order #{number} is now fully refunded.",
    "checkout.op.refund_too_much": "Order #{number}: at most {max} can still be refunded. Nothing was recorded.",
    "checkout.op.refund_not_allowed": "Order #{number} is {status} and cannot be refunded.",
    "checkout.op.refund_invalid_amount": "Invalid amount: {reason}. Example: {example}",
    "checkout.op.refund_failed": (
        "The payment provider did not accept the refund for order #{number}. Nothing was recorded. "
        "Reference: {error_id}."
    ),
    "checkout.op.credits_unavailable": "Refunds to store credit need the credits feature to be enabled and configured.",
    "checkout.op.refund_currency": (
        "Order #{number} was paid in {currency}, but store credit is now kept in {shop_currency}; refund it as money "
        "instead. Nothing was recorded."
    ),
    "checkout.op.reason_too_long": "The reason must be at most {max} characters.",
    # applied store credit (given back all at once)
    "checkout.op.credit_returned": (
        "The store credit applied to order #{number} ({amount}) was given back to the customer."
    ),
    "checkout.op.credit_return_not_allowed": (
        "Order #{number} is {status}; it has no applied store credit that can be given back. Nothing was changed."
    ),
    "checkout.op.credit_returnable": "Applied store credit not given back yet: {amount}.",
    "checkout.op.paid_cancel": "Order #{number} had been paid: nothing was refunded or given back automatically.",
    # /ticket close in an order ticket (staff)
    "checkout.op.ticket_closed": "Order ticket {number} closed.",
    "checkout.op.ticket_session_closed": (
        "Order ticket {number} closed. The customer's unfinished order (never submitted) was cancelled."
    ),
    "checkout.op.ticket_close_pending": (
        "The customer's unfinished order in ticket {number} was cancelled, but the channel could not be closed right "
        "now. The bot retries and closes it automatically."
    ),
    "checkout.op.ticket_order_open": (
        "Ticket {number} belongs to order #{order}, which is {status}. Nothing was changed."
    ),
    "checkout.op.ticket_order_open_hint": (
        "End the order with its record's controls in the staff channel ({record}) or with /order cancel; its ticket "
        "then closes automatically."
    ),
    "checkout.op.ticket_not_open": "Ticket {number} is {status}; it is not open any more. Nothing was changed.",
    "checkout.op.ticket_busy": "Ticket {number} is in use right now. Nothing was changed; try again.",
    # refund form (staff only)
    "checkout.refund.title": "Refund order #{number}",
    "checkout.refund.title_credit": "Refund #{number} as store credit",
    "checkout.refund.amount": "Amount ({currency})",
    "checkout.refund.amount_hint": "At most {max}. Example: {example}",
    "checkout.refund.reason": "Reason (optional, internal)",
    "checkout.refund.reason_hint": "Kept in the order history; the customer does not see it.",
    "checkout.refund.title_return": "Return store credit of order #{number}",
    "checkout.refund.return_hint": "Gives {amount} back to the customer's store credit.",
    # claims, notes, correction requests, tracking details, rejection with a reason (order-record buttons)
    "checkout.op.claimed": "You claimed order #{number}.",
    "checkout.op.claim_taken": "Order #{number} is already claimed by {staff}. Nothing was changed.",
    "checkout.op.claim_state": "Order #{number} is {status}; only open orders can be claimed. Nothing was changed.",
    "checkout.op.unclaimed": "The claim on order #{number} was released.",
    "checkout.op.not_claimed": "Order #{number} is not claimed. Nothing was changed.",
    "checkout.op.not_claimer": (
        "Order #{number} is claimed by {staff}; only they or an admin can release it. Nothing was changed."
    ),
    "checkout.op.correction_sent": (
        "Correction request for order #{number} recorded; the customer was notified {delivery}."
    ),
    "checkout.op.correction_state": (
        "Order #{number} is {status}; a correction can only be requested while it waits for payment. Nothing was sent."
    ),
    "checkout.op.tracking_saved": (
        "Tracking details of order #{number} saved. The customer is not told yet: Mark shipped sends the carrier and "
        "tracking number (an order without an address gets them with the completion notification)."
    ),
    "checkout.op.tracking_sent": "Tracking details of order #{number} saved; the customer was notified {delivery}.",
    "checkout.op.tracking_state": (
        "Order #{number} is {status}; tracking details can be added once it is paid. Nothing was changed."
    ),
    "checkout.op.tracking_unavailable": "Tracking details are not used (checkout.staff.button.tracking is not set).",
    "checkout.op.text_invalid": "The text must be between 1 and {max} characters. Nothing was changed.",
    "checkout.op.delivery.dm": "by direct message",
    "checkout.op.delivery.channel": "in the order channel",
    "checkout.op.delivery.none": "- the notification could NOT be delivered (recorded in the audit log)",
    "checkout.op.delivery.duplicate": "(it had already been sent)",
    # staff-only forms of the order record
    "checkout.note.title": "Note for order #{number}",
    "checkout.note.label": "Internal note",
    "checkout.note.hint": "Only staff see it: the order record in the staff channel and its details.",
    "checkout.correction.title": "Correction for order #{number}",
    "checkout.correction.label": "Message to the customer",
    "checkout.correction.hint": "Sent privately with your correction wording; the order keeps waiting for payment.",
    "checkout.tracking.title": "Tracking for order #{number}",
    "checkout.ship.title": "Order #{number} shipped",
    "checkout.ship.carrier": "Carrier",
    "checkout.ship.carrier_hint": "For example: USPS, UPS, DHL",
    "checkout.ship.number": "Tracking number",
    "checkout.ship.url": "Tracking link (optional)",
    "checkout.ship.url_hint": "An https:// link to the carrier's tracking page for this package.",
    "checkout.op.tracking_url_invalid": (
        "The tracking link must be an https:// address without spaces. Nothing changed."
    ),
    # gift card payments (payments.provider = "gift_card"; staff with payments.gift_card.staff_roles only)
    "checkout.gift.reveal.title": "Gift cards · order #{number}",
    "checkout.gift.reveal.intro": (
        "Redeem the cards now in the shop's account and check that each card's value matches the amount shown (that "
        "amount counts toward the order, and anything beyond the total becomes the customer's store credit). Then "
        "press **Payment confirmed** on the order record, or **Card problem** for a card that is used, worth less "
        "or not accepted. The codes are deleted as soon as you decide. Do not copy them anywhere else."
    ),
    "checkout.gift.reveal.card": "Card {index} · {amount} · ending {last4}",
    "checkout.gift.reveal.code": "Code: ||`{code}`||",
    "checkout.gift.reveal.pin": "PIN: ||`{pin}`||",
    "checkout.gift.reveal.no_pin": "No PIN given.",
    "checkout.gift.reveal.unreadable": (
        "This code cannot be read with the current key (was GIFT_CARD_KEY changed?). Use Card problem so the customer "
        "submits the card again."
    ),
    "checkout.gift.reveal.erased": (
        "The code was deleted after {days} days without a decision. Use Card problem so the customer submits the card "
        "again."
    ),
    "checkout.gift.reveal.redeemed_button": "Card {index} redeemed",
    "checkout.gift.reveal.problem_button": "Card {index} problem",
    "checkout.gift.problem.title": "Card problem · order #{number}",
    "checkout.gift.problem.reason": "What's wrong? (the customer sees this)",
    "checkout.gift.problem.reason_hint": "For example: The card was already redeemed.",
    "checkout.gift.no_permission": (
        "Only the payment staff (payments.gift_card.staff_roles, owners and admins) can see gift card codes or decide "
        "on them."
    ),
    "checkout.gift.not_waiting": "Order #{number} has no gift card waiting for a decision (status: {status}).",
    "checkout.gift.decided": "Those gift cards were already decided on; nothing changed.",
    "checkout.gift.key_missing": (
        "Gift card codes cannot be read right now: the key could not be loaded (check GIFT_CARD_KEY, or the "
        "gift_card.key file next to the database)."
    ),
    "checkout.gift.redeemed": "Order #{number}: {count} gift card(s) marked redeemed.",
    "checkout.gift.refused": "Order #{number}: {count} gift card(s) marked not accepted; the customer is told why.",
    "checkout.gift.outcome.paid": "The order is paid.",
    "checkout.gift.outcome.waiting": "Other cards of this order still wait for a decision.",
    "checkout.gift.outcome.pending": "The order waits for the customer's payment again.",
    "checkout.gift.outcome.credit": "{amount} beyond the total went to the customer's store credit.",
    "checkout.tracking.label": "Tracking details",
    "checkout.tracking.hint": "The customer sees them (completion notification, or right away if completed).",
    "checkout.reject.title": "Reject order #{number}",
    "checkout.reject.label": "Reason (sent to the customer)",
    "checkout.reject.hint": "Included in the customer's rejection notification. Stock and credit are returned.",
    # /order list and /orders
    "checkout.list.filter_all": "All statuses",
    "checkout.list.filter_placeholder": "Filter by status",
    "checkout.list.view_option": "#{number} · {status}",
    "checkout.list.view_placeholder": "Show an order's full record",
    "checkout.list.title": "Orders ({total})",
    "checkout.list.empty": "No orders match.",
    "checkout.list.line": "#{number} · {status} · {customer} · {total} · {created}",
    "checkout.list.page": "Page {page}/{pages}",
    "checkout.list.previous": "Previous",
    "checkout.list.next": "Next",
    # /order lookup
    "checkout.lookup.title": "Order #{number}",
    "checkout.lookup.status": "Status",
    "checkout.lookup.customer": "Customer",
    "checkout.lookup.channel": "Order channel",
    "checkout.lookup.created": "Submitted",
    "checkout.lookup.paid": "Paid",
    "checkout.lookup.fulfilled": "Fulfilled",
    "checkout.lookup.subtotal": "Subtotal",
    "checkout.lookup.fulfillment": "Fulfillment",
    "checkout.lookup.fulfillment_value": "{option} (fee {fee})",
    "checkout.lookup.fees": "Fees",
    "checkout.lookup.credits": "Credits applied",
    "checkout.lookup.total": "Total",
    "checkout.lookup.refunded": "Refunded",
    "checkout.lookup.credit_returned": "Credit given back",
    "checkout.lookup.payment": "Payment",
    "checkout.lookup.payment_value": "{provider} · {status} · reference {reference}",
    "checkout.lookup.items": "Items",
    "checkout.lookup.item": "{quantity} × {name} (product {product_id}) · {unit} each · {total}",
    "checkout.lookup.fee_lines": "Fee details",
    "checkout.lookup.fee_line": "{fee}: {amount}",
    "checkout.lookup.unlabelled": "Answer '{key}'",
    "checkout.lookup.refunds": "Refunds",
    "checkout.lookup.refund_line": "{amount} · {method} · {actor} · {when} · {reason}",
    "checkout.lookup.credit_return_line": "{amount} · store credit given back · {actor} · {when} · {reason}",
    "checkout.lookup.history": "History (latest)",
    "checkout.lookup.event": "{when} · {old} → {new} · {actor} · {note}",
    "checkout.lookup.automatic": "automatic",
    "checkout.lookup.claimed": "Claimed by",
    "checkout.lookup.claimed_value": "{staff} since {when}",
    "checkout.lookup.tracking": "Tracking details",
    "checkout.lookup.activity": "Staff activity (latest)",
    "checkout.lookup.activity_line": "{when} · {kind} · {actor} · {text}",
    # /setup report
    "checkout.publish.no_guild": "Shop panel: the configured server is not available.",
    "checkout.publish.no_channel": "Shop panel: channel '{name}' does not exist yet (run /setup to create it).",
    "checkout.publish.missing_permissions": "Shop panel: the bot lacks {permissions} in {channel}.",
    "checkout.publish.missing_content": "Shop panel: content '{name}' is not configured.",
    "checkout.publish.invalid": "Shop panel violates Discord limits: {error}",
    "checkout.publish.failed": "Shop panel could not be posted (HTTP {status}).",
    "checkout.publish.panel": "Shop panel {action} in {channel}.",
    "checkout.publish.staff_channel_missing": (
        "checkout.staff_channel: channel '{name}' does not exist yet; staff order controls cannot be posted."
    ),
    "checkout.publish.staff_channel_permissions": "checkout.staff_channel: the bot lacks {permissions} in {channel}.",
    "checkout.publish.category_missing": (
        "checkout.order_category: category '{name}' does not exist yet; order channels would be created outside "
        "any category."
    ),
    "checkout.publish.transcripts_unavailable": (
        "checkout.transcript_on_close is on, but order transcripts need tickets.transcript_channel and a ready "
        "tickets feature (its transcript texts); order channels are closed without a transcript."
    ),
    "checkout.publish.category_permissions": (
        "checkout.order_category: the bot lacks {permissions} where order channels are created."
    ),
}
