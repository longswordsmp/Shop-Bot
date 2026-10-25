# Report a Problem (`problems`)

Specification: section 3 (#purchase "Report a Problem" button; support through the private ticket system),
section 7 (Problem Reports) and section 6 (decision notifications).

## What it does

1. **Button on #purchase.** The #purchase panel (published by checkout) shows a *Report a Problem* button next to the
   Start button as soon as this feature is ready. Run `/setup` after the feature becomes ready (or stops being ready)
   so the panel is edited in place; a button left on an old panel answers with the configured "unavailable" text.
2. **Private choice step.** Clicking the button checks the customer's open-report limit and the creation cooldown,
   then shows a private (ephemeral) message with
   - an **order menu**: the customer's own orders in `problems.eligible_statuses`, newest first (max 25 options),
     plus a "not about an order" option when `problems.allow_without_order = true`;
   - an **issue-type menu** when more than one `[[problems.types]]` exists;
   - the shared **Continue** button (enabled once everything is chosen), and the note that screenshots and files
     can be uploaded in the ticket once it is open.

   A menu with a single possible answer is skipped (chosen automatically). When there is nothing to choose at all
   (one type and no eligible order, or exactly one order and no "not about an order" option) the button opens the
   form right away. Without any eligible order and with `allow_without_order = false` the customer gets the
   `problems.no_orders.description` reply. Picking in a menu re-renders the step with the choice marked; the menus
   never open the form directly (Discord would not let the customer reopen a closed form by picking the same option
   again), the Continue button does, as often as needed.
3. **Form.** The chosen type's form (up to 5 fields, the ticket-form schema). A type without a form creates the
   report from Continue directly.
4. **Private ticket.** Submitting creates, through the ticket service, a support ticket of the reserved category
   `problem-report` in `problems.discord_category`: numbered like every ticket, named with
   `tickets.channel_name_pattern` and `problems.channel_prefix`, visible only to the customer, the staff roles
   (`permissions.staff_roles`, admins, `tickets.staff_roles`, `problems.staff_roles`) and the bot. The customer can
   post and upload attachments there. The bot posts the **report panel** (one live embed: type, order, status, staff
   decision, the Resolve / Reject staff buttons and the ticket's own Close button) followed by the customer's
   answers (escaped, never cut, split over several embeds/messages when long). Nothing about the report is posted
   in a public channel. Staff are alerted in `tickets.staff_alert_channel` (the alert names the customer, the
   type, the order and links the ticket; the answers stay in the ticket), optionally pinging the staff roles
   (`problems.ping_staff`; without an alert channel the ping goes with the panel). The customer gets a private
   confirmation linking the ticket. The order (when one was chosen) gets a history note, and `problems.reported`
   is audited.
5. **Decision.** Staff press **Resolve** (form with an optional note) or **Reject** (form with the reason, required
   when `problems.require_reason_on_reject = true`). The report changes `open -> resolved | rejected` exactly once
   (conditional update; a second or concurrent decision is refused with an operator reply). Then:
   - the report panel shows the status, the reason/note and who decided (its staff buttons are removed);
   - the customer is told privately: by direct message when `features.dm_notifications` is on. The updated report
     panel is the fallback: when the direct message cannot be delivered the panel already shows the decision, and
     when the panel itself cannot be updated the decision message is posted in the ticket with a mention
     (`notifications.fallback_to_channel`). Failures are logged and recorded in `notifications`;
   - the order gets a history note (`order_events`, staff-only wording) and its staff message is refreshed;
   - `problems.resolved` / `problems.rejected` is audited.
6. **Closing** uses the ticket system unchanged: the Close button on the report panel is the ticket system's own
   (`tickets.allow_user_close`, `tickets.close_confirmation`, lock, transcript to `tickets.transcript_channel`,
   reopen/delete buttons, automatic closing and deletion timers). A report can still be decided after its ticket
   was closed. A report whose ticket was closed or deleted without a decision no longer counts toward the limit.

## Settings (`[problems]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `discord_category` | `"purchase"` | Category key where report tickets are created (required). |
| `eligible_statuses` | `["paid", "processing", "fulfilled"]` | Order statuses a report may refer to (`[]` = reports never refer to an order). |
| `allow_without_order` | `true` | Offer "not about an order" and allow reports when the customer has no eligible order. |
| `max_open_per_user` | `1` | Undecided reports with an active ticket per customer (1-20). |
| `staff_roles` | `[]` | Extra role keys that can see report tickets and Resolve/Reject (the ticket system treats them as staff of report tickets too). |
| `require_reason_on_reject` | `true` | Rejecting requires a reason (shown to the customer). |
| `ping_staff` | `false` | Mention the staff roles in the new-report alert (or on the panel without an alert channel). |
| `[[problems.types]]` | one type `general` with a paragraph field `details` | `id` (internal) and an optional `form` (max 5 fields: `id`, `style`, `required`, `min_length`, `max_length`). Max 25 types. |

The tickets also follow `[tickets]`: `staff_alert_channel`, `transcript_channel`, `creation_cooldown_seconds`
(also the cooldown between two reports), `max_created_per_user_per_day`, `channel_name_pattern`, `number_padding`,
closing options and timers. Settings problems (the feature stays hidden until fixed): missing/unknown
`discord_category`, no types, unknown order statuses, unknown staff roles, a non-private staff alert channel, and
`eligible_statuses = []` together with `allow_without_order = false`. Problem reports depend on the `tickets` and
`checkout` features being ready.

Ticket system change made for this feature: the standalone ticket panel is optional. With
`tickets.panel_channel = ""` (shipped) the panel text is not required, `/setup` posts no ticket panel (and removes
one posted earlier), and `tickets.categories` may be empty (the shipped `general` category is now a commented
example). Other features can declare their own support-ticket category with
`tickets.service.register_external_category` so their staff roles count in the ticket controls and `/ticket`.

## Content keys (`problems.*`)

Every customer-visible word is a content key (see `docs/CONTENT_CHECKLIST.md` for the full list with limits and
placeholders):

- `problems.button.report` (+ optional `report_emoji`): the #purchase button.
- `problems.choose.*`: the private step (description, menu placeholders, order option label/description with
  `{order_number}`, `{total}`, `{status}`, the "not about an order" option); `problems.attachments_note`: the
  screenshots/files note.
- `problems.type.<id>.label|description|emoji|form_title` and `problems.type.<id>.form.<field>.label|description|placeholder`.
- `problems.created.description`, `problems.limit_reached.description`, `problems.no_orders.description`.
- `problems.channel_prefix`, `problems.channel_topic`.
- `problems.ticket.*` (panel title, text, field names, order value), `problems.status.open|resolved|rejected`
  (decision labels), `problems.button.resolve|reject`.
- `problems.notify.resolved|rejected.title|description`: the decision message.
- `problems.staff_alert.title|description`: the new-report staff alert (required when an alert channel is set).

Shared keys it shows: `tickets.button.close`, `common.button.continue`, `common.money_format` and
`checkout.status_label.<status>` (order menu and order field). Staff-only forms and replies use operator text
(`problems.form.*`, `problems.op.*`, `problems.order_note.*`, `problems.publish.*`; overridable with
`operator.<key>`).

## Commands

None of its own. Staff use the report panel buttons and the ticket commands (`/ticket close`, `/ticket list`,
`/ticket transcript`, ...), which work in report tickets for every report staff role.

## Data

Table `problem_reports` (migration `0007_problems.sql`): one row per report with its order (nullable), type,
answers (JSON), ticket, report-panel message, status (`open`/`resolved`/`rejected`, CHECKed with the decision
columns), reason, who decided and when. `UNIQUE(guild_id, user_id, nonce)` makes a repeated submission of the same
flow impossible; the open-report limit, the order's eligibility, the report row, its ticket row and its finishing
task are written in one transaction. Durable tasks: `problems.finish` completes or cleans up a creation that was
interrupted by a restart (after the ticket system's own recovery decided about the ticket), and
`problems.after_decision` repeats a decision's follow-ups (panel, notification, audit) if the process stopped
right after the decision. Both are idempotent.

## Testing in Discord (manual)

1. Fill every `problems.*` key reported by `/status` (plus the tickets and checkout keys), run `/setup`: #purchase
   now shows the Report a Problem button (`/setup` reports `Problem reports: ...` diagnostics).
2. As a member without orders: click it -> the form opens (one type). Submit -> private confirmation with a link;
   the ticket is visible to you and staff only, in the configured category; the panel, your answers and (in the
   staff alert channel) the alert appear. Upload a screenshot in the ticket.
3. Click the button again -> "limit reached" with a link to your open report.
4. With a paid/fulfilled order: the private step lists it (newest first) plus "not about an order"; choose it,
   Continue, submit; the panel shows the order and the order's `/order view` history has a note.
5. As a member, press Resolve/Reject in your ticket -> permission error. As staff: Reject without a reason ->
   refused; with a reason -> the panel updates, the customer gets a DM (close DMs to see the panel-only fallback),
   `#bot-log` shows `problems.rejected`. Press Reject again -> "already rejected".
6. Close the ticket with its Close button -> normal close confirmation, transcript in #transcripts.
7. Restart the bot between any two steps: every button, menu and form keeps working.

## Limitations

- The #purchase panel's button follows readiness only when `/setup` republishes the panel (like the other optional
  panel buttons).
- The report panel's Close button is rendered with the ticket's state when the panel was last updated; after the
  ticket is closed from elsewhere it stays visible and answers with `tickets.error.not_open` (it works again after a
  reopen).
- A report whose ticket is deleted before a decision stays `open` in the database (it no longer counts toward the
  limit and cannot be decided from Discord).
- Order history notes use operator (staff) wording; features that show an order's history to customers should
  treat notes as internal.
- Rendered option labels (`problems.choose.order_option` with long status names or totals) must stay within
  Discord's 100 characters; the preview checks the sample data, real values longer than the limit make the step
  fail with the generic error (logged) instead of being cut.
- Not yet verified in a live Discord server (automated tests use the repository's Discord fakes).
