# Operations: the staff side of orders

Package `operations` of the server specification (sections 2, 5, 6 and 8). It covers:

| Spec item | Where it lives |
|---|---|
| `#orders`: one live record per order (reference, customer, status, summary, timeline), staff action buttons | `checkout` (`staff_views`, `staff`, `handlers`, `handling_repo`) |
| Claim / Unclaim, Mark in progress, Mark completed, Add internal note, Request correction, Reject with reason, Cancel, Details, tracking details | `checkout` |
| `#staff-chat`: alerts for new and paid orders, reminders, failure alerts | `operations` (`alerts`, `reminders`) |
| Owner: private administrative alerts (DM, falling back to `#staff-chat`) | `operations.alerts` |
| `#bot-log`: failures, notification failures, role changes; sensitive values masked | `core.audit` + `operations.alerts` |
| `#transcripts`: closure time, final status and close reason in the transcript message | `tickets` (`{closed_at}`, `{final_status}`, `{close_reason}`) |
| Customer role: assigned and removed by configurable rules | `operations.customer_role` |
| `/orders` (browse, filter, paginate, open a record) and `/order view` | `checkout.commands` |

The `operations` feature module has **no switch of its own**: it is enabled whenever `features.checkout` or
`features.tickets` is on. Like every feature it is *ready* only when its settings are valid and its texts are
configured; until then nothing is posted (alerts and reminders are skipped and logged), and the rest of the bot is
unaffected. It is not customer-facing.

## The live order record (`#orders`)

One message per order in `checkout.staff_channel`, edited in place on every change (status, claim, note, correction
request, tracking details, refund). It always shows:

- the order's unique reference (its number), the customer (mention, no ping), the order channel, the current status
  (`checkout.status_label.*`), who claimed it and since when (while claimed), the tracking details (when used);
- the order lines and totals;
- a **timeline**: every status change from `order_events` (submitted, paid, in progress, completed, ...) plus claims,
  releases, correction requests and tracking details, each with its time (Discord timestamps, shown in each reader's
  time zone) and who did it;
- the latest **internal notes** (never shown to customers).

When the record would exceed Discord's limits, the order lines move to separate messages first, then the oldest notes
and timeline entries are left out (the **Details** button shows everything). Configured text is never cut.

**Privacy.** What the customer typed in the order forms (details form, fulfillment option form, submission form) is
not part of the record and is not posted in #orders: staff open it privately with **Details** or `/order view`, after
the bot's staff check. `operations.answers_in_orders_channel = true` also posts the answers as messages under each
record (the previous behaviour). See `docs/features/orders_privacy.md`.

### Staff actions

All buttons are routed (`sb:cs:<action>:<order id>[...]`), keep working after a restart, re-check the staff role on
the server (`permissions.staff_roles`, `checkout.staff_roles`, admins) and reload the order. Every change is one
conditional database write (a double click or two staff members acting at once never apply twice), audited, and
followed by a durable follow-up task (`checkout.activity_followup`) that updates the record and sends the customer
notification even after a crash.

| Button | Effect |
|---|---|
| Mark paid / In progress / Completed / Cancel | the order state machine (unchanged); a button acts only on the status it was rendered for |
| Reject | opens a form for the **reason**; the reason is included in the customer's rejection notification |
| Claim | records who handles the order and when; a second claim (by anyone) is refused |
| Release claim | only the member holding the claim, or an admin |
| Request correction | (waiting for payment) opens a form; the customer is notified privately with the configured wording plus the staff message; the order keeps waiting; recorded in the timeline |
| Tracking details | optional (only when `checkout.staff.button.tracking` is set); stores tracking text; included in the completion notification, or sent to the customer when the order is already completed |
| Add note | opens a form; the internal note is stored and listed on the record |
| Details | the full record incl. the customer's form answers, privately to the clicking staff member (same as `/order view`) |
| Refund / Refund to credit / Return credit | unchanged |

Internal notes used to be `order_events` rows without a status change; migration `0008_operations` moves them to
`order_activity`, so `order_events` holds only status changes (customer-facing timelines are built from it).

## Alerts in `#staff-chat` and the Owner

| Alert | When | Pings `operations.ping_roles` | Owner DM |
|---|---|---|---|
| New order | an order is submitted | yes | yes |
| Paid order | an order's payment is confirmed (it waits to be completed) | yes | yes |
| Notification failed | a private notification reached nobody (neither DM nor the fallback channel), or a bot post to a channel failed (channel missing or a Discord error) | yes | no |
| Task failed | a scheduled task failed permanently | yes | yes (critical) |
| Error | a button, form or command failed unexpectedly (with the reference the member saw) | no | no |
| Customer role | the customer role could not be given/removed | yes | no |

Alerts carry identifiers only (numbers, mentions, totals, links, internal names); never form answers, payment details
or error texts. Every alert is a durable `operations.alert` task with a dedupe key, so it survives a restart and is
sent at most once. Failure alerts of the same kind and source are grouped (one per 10 minutes); every failure is still
audited. The Owner (`OWNER_ID`) receives order alerts and task failures by DM when `operations.owner_dm_alerts` is on;
when the DM fails, the alert is posted in `#staff-chat` with a mention of the Owner and the failure is audited
(`operations.owner_dm_failed`).

Pings notify only when the role is mentionable or the bot has "Mention @everyone, @here and All Roles"; `/setup` reports
roles that cannot be pinged.

## Reminders

One self-rescheduling task (`operations.reminders`) checks open records every minute and posts a reminder in
`#staff-chat` when a record waited longer than its threshold, then every `reminder_repeat_minutes` while it still
waits:

