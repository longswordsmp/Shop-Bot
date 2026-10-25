# Reviews (`reviews`)

Specification: section 3 (CATALOG / `#reviews`): "Verified reviews submitted through the bot's review workflow. Each
review embed can show the review rating, approved comment, verification status, and a reference to the relevant
completed transaction or record. Only eligible users can submit reviews. Enforce the configured review limits and
prevent duplicate submissions for the same qualifying transaction. Do not display aggregate ratings unless explicitly
enabled in configuration." Also section 6 (approval / rejection notifications) and section 2 (the Owner and staff
approve or reject submissions).

## What it does

1. **Review request.** When an order becomes `fulfilled` (checkout calls `schedule_review_prompt`), the task
   `reviews.prompt` runs `reviews.prompt_delay_minutes` later and sends the customer a request with a rating menu and
   a decline button, by direct message and/or in the order channel (`reviews.prompt_via`). Only the order's customer
   can answer it, only while the order is still fulfilled.
2. **Rating and comment.** Picking a rating opens the comment form (`reviews.allow_comment`, optional or required,
   at most `reviews.comment_max_length` characters) or finishes the review right away. A comment the published review
   (or the staff approval message) could not show within Discord's limits is refused, so it can be shortened.
3. **Limits and duplicates.** One review per order (`UNIQUE(order_id)`); every step is a conditional update, so a
   double click or a second form submission never counts twice, and a finished review cannot be changed (the
   customer gets `reviews.already_reviewed.description`). Only fulfilled orders can be reviewed.
4. **Publication.** Without approval, the submitted review is approved automatically and published in
   `reviews.channel` (#reviews). The published embed shows the title (with `{rating}`, `{user_name}` and
   `{order_number}`, the reference to the completed order), the comment exactly as written (escaped, it never pings),
   the rating, and the optional **verification marker** (`reviews.published.verified_field` +
   `reviews.published.verified_value`; every review comes from a completed order, so every published review is a
   verified purchase). A failed publication is retried (task `reviews.publish`) until it succeeds; a crash between the
   post and its record never posts a second copy (claim + adoption of the unrecorded post). No aggregate rating is
   shown anywhere.
5. **Staff approval (`reviews.require_approval = true`).** The submitted review is *not* published. Instead:
   - the customer sees `reviews.thanks_pending.description` (falls back to `reviews.thanks.description`);
   - the review becomes `pending_approval` and an **approval message** is posted in `reviews.approval_channel`
     (a staff-only channel, shipped `staff_chat`): title, the comment, rating, order number, the customer (mention),
     status, and the **Approve** / **Reject** buttons. Posting is retried (task `reviews.approval`) until it works and
     never duplicated (same claim/adoption protocol as the publication);
   - **Approve** (staff only, checked by the router and again in the handler) changes `pending_approval -> approved`
     exactly once, the message shows "approved by @staff" without buttons, and the review is published in #reviews
     with the same embed as without approval (the comment is now the approved comment). Approving is refused when
     the order is no longer fulfilled (refunded/cancelled meanwhile): such a review cannot be a verified review, so
     staff reject it instead;
   - **Reject** opens a staff-only form (operator text) with an optional reason (max 500 characters). The review
     becomes `rejected` exactly once and is never published; the message shows "rejected by @staff" and the reason
     (when `reviews.approval.reason_field` is set);
   - the customer gets an optional private **notice** for both outcomes (`reviews.notice.approved.*` once the review
     is published, with `{review_link}`; `reviews.notice.rejected.*`, with the reason under
     `reviews.notice.rejected.reason_field` when that heading is set). Notices go by direct message when
     `features.dm_notifications` is on, else (or when DMs are closed) in the order channel with a mention
     (`notifications.fallback_to_channel`); an undeliverable notice is logged, recorded and alerted. Each review gets
     at most one notice;
   - every decision is audited (`reviews.approved` / `reviews.rejected`, actor, order number, rating, whether a
     reason was given - not the reason text) and the follow-ups (message update, publication or notice, audit) run
     again from the durable task `reviews.decided` if the process stopped right after the decision;
   - a second click, a click by a second staff member at the same time, or a late `/review approve|reject` changes
     nothing and is answered privately with the review's current state (the clicked message is refreshed).
6. **Staff tools.** `/review list|remove|resend|approve|reject` (see Commands).

Turning approval off again does not publish reviews that are still waiting: they keep their approval message and can
still be approved or rejected (buttons or commands). New submissions are published right away again.

## Settings (`[reviews]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `channel` | `"reviews"` | Where published reviews are posted (required). |
| `prompt_delay_minutes` | `-1` | **Business decision**: minutes after fulfillment before the request (0 = immediately). |
| `rating_scale` | `0` | **Business decision**: number of rating options (1-10). |
| `allow_comment`, `comment_required`, `comment_max_length` | `true`, `false`, `1000` | The comment form. |
| `prompt_via` | `"dm_then_channel"` | `"dm"`, `"channel"` (the order channel) or DM with the order channel as fallback. |
| `require_approval` | `false` | `true`: submitted reviews wait for a staff decision in `approval_channel` before they are published. |
| `approval_channel` | `"staff_chat"` | Channel key for approval messages; must be a staff or admin channel. Used (and checked) only when `require_approval = true`. |

