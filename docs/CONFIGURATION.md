# Configuration reference

The bot is configured by two files in `config/` (or `CONFIG_DIR`) plus environment variables:

| File | Contains | Who decides |
|---|---|---|
| `config/settings.toml` | Behaviour: feature flags, server structure, timers, limits, prices-related settings | You (business decisions) and technical defaults |
| `config/content.toml` | Every piece of text customers can see, by content key | You — it ships with a complete starting text pack you can change |
| `config/banners/` | One banner picture per kind of message (`<id>.png`) | You — it ships with your 31 banners |
| Environment variables | Secrets and deployment paths (`DISCORD_TOKEN`, `GUILD_ID`, `OWNER_ID`, `DB_PATH`, …) | You (see `docs/DEPLOYMENT.md`) |

Changes to the files take effect after a redeploy, or immediately with `/reload` (the running configuration is
kept if the new files are invalid). Text can also be changed in Discord: right-click a bot message → **Apps → Edit
text**, or `/content set` (stored in the database; takes precedence over `content.toml`; `/content export` downloads
everything as a `content.toml`). After a change, the posted panels are updated at once.

`settings.toml` is validated strictly at startup: unknown keys (typos) and wrong types stop the bot with the exact
path in the log. Semantic problems (for example a feature that refers to a channel that is not defined, or a
business decision that is not made yet) do **not** stop the bot; they keep only the affected feature disabled and
are listed by `/status`, `/setup`, and `python -m tools.checklist`.

## Decisions that keep a feature off until they are made

The shipped files make these for you (see `docs/DECISIONS.md` §10a); if you clear one, the dependent feature stays
disabled (and `/status` says why) until you set it again:

| Setting | Needed by |
|---|---|
| `checkout.currency` (shipped: `USD`) | catalog prices, checkout, credits |
| `payments.provider` (shipped: `gift_card`; also `manual`) | checkout |
| `reviews.prompt_delay_minutes`, `reviews.rating_scale` (shipped: 10080 = 7 days, 5 stars) | reviews |
| `verification.rules` (shipped: 6) | #welcome |
| every required customer-facing text (content keys) | the message that shows it |

Fulfillment options and fees are optional (none are configured). Automation timers are `0` (off) unless set.

## `[features]`

Each flag turns a feature on. A feature is *ready* (shown to customers) only when it is enabled, its settings are
valid, its dependencies are ready, and all of its required content keys are configured.

