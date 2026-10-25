# Customer panel: the private order ticket and `/myorders`

Specification sections 3 (#purchase: existing ticket link), 4 (private ticket messages), 6 (submission received,
status changes, reminders) and 8 (`/myorders`). The customer side of the checkout feature
(`shopbot/features/checkout/`) and the new feature `my_orders` (`shopbot/features/my_orders/`).

## What it does

**Starting from #purchase.** The Start button creates the customer's private order ticket (only the customer, staff
and the bot can see it), or privately answers with:

- a link to the unfinished ticket (`checkout.session_exists.description`), when the customer already has one;
- a link to the ticket of their newest open order (`checkout.order_exists.description`, with `{channel_mention}`,
  `{order_number}`, `{limit}`), when they already have `checkout.max_open_orders_per_user` open orders;
- the limit text (`checkout.limit_reached.description`) only when no open ticket can be linked.

Every reply can carry an optional link button that opens the ticket (`checkout.ticket_link.button`).

**The ticket introduction.** The first message of every order ticket identifies the ticket (its number, also shown
as a field) and explains who can see it (`checkout.ticket.intro_title` optional, `checkout.ticket.intro_description`
required). It is posted once per ticket (recorded with a dedupe key), before the live panel; if it cannot be posted,
the ticket is not opened (the half-made channel is removed, as when the panel itself fails). Restart recovery posts
it if a ticket never got it.

**The live panel** (one message, edited as the customer progresses) always shows:

| What | How |
|---|---|
| Unique reference | field `checkout.ticket.reference_field` = the ticket number (padded like the channel name) |
| Current step and progress | the step's title, and `common.progress_format` in the footer (now required for checkout) |
| What was entered/selected | the cart on every step (on later steps only its subtotal when the lines do not fit next to the rest), the chosen fulfillment option and fee, the details-form answers; the review lists everything incl. the answers |
| Current status | field `checkout.ticket.status_field` = `checkout.ticket.status_in_progress` before submission, the stage name (`checkout.ticket.stage.<status>`) afterwards |
| Actions | Back on every step after the first, Continue/Submit, Cancel; after submission the customer's cancel button while unpaid |

