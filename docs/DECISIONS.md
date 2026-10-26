# Technical decisions

This records how the bot is built and why, the assumptions made, known limitations, and the business decisions
that are intentionally left to the owner.

## 1. Architecture

```
shopbot/
  app.py, __main__.py   process entry: env → logging → instance lock → config → DB/migrations → Discord
  bot.py                discord.Client subclass: command sync, persistent components, listeners, recovery
  context.py            App container (settings, content, db, scheduler, notifier, audit, router, ui, ...)
  env.py                environment variables (secrets never logged)
  logging_setup.py      JSON/text logs with token redaction
  config/               settings schema + parser, content keys/store, readiness, loader, operator text
  db/                   SQLite wrapper, migrations (*.sql), shared repositories
  ui/                   pure message/component specs, Discord limits, rendering, routing, design system
  core/                 scheduler, notifier, audit, permissions, resources, channels, panels, money, rate limits
  features/<name>/      one package per feature (content keys, repo, service, views, handlers, commands, tasks, preview)
tools/                  offline limits checker, HTML preview, checklist, reference docs, invite URL, secret scan
tests/                  pytest suites with discord.py-typed fakes
```

Features: `core` (shared text), `server` (/setup), `admin` (operator tools), `verification` (#welcome),
`how_to_buy`, `faq`, `shipping` (the guides), `announcements` (/announce), `tickets`, `catalog`, `new_arrivals`,
`checkout` (cart, orders, payments, the live order ticket, the #orders staff record), `my_orders` (/myorders),
`problems` (Report a Problem), `operations` (staff alerts, reminders, Owner alerts, Customer role), `reviews`,
`credits`, `info_panels`. Each implements the `FeatureModule` contract (`shopbot/features/base.py`); features talk
to each other through the in-process event bus (`shopbot/core/events.py`) where possible. One page per
specification feature lives in `docs/features/`.

## 2. Customer-facing text

- **Decision:** every visible string is a *content key* declared by the feature that shows it (kind → Discord
  length limit, placeholders, required/optional, documentation). Values live in `config/content.toml` or are set
  with `/content set` (database override). The bot ships with every value empty.
- **Readiness gate:** a feature is shown to customers only when it is enabled, its settings are valid, its
  dependencies are ready, and all its required keys are configured and valid. Otherwise its panels are not
  published, panels already published are withdrawn automatically (at startup, after `/reload`, after
  `/content set` and `/content clear`, and by `/setup`), and its controls answer with the configured
  "unavailable" message. Text
  *changes* to a ready feature's panels are applied by running `/setup` (it edits panels in place).
- **Missing text at runtime:** never replaced by invented wording. Error replies fall back to a silent
  acknowledgement; messages that cannot be rendered are not sent.
- **Exact text:** placeholders are substituted only for the names a key declares; everything else, including
  braces, emoji and whitespace, is kept verbatim. Discord limits are validated; text is never truncated.
- **Operator text:** replies to admin/staff slash commands, staff-only forms, `/setup` reports and the audit channel
  use built-in technical English ("operator text"), shown only after an admin/staff check, ephemerally or in
  admin-only channels. Every entry can be overridden with `operator.<key>` in `content.toml`. This is necessary
  so the bot can tell an administrator what is missing *before* any content exists.
- **Slash commands** keep the names the specification gives (`/setup`, `/announce`, `/orders`, `/order view`,
  `/myorders`, `/product ...`). Staff/admin command descriptions and option texts are technical English (Discord
  requires them at registration, before any content can be read). The customer commands, `/myorders` and `/review`,
  take their descriptions from content (`my_orders.command.description`, `reviews.command.*`) and are not registered
  until they are set.
- **Staff-facing business messages** that are part of operations (staff alerts, the staff order control message)
  are content keys, because they are notifications the owner should word.
- **Test fixtures** (`[t17]`-style tokens) are generated at test time and never exist in configuration files.
  The preview marks unconfigured text as `⟦key⟧` and uses bracketed synthetic data (`[sample product 1]`).

## 3. Interactive components

- **One routed `DynamicItem`** (`ui/routing.py`) wraps every button and select. Custom IDs follow
  `sb:<route>:<action>[:<ids>]`. discord.py dispatches by regex on the custom ID, so components keep working after
  restarts without per-message state in memory. Sending only `DynamicItem`s avoids discord.py dispatching the same
  click to a stored view as well (verified in `discord/ui/view.py`).
- **Forms (modals)** are routed from `on_interaction` by custom ID; the in-memory `Modal` is stopped right after it
  is sent, so a restart between opening and submitting a form behaves like the normal path.
- **Router protections** (before any handler): configured guild only (DMs only for routes that allow them), per-user
  rate limit, anti-double-click spacing, in-flight duplicate guard (a menu registered with `per_value`, such as the
  #catalog categories, counts each picked option as its own click), feature readiness, access level. Handlers then
  reload records by ID and re-check ownership/state; custom IDs are never trusted beyond the ID.
- `tools.check_limits --all-features --fixtures` builds every preview state your settings allow (customer and
  staff, using in-memory synthetic tokens for text that is not configured yet) and fails if any component has no
  registered handler or any state breaks a Discord limit. States that exist only with optional settings
  (fulfillment options, the order details form, ticket category forms) are built with full example settings by
  the pytest preview tests, which apply the same limit and handler checks. CI runs both on every push, so a button
  that "does nothing" cannot ship unnoticed.

## 4. Data

- **SQLite** with one connection owned by one worker thread. Each unit of work is a synchronous function executed
  atomically (`BEGIN IMMEDIATE` … `COMMIT`); because it cannot `await`, nothing interleaves with it, which makes
  check-then-write logic race-free inside a transaction. WAL journal, `synchronous=FULL`, foreign keys on.
- **Money** is stored and computed in integer minor units (`*_minor`). Percent fees use basis points rounded half up.
  No floating point is used for money anywhere.
- **Invariants in the schema:** CHECK constraints (non-negative amounts, reserved ≤ stock, order total identity),
  UNIQUE constraints (one order per checkout session, one review per order, one credit debit/return per order,
  ticket/order numbers per guild), partial unique index (one active session per order channel).
- **Migrations** are forward-only SQL files applied in transactions; a backup copy is written before migrating a
  database with data; a database newer than the code is refused (no silent downgrade).
- **Timestamps** are UNIX seconds; time comes from an injectable clock so expiry logic is testable.

## 5. Scheduling and recovery

- Persistent scheduler table with dedupe keys, conditional claiming, exponential backoff, permanent failure
  (audited), and recovery of tasks interrupted by a restart. Handlers are idempotent and re-check state; they can
  re-arm themselves (`RescheduleTask`).
- Startup recovery runs once per process after the gateway is READY **and** the guild is available (an unavailable
  guild would otherwise look like every channel had been deleted).
- One process per database: Railway does not overlap deployments with a volume, and the bot also holds an
  exclusive file lock next to the database.
- Shutdown drains: on `SIGTERM` new clicks are refused with the "unavailable" text and running ones get up to
  10 s to finish. A hard kill is still safe (atomic transactions + startup recovery). Slash commands are not
  drained (they are short admin actions and can be repeated).

## 6. Tickets and orders share one channel model

Support tickets and checkout order channels are both rows in `tickets` (`kind = support | order`) created through
the same service (private overwrites, numbering, closing, transcripts, deletion, restart recovery). Checkout adds
its own UI and order records on top.

## 7. Payments

- **Decision:** a provider interface with two providers. `gift_card` (shipped, the owner's choice): customers pay
  the exact total with DoorDash eGift cards entered in a private form; codes are stored encrypted, revealed only to
  the Payments role (Owner by default), deleted on every decision or after 7 days; several cards may pay one order
  and value beyond the total becomes store credit. `manual`: the customer sees the payment instructions the owner
  configures and staff confirm payment with a button. Refunds are recorded (store credit, or returned outside the
  bot). No bank or credit card details are ever requested in Discord.
- **Why a gift card is never redeemed early:** staff can reveal codes only once the cards cover the total
  (`payment_submitted`), so a card is never redeemed for an order that could still end unpaid; if one of several
  cards is refused, the redeemed ones keep counting, and if the order is then cancelled their value becomes store
  credit (the customer never loses money).
- **Not decided by the bot:** which payment method to use. `payments.provider` starts unset and checkout stays
  disabled until the owner chooses. An automated provider (hosted checkout/payment link + verified webhook) can be
  added behind the interface; it requires an HTTP endpoint for webhooks, which a worker deployment does not have,
  so it was deliberately not implemented speculatively.

## 8. Inventory

Stock is reserved when an order is submitted (all-or-nothing, single conditional UPDATE per product, so concurrent
orders cannot oversell), committed when payment is confirmed, and released when an unpaid order is rejected,
cancelled or expires. Cancelling a *paid* order does not restock automatically; staff decide (refund, restock).

## 9. Deployment

Railway worker started with `python -m shopbot`, persistent volume at `/data`, `DB_PATH=/data/bot.db`, one replica.
On Railway the bot refuses to start when `DB_PATH` is not on the attached volume (`RAILWAY_VOLUME_MOUNT_PATH`),
because running on the container's temporary disk would lose every order at the next redeploy
(`ALLOW_EPHEMERAL_DB=true` overrides this for throwaway test services).
`railway.json` is provided as requested; Railway documents config-as-code as deprecated (existing services until
2026-12-01, not read by new services), so the equivalent dashboard settings are documented in `DEPLOYMENT.md`.

## 10. Technical defaults chosen (all configurable)

| Setting | Default | Why |
|---|---|---|
| Rate limit | 12 interactions / 10 s per user | Anti-spam without affecting normal use |
| Anti-double-click | 700 ms per control | Drops accidental double clicks |
| Ticket creation cooldown | 30 s | Anti-spam |
| Tickets created per user per day | 10 | Prevents one user filling the ticket category (Discord: 50 channels per category) |
| Open tickets per user per category | 1 | Duplicate-ticket prevention |
| Open orders per customer | 1 | Duplicate-order prevention |
| Max cart lines | 20 | Keeps every order summary inside Discord embed limits |
| Scheduler retries | 5, backoff from 30 s | Survives transient Discord/network errors |
| Message Content / Server Members intents | on / on | Transcripts and the gift card chat guard need message text; the welcome mention needs member joins. A missing portal switch is left out (the bot still starts) |
| Audit retention | forever | Owner decides when to purge |
| Finished scheduled tasks kept | 30 days | Housekeeping (technical) |
| `/backup` copies kept | 10 | Disk usage on the volume |

### 10a. Behaviour defaults shipped in `settings.toml` — please review

The shipped configuration follows the owner's server specification (layout, roles and channel names come from it).
The values below are choices made where the specification leaves room. **Review and change any that do not match
how you want to operate** (each is one line in `config/settings.toml`).

| Setting | Shipped value | Effect |
|---|---|---|
| server layout | 4 categories, 13 channels, 5 roles | Exactly the specification's structure; names in content.toml |
| START HERE `public_readonly`; CATALOG/PURCHASE `members_readonly`; STAFF `staff` | – | Unverified users see only START HERE |
| `permissions.admin_roles` / `owner_role` | `["owner"]` / `owner` | The Owner role is admin; `/setup` gives it to `OWNER_ID` |
| Staff role `mentionable` / Restock Alerts `mentionable` | true / true | Alerts and #new-arrivals updates can notify those roles |
| Staff role `permissions` | `["manage_messages"]` | Staff see the staff slash commands (`/orders`, `/order`, `/stats`, `/ticket`, `/reviews`, `/announce`) in Discord's picker; it also lets them delete/pin messages. `/setup` adds it and keeps the role's other permissions (`permissions_mode = "add"`). Fine-tune per command under Server Settings → Integrations; the bot's staff checks stay authoritative |
| `verification.rules` | 6 | Six general community rules on #welcome (text in content.toml) |
| `verification.dm_confirmation` | false | Confirmation only privately in Discord, no DM |
| `verification.welcome_mention_seconds` | 30 | A new member is mentioned in #welcome and the mention is deleted 30 s later |
| `how_to_buy.steps` / `link_channels` | 4 / catalog, purchase, faq | Guide size and its link buttons |
| `faq.sections` | ordering, payment, shipping, tracking, problems (5/6/6/2/2 questions; ordering answers the two bulk-order questions); all in one card | Adjust counts |
| `shipping.groups` / `excluded_areas` / `not_available` | North America, Central America, the Caribbean, South America and Europe (44 countries); in the US every state except Alaska and North Dakota; Cuba, Russia and Belarus listed as not available (sanctions; most carriers refuse parcels) | The owner's delivery area |
| `shipping.info_sections` | 2 | Processing; tracking |
| `checkout.fulfillment_options` | Standard $5.00, Expedited $15.00 | The owner's delivery prices |
| `catalog.products_per_listing_message` | 1 | One #catalog message per item |
| `catalog.browse_enabled` | false | No extra private browsing button (the spec only asks for per-item Buy buttons) |
| `catalog.low_stock_threshold` | 3 | 1-3 units left shows "low stock" |
| `catalog.product_cards` / `products_per_category_page` | true / 5 | Public item cards stay under the category menu; 5 items per private category page |
| `new_arrivals.announce_new_products` / `announce_restocks` / `mention_role` | true / true / true | Automatic updates ping Restock Alerts |
| `tickets.panel_channel` | "" | No standalone ticket panel: support starts with Report a Problem in #purchase |
| `tickets.channel_name_pattern` | `{prefix}-{number}-{user}` | Ticket names contain a unique number and the username |
| `tickets.staff_alert_channel` / `transcript_channel` | staff_chat / transcripts | |
| `checkout.panel_channel` / `order_category` / `staff_channel` | purchase / purchase / orders | |
| `checkout.transcript_on_close` | true | Order tickets also get a transcript when they close |
| `checkout.customer_role` / `customer_role_on` / `customer_role_remove_on_refund` | customer / fulfilled / true | |
| `problems.eligible_statuses` | paid, processing, shipped, fulfilled (delivered) | Orders a report may refer to |
| `features.credits` | on | Gift card overpayments and refunds after payment are store credit |
| `problems.allow_without_order` / `require_reason_on_reject` | true / true | |
| problem types | not received, damaged, wrong item, something else (one paragraph field each) | Add types and fields as needed |
| `operations.ping_roles` / `owner_dm_alerts` | staff / true | Who is notified about orders and failures |
| `operations.answers_in_orders_channel` | false | The customer's typed form answers are not posted in #orders; staff open them privately (Details button, `/order view`) |
| `operations.*_minutes` reminders | unclaimed 60, unpaid 1440, paid but not shipped 2880, unanswered ticket 240 | #staff-chat reminders (once each) |
| `checkout.currency` / `payments.provider` | `USD` / `gift_card` | DoorDash eGift cards for the exact total, submitted in a private form; the Payments role (Owner) reveals, redeems and confirms |
| `checkout.submission_form` | full name, shipping address, optional delivery notes | Asked at Submit; shown to staff only |
| `checkout.session_expiry_minutes` / warning / `payment_reminder_minutes` | 1440 / 60 / 1440 | Unsubmitted carts expire after 24 h (warning 1 h before); unpaid orders get one reminder after 24 h; unpaid orders never expire automatically |
| `reviews.rating_scale` / `prompt_delay_minutes` | 5 / 10080 | 1-5 stars, asked 7 days after the order is completed |
| `announcements.mention_roles` | restock_alerts | Staff may choose to ping Restock Alerts with an announcement |
| `branding.progress_filled` / `progress_empty` | 🟧 / ⬜ | Step indicator in the order ticket |
| `[banners]` | enabled, all 31 pictures in `config/banners/` | One banner picture at the bottom of every kind of message's card (§11a, §13) |
| `announcements.allow_image` | true | |
| `my_orders.orders_per_page` | 5 | |
| `features.info_panels` | false | Not part of the specification |
| `audit.channel` | bot_log | Staff-only audit channel |
| `checkout.decimal_separator` / `thousands_separator` | `.` / `,` | How amounts are written |

## 11. Business decisions and text

The specification asked that all wording come from the owner. The owner later asked us to write everything and set
everything up so `/setup` posts every panel ("just make it up and then I'll fix it"). So the shipped
`config/content.toml` holds a complete text pack (every key, in a short professional voice) and `settings.toml` holds
the decisions in §10a. The text describes only what the bot does; it invents no prices, shipping costs, delivery
times, refund windows, guarantees or payment methods: where customers need those, it says staff confirm them in the
order ticket. Everything stays configurable: the owner changes any text from Discord with **Edit text**
(right-click a bot message → Apps) or `/content set`, or in the files. Still the owner's: payment instructions beyond
"staff post them in your ticket", fulfillment options and fees (none are configured), the minimum order, auto-close
and deletion timers, and of course the products themselves (`/product add`).

## 11a. Banner pictures and the "Edit text" tool

- **Banners.** The owner supplied 31 banner pictures (1500×500), one per kind of message. Each message names its
  banner id in its builder (`ui.banner("faq")`, ids in `shopbot/config/banner_ids.py`); the picture is uploaded with
  the message as its first attachment. The owner asked for the picture inside the card, at the bottom, so when a
  spec becomes a Discord message (`ui.banners.place_banner`, used by sending, editing, the limits check before
  sending and the offline preview) the banner becomes the image of the message's last embed (`attachment://<name>`).
  Builders never refer to it themselves, so one rule places it everywhere. The last embed is the only place: an embed
  shows one image, at its bottom, and the last one closes the message. When that embed already shows a picture (a
  product image, an announcement image, the welcome image, the `branding.panel_banner` image) the banner stays an
  attachment no embed uses, which Discord shows above the embeds as before; an image-only extra embed was not used
  because it would take one of the ten embed slots. Edits keep an unchanged banner without uploading it again (the
  attachment name contains the picture's hash) and still refer to it: while the card shows the banner, an edit always
  sends the message's attachments, kept banner included, because Discord resolves an embed's `attachment://` image
  against the attachments sent with the message or edit (Discord's documented rule; an edit that refers to a kept
  attachment could not be tried against a live server from here). A banner that changed is replaced, one that is no
  longer wanted removed; a `PartialMessage` is fetched once before such an edit so this is decided from its real
  attachments. Multi-message panels show the banner on one message only: the guides (#how-to-order, #faq,
  #shipping) on their last message, so the picture closes the panel at its bottom; other multi-message replies and
  records on their first. Product cards (private catalog pages, #new-arrivals updates) keep it above the card, so it
  is never taken for the product's photo (`MessageSpec.banner_in_card`). The panel hash is taken from the placed
  spec, so the first /setup after this change re-edits each banner panel once; live messages (the order ticket panel, the #orders record, report and review-approval messages) change
  banner with their state. The banner counts toward the per-message file limits and the panel hash; transcripts
  leave the bot's banners out. Pictures live in `config/banners/` (≤ 2 MB each) and are read at startup and on `/reload`; a missing or
  invalid picture only means that kind of message shows none (listed in the logs and by the checker).
- **Edit text.** A message command (right-click a bot message → Apps → Edit text, administrators only) finds the
  texts the message shows by comparing every text of the message with every content value (placeholders match any
  data), lists them in display order, and opens the `/content set` form for the one picked. After a save, the panels
  that are already posted are refreshed at once (`/setup` is still what posts new panels). Private (ephemeral) replies
  cannot be right-clicked in Discord, so `/content set` suggestions also match a few words of the current text.

## 11b. /reset

- **Owner only, typed confirmation, backup and setup channel first.** `/reset` deletes channels, categories and
  roles, so it is limited to `OWNER_ID` and the server owner, previews privately, and needs `RESET <server name>`
  (case and spacing ignored; shown in a code span exactly as typed). The preview's fingerprint travels in the form's
  id: if the server changed in between, nothing happens. The result goes by DM because the command's channel is
  deleted too; if the DM fails, nothing happens. The backup is written before any deletion and named
  `before-reset-…` so the `/backup` rotation never removes it. The private 🛠・setup channel is created next (a server
  without channels has nowhere to type `/setup`); if Discord refuses it, nothing is deleted. It only grants
  permissions the bot holds itself.
- **Never deleted.** Besides what Discord protects (managed and booster roles, roles above the bot, Community
  channels): every role the bot has (deleting one could take its Manage Roles/Channels away half-way), the
  Administrator roles of an owner who is not the server owner (they need them for `/setup`), objects set by `id` in
  settings (`/setup` cannot re-create those), and the channels holding history: transcripts, the bot log and
  published reviews live only as Discord messages, so their channels are kept and `/setup` moves them into the new
  layout (they keep their records).
- **Data kept by default.** Orders, products, discount codes, reviews and store credit stay. Ticket and order channels
  are deleted like any other channel, through the same path as a channel deleted by hand (tickets recorded as deleted,
  unfinished checkouts expired). `/setup` afterwards posts the staff cards of open orders whose channel is gone, with
  their item and answer messages (dedupe keys per new channel), and, when the reset deleted the Customer role, gives the
  new one back to every member with a qualifying order (the normal role task, which fetches the member itself, so it
  works without the member cache). Unpaid orders are counted in the preview: their customers pay in the deleted order
  channel. Members verify again on the new welcome panel.
- **Interruptions.** Each deletion catches any error (reported, the run goes on). Forgetting the records, the audit
  entry (`completed: false` when cut off) run in a `finally`; a `running` marker in `kv_state` is cleared only once
  the owner was told the result, so a restart in the middle is reported by DM at the next start. The router refuses
  every button and form while the reset deletes.
- **Wipe is explicit and complete.** `wipe_shop_data:True` empties the tables in `reset_store.WIPED_TABLES` in one
  transaction (foreign keys checked at commit) after every deletion was attempted, never when the run was cut off.
  Counters and AUTOINCREMENT sequences are kept, so no order number or id is ever reused (old DMs and buttons can
  never point to a new order). Pending tasks of wiped features are cancelled; system tasks (`KEPT_TASK_KINDS`) stay.
  Tests fail when a table or a kept task kind is not classified.
- **Pace.** One deletion per second (`reset.PACE_SECONDS`): discord.py already waits on rate limits, and Discord's
  anti-abuse systems watch for bots deleting many channels at once.

## 11c. Icons and the house style

- **Icons only where Discord shows them.** Descriptions, section headings, field names and values, and the emoji of
  buttons and menu options (a label may start with one `{icon_x} `, which becomes the component's emoji). Embed
  titles, author lines and footers print custom emoji as raw text, so `discord_render.to_embed` leaves them out there
  (a safety net; the HTML preview shows the same) and the shipped card titles are plain. A custom emoji anywhere else
  in a label (e.g. from a placeholder value) is left out too.
- **Checked, not just written down.** `docs/QUALITY_STANDARDS.md` is the house style; `tools/check_quality.py`
  checks its [checked] rules on every preview state with the shipped texts (icons Discord cannot show, leftover
  placeholders and internal words, spacing, title and button length, field grids, walls of text, one icon per button
  label). Customer and operator messages are told apart by the branded footer; stress-test states with synthetic text
  only get the icon rules. Accepted exceptions are listed in the tool with a reason each.

## 11d. Payment methods (cards temporarily down)

- **What the owner asked for, said honestly.** The shop's regular payment options (credit and debit cards, Apple Pay,
  Google Pay, PayPal, Cash App) are listed as *temporarily down*, with the owner's reason ("our card option is
  currently down; regular payment options will resume as soon as they are up"), and DoorDash eGift cards as the one
  method that works now. No text says a down method works today: the order card lists each method with its state,
  pressing a down method opens a private "Dear customer, we're sorry …" pop-up with buttons for the working methods,
  and #faq and #how-to-order say the same. Keep only the methods the shop really offers (`[[payments.methods]]`).
- **Ready to switch back on.** A method returns with a settings change: `status = "active"` and `kind = "staff"` (the
  customer's choice is posted once in the ticket and alerted in #staff-chat; staff send the details and press Mark
  paid) or `kind = "link"` (a link button to the shop's own payment page). The bot never takes card details in
  Discord: real card processing means a payment provider with a hosted page and a verified webhook (payments
  package), whose page is then the active method.
- **A part payment asks for the rest.** Once a gift card counts toward an order, the instructions show what is still
  to pay instead of the whole total.

## 11e. Discount codes, buy one get one free and coupons

- **What.** Catalog managers create discount codes with `/discount create`: percent off (1-100 %), an amount off (in
  the shop currency) or buy one, get one free; optionally limited to some products, with a minimum items subtotal, a
  total number of uses, an end date (1-365 days) and once per customer (the default). Customers type a code on the
  review step (**Add discount code**, shown while the shop has a code that is not ended or expired), or press **Use
  coupon CODE** for a coupon they claimed with a claim button (for example under an announcement). `/discount list`
  shows the codes with their terms, uses and claims; `/discount end` ends one. `[checkout] discount_codes = false`
  switches it all off.
- **Why it is built this way.** A discount changes the money of an order, so it follows the same rules as the rest
  of checkout: one pure function computes it (`checkout/discounts.py`), the totals function takes it, and the
  submission checks it again inside the transaction that creates the order. The submit button already carries the
  totals the customer saw, so a code that stops applying between the review and Submit (it expired, was ended or
  used up) gives the existing "the total changed" review instead of an order at a price nobody saw.
- **The rules.**
  - The items a code counts: every line, or only the lines of its products. Delivery, extras, fees and store credit
    never count.
  - Percent: the percentage of those items, rounded half up. Amount: the amount, at most those items.
  - Buy one, get one free: every eligible unit is listed by unit price, highest first, and every second unit is free,
    so the cheaper unit of each pair is free (units of $10, $8 and $5 save $8; a fourth unit of $3 saves $3 more). The
    cart needs at least two eligible units.
  - The minimum is the items subtotal of the whole cart.
  - Percent fees are charged on the items after the discount; flat fees and delivery are unchanged; store credit
    covers what is left. The order stores the discount (`discount_minor`), the code's id, and the code and its kind
    as they were (a snapshot), and its total is subtotal + delivery + fees - discount - credit (a database CHECK).
  - One code per order (applying another replaces it).
  - Only items with a price count, and a code that would take nothing off (a free item in a buy one, get one free
    pair, 1% of a few cents) does not apply: an order never stores, or counts a use of, a code that saved nothing (a
    once-per-customer code would otherwise be used up for nothing).
  - Each reason a code does not apply has its own customer text (unknown, ended, expired, used up, already used,
    minimum, no eligible items, needs two items). A code that stops applying while the order is put together (the
    cart changed, the code ended) shows "not applied" in the discount's place and the reason under the total; the
    total is then without it.
- **How uses are counted.** From the orders that used the code, never with a counter that could drift: an order
  counts unless it ended without ever being paid (cancelled, expired or rejected while unpaid). An order that was
  paid and refunded later still counts. `max_uses` and once per customer are checked again when the order is
  created; SQLite's write lock serializes two customers submitting at the same moment, so only one gets the last use.
- **Refunds.** A discount was never paid, so it is never refunded: what can be refunded is the total (what the
  customer paid), a full refund still means the total plus the store credit applied, and the gift card amount to pay
  is the total.
- **Guessing codes.** At most 5 typed codes that could not be used per customer in 10 minutes; then the form answers
  "please wait a few minutes" (kept in memory: a restart forgets it). A code that does not exist is repeated in the
  reply with only the characters codes can have.
- **Coupons.** A claim button (`sb:dc:claim:<code id>`) records the member's claim once and answers privately with
  the terms and how to use it; the review step offers the two newest claimed coupons that can still be used. When the
  offer has ended (the code ended, expired or was used up, or codes are off) it says so. A code is never deleted:
  ending it keeps it on the orders that used it, and the same name can be created again. `/discount end` publishes
  `DISCOUNT_CODE_ENDED`, so a post that offered the code can be marked as ended.

## 11f. Announcement presets (the warehouse sale)

- **What.** `/announce preset:<id>` posts a ready-made announcement (`[[announcements.presets]]`, texts
  `announcements.preset.<id>.*`) after a private preview, with the claim button of its coupon. The shipped
  `warehouse_sale` preset is the owner's announcement of the new warehouse and the buy one, get one free sale on the
  old stock (coupon `BOGO`), written from the owner's words: every point kept (thanks for the support, the brand-new
  warehouse it made possible, clearing the old stock, buy one, get one free for a limited time until the old stock is
  gone, claim the coupon below and redeem it at checkout, the 20% off coupon in every package for the next order
  (old stock only, can be given to a friend), thanks again), the grammar fixed, steps with icons. The package
  coupon is printed, not claimed: staff create it with `/discount` (see `docs/features/announcements.md`).
- **A preview with a Post button instead of a form.** The text is fixed, so there is nothing to type; what can go
  wrong is the coupon (not created yet, ended, used up, codes off). The preview shows the post exactly and names the
  fix (the exact `/discount create` command, `coupon_command`) before anything is posted, and Post checks it all again.
- **Posted once.** The preview reuses the announcements table and its claim-once update (draft → posting); the
  preview's row keeps the code id it showed (Post refuses a coupon that was ended and created again since, because
  staff never saw its terms), and a unique index allows one posting or posted announcement per preset and code, so
  two staff members (or a double press, also across a restart) cannot post the same offer twice. A post deleted in
  Discord stops counting at the next preview, so the offer can be posted again.
- **The button follows the offer.** When the code ends (`/discount end`) or a press finds it expired, a durable task
  edits only the button (the disabled ended label); a startup sweep catches codes that ended or expired while nobody
  watched. Used up does not switch it (a use comes back when an unpaid order is cancelled). The claim button itself
  belongs to checkout (`sb:dc:claim:<id>`, part of 11e), so claiming works the same under any post.

## 11g. Tips for our team at checkout

- **What the owner asked for.** "Make it so you can tip our support team and that 100% of all tips go to our team."
  The review step has a tip menu (`[checkout.tips]`: No tip, $2.00, $5.00, $10.00, Other amount from $1.00 to
  $100.00 in a private form) and, as the card's last line, that tips are optional and "100% of every tip goes to our
  team." The menu comes after the buttons that take money off (store credit, coupons) and says "(optional)", so the
  step never pushes a tip.
- **Paid with the order.** Payments are DoorDash eGift cards checked by staff per order, so a tip is added to the order
  and paid with it: the gift card amount to pay is the total, tip included. A tip on its own, without an order, is not
  offered yet (`docs/NEXT_STEPS.md`).
- **The rules.** The tip is added after the extras and fees (`checkout/tips.py`, `totals.compute_totals`): a discount
  code never applies to it, percent fees are not charged on it, and store credit may cover it like the rest. It is
  checked again inside the transaction that creates the order: a tip that is no longer offered (tips switched off, the
  amount no longer listed or above the maximum) counts as none, and the Confirm order button, which carries the totals
  the customer saw, gives the existing "the total changed" review instead of an order at a price nobody saw.
- **Stored inside the fees.** Migration 0014 adds `tip_minor` to unfinished orders and orders. An order counts its tip
  inside `fees_minor` (`tip_minor` is its part of them, a CHECK keeps it so), so the total CHECK of 0013 holds without
  rebuilding the orders table. Every display lists the tip on its own line ("Tip for our team"), never as a fee or an
  extra: the review step, the order card, the live record and the full record.
- **Refunds and /stats.** The tip was paid, so it is refunded like the rest of the order. Tips go 100% to the team, so
  `/stats` shows them on their own line ("Tips for the team") and leaves them out of the revenue: revenue = paid
  totals - money refunds - kept tips. A paid order keeps its tip while it still holds at least that much (paid with
  money and store credit, minus every refund and the credit given back), else only what it holds: a refund counts
  against the items first, never takes an order's revenue below zero, and a full refund keeps no tip. A refund of the
  tip alone cannot be told apart from an item's (marking a refund as the tip is not offered yet). A tip paid with
  store credit is still paid to the team, so it is taken off the revenue too (a period's revenue can then be below
  zero).

## 11h. /review for every member, anonymous reviews with a photo, and the #reviews intro

- **What the owner asked for.** "Submit a review by doing /review ... it doesn't show their name but they can put in an
  image ... their description and the amount of stars", and at the top of #reviews: since the server was redone the
  older reviews are gone, they should come back in time, and a welcome to new customers.
- **Two commands.** `/review` (stars, an optional photo, then a private form for the description) is now the members'
  command; the staff tools moved to `/reviews` (same subcommands and checks). `remove`, `approve` and `reject` also
  take a review number, because a review written without an order has no order number.
- **Which review.** The member's newest delivered order without a finished review is reviewed (a verified review,
  one per order, as before). Without one, an unverified review (no "Verified purchase" marker) lets returning customers
  review again after the rebuild (`reviews.allow_unverified`, shipped on): it always waits for staff approval, even with
  `require_approval` off, and a member writes at most one every `unverified_cooldown_days` (shipped 30; every one
  counts, also a rejected one, so a refused review is not simply sent again).
- **A photo is checked first.** A review sent with a photo waits for staff approval too, also a verified one with
  `require_approval` off (`reviews.photo_requires_approval`, shipped on): any picture would otherwise appear in a
  public channel unchecked. The option is called `photo` in Discord, as every customer text calls it, and only PNG,
  JPEG, GIF and WebP are accepted before the form opens (the bytes are checked again when the review is sent).
- **Anonymous.** Published reviews show the stars, the text, the photo and, for a verified review, "Verified purchase
  / Order #N" (an order number identifies nobody to other members). `{user_name}` is no longer offered to published
  texts: the title and the verified text, which have placeholders, refuse it in the content check; the other public
  texts have none, so a `{user_name}` typed there would be shown as written, never filled in. The decision notices no
  longer offer `{order_number}` (a review may have none). Staff still see who wrote a review on the staff-only
  approval message (its order field reads "#12", so it is not mixed up with the review number in its title).
- **The photo is stored.** Approval can take days and Discord's attachment links expire, so the picture is downloaded
  when the review is sent and kept in the database (`review_images`, like product images: one file to back up, written
  in the review's transaction, wiped by `/reset` with the reviews), then attached to the approval message and the
  published review. The bytes are released once the review is published, rejected or removed (the published message
  carries the photo from then on), and only after the decision's durable follow-up has run, so an approval message
  updated after a restart still shows it; SQLite overwrites them in the file (`secure_delete`), backups taken earlier
  keep them. `/reviews resend` only re-sends review requests, so it never needs them. /setup edits pending approval
  messages and keeps the photo they show instead of uploading it again.
- **The intro panel** is a normal /setup panel (`reviews:intro`, edited in place, never duplicated, not pinned: the
  panel system does not pin). Its last line is chosen from the settings so "our team checks reviews" is always true.
  A panel withdrawn while reviews are not ready (e.g. a missing text) is posted again by the next /setup at the
  bottom of #reviews, below the reviews published meanwhile.
- **A Write a review button as well.** Every channel a member sees is read-only for them (#reviews too), a delivered
  order's ticket is locked, and Discord disables the message box, and with it slash commands, where a member cannot
  send messages. So a returning customer without an order, or one whose order was delivered, would have nowhere to
  type `/review`. The intro panel therefore has a **Write a review** button: a private stars menu, then the same form
  and the same rules as `/review`. Its photo is uploaded in the form itself (Discord's file upload field for forms,
  `ui.spec.FileUploadSpec`); Discord lets members pick any file there and the bot is told only when the form is sent,
  so the photo is checked then, and a refused one answers with the member's text so nothing typed is lost. `/review`
  stays for members who can type (an open ticket); its form asks for the photo only when the option was left out.
  Reviews written either way are `source = 'command'` (one step, the same writer), so migration 0015 is unchanged.

## 11i. The shipping areas announcement

- **What the owner asked for.** "An announcement that we are sorry that we aren't able to ship to Alaska [and]
  South Dakota at the moment, just make up a reason." It is the `shipping_areas` preset (`/announce
  preset:shipping_areas`, no coupon, the shipping banner): an apology, a reason, everywhere else is not affected (with
  a #shipping link), we will announce it when we ship there again, thanks.
- **Which Dakota.** The shipped `[shipping.excluded_areas]` is Alaska and North Dakota (the owner's earlier choice),
  while the message says South Dakota; the owner is being asked. The text therefore never names the areas itself:
  `{excluded_areas}` reads them from the settings at every preview and Post, with the names and joining text #shipping
  uses, so the announcement, #shipping and the address step can never disagree. While nothing is excluded the
  preview says so and has no Post button.
- **The reason.** Made up, as asked, and kept plain: our shipping partners can't currently deliver there within the
  delivery times and prices we promise, so rather than send slow or costly parcels we paused it. It blames no one by
  name, does not alarm, and promises only what the bot does (an announcement once we ship there again), not a date.

## 11j. Catalog categories (a menu on the #catalog header, results in a private message)

- **What the owner asked for.** Members choose a category the shop sets up and see its items in a message only they
  can see; an item can be in several categories; "All" shows everything. Catalog managers make the categories
  (`/category create|edit|delete|list`) and put items in them (`/product categories`); see
  `docs/features/catalog.md`.
- **The menu is part of the header card**, not a panel of its own. The header is always the first #catalog message,
  so the menu stays at the top even when product cards already exist (a new message would land under them), and
  #catalog keeps one card with one banner at the top ("keep everything in one embed"). `/setup` and every category
  change refresh it with the listing.
- **The private message is the one place with several cards**: a title card over the category's items as the public
  cards show them, 5 per page (`catalog.products_per_category_page`), within Discord's limits per page. The owner
  allowed this exception (the catalog shows a private embed); the house-style checker lists it. Each Buy button names
  its item, since the cards share one message. The page is read when it is shown, so it never needs re-posting.
- **"All" is built in** (always the first option, every listed item) and not stored. At most 24 categories, so the
  menu fits Discord's 25 options. Names are unique ignoring case. A category's emoji is one of the bot's icons, one
  unicode emoji or one country flag; symbols, skin tones and joined emoji are refused, since whether such a
  combination exists cannot be told from its parts. Discord refuses the whole header edit for an emoji it does not
  know, so a refused header is sent once more without the menu's unicode emoji (the listing keeps updating; the
  refresh report tells staff to change the emoji).
- **The menu appears with the first category.** Before it, its only choice would be "All", the same items as the
  public cards; with `catalog.product_cards = false` it always shows. Picks are private and safe to repeat, so the
  router's anti-double-click guard counts each picked category as a click of its own (`per_value`).
- **The public cards stay** (`catalog.product_cards = true`); `false` leaves only the header with its menu. Discord
  opens a channel at its newest message, so with many cards members land at the bottom while the menu is on the
  first message: the owner chooses between pinning the header and `product_cards = false` (`docs/NEXT_STEPS.md`).

## 12. Assumptions

- One Discord server per bot instance (`GUILD_ID`).
- English technical operator text is acceptable for administrators (every entry can be overridden with
  `operator.<key>` in content.toml).
- Staff confirm manual payments themselves; the bot does not verify bank transfers or other external payments.
- Product images are either `https://` links or uploads stored in the database and attached to every message.
- Slash-command names are the ones in the specification; the customer commands (`/myorders`, `/review`) take their
  descriptions from content and are registered at startup only when they are set.

## 13. Known limitations

- Banner placement (inside the last card, or above the embeds when that card has its own picture) follows Discord's
  documented `attachment://` embed images and message layout; it could not be seen in a real client from here, nor
  could an edit that keeps the uploaded banner and refers to it again be tried against Discord. Panels posted before
  this placement keep their banner above until their next change (or `/setup`) edits them. Each banner upload adds 0.5-1.1 MB to the message (mainly #bot-log entries,
  one per audited action: switch them off with `[banners] off = ["activity-log"]` if that matters).
- Not yet verified against a live Discord server or Railway from here. All behaviour is verified with automated
  tests using faithful fakes and the offline preview — see `docs/MORNING.md` for the live verification checklist.
- Railway config-as-code deprecation (see §9).
- Transcripts include message text only with the Message Content intent enabled; very large channels may exceed
  Discord's upload size (the transcript is then retried and reported; deletion waits for it). Attachment links in
  transcripts are Discord CDN links, which Discord expires after some time.
- `/content set` accepts up to 4000 characters (Discord form limit); longer descriptions must be set in content.toml.
- `/reload` does not re-sync slash-command descriptions (`/myorders`, `/review`); restart the bot after changing them.
- Panels change appearance only when `/setup` republishes them (e.g. the #purchase Report a Problem button and the
  #catalog Buy buttons appear after the features become ready and `/setup` runs). A button that was posted before
  its feature stopped being ready answers with the configured "unavailable" text.
- Discord limits a category to 50 channels: ticket and order channels share the PURCHASE category, so enable
  automatic deletion of closed tickets on a busy server; a full category refuses new tickets with the generic error
  (logged as `tickets_category_full`, audited, alerted in #staff-chat).
- The #welcome panel must fit one Discord message (6000 characters); a longer rules text is reported by /setup and
  the previous panel stays.
- Restocks caused by released reservations (a cancelled or expired order) are not announced in #new-arrivals.
- A problem report whose ticket was deleted before a decision stays open in the database (it no longer counts
  toward the limit); there is no command to list reports.
- Staff reminders run on a one-minute sweep (at most 10 per run).
- A customer notification that cannot be delivered by DM *or* the fallback channel is recorded, audited and alerted
  in #staff-chat, but not retried.
- Retention: audit events (configurable) and finished tasks (30 days) are purged automatically. Order/ticket
  records, problem reports, uploaded product images, notification records and pre-migration backups are kept
  until deleted manually.
- A single bot instance per database (by design). No automated payment provider (see §7).
