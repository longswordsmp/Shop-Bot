# Configuration reference

The bot is configured by two files in `config/` (or `CONFIG_DIR`) plus environment variables:

| File | Contains | Who decides |
|---|---|---|
| `config/settings.toml` | Behaviour: feature flags, server structure, timers, limits, prices-related settings | You (business decisions) and technical defaults |
| `config/content.toml` | Every piece of text customers can see, by content key | You only — the bot ships it empty |
| Environment variables | Secrets and deployment paths (`DISCORD_TOKEN`, `GUILD_ID`, `OWNER_ID`, `DB_PATH`, …) | You (see `docs/DEPLOYMENT.md`) |

Changes to the files take effect after a redeploy, or immediately with `/reload` (the running configuration is
kept if the new files are invalid). Text can also be changed in Discord with `/content set` (stored in the
database; takes precedence over `content.toml`; `/content export` downloads everything as a `content.toml`).

`settings.toml` is validated strictly at startup: unknown keys (typos) and wrong types stop the bot with the exact
path in the log. Semantic problems (for example a feature that refers to a channel that is not defined, or a
business decision that is not made yet) do **not** stop the bot; they keep only the affected feature disabled and
are listed by `/status`, `/setup`, and `python -m tools.checklist`.

## What the bot never decides for you

These start unset; the dependent feature stays disabled until you set them:

| Setting | Needed by |
|---|---|
| `checkout.currency` | catalog prices, checkout, credits |
| `payments.provider` (`"manual"` is the implemented option) | checkout |
| `reviews.prompt_delay_minutes`, `reviews.rating_scale` | reviews |
| fulfillment options, fees (none by default) | checkout (optional) |
| every automation timer (auto-close, deletion, expiry, reminders) | off (`0`) until you set a value |
| every customer-facing text (content keys) | everything customers see |

## `[features]`

Each flag turns a feature on. A feature is *ready* (shown to customers) only when it is enabled, its settings are
valid, its dependencies are ready, and all of its required content keys are configured.

| Flag | Feature | Depends on |
|---|---|---|
| `info_panels` | Informational embeds (rules, FAQ, announcements) | – |
| `tickets` | Support tickets | – |
| `catalog` | Product records, live listing, browsing | `checkout.currency` |
| `checkout` | Cart, orders, payment, staff order controls | catalog |
| `credits` | Store credit balances, applying credit at checkout | checkout |
| `reviews` | Review prompts after fulfillment | checkout |
| `dm_notifications` | Notify customers by DM (falls back to their ticket/order channel when DMs are closed) | – |

## `[bot]` (technical defaults)

| Key | Default | Meaning |
|---|---|---|
| `interaction_cooldown_ms` | 700 | Minimum spacing for repeated clicks (anti-double-click). |
| `rate_limit_per_10s` | 12 | Max component interactions per user per 10 seconds. |
| `scheduler_poll_seconds` | 5 | How often due scheduled tasks are checked. |
| `scheduler_batch_size` | 20 | Tasks processed per poll. |
| `scheduler_max_attempts` | 5 | Retries for a failing task before it is marked failed (audited). |
| `scheduler_retry_base_seconds` | 30 | Exponential backoff base (capped at 1 hour). |
| `sync_commands_on_startup` | true | Register slash commands in your server at startup. |
| `message_content_intent` | true | Privileged intent; needed for ticket transcripts to contain message text. Enable it in the Developer Portal. |
| `members_intent` | false | Privileged intent; not required by any current feature. |

## `[permissions]`

