# Workflows

The specification features (#welcome, guides, /announce, #catalog Buy buttons, #new-arrivals, the live order ticket and /myorders, Report a Problem, staff operations) are described one per page in `docs/features/`. This page covers the shared ticket and checkout mechanics.

How each customer-facing workflow behaves. Every visible word comes from content keys (`docs/CONTENT_CHECKLIST.md`);
this document describes behaviour only.

## Ticket system

**Panel** (`tickets.panel_channel`): one button per ticket category, or a dropdown (`tickets.panel_style = "select"`).

**Opening a ticket**
1. Router checks: configured server, feature ready, rate limit, duplicate click.
2. Ticket checks: creation cooldown, open-ticket limit per category, daily creation cap
   (`tickets.max_created_per_user_per_day`).
3. If the category has a form, it opens; the limits are checked again on submit.
4. One transaction reserves the ticket number and inserts the ticket (`creating`).
5. A private channel is created in the category's Discord category: `@everyone` cannot see it; the owner, staff
   roles (global, `tickets.staff_roles`, category staff roles), admin roles and the bot can. Owners cannot create
   threads.
6. The welcome message (with form answers) and controls are posted; the ticket becomes `open`.
7. Staff alert (optional ping), audit entry, inactivity timers (if configured), private confirmation to the user.
If anything fails, the channel is removed and the ticket is marked `failed`; an interrupted creation is recovered
by a scheduled sweep.

**Controls**
| Control | Who | Effect |
|---|---|---|
| Close | owner (if `allow_user_close`) or staff | optional confirmation → `closed`; owner can no longer post (`lock_on_close`); optional move to `closed_category`; transcript; optional scheduled deletion |
| Claim | staff | assigns the ticket to the staff member who clicked |
| Unclaim | the assigned staff member, or an admin | removes the assignment |
| Reopen | staff (owner if `user_can_reopen`, within limits) | `closed` → `open`, restores access, cancels deletion |
| Transcript | staff | generates and posts a transcript now |
| Delete | staff, with confirmation | only closed tickets; transcript first; never deletes if the transcript cannot be produced |
| `/ticket add/remove` | staff | adds/removes a participant (channel access + notice) |

**Automation** (all off by default): inactivity warning and auto-close, deletion after close. Timers re-check
activity before acting, so a message in the ticket postpones them.

**States:** `creating → open ⇄ closed → deleted` (`failed` if creation fails). A channel deleted manually marks the
ticket `deleted`.

**Transcripts:** self-contained HTML (all values escaped) with timestamps, authors, message text, attachment links,
and embed text; posted to `tickets.transcript_channel`, optionally sent to the owner by DM. Attachment links point
to Discord's CDN and expire after a while (Discord signs them); the transcript keeps the file names and links, not
the files themselves.

**Channel limits:** Discord allows at most 50 channels in a category and 500 in a server. Closed ticket and order
channels stay until deleted, so set `tickets.delete_after_close_minutes` (and
`checkout.delete_channel_after_close_minutes`) or a `tickets.closed_category`, or delete closed channels yourself.
When a category is full, new tickets are refused with your generic error text and the bot logs
`tickets_category_full` and records the failure in the audit log.

## Catalog

- Staff manage products with `/product` (add/edit forms, price, stock, image URL, active toggle, archive, sort).
  Edits use optimistic concurrency: a form opened before another change is rejected as stale.
- The live listing (`catalog.listing_channel`) is a set of bot messages kept in sync after every change (edited in
  place, never duplicated).
- Browsing: a private, paginated view, one product per page.
- Inventory: `stock_on_hand` (empty = unlimited), `reserved` (held by unpaid orders). Available = stock − reserved.
  Stock can never be set below what is reserved.

## Checkout

**Start** (shop panel): if the customer already has an active cart, they are pointed to its channel instead of
getting a second one; otherwise (within `max_open_orders_per_user`) a private order channel is created with a single
workflow message that is edited in place for every step.

**Steps:** browse (paginated product menu → quantity form) → cart (edit quantities, minimum order) → fulfillment
(only if options are configured) → details (only if forms are configured; split over two forms when more than five
fields) → review (items, discount code, fees, fulfillment fee, store credit, total) → submit. Back/Continue/Cancel
move between steps; Continue is disabled until the step is complete.

**Discount codes** (review step, `checkout.discount_codes`; codes are made with `/discount`): **Add discount code**
(while the shop has a code that is not ended or expired) opens a private form; **Use coupon CODE** applies a coupon
the customer claimed (the two newest that can still be used). These buttons are secondary and share the store
credit's row (Confirm order stays the one main action). A code that applies is shown under the subtotal
("Discount (SAVE10) · -$2.00", or "Buy one, get one free (BOGO) · -$5.99") and **Remove discount** replaces those
buttons; one code per order. A code that cannot be used gets a private reply with its reason (unknown, ended, expired,
used up, already used, a minimum, no eligible items, buy one get one free needs two items); after 5 such typed codes
in 10 minutes the customer waits a few minutes. A code that would take nothing off (only free items) does not apply.
A code that stops applying while the order is put together (the cart changed, it ended) shows "Discount (CODE) · not
applied" in the summary and its reason under the total, and the total is without it. The rules (buy one get one free
pairs, product limits, how uses are counted) are in `docs/DECISIONS.md` §11e.

**Totals** (one function for display and charging): subtotal = Σ unit price × quantity; discount (from the discount
code, at most the items); fulfillment fee; each fee (flat, or percent of the items after the discount, rounded half
up); credits = min(balance, total) when applied; total = subtotal − discount + fees − credits. Integer minor units
throughout.