| Setting | Waiting since |
|---|---|
| `unclaimed_order_minutes` | an open order nobody claimed: since it was submitted (or since its claim was released) |
| `unpaid_order_minutes` | an order waiting for payment: since it was submitted |
| `unfulfilled_order_minutes` | a paid order not completed: since the payment was confirmed |
| `unanswered_ticket_minutes` | a support ticket: since the customer's first message after the last staff reply (or since it was opened) |

What was sent is recorded per waiting period (`operations_reminders`), so restarts never repeat a reminder. Staff
replies are detected from messages in the ticket (bots ignored).

## Customer role

`checkout.customer_role` (role key, `""` = off) is given when one of a member's orders reaches
`checkout.customer_role_on` (`paid` or `fulfilled`) and removed when an order is fully refunded and none of the
member's other orders qualifies (`checkout.customer_role_remove_on_refund`). Durable, idempotent task
(`operations.customer_role`); audited (`checkout.customer_role_added/removed/failed`); a missing role, a role above
the bot's highest role or a 403 is audited and reported in `#staff-chat`; a member who left is skipped.

## Settings

```toml
[operations]
staff_channel = "staff_chat"   # required; staff/admin channel
ping_roles = ["staff"]
owner_dm_alerts = true
answers_in_orders_channel = false  # true = also post the customer's form answers under each record in #orders
unclaimed_order_minutes = 0    # reminders: 0 = off (business decisions)
unpaid_order_minutes = 0
unfulfilled_order_minutes = 0
unanswered_ticket_minutes = 0
reminder_repeat_minutes = 0    # 0 = remind once

[checkout]
customer_role = "customer"
customer_role_on = "fulfilled"
customer_role_remove_on_refund = true
```

Problems reported by `/status`: missing or public `operations.staff_channel`, unknown `ping_roles`, unknown
`checkout.customer_role`.

## Content keys (overview)

- `checkout.staff.*`: the record's field names (`reference_field`, `customer_field`, `channel_field`, `status_field`,
  `claimed_field`, `tracking_field`, `timeline_field`, `notes_field`), line formats (`timeline_line`: `{time}`
  `{stage}` `{actor}`; `note_line`: `{time}` `{actor}` `{note}`), stage names (`stage.claimed`, `stage.unclaimed`,
  `stage.correction`, `stage.tracking`) and buttons (`button.claim`, `button.unclaim`, `button.note`,
  `button.correction`, `button.details`, optional `button.tracking`).
- `checkout.notify.rejected.reason_field`, `checkout.notify.correction.*`, `checkout.notify.tracking.*`: customer
  notifications sent by staff actions. Tracking texts are needed only once `checkout.staff.button.tracking` is set.
- `operations.alert.{new_order,paid_order}.*`, `operations.reminder.{unclaimed,unpaid,unfulfilled,unanswered}.*`,
  `operations.failure.{notification,task,error,role}.*`, optional link buttons `operations.button.*`, and the Owner's
  optional versions `operations.owner.*` (empty = the staff wording). Reminder texts are needed only while their
  threshold is above 0.
- `tickets.transcript.description` gained `{closed_at}`, `{final_status}`, `{close_reason}`.

Staff-only forms and private replies to staff are operator text (`checkout.note.*`, `checkout.correction.*`,
`checkout.tracking.*`, `checkout.reject.*`, `checkout.op.*`).

## Commands

- `/orders [status] [member]`: staff; the order list (10 per page) with a status filter menu, a menu that opens an
  order's full record privately, and previous/next buttons. Same list as `/order list`.
- `/order view <number>`: staff; the full record (same as `/order lookup`).
- `/order note` now stores the note as staff activity (shown on the record).
- `/ticket close` in an order ticket: closes the ticket of an order that was never submitted (the unfinished order is
  cancelled); refused while a submitted order of the ticket is open (see `docs/features/orders_privacy.md`).

## Testing manually in Discord

1. Configure the texts above (`python -m tools.checklist`), set `[operations]` and run `/setup`; read its report
   (staff channel, ping roles, customer role hierarchy).
2. Place an order as a member: `#orders` shows the record (reference, customer, status, timeline); `#staff-chat` shows
   the new-order alert with a ping; the Owner receives a DM (close the Owner's DMs to see the `#staff-chat` fallback).
3. Click Claim (twice, and with a second staff account), Add note, Request correction (the customer receives the
   message), Reject (enter a reason; the customer's rejection notification shows it).
4. On another order: Mark paid (paid alert), Tracking details, Mark completed (the completion notification shows the
   tracking details; the customer gets the Customer role), then refund it fully (the role is removed).
5. Set a reminder threshold to 1 minute and wait: a reminder appears in `#staff-chat`.
6. `/orders`, filter by status, open a record from the menu; `/order view 1`.
7. Check `#bot-log` for the audit entries and `#transcripts` for the closure time and final status.

## Limitations

- Reminder sweeps run every minute: a reminder can be up to a minute late; at most 10 reminders are posted per sweep.
- Threshold changes apply at the next sweep; a reminder already sent for a waiting period is not sent again when the
  threshold is lowered.
- Ticket replies are tracked from the moment this version runs; tickets opened earlier count as waiting since they
  were opened until the next staff message.
- Alert pings depend on Discord role settings (mentionable role, or the bot may mention all roles).
- Order alerts and the customer role react to events published by the checkout follow-ups; if a follow-up's other steps
  all completed but scheduling the alert failed (database error), that alert is lost (logged).
- Not verified in a live Discord server yet (automated tests with fakes only).
