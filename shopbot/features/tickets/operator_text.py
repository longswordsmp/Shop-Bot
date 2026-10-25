"""Operator text of the ticket system: staff-only command replies and /setup report lines.

Shown only after a staff/admin check passed, always privately (or in admin-only channels). Each entry
can be overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # /ticket commands
    "tickets.cmd.not_ticket": "This channel is not a support ticket.",
    "tickets.cmd.not_staff_here": "You are not staff for this ticket's category.",
    "tickets.cmd.wrong_state": "Ticket {number} is {status}; this action is not possible.",
    "tickets.cmd.reopen_disabled": "Reopening tickets is disabled (tickets.allow_reopen = false).",
    "tickets.cmd.discord_error": "Discord rejected the change (HTTP {status}). Check the bot's permissions here.",
    "tickets.cmd.added": "{user} now has access to ticket {number}.",
    "tickets.cmd.already_added": "{user} already has access to ticket {number}.",
    "tickets.cmd.owner": "{user} owns ticket {number} and always has access.",
    "tickets.cmd.bot_member": "Bots cannot be added to or removed from tickets.",
    "tickets.cmd.removed": "{user} no longer has access to ticket {number}.",
    "tickets.cmd.not_participant": "{user} was not added to ticket {number}.",
    "tickets.cmd.closed": "Ticket {number} closed.",
    "tickets.cmd.reopened": "Ticket {number} reopened.",
    "tickets.cmd.claimed": "You claimed ticket {number}.",
    "tickets.cmd.already_claimed": "Ticket {number} is already claimed by {staff}.",
    "tickets.cmd.transcript_posted": "Transcript of ticket {number} posted in {channel}.",
    "tickets.cmd.transcript_attached": "Transcript of ticket {number} (no transcript channel is configured).",
    "tickets.cmd.transcript_failed": "The transcript of ticket {number} could not be generated. See the bot logs.",
    # /ticket info
    "tickets.info.title": "Ticket {number}",
    "tickets.info.status": "Status",
    "tickets.info.category": "Category",
    "tickets.info.owner": "Owner",
    "tickets.info.assigned": "Claimed by",
    "tickets.info.channel": "Channel",
    "tickets.info.created": "Created",
    "tickets.info.last_activity": "Last activity",
    "tickets.info.closed": "Closed",
    "tickets.info.closed_by": "Closed by",
    "tickets.info.close_reason": "Close reason",
    "tickets.info.transcript": "Transcript",
    "tickets.info.participants": "Added members",
    "tickets.info.automatic": "automatic",
    # /ticket list
    "tickets.list.title": "Tickets ({count})",
    "tickets.list.empty": "No tickets match.",
    "tickets.list.line": "#{number} · {status} · {category} · {owner} · {channel}",
    # /setup report
    "tickets.publish.no_guild": "Ticket panel: the configured server is not available.",
    "tickets.publish.no_channel": "Ticket panel: channel '{name}' does not exist yet (run /setup to create it).",
    "tickets.publish.missing_permissions": "Ticket panel: the bot lacks {permissions} in {channel}.",
    "tickets.publish.missing_content": "Ticket panel: content '{name}' is not configured.",
    "tickets.publish.invalid": "Ticket panel violates Discord limits: {error}",
    "tickets.publish.failed": "Ticket panel could not be posted (HTTP {status}).",
    "tickets.publish.panel": "Ticket panel {action} in {channel}.",
    "tickets.publish.category_missing": (
        "Ticket category '{category}': Discord category '{discord_category}' does not exist yet; tickets would "
        "be created outside any category."
    ),
    "tickets.publish.category_permissions": (
        "Ticket category '{category}': the bot lacks {permissions} where its ticket channels are created."
    ),
    "tickets.publish.channel_missing": "{setting}: channel '{name}' does not exist yet.",
    "tickets.publish.channel_permissions": "{setting}: the bot lacks {permissions} in {channel}.",
    "tickets.publish.no_content_intent": (
        "Transcripts are enabled but bot.message_content_intent is false: transcripts will not contain message text."
    ),
}