After submission the panel shows the reference, the order number (`checkout.ticket.order_number_field`), the
current stage, the order lines and totals, the payment instructions (while unpaid) and the **timeline**
(`checkout.ticket.timeline_field`): one line per stage reached, written with `checkout.ticket.timeline_entry`
(`{stage}`, `{time}`, `{relative}` - Discord timestamps, shown in each reader's own time zone). The timeline comes
from `order_events` status changes only; staff notes are never shown to customers. The panel is re-rendered on every
status change (the existing durable follow-up of every transition). A stage without a configured name (e.g. a
status added later) is left out rather than shown with invented text.

**Submission form** (`checkout.submission_form`, optional, at most 5 fields, same schema as `details_form`). When
configured, pressing Submit opens a form (title `checkout.submission.modal_title`, labels
`checkout.submission.form.<id>.label/.description/.placeholder`) instead of submitting directly. The answers are
checked on the server (required, length), stored in the session (so they are filled in again when the submission has
to be repeated, e.g. after a price change) and become part of the order (`orders.details`, keys `s.<id>`;
`Order.submission_answers`). Staff see them with their labels in the form-answers message under the staff order
controls and in `/order lookup`. The order cannot be submitted without the required answers (checked again in the
submit transaction). The form carries the totals the customer saw; if they changed, the price-changed notice is
shown and nothing is submitted. Empty `submission_form` = submit directly (previous behaviour).

**Notifications** (DM with the configured fallback to the ticket):

- *Submission received* (`checkout.notify.pending_payment.*`), once per order, part of the durable submission
  follow-up (repeated if the process stops before it was sent);
- the existing status notifications (paid, processing, fulfilled, cancelled, rejected, expired, refunded, refunds);
- *expiry warning* (`checkout.notify.session_expiry_warning.*`, `{minutes}`, `{expires}`): posted **in the ticket**,
  mentioning the customer, `checkout.session_expiry_warning_minutes` before an idle unfinished order expires. It is a
  durable task (`checkout.session_warning`, dedupe key per session) that follows the session's activity: one warning
  per idle period, a new one after the customer was active again; it ends with the session (submitted, cancelled,
  expired) and is re-armed by restart recovery (also when the warning is switched on later).

**`/myorders`** (the only customer command, any member, private reply): the member's own orders, newest first, one
card per order with the order number, current stage, total, timeline, a link to its ticket while it is open
(optional `my_orders.order.ticket_field`) and the tracking details staff recorded (optional
`my_orders.order.tracking_field`). Pages hold at most `my_orders.orders_per_page` orders and only as many as fit one
Discord message next to the configured text; previous/next buttons (`common.button.previous_page/next_page`, page
indicator `common.page_indicator`) carry only the member's id and the page, re-check that the clicking member owns
the list and reload from the database, so they keep working after a restart and never show another member's orders.

## Settings

| Setting | Shipped | Meaning |
|---|---|---|
| `checkout.session_expiry_warning_minutes` | 0 | Minutes before an idle cart expires at which the customer is warned in the ticket. 0 = off. Needs `session_expiry_minutes` > 0 and must be less than it (otherwise checkout reports a settings problem). |
| `checkout.submission_form` | none | Optional form asked when the customer presses Submit (`[[checkout.submission_form]]`, `id`, `style`, `required`, `min_length`, `max_length`; at most 5). |
| `my_orders.orders_per_page` | 5 | Orders per `/myorders` page (1-9; fewer when the cards would not fit one message). |
| `features.my_orders` | true | Enables `/myorders` (depends on checkout). |

## Content keys (overview)

All in `config/content.toml` / `docs/CONTENT_CHECKLIST.md` with their documentation:

- Checkout, group "order ticket": `checkout.ticket.intro_title` (optional), `checkout.ticket.intro_description`,
  `checkout.ticket.reference_field`, `checkout.ticket.order_number_field`, `checkout.ticket.status_field`,
  `checkout.ticket.status_in_progress`, `checkout.ticket.timeline_field`, `checkout.ticket.timeline_entry`.
- Checkout, group "order stage names": `checkout.ticket.stage.<status>` for every order status (also used by
  `/myorders`).
- Checkout, private replies: `checkout.order_exists.description`, `checkout.ticket_link.button` (optional).
- Checkout, notifications: `checkout.notify.pending_payment.title` (optional) / `.description`,
  `checkout.notify.session_expiry_warning.title` (optional) / `.description` (required when the warning is on).
- Checkout, submission form (only when configured): `checkout.submission.modal_title`,
  `checkout.submission.form.<id>.label` (+ optional `.description`, `.placeholder`).
- Shared: `common.progress_format` is now required by checkout (the panel always shows the progress).
- My orders: `my_orders.command.description` (the command's description in Discord, max 100 characters),
  `my_orders.list.title` (`{count}`), `my_orders.list.description` (optional), `my_orders.empty.description`,
  `my_orders.order.title` (`{order_number}`, `{status}`, `{total}`, `{date}`), `my_orders.order.number_field`,
  `.status_field`, `.total_field`, `.timeline_field`, `.ticket_field` (optional), `.tracking_field` (optional).

Missing required text keeps the feature unavailable (checkout: its panel is withdrawn and controls answer with the
"unavailable" text; `/myorders`: answers with the "unavailable" text, or is not registered at all when its
description is missing). Nothing incomplete is sent.

## Commands

`/myorders` - registered only when `features.my_orders` is on **and** `my_orders.command.description` is configured
and valid when the bot starts. Discord receives command descriptions when the commands are synced at startup:
**`/reload` does not re-sync them - restart the bot** after changing the description or switching the feature on.
(`docs/COMMANDS.md` lists `/myorders` only when its description is configured in `content.toml`.)

## How to test it in Discord

1. Configure the content keys above (and the checkout prerequisites), run `/setup`, restart the bot.
2. As a member, press Start in #purchase: the new ticket shows the introduction (ticket number, who can see it), then
   the panel with reference, status and progress. Go through the steps: every step shows what you selected, Back and
   Cancel.
3. On the review step, check the summary (cart, option, fees, your answers, total, status). Press Submit (with a
   `submission_form`, fill the form). You get the 'submission received' DM; the panel shows the order number, the
   stage and a one-line timeline.
4. As staff, mark the order paid / in progress / fulfilled: the panel's stage and timeline update each time; a
   staff note never appears in the customer's ticket.
5. Press Start again while the order is open: you get a private link to its ticket.
6. With `session_expiry_minutes = 30` and `session_expiry_warning_minutes = 10`, start a ticket and wait 20 minutes:
   the warning appears in the ticket mentioning you; act in the ticket and the timer starts again.
7. Run `/myorders`: a private list of your own orders only; with more orders than fit one page, page through them,
   restart the bot and use the buttons again. Another member's `/myorders` never shows your orders.

## Limitations

- Tracking details: `/myorders` reads them defensively from an `orders.tracking` (or `tracking_text` /
  `tracking_details`) column, which the staff-operations package adds. TODO (integrator): replace the lookup in
  `shopbot/features/my_orders/repo.py` with the order model's tracking accessor once that package is merged; the
  ticket panel does not show tracking details yet.
- Very long form answers may not all fit on the details/review steps next to the configured text (Discord allows
  6000 characters per message); the ones that do not fit are not shown there but are stored and shown to staff.
- A `/myorders` card whose configured title, after its placeholders are filled, exceeds Discord's 256 characters
  cannot be sent; `python -m tools.check_limits` reports it with the configured text.
- The expiry warning needs the ticket channel; if it was deleted, no warning is sent (the session expires as before).
  Switching the warning on with `/reload` applies to tickets opened afterwards; existing unfinished tickets get it
  after the next restart (restart recovery arms it).
- `/myorders` looks at a member's newest 500 orders (far more than a member normally has).
- Not yet verified against a live Discord server (automated tests use faithful fakes).
