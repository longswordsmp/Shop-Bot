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

**The ticket introduction.** The first message of every order ticket identifies the ticket (its number, in the
reference line `checkout.ticket.reference_line` at the top of the card) and explains who can see it (`checkout.ticket.intro_title` optional, `checkout.ticket.intro_description`
required). It is posted once per ticket (recorded with a dedupe key), before the live panel; if it cannot be posted,
the ticket is not opened (the half-made channel is removed, as when the panel itself fails). Restart recovery posts
it if a ticket never got it.

**The live panel** (one message, edited as the customer progresses) is one card that reads like a receipt: a key
facts line at the top, the step's lead text, then sections (a bold heading starting with an icon over short lines).
It always shows:

| What | How |
|---|---|
| Unique reference and current status | the key facts line: `checkout.ticket.draft_line` (ticket number, padded like the channel name, and `checkout.ticket.status_in_progress`) before submission, `checkout.ticket.order_line` (ticket number, order number and the stage name `checkout.ticket.stage.<status>`) afterwards |
| Current step and progress | the step's title, and `common.progress_format` in the footer (now required for checkout) |
| What was entered/selected | the items section (`checkout.cart.heading`, one `checkout.cart.line` per item) on every step (on later steps left out only when the card would not fit one message; a long card continues in a second one of the same colour), the summary section (`checkout.summary.*`: subtotal, the discount when a code is applied (`Discount (CODE) · -$X`, or "not applied" with the reason under the total), the chosen delivery option and its fee, extras and fees, the tip for our team (`Tip for our team · $X`, its own line, never among the extras), store credit, the total in bold), the details-form answers; the review lists everything incl. the answers, and while `[checkout.tips]` is on its last line, after the address, says tips are optional and "100% of every tip goes to our team" (a thank-you once a tip is chosen) |
| Actions | Back on every step after the first, Continue/Submit, Cancel; on the review step also Use store credit, Add discount code (a private form), Use coupon CODE for coupons the customer claimed from an announcement, and Remove discount once one is applied (one code per order; see `docs/WORKFLOWS.md` and `docs/DECISIONS.md` §11e), and the tip menu (its own row after the store credit and discount buttons, above Back / Confirm order / Cancel: "Add a tip for our team (optional)", No tip, $2.00 tip / $5.00 tip / $10.00 tip from `[checkout.tips] amounts`, Other amount opens a private form; the chosen tip shows as selected, the customer's own amount as its own option; see `docs/DECISIONS.md` §11g); after submission the customer's cancel button while unpaid |

**Shipping address and apartment details** (`checkout.ask_address`). The address step has a region menu, the chosen
region's country menus and a private address form (name, street, city, state or region, postal code: Discord's 5
inputs). Next to its button, **Add apartment (optional)** (`checkout.address.apartment_button`, shown from the start and
usable once a country is chosen) opens a second private form (`checkout.address.apartment_modal_title`, "Apartment
details (optional)") with four optional fields: apartment, suite or unit; building or floor; entry or buzzer code;
delivery instructions (labels and placeholders `checkout.address.<unit|building|access|notes>.label/.placeholder`). The
form opens with what was entered before, the button then reads **Edit apartment details** (`.apartment_button_change`),
and emptying every field removes them. The answers are checked on the server (at most 50 characters each, 200 for the
delivery instructions, kept on one line) and stored with the address (`a.unit`, `a.building`, `a.access`, `a.notes`), so
they become part of the order with it. The card never shows them: SHIP TO on the address step and the review only adds
the line `checkout.address.apartment_line` ("Apartment details added"; the button and the line share the house icon).
Staff see them wherever they see the full address (privately: the record's Details button, `/order view` / `/order
lookup`; only with `operations.answers_in_orders_channel = true` also in the form-answers message in #orders), in
shipping-label order: name, street, apartment, building, city, region, postal code, country, then the entry code and the
delivery instructions on their own lines. The region menu, the chosen region's country menus (25 countries each) and
these buttons share Discord's 5 rows: the address buttons come first and Back / Continue / Cancel get a row of their own
whenever the menus leave room for it (otherwise they share the address buttons' row, Cancel last). A region of more than
75 countries (50 with a `checkout.details_form`, whose buttons need a row of their own) is reported as a settings
problem.

**Closed by staff.** Staff can close the ticket of an order that was never submitted with `/ticket close`: the
unfinished order is cancelled and the panel shows `checkout.staff_closed.title` / `.description` (optional; empty =
the cancelled texts) without controls. A submitted open order is refused; staff end it from #orders (see
`docs/features/orders_privacy.md`).

After submission the panel shows the key facts line (ticket, order number, current stage), the items, the summary,
the payment sections (while unpaid: the payment methods, how to pay, what was paid so far), the shipment while the
order is shipped (fields `checkout.notify.shipped.carrier_field` and `.number_field`, and the
`checkout.notify.shipped.track_button` link, the same texts as the shipped notification) and the **progress**
section (`checkout.progress.heading`): the first stage `checkout.progress.start`, then one line per stage reached
(`checkout.progress.done`, the current one `checkout.progress.current`, an ending stage `checkout.progress.stopped`)
and the stages still to come (`checkout.progress.next`), each with `{stage}` and a date (`{date}`, `{time}`,
`{relative}` - Discord timestamps, shown in each reader's own time zone). The progress comes from `order_events`
status changes only; staff notes are never shown to customers. The panel is re-rendered on every status change (the
existing durable follow-up of every transition). A stage without a configured name (e.g. a status added later) is
left out rather than shown with invented text.

**Submission form** (`checkout.submission_form`, optional, at most 5 fields, same schema as `details_form`). When
configured, pressing Submit opens a form (title `checkout.submission.modal_title`, labels
`checkout.submission.form.<id>.label/.description/.placeholder`) instead of submitting directly. The answers are
checked on the server (required, length), stored in the session (so they are filled in again when the submission has
to be repeated, e.g. after a price change) and become part of the order (`orders.details`, keys `s.<id>`;
`Order.submission_answers`). Staff see them with their labels privately: the order record's Details button and
`/order view` / `/order lookup` (and, only with `operations.answers_in_orders_channel = true`, in a form-answers
message under the staff order controls in #orders). The order cannot be submitted without the required answers
(checked again in the submit transaction). The form carries the totals the customer saw; if they changed, the price-changed notice is
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

**`/myorders`** (a customer command, like `/review`; any member, private reply): one card with the member's own orders,
newest first, one section per order: its number and current stage in bold (`my_orders.order.title`), its total and
date (`my_orders.order.summary`), a link to its ticket while it is open (optional `my_orders.order.ticket_field`) and
the tracking details staff recorded (optional `my_orders.order.tracking_field`). Pages hold at most `my_orders.orders_per_page` orders and only as many as fit one
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
  `checkout.ticket.reference_line`, `checkout.ticket.draft_line`, `checkout.ticket.order_line`,
  `checkout.ticket.status_in_progress`.
- Checkout, group "order ticket - items, summary and progress": `checkout.cart.heading`, `checkout.summary.heading`,
  `.line`, `.total_line`, `.credit_line`, `.free` (optional), `checkout.progress.heading`, `.start`, `.done`,
  `.current`, `.stopped`, `.next`.
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
  `my_orders.order.title` and `my_orders.order.summary` (`{order_number}`, `{status}`, `{total}`, `{date}`),
  `.ticket_field` (optional), `.tracking_field` (optional).

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
   the panel with its key facts line (ticket number, status) and progress. Go through the steps: every step shows what you selected, Back and
   Cancel.
3. On the review step, check the summary (cart, option, fees, your answers, total, status). With a code created by
   `/discount create`, press Add discount code and enter it (a wrong code gives its reason privately), or press Claim
   coupon on a preset announcement first and then Use coupon CODE: the discount line appears under the subtotal and
   the total drops; Remove discount takes it off. Pick "$5.00 tip" in the tip menu: "Tip for our team · $5.00"
   appears after the extras and the total grows by $5.00; pick Other amount, type 7.50 (then "abc", "€5" and 500:
   each gives its reason privately), and pick No tip to take it off. Press Submit (with a
   `submission_form`, fill the form). You get the 'submission received' DM; the panel shows the order number, the
   stage and the progress section.
4. As staff, mark the order paid / in progress / fulfilled: the panel's stage and progress update each time; a
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