Settings problems (reviews stay hidden until fixed): the two business decisions unset, a missing/unknown channel,
`prompt_via = "dm"` without `features.dm_notifications`, `comment_required` without `allow_comment`, and with approval
on: no approval channel, an unknown one, or one that is not staff/admin-only (the approval message shows comments
that are not public). Staff for the approval buttons and `/review`: `permissions.staff_roles`, admins and
`checkout.staff_roles`.

## Content keys (`reviews.*`)

All customer-visible text is a content key (full list with limits and placeholders in `docs/CONTENT_CHECKLIST.md`):

- Request: `reviews.prompt.title` (optional), `.description`, `.select_placeholder`, `reviews.button.decline`,
  `reviews.rating.<n>.label|description|emoji|display`.
- Comment form: `reviews.comment.title|label` (required with `allow_comment`), `.description|placeholder` (optional).
- Published review: `reviews.published.title`, `.rating_field`; verification marker `reviews.published.verified_field`
  + `.verified_value` (optional; set both or neither - one without the other is reported and keeps reviews hidden).
- Replies: `reviews.thanks.description`, `reviews.thanks_pending.description` (optional, approval on),
  `reviews.declined.description`, `reviews.already_reviewed.description`, `reviews.expired.description`.
- Approval message (staff business message; required only when `require_approval = true`): `reviews.approval.title`,
  `.rating_field`, `.order_field`, `.customer_field`, `.status_field`,
  `reviews.approval.status.pending|approved|rejected|removed` (`{staff_mention}` for approved/rejected),
  `reviews.approval.button.approve|reject`, and the optional `reviews.approval.reason_field`.
- Notices (optional; nothing is sent when the description is empty, a title without its description is reported):
  `reviews.notice.approved.title|description` (`{order_number}`, `{review_link}`),
  `reviews.notice.rejected.title|description|reason_field`.

Staff replies, the reject form and `/review` output are operator text (`reviews.status.*`, `reviews.form.*`,
`reviews.decision.*`, `reviews.list.*`, `reviews.cmd.*`, `reviews.setup.*`; overridable with `operator.<key>`).

## Commands (staff)

| Command | What it does |
|---|---|
| `/review list [status]` | The 25 most recently changed reviews (statuses include `pending_approval` and `rejected`). |
| `/review remove <order_number>` | Deletes the published review message and closes the review; a pending review's approval message shows "removed". |
| `/review resend <order_number>` | Sends an unanswered review request again. |
| `/review approve <order_number>` | Approves a review waiting for approval (same as the Approve button, e.g. when the approval message was deleted). |
| `/review reject <order_number> [reason]` | Rejects a review waiting for approval (same as the Reject form). |

## Data

Table `reviews` (rebuilt by migration `0009_reviews_approval.sql`, existing rows kept; submitted and published reviews
are recorded as approved automatically): status `prompted | rated | pending_approval | published | rejected |
declined`, rating, comment, the request and published messages, and the moderation record: `approval`
(`automatic | approved | rejected`), `decided_by`, `decided_at`, `decision_reason` (1-500 characters),
`approval_channel_id` / `approval_message_id` (UNIQUE). CHECK constraints make inconsistent states impossible (a
pending or rejected review without rating/comment, a published or submitted review without an approval, a staff
decision without who/when, a reason on a non-rejected review, a message id without its channel).

Durable tasks: `reviews.prompt`, `reviews.publish`, `reviews.approval` (post the approval message), `reviews.decided`
(decision follow-ups, scheduled in the decision's transaction). Startup re-queues unpublished submitted reviews and
approval messages that were not posted; `/setup` additionally edits the approval messages of pending reviews to the
current text and re-posts deleted ones.

## Testing in Discord (manual)

1. Set `reviews.prompt_delay_minutes`, `reviews.rating_scale`, fill the `reviews.*` keys `/status` reports, run
   `/setup`. Optionally fill `reviews.published.verified_field|verified_value`.
2. Without approval: mark an order fulfilled (staff controls in #orders), answer the request (rating + comment) -> the review
   appears in #reviews with the rating, the comment, the order reference and the verification marker; rating again
   -> "already reviewed".
3. Set `reviews.require_approval = true`, fill the `reviews.approval.*` keys (and optionally the notices),
   `/reload`, `/setup`. Submit another review -> the customer sees the pending text, nothing appears in #reviews,
   #staff-chat shows the approval message with Approve / Reject.
4. As a member without the staff role, click Approve -> permission error. As staff: click Approve -> the message
   shows the decision, the review appears in #reviews, the customer gets the approval notice with a link; click
   Approve again -> "no longer waits for approval".
5. Submit a third review, click Reject, enter a reason -> nothing in #reviews, the message shows the reason, the
   customer gets the rejection notice (close your DMs to see the order-channel fallback); `#bot-log` shows
   `reviews.rejected`.
6. Delete a pending approval message and run `/setup` -> it is posted again; or use `/review approve <number>`.
7. Restart the bot between any two steps: every button and form keeps working.

## Limitations

- Approval messages do not ping staff roles; they appear in the approval channel like other staff messages.
- An approval message that a staff member deleted is re-posted only by `/setup` (or the review is decided with
  `/review approve|reject`).
- The approval notice is sent once the approved review is published; if publication keeps failing (e.g. the bot lost
  access to #reviews) the customer is told only after it succeeds.
- A review that waits for approval when its order is refunded can only be rejected (or removed).
- Rejected reviews are final for that order: the customer cannot submit a new review for the same order.
- Not yet verified in a live Discord server (automated tests use the repository's Discord fakes).
