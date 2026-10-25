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

Features: `core` (shared text), `server` (/setup), `admin` (operator tools), `info_panels`, `tickets`, `catalog`,
`checkout` (cart, orders, payments), `credits`, `reviews`. Each implements the `FeatureModule` contract
(`shopbot/features/base.py`), so adding a feature never requires touching the others.

## 2. Customer-facing text

- **Decision:** every visible string is a *content key* declared by the feature that shows it (kind → Discord
  length limit, placeholders, required/optional, documentation). Values live in `config/content.toml` or are set
  with `/content set` (database override). The bot ships with every value empty.
- **Readiness gate:** a feature is shown to customers only when it is enabled, its settings are valid, its
  dependencies are ready, and all its required keys are configured and valid. Otherwise its panels are not
  published (and are withdrawn by `/setup`), and its controls answer with the configured "unavailable" message.
- **Missing text at runtime:** never replaced by invented wording. Error replies fall back to a silent
  acknowledgement; messages that cannot be rendered are not sent.
- **Exact text:** placeholders are substituted only for the names a key declares; everything else, including
  braces, emoji and whitespace, is kept verbatim. Discord limits are validated; text is never truncated.
- **Operator text:** replies to admin/staff slash commands, staff-only forms, `/setup` reports and the audit channel
  use built-in technical English ("operator text"), shown only after an admin/staff check, ephemerally or in
  admin-only channels. Every entry can be overridden with `operator.<key>` in `content.toml`. This is necessary
  so the bot can tell an administrator what is missing *before* any content exists.
- **Slash-command descriptions** are technical admin-facing text (Discord requires one). All slash commands are
  staff/admin tools; customers use only panels (buttons, menus, forms), so no customer-facing command text exists.
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
- `tools.check_limits` fails if any component in any preview state has no registered handler, so a button that
  "does nothing" cannot ship unnoticed.

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

## 11. Business decisions left to the owner

Currency; payment provider and instructions; fulfillment options and fees; minimum order; review timing and rating
scale; every automation timer (auto-close, deletion, cart/payment expiry, reminders); ticket categories and forms;
server structure names; every word customers see. See `docs/CONTENT_CHECKLIST.md` and `/status`.

## 12. Assumptions

- One Discord server per bot instance (`GUILD_ID`).
- English technical operator text is acceptable for administrators (overridable).
- Staff confirm manual payments themselves; the bot does not verify bank transfers or other external payments.
- Product images are referenced by `https://` URL (Discord attachment links expire).

## 13. Known limitations

- Not yet verified against a live Discord server or Railway (no credentials were available while building). All
  behaviour is verified with automated tests using faithful fakes and the offline preview — see `docs/MORNING.md`
  for the live verification checklist.
- Railway config-as-code deprecation (see §9).
- Transcripts include message text only with the Message Content intent enabled; very large channels may exceed
  Discord's upload size (the transcript is then retried and reported; deletion waits for it).
- `/content set` accepts up to 4000 characters (Discord form limit); longer descriptions (up to 4096) must be set
  in `content.toml`.
- A single bot instance per database (by design).
- No automated payment provider (see §7).