| Key | Meaning |
|---|---|
| `admin_roles` | Role keys (from `[[server.roles]]`) treated as bot admins. The owner (`OWNER_ID`) and members with Discord's Administrator permission are always admins. |
| `staff_roles` | Role keys treated as staff for every feature. Features add their own (`tickets.staff_roles`, a ticket category's `staff_roles`, `checkout.staff_roles`). |

## `[branding]` and `[branding.colors]`

| Key | Meaning |
|---|---|
| `logo_url` | `https://` image: author icon (when `branding.author_name` text is set) and panel thumbnail. |
| `banner_url` | `https://` image at the bottom of panels when `panel_banner = true`. |
| `footer_icon_url` | Icon next to the footer text (`branding.footer_text`). |
| `panel_thumbnail`, `panel_banner` | Show logo/banner on panels. |
| `show_timestamps` | Timestamps on workflow/status embeds. |
| `progress_filled`, `progress_empty` | Symbols used by `{bar}` in `common.progress_format`. |
| `colors.primary/info/success/warning/danger/neutral` | `#RRGGBB` embed colors per tone; empty = Discord's default. |

## `[[server.roles]]`, `[[server.categories]]`, `[[server.channels]]`

The structure `/setup` creates or adopts. `key` is an internal identifier; names come from content keys
`server.role.<key>.name`, `server.category.<key>.name`, `server.channel.<key>.name` (and optional
`server.channel.<key>.topic`). Set `id` to adopt an existing role/channel instead of creating one.

| Field | Applies to | Meaning |
|---|---|---|
| `key` | all | Internal identifier referenced by other settings. |
| `id` | all | Adopt this existing Discord object (0 = create or adopt by name). |
| `permissions` | roles | Discord permission names granted server-wide, e.g. `["manage_messages"]`. Empty leaves an adopted role's permissions untouched. |
| `color`, `hoist`, `mentionable` | roles | Role appearance. |
| `access` | categories, channels | `public_readonly`, `public_interactive`, `staff`, `admin`, or (channels) `inherit` from the category. |
| `category` | channels | Parent category key. |
| `slowmode_seconds` | channels | Channel slowmode. |

`/setup` never deletes anything. It repairs the permissions it manages (`@everyone`, the bot, staff/admin roles)
and leaves overwrites you added for other roles/members untouched.

## `[[info_panels]]`

| Field | Meaning |
|---|---|
| `key` | Panel identifier; text under `info_panels.<key>.*`. |
| `channel` | Channel key to post in. |
| `fields` | Number of embed fields (`info_panels.<key>.field.<n>.name/.value`). |
| `inline_fields` | Show fields side by side. |
| `links` | Link-button ids (`info_panels.<key>.link.<id>.label/.url/.emoji`). |

## `[tickets]` and `[[tickets.categories]]`

| Key | Default | Meaning |
|---|---|---|
| `panel_channel` | `ticket_panel` | Where the ticket panel is posted. |
| `panel_style` | `buttons` | `buttons` (one per category) or `select` (dropdown). |
| `staff_roles` | `[]` | Extra role keys with access to every ticket. |
| `transcript_channel` | `transcripts` | Staff channel for HTML transcripts (`""` = none). |
| `transcript_dm_user` | false | Also DM the transcript to the ticket owner. |
| `staff_alert_channel` | `staff_alerts` | New-ticket alerts (`""` = none). |
| `max_open_per_user` | 1 | Open tickets per user per category (a category can override). |
| `creation_cooldown_seconds` | 30 | Minimum time between ticket creations per user. |
| `max_created_per_user_per_day` | 10 | Anti-abuse cap on tickets a user can create per 24 hours (0 = unlimited); prevents one user from filling the ticket category with channels. |
| `allow_user_close` | true | The owner may close their ticket. |
| `allow_reopen` / `user_can_reopen` | true / false | Reopening (staff; and optionally the owner). |
| `close_confirmation` | true | Ask for confirmation before closing. |
| `lock_on_close` | true | The owner can no longer post after closing. |
| `closed_category` | `""` | Move closed tickets to this category. |
| `delete_after_close_minutes` | 0 | Delete closed ticket channels after N minutes (transcript first). 0 = never. |
| `auto_close_inactive_minutes` | 0 | Close inactive tickets automatically. 0 = never. |
| `auto_close_warning_minutes` | 0 | Warn N minutes of inactivity before auto-close (must be smaller). |
| `channel_name_pattern` | `{prefix}-{number}` | `{prefix}` is the category's content prefix; `{number}` is required; `{user}` optional. |
| `number_padding` | 4 | Zero-padding of ticket numbers. |

Per category: `id` (internal), `discord_category` (category key where channels are created), `staff_roles`,
`max_open_per_user` (0 = global), `button_style`, `ping_staff` (mention staff roles in the alert), and an optional
`[[tickets.categories.form]]` (up to 5 fields: `id`, `style` = `short`/`paragraph`, `required`, `min_length`,
`max_length`) shown before the ticket is created.

## `[catalog]`

| Key | Default | Meaning |
|---|---|---|
| `listing_channel` | `catalog` | Live product listing channel (`""` = no listing). |
| `products_per_listing_message` | 5 | Product cards per listing message (1–10; split further to respect Discord limits). |
| `manager_roles` | `[]` | Extra role keys allowed to manage products (admins always can). |
| `browse_enabled` | true | Private, paginated product browsing. |
| `image_hosts` | `[]` | Allowed hosts for product image URLs (`[]` = any `https://` host). |

## `[checkout]`, `[[checkout.fulfillment_options]]`, `[[checkout.fees]]`, `[[checkout.details_form]]`

| Key | Default | Meaning |
|---|---|---|
| `panel_channel` | `shop` | Shop panel channel. |
| `order_category` | `orders` | Category for private order channels. |
| `staff_roles` | `[]` | Extra role keys that act as order staff. |
| `staff_channel` | `order_queue` | Staff order controls are posted here. |
| `currency` | `""` | **Business decision.** ISO 4217 code. |
| `currency_exponent` | -1 | Minor-unit digits (-1 = standard for the currency). |
| `decimal_separator`, `thousands_separator` | `.` `,` | Number formatting for `{amount}`. |
| `max_open_orders_per_user` | 1 | Open orders per customer. |
| `max_line_quantity` | 0 | Max quantity per cart line (0 = only stock/product limits). |
| `max_cart_lines` | 20 | Distinct products per order. |
| `min_order_total_minor` | 0 | Minimum subtotal. |
| `products_per_page` | 25 | Products per selection page (max 25). |
| `session_expiry_minutes` | 0 | Expire unsubmitted carts. 0 = never. |
| `payment_expiry_minutes` | 0 | Expire unpaid orders (releases stock). 0 = never. |
| `payment_reminder_minutes` | 0 | Remind about unpaid orders. 0 = never. |
| `delete_channel_after_close_minutes` | 0 | Delete finished order channels. 0 = never. |
| `transcript_on_close` | false | Transcript of the order channel when it closes (uses the tickets transcript channel and text). |

Fulfillment options: `id`, `fee_minor`, optional `[[...form]]`. Fees: `id`, `kind` = `flat` (`amount_minor`) or
`percent` (`basis_points`, 100 = 1%; rounded half up). Details form: up to 5 fields like ticket forms.

## `[payments]`

`provider`: **business decision.** `"manual"` shows your configured payment instructions to the customer and lets
staff confirm payment; refunds are recorded and returned outside the bot. The bot never collects card data.

## `[credits]`

`allow_at_checkout` (true): customers may apply their balance at checkout.

## `[reviews]`

| Key | Default | Meaning |
|---|---|---|
| `channel` | `reviews` | Where published reviews are posted. |
| `prompt_delay_minutes` | -1 | **Business decision**: minutes after fulfillment (0 = immediately). |
| `rating_scale` | 0 | **Business decision**: 1–10 rating options. |
| `allow_comment`, `comment_required`, `comment_max_length` | true, false, 1000 | Comment form. |
| `prompt_via` | `dm_then_channel` | `dm`, `channel` (order channel), or DM with channel fallback. |

## `[notifications]`, `[audit]`

- `notifications.fallback_to_channel` (true): when a DM cannot be delivered, post in the customer's ticket/order
  channel with a mention.
- `audit.channel` (`audit_log`): admin-only channel for audit entries (`""` = database only).
- `audit.retention_days` (0): delete audit events older than N days (0 = keep forever).

## Content keys

See `docs/CONTENT_CHECKLIST.md` (generated) for every key, where it appears, its Discord length limit, whether it
is required, and its placeholders. Rules:

- Empty means not configured. Required keys must be set for the feature to be shown.
- Text is used exactly as written. Only the placeholders listed for a key are replaced; any other text, including
  braces, stays as written.
- Discord limits are checked, never worked around: an over-long value is reported (`/status`,
  `python -m tools.check_limits`) and the affected feature stays disabled.
- Multi-line text in `content.toml`: use `'''…'''` (the newline right after the opening quotes is ignored).