**Submit** (one atomic transaction): the total in the button must match a fresh recomputation (else the review is
shown again with your price-changed text; a discount code that expired, ended or was used up since the review changes
the total this way); every product must still be available; stock is reserved for every line (all or nothing —
concurrent customers cannot oversell); credits are debited; the order (number, items, fees, discount code and amount,
reservations, payment record, history) is written. A duplicate submit returns the same order.

After submit: the customer sees your payment instructions; staff get a control message in `checkout.staff_channel`.

**Order states** (each change is one conditional update plus a history row, so concurrent clicks cannot double-apply):

```
pending_payment   -> payment_submitted | paid | rejected | cancelled | expired
payment_submitted -> pending_payment | paid
                     (gift cards cover the total; it leaves only through a decision on its cards: redeemed cards
                     covering the total make it paid, otherwise it waits for payment again. It is never rejected or
                     cancelled while cards wait, because staff may have redeemed a revealed card: Card problem first)
paid              -> processing | shipped | fulfilled | cancelled | refunded
processing        -> shipped | fulfilled | cancelled | refunded
shipped           -> fulfilled | refunded                (carrier and tracking number recorded)
fulfilled         -> refunded                            (shown to customers as "Delivered")
cancelled         -> refunded   (only if it had been paid)
```

| Change | Stock | Credits | Channel |
|---|---|---|---|
| → paid (staff confirms payment) | reserved stock is sold | – | stays open |
| → rejected / cancelled / expired before payment | reservations released | debit returned | closed |
| → cancelled after payment | not restocked automatically (staff decide) | not returned automatically: staff use the refund and "give back applied credit" controls | closed |
| → shipped (Mark shipped or `/order ship`) | – | – | stays open; the customer gets the carrier, the tracking number and a Track package button when a link was entered |
| → fulfilled (Mark delivered) | – | – | closed; review prompt scheduled (reviews on) |
| refunds (partial or full) | – | optionally refunded as store credit | – |

**Shipping:** an order with a shipping address (`checkout.ask_address`) is marked shipped before it is delivered:
Mark shipped (or `/order ship`) asks for the carrier, the tracking number and an optional `https://` tracking link,
and the order only becomes "shipped" together with them. An order without an address (a shop that asks for none)
is completed directly. Tracking details changed after the order shipped are sent to the customer on their own.
The "paid but not shipped" staff reminder (`operations.unfulfilled_order_minutes`) stops once the order ships.

After every change the customer's workflow message and the staff message are updated, the customer is notified
(DM, or a mention in the order channel when DMs are closed), and the change is audited. These follow-up steps are
also scheduled as a durable task in the same database transaction as the status change, so a crash or a Discord
error cannot lose them; each step is idempotent.

**Refunds and store credit after payment:** money refunds (partial or full, optionally paid out as store credit) and
giving back the store credit applied to the order are separate staff actions. An order counts as fully refunded
when money refunds plus returned credit cover the total plus the credit applied. Neither ever exceeds what was paid
or applied, and repeating an action changes nothing. A discount was never paid, so it is never refunded.

**Staff controls carry the status they were shown for:** a button from an older view (the order changed meanwhile)
does nothing except refresh the controls, so a stale "cancel" can never cancel an order that was paid in between.
The submit button carries both the gross amount and the total the customer saw; any difference (price, fee, option,
or credit change) shows the review again instead of placing the order.

**Timers** (all off by default): cart expiry, payment reminder, payment expiry.

**Payments (`gift_card`, shipped):** the order ticket shows the instructions with **Submit gift card** (a private
form: code, optional PIN, the card's amount, prefilled with what is still to pay) and **Buy a gift card**. Each card
is sealed before it is stored; the order view lists the cards received and what is still to pay. When the cards
cover the total the order becomes `payment_submitted`: the customer gets "Order placed" (ticket and DM), the payment
staff get an alert, and the record shows **Reveal gift card**, **Payment confirmed** and **Card problem**. Reveal is
a private reply with the waiting codes and their declared values, which staff check while redeeming (with several
cards: a redeemed / problem button per card). Payment confirmed marks the waiting cards redeemed: the order is paid
when the redeemed cards cover the total, otherwise it waits for the rest. Card problem asks for the reason, which the
customer is shown, and the order waits for payment again (its payment expiry, when on, starts again). An order
staff mark paid while it still waits for payment (paid another way) gives up the cards nobody could reveal yet. Codes are deleted on every decision, when the order ends unpaid,
and after `keep_days`. Value beyond the total, or cards redeemed for an order cancelled afterwards, becomes store
credit.

**Payments (`manual`):** instructions you write; staff confirm receipt; refunds are recorded and returned
outside the bot. No payment details are ever requested in Discord. Each order has a payment record that becomes
`succeeded` when staff confirm payment and `cancelled` when the order is rejected, cancelled or expires before
payment. The `failed` payment state exists for automated providers (a declined card, a failed payment link); the
manual provider never produces it.

## Store credit

Append-only ledger per user. Admins adjust with `/credits adjust` (optional notice to the member); customers check
their balance with the balance button and can apply it at checkout. Order debits and returns are idempotent and
enforced unique per order by the database.

## Reviews

After an order is fulfilled (and after `reviews.prompt_delay_minutes`), the customer receives a prompt (DM, order
channel, or DM with channel fallback) with a rating menu and a decline button. Choosing a rating opens a comment
form when comments are allowed; the review is then posted in `reviews.channel`. One review per order; failed
publications are retried until they succeed. Staff can list, remove, or resend with `/review`.

## Information panels

Each `[[info_panels]]` entry becomes one embed (title, description, fields, image, link buttons) in its channel,
updated in place by `/setup`, and withdrawn if the feature is switched off.
