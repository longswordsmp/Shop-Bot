"""Operator text of the credits feature: ``/credits`` replies and the staff credit history.

Shown only to staff/admins (ephemeral command replies and the staff-only history pages) and never to customers.
Each entry can be overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # amounts in operator replies (format_amount + the currency code)
    "credits.amount": "{amount} {currency}",
    "credits.amount_positive": "+{amount}",
    # /credits balance
    "credits.cmd.balance_title": "Credit balance",
    "credits.cmd.balance": "{member} has {balance}.",
    # /credits adjust
    "credits.cmd.added_title": "Credits added",
    "credits.cmd.removed_title": "Credits removed",
    "credits.cmd.adjusted": "{member}: {delta}. New balance: {balance} (entry #{entry_id}).",
    "credits.cmd.rejected_title": "Credits not changed",
    "credits.cmd.insufficient": "Cannot remove {amount}: {member} has only {balance}. Nothing was changed.",
    "credits.cmd.invalid_amount": "Invalid amount: {reason}. Example: {example}",
    "credits.cmd.zero_amount": "The amount must be greater than 0.",
    "credits.cmd.bot_member": "Bots cannot hold credits.",
    "credits.cmd.invalid_note": "The note must be 1-{max} characters.",
    "credits.cmd.no_currency": "Credits cannot be changed until settings.checkout.currency is set.",
    # member notification after an adjustment
    "credits.notify.sent": "The member was notified by direct message.",
    "credits.notify.duplicate": "The member was already notified about this change.",
    "credits.notify.not_configured": "The member was not notified: no adjustment notice is configured "
    "(credits.adjusted_notice.title / .description).",
    "credits.notify.dm_disabled": "The member was not notified: direct-message notifications are switched off "
    "(features.dm_notifications).",
    "credits.notify.failed": "The member could not be notified (direct messages from the bot are closed or failed).",
    "credits.notify.missing_content": "The member was not notified: content key {content_key} is not configured.",
    "credits.notify.error": "The member could not be notified because of an unexpected error. Reference: {error_id}.",
    # /credits history (staff-only pages)
    "credits.history.title": "Credit history",
    "credits.history.summary": "{member} | balance {balance} | {count} entries",
    "credits.history.empty": "No credit entries yet.",
    "credits.history.entry": "#{entry_id} | {delta}",
    "credits.history.details": "{kind} | {date}",
    "credits.history.by": "by {actor}",
    "credits.history.by_system": "automatic",
    "credits.history.order": "order #{order_number}",
    "credits.history.note": "Note: {note}",
    "credits.history.page": "Page {page}/{pages}",
    "credits.history.previous": "Previous",
    "credits.history.next": "Next",
    # ledger entry kinds
    "credits.kind.admin_adjust": "Staff adjustment",
    "credits.kind.order_debit": "Used for an order",
    "credits.kind.order_return": "Returned from an order",
    "credits.kind.refund_to_credit": "Refund to credit",
    "credits.kind.gift_card": "Gift card balance",
}