| Flag | Feature | Depends on |
|---|---|---|
| `verification` | #welcome rules + "I agree" button granting `permissions.member_role` | – |
| `how_to_buy`, `faq`, `shipping` | The START HERE guides | – |
| `announcements` | `/announce` posting to #announcements | – |
| `new_arrivals` | #new-arrivals Restock Alerts toggle + automatic product updates | catalog |
| `problems` | #purchase Report a Problem workflow | tickets, checkout |
| `my_orders` | `/myorders` for customers | checkout |
| `info_panels` | Generic extra informational embeds (not used by the specification) | – |
| `tickets` | Private tickets (support; used by Report a Problem) | – |
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
| `staff_roles` | Role keys treated as staff for every feature. Features add their own (`tickets.staff_roles`, a ticket category's `staff_roles`, `checkout.staff_roles`, `problems.staff_roles`, `announcements.roles`). |
| `owner_role` | Role key `/setup` gives to `OWNER_ID` (list it in `admin_roles` too; shipped `owner`). |
| `member_role` | Role key granted by the #welcome "I agree" button; it unlocks `members_*` channels (shipped `member`). It must not appear in any staff/admin role list. |

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

## `[banners]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | false (shipped: true) | Show a banner picture at the top of the bot's messages. |
| `directory` | `banners` | Folder inside the config folder with one picture per banner id: `<id>.png` (or `.jpg`, `.gif`, `.webp`), at most 2 MB; 1500×500 recommended. |
| `off` | `[]` | Banner ids not to show, e.g. `["activity-log"]` (the #bot-log entries). |

Banner ids (the kind of message each is used for): `welcome` (#welcome rules), `how-it-works` (#how-to-buy), `faq`,
`shipping`, `announcements`, `catalog` (first #catalog message), `new-arrivals`, `reviews` (review request and
published reviews), `purchase-panel` (#purchase), `ticket-created` (ticket opened / introduction), `selection-and-cart`,
`details-and-options` (details step; correction requests), `review-and-summary` (order review step),
`submission-and-processing` (processing), `successful-confirmation` (confirmed / paid / saved), `pending-review`
(submitted, awaiting payment or approval), `rejected-or-unsuccessful-submission`, `order-status` (tracking and other
status updates), `completed-or-dispatched`, `staff-dashboard` (staff lists, records and /status), `staff-notifications`
(#staff-chat alerts), `activity-log` (#bot-log), `transcripts`, `direct-message-notification` (the Owner's DM alerts),
`reminder`, `support-request` (Report a Problem and report tickets), `resolution` (resolved / refunded / closed),
`cancellation`, `account-and-personal-records` (/myorders), `error` (error replies), `general-response` (other replies
and confirmation prompts). Messages spread over several Discord messages show the banner on the first one only; a
live message (order ticket panel, #orders record) changes banner with its status. Replace a picture file and run
`/reload` then `/setup` to update posted panels.

## `[icons]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | false (shipped: true) | Use the bot's own icons (small pictures shown like emojis) in its texts. |
| `directory` | `emojis` | Folder inside the config folder with one `<name>.png` per icon (names: a-z, 0-9 and `_`, 2-32 characters; at most 256 KB; 128×128 recommended). |

The bot uploads the icons as *application emojis* when it starts: they belong to the bot, work in every channel and
direct message, use none of the server's emoji slots and need no permission (Discord allows 2000 per bot). Uploaded
icons are reused on the next start; a changed picture is uploaded again. A text shows an icon with `{icon_<name>}`,
for example `{icon_truck} **Delivery**`; a button or option emoji can be `{icon_<name>}` too. Icons work in titles,
descriptions, field names and values, and emojis; Discord does not show them in footers, author lines, button labels,
select options or modal texts, so `/content set` refuses them there. Until an icon is uploaded (or if its upload
failed) it shows nothing, or a plain dot for the `dot_*` status dots. The shipped set (`config/emojis`, from Tabler
Icons, MIT licence) is rendered by `tools/icons/render.js`: status dots `dot_blue` (we ship there), `dot_yellow` (we
ship to parts of it) and `dot_grey` (we do not ship there); step numbers `step_1` to `step_6`; order statuses
`st_submitted`, `st_confirmed`, `st_shipped`, `st_delivered`, `st_cancelled`; and section icons such as `truck`,
`gift`, `shield`, `box`, `receipt`, `lock`, `ticket`, `star` (see the folder for all of them). Run
`/reload` after changing a picture, then `/setup` to update posted panels.

## `[[server.roles]]`, `[[server.categories]]`, `[[server.channels]]`

The structure `/setup` creates or adopts. `key` is an internal identifier; names come from content keys
`server.role.<key>.name`, `server.category.<key>.name`, `server.channel.<key>.name` (and optional
`server.channel.<key>.topic`). Set `id` to adopt an existing role/channel instead of creating one.

| Field | Applies to | Meaning |
|---|---|---|
| `key` | all | Internal identifier referenced by other settings. |
| `id` | all | Adopt this existing Discord object (0 = create or adopt by name). |
| `permissions` | roles | Discord permission names granted server-wide, e.g. `["manage_messages"]`. Empty leaves an adopted role's permissions untouched. |
| `permissions_mode` | roles | `exact` (default: the role gets exactly `permissions`) or `add` (the listed permissions are added; the role's other permissions are kept — the shipped Staff role uses this so adopting an existing Staff role never removes its permissions). |
| `color`, `hoist`, `mentionable` | roles | Role appearance. |
| `access` | categories, channels | `public_readonly` (everyone reads, only staff/bot post), `public_interactive`, `members_readonly` / `members_interactive` (hidden until the Member role; staff always see), `staff`, `admin`, or (channels) `inherit` from the category. |
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
| `panel_channel` | `""` | Where a standalone ticket panel is posted (`""` = none: support starts from Report a Problem; categories are then optional). |
| `panel_style` | `buttons` | `buttons` (one per category) or `select` (dropdown). |
| `staff_roles` | `[]` | Extra role keys with access to every ticket. |
| `transcript_channel` | `transcripts` | Staff channel for HTML transcripts (`""` = none). |
| `transcript_dm_user` | false | Also DM the transcript to the ticket owner. |
| `staff_alert_channel` | `staff_chat` | New-ticket and new-report alerts (`""` = none). |
| `max_open_per_user` | 1 | Open tickets per user per category (a category can override). |
| `creation_cooldown_seconds` | 30 | Minimum time between ticket creations per user. |
| `max_created_per_user_per_day` | 10 | Anti-abuse cap on tickets a user can create per 24 hours (0 = unlimited); prevents one user from filling the ticket category with channels. |
| `allow_user_close` | true | The owner may close their ticket. |
| `allow_reopen` / `user_can_reopen` | true / false | Reopening (staff; and optionally the owner). |
| `close_confirmation` | true | Ask for confirmation before closing. |
| `lock_on_close` | true | The owner can no longer post after closing. |
| `closed_category` | `""` | Move closed tickets to this category. |
| `delete_after_close_minutes` | 0 | Delete closed ticket channels after N minutes (transcript first). 0 = never. Discord allows only 50 channels per category: on a busy server set this (or `closed_category`), otherwise the ticket category eventually fills up and new tickets are refused. |
| `auto_close_inactive_minutes` | 0 | Close inactive tickets automatically. 0 = never. |
| `auto_close_warning_minutes` | 0 | Warn N minutes of inactivity before auto-close (must be smaller). |
| `channel_name_pattern` | `{prefix}-{number}-{user}` | `{prefix}` is the category's content prefix; `{number}` is required; `{user}` optional. |
| `number_padding` | 4 | Zero-padding of ticket numbers. |

Per category: `id` (internal), `discord_category` (category key where channels are created), `staff_roles`,
`max_open_per_user` (0 = global), `button_style`, `ping_staff` (mention staff roles in the alert; the pinged roles
must be mentionable — `mentionable = true` in `[[server.roles]]` — or the bot cannot notify them, which `/setup`
reports), and an optional
`[[tickets.categories.form]]` (up to 5 fields: `id`, `style` = `short`/`paragraph`, `required`, `min_length`,
`max_length`) shown before the ticket is created.

## `[catalog]`

| Key | Default | Meaning |
|---|---|---|
| `listing_channel` | `catalog` | Live product listing channel (`""` = no listing). |
| `products_per_listing_message` | 1 | Product cards per listing message (1–10; split further to respect Discord limits). Each card has its own Buy button. |
| `manager_roles` | `[]` | Extra role keys allowed to manage products (admins always can). |
| `browse_enabled` | false | Private, paginated product browsing button on the listing header. |
| `low_stock_threshold` | 0 | Show the "low stock" state (warning color) at or below this stock (0 = off). |
| `image_hosts` | `[]` | Allowed hosts for product image URLs (`[]` = any `https://` host). |

## `[checkout]`, `[[checkout.fulfillment_options]]`, `[[checkout.fees]]`, `[[checkout.details_form]]`

| Key | Default | Meaning |
|---|---|---|
| `panel_channel` | `purchase` | Channel of the #purchase panel (Start, Report a Problem). |
| `order_category` | `purchase` | Category for private order channels. |
| `staff_roles` | `[]` | Extra role keys that act as order staff. |
| `staff_channel` | `orders` | The live #orders record of every order is posted here. |
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
| `transcript_on_close` | true | Transcript of the order channel when it closes (uses the tickets transcript channel and text). |
| `session_expiry_warning_minutes` | 0 | Warn in the ticket this long before an idle cart expires (0 = off). |
| `customer_role` / `customer_role_on` / `customer_role_remove_on_refund` | `customer` / `fulfilled` / true | Role given when an order reaches `paid` (also counts later statuses, shipped included) or `fulfilled` (delivered); removed after a full refund unless another order qualifies. |

Submission form (`[[checkout.submission_form]]`, up to 5 fields, asked when the customer presses Submit; answers are
stored with the order and shown to staff). Fulfillment options: `id`, `fee_minor`, optional `[[...form]]`. Fees: `id`, `kind` = `flat` (`amount_minor`) or
`percent` (`basis_points`, 100 = 1%; rounded half up). Details form: up to 5 fields like ticket forms.

## Specification features (`[verification]`, `[how_to_buy]`, `[faq]`, `[shipping]`, `[announcements]`, `[new_arrivals]`, `[problems]`, `[my_orders]`, `[operations]`)

Each section is documented, key by key, in its own page and in the comments of `config/settings.toml`:
`docs/features/verification.md`, `docs/features/guides.md` (how_to_buy, faq, shipping),
`docs/features/announcements.md`, `docs/features/new_arrivals.md`, `docs/features/problems.md`,
`docs/features/customer_panel.md` (my_orders and the order ticket), `docs/features/operations.md` (staff alerts,
reminders, Owner alerts, Customer role) and `docs/features/catalog.md`.

`operations.answers_in_orders_channel` (shipped false): post the customer's form answers under the order record in #orders; when false they appear only in the private Details view and `/order view`.

## `[payments]`

`provider`: **business decision.** Shipped: `"gift_card"`.

- `"gift_card"`: the customer pays the exact total with gift cards (DoorDash eGift cards in the shipped text). Under
  the payment instructions they press **Submit gift card** and enter the code, the PIN (if the card has one) and the
  card's amount in a private form; several cards may pay one order. Once the cards cover the total the order is
  placed (`payment_submitted`) and the payment staff press **Reveal gift card** on the record (a private reply with
  the codes, spoiler-hidden, and the values the customer declared), redeem the cards in the shop's account, check each
  card's value and press **Payment confirmed**, or **Card problem** with the reason the customer is shown (the order
  then waits for payment again). A placed order cannot be rejected or cancelled while its cards wait: decide on the
  cards first. Value beyond the total,
  or cards redeemed for an order that is cancelled afterwards, becomes the customer's store credit.
- `"manual"`: shows your configured payment instructions (`checkout.payment.manual.*`) and lets staff confirm
  payment.

Refunds are recorded (as store credit, or returned outside the bot). The bot never asks for bank or credit card
details.

### `[payments.gift_card]`

| Setting | Shipped | Meaning |
|---|---|---|
| `buy_url` | `https://www.doordash.com/gift-cards/` | The **Buy a gift card** link button under the instructions (empty: no button). |
| `staff_roles` | `["owner"]` | Roles (keys) that may reveal codes and confirm or refuse gift card payments; owners and admins always may. Other staff see the cards on the record (amount, last characters, state) but never a code. |
| `max_cards` | 5 | Cards one order may be paid with (1-5: the reveal reply has a redeemed and a problem button per card). |
| `keep_days` | 7 | A code nobody decided on is deleted after this many days (the card stays listed; staff use Card problem). |
| `chat_guard` | true | Delete a customer's message in their order ticket when it looks like a gift card code, and tell them privately to use the form (needs `bot.message_content_intent`). |

Before you switch `provider` away from `"gift_card"`, finish the orders whose gift cards wait for a decision
(`/orders status:payment_submitted`): the gift card buttons are only shown while gift cards are the configured
payment.

Codes are stored only encrypted, with the key from the `GIFT_CARD_KEY` environment variable or, when it is unset,
a `gift_card.key` file the bot creates next to the database (see `docs/SECURITY.md`). A code is deleted as soon as
staff decide on its card, when the order ends unpaid, or after `keep_days`.

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
| `require_approval` | false | `true`: a submitted review is posted with Approve / Reject buttons in `approval_channel` and published only after a staff member approves it (the `reviews.approval.*` texts are then required). `false`: published right away (approved automatically). |
| `approval_channel` | `staff_chat` (shipped) | Staff or admin channel key for approval messages; checked only when `require_approval = true`. |

Staff for the Approve / Reject buttons and `/review approve|reject`: `permissions.staff_roles`, admins and
`checkout.staff_roles`. The customer's optional decision notices (`reviews.notice.*`) and the optional verification
marker on published reviews (`reviews.published.verified_field` + `verified_value`) are content keys; see
`docs/features/reviews.md`.

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
