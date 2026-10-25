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
  requires them at registration, before any content can be read). The only customer command, `/myorders`, takes its
  description from content (`my_orders.command.description`) and is not registered until it is set.
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
  rate limit, anti-double-click spacing, in-flight duplicate guard, feature readiness, access level. Handlers then
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

- **Decision:** a provider interface with one implemented provider, `manual`: the customer sees the payment
  instructions the owner configures; staff confirm payment with a button; refunds are recorded and returned outside
  the bot. No payment credentials are ever requested in Discord.
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
| Message Content intent | on | Transcripts need message text |
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
| Staff role `permissions` | `["manage_messages"]` | Staff see the staff slash commands (`/orders`, `/order`, `/ticket`, `/review`, `/announce`) in Discord's picker; it also lets them delete/pin messages. `/setup` adds it and keeps the role's other permissions (`permissions_mode = "add"`). Fine-tune per command under Server Settings → Integrations; the bot's staff checks stay authoritative |
| `verification.rules` | 6 | Six general community rules on #welcome (text in content.toml) |
| `verification.dm_confirmation` | false | Confirmation only privately in Discord, no DM |
| `how_to_buy.steps` / `link_channels` | 4 / catalog, purchase, faq | Guide size and its link buttons |
| `faq.sections` | general, transactions, order_status, cancellations, problems (3/3/3/2/2 questions) | Adjust counts |
| `shipping.statuses` / regions | full, none / United States (all states except Alaska and North Dakota) and Europe fully supported; Alaska, North Dakota and the rest of the world not supported | The owner's delivery area |
| `shipping.info_sections` | 2 | How shipping works; where to ask |
| `catalog.products_per_listing_message` | 1 | One #catalog message per item |
| `catalog.browse_enabled` | false | No extra private browsing button (the spec only asks for per-item Buy buttons) |
| `catalog.low_stock_threshold` | 3 | 1-3 units left shows "low stock" |
| `new_arrivals.announce_new_products` / `announce_restocks` / `mention_role` | true / true / true | Automatic updates ping Restock Alerts |
| `tickets.panel_channel` | "" | No standalone ticket panel: support starts with Report a Problem in #purchase |
| `tickets.channel_name_pattern` | `{prefix}-{number}-{user}` | Ticket names contain a unique number and the username |
| `tickets.staff_alert_channel` / `transcript_channel` | staff_chat / transcripts | |
| `checkout.panel_channel` / `order_category` / `staff_channel` | purchase / purchase / orders | |
| `checkout.transcript_on_close` | true | Order tickets also get a transcript when they close |
| `checkout.customer_role` / `customer_role_on` / `customer_role_remove_on_refund` | customer / fulfilled / true | |
| `problems.eligible_statuses` | paid, processing, fulfilled | Orders a report may refer to |
| `problems.allow_without_order` / `require_reason_on_reject` | true / true | |
| problem types | not received, damaged, wrong item, something else (one paragraph field each) | Add types and fields as needed |
| `operations.ping_roles` / `owner_dm_alerts` | staff / true | Who is notified about orders and failures |
| `operations.answers_in_orders_channel` | false | The customer's typed form answers are not posted in #orders; staff open them privately (Details button, `/order view`) |
| `operations.*_minutes` reminders | unclaimed 60, unpaid 1440, unfulfilled 2880, unanswered ticket 240 | #staff-chat reminders (once each) |
| `checkout.currency` / `payments.provider` | `USD` / `manual` | Staff post payment instructions in the order ticket and confirm the payment |
| `checkout.submission_form` | full name, shipping address, optional delivery notes | Asked at Submit; shown to staff only |
| `checkout.session_expiry_minutes` / warning / `payment_reminder_minutes` | 1440 / 60 / 1440 | Unsubmitted carts expire after 24 h (warning 1 h before); unpaid orders get one reminder after 24 h; unpaid orders never expire automatically |
| `reviews.rating_scale` / `prompt_delay_minutes` | 5 / 10080 | 1-5 stars, asked 7 days after the order is completed |
| `announcements.mention_roles` | restock_alerts | Staff may choose to ping Restock Alerts with an announcement |
| `branding.progress_filled` / `progress_empty` | 🟧 / ⬜ | Step indicator in the order ticket |
| `[banners]` | enabled, all 31 pictures in `config/banners/` | One banner picture at the top of every kind of message (§13) |
| `announcements.allow_image` | true | |
| `my_orders.orders_per_page` | 5 | |
| `features.credits` / `info_panels` | false | Not part of the specification |
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
  the message as its first attachment and never referenced by an embed, because Discord shows such pictures above the
  embeds at full width (an embed image would sit at the bottom, smaller; an image-only first embed would use one of
  the ten embed slots). Edits keep an unchanged banner without uploading it again (the attachment name contains the
  picture's hash), and replace or remove it when it changed; a `PartialMessage` is fetched once before such an edit so
  this is decided from its real attachments. Multi-message panels show the banner on their first message only; live
  messages (the order ticket panel, the #orders record, report and review-approval messages) change banner with their
  state. The banner counts toward the per-message file limits and the panel hash; transcripts leave the bot's
  banners out. Pictures live in `config/banners/` (≤ 2 MB each) and are read at startup and on `/reload`; a missing or
  invalid picture only means that kind of message shows none (listed in the logs and by the checker).
- **Edit text.** A message command (right-click a bot message → Apps → Edit text, administrators only) finds the
  texts the message shows by comparing every text of the message with every content value (placeholders match any
  data), lists them in display order, and opens the `/content set` form for the one picked. After a save, the panels
  that are already posted are refreshed at once (`/setup` is still what posts new panels). Private (ephemeral) replies
  cannot be right-clicked in Discord, so `/content set` suggestions also match a few words of the current text.

## 12. Assumptions

- One Discord server per bot instance (`GUILD_ID`).
- English technical operator text is acceptable for administrators (every entry can be overridden with
  `operator.<key>` in content.toml).
- Staff confirm manual payments themselves; the bot does not verify bank transfers or other external payments.
- Product images are either `https://` links or uploads stored in the database and attached to every message.
- Slash-command names are the ones in the specification; `/myorders` (the only customer command) takes its
  description from content (`my_orders.command.description`) and is registered at startup only when it is set.

## 13. Known limitations

- Banner placement (above the embeds) follows Discord's documented message layout and community reports; it could not
  be seen in a real client from here. Each banner upload adds 0.5-1.1 MB to the message (mainly #bot-log entries,
  one per audited action: switch them off with `[banners] off = ["activity-log"]` if that matters).
- Not yet verified against a live Discord server or Railway from here. All behaviour is verified with automated
  tests using faithful fakes and the offline preview — see `docs/MORNING.md` for the live verification checklist.
- Railway config-as-code deprecation (see §9).
- Transcripts include message text only with the Message Content intent enabled; very large channels may exceed
  Discord's upload size (the transcript is then retried and reported; deletion waits for it). Attachment links in
  transcripts are Discord CDN links, which Discord expires after some time.
- `/content set` accepts up to 4000 characters (Discord form limit); longer descriptions must be set in content.toml.
- `/reload` does not re-sync slash-command descriptions (`/myorders`); restart the bot after changing it.
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
