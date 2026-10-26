# Security considerations

## Secrets

- `DISCORD_TOKEN` is read only from the environment (Railway Variables in production, `.env` locally).
  It is never written to the database, embeds, files, or logs. The log formatter redacts the token value and
  anything shaped like a Discord token, including inside exception traces.
- `.env`, databases, backups, and logs are git-ignored. `python -m tools.secret_scan` checks tracked files for
  token-shaped strings and forbidden file types; run it before pushing.
- **The GitHub repository `longswordsmp/Shop-Bot` was public when this project was built.** Make it private
  (GitHub → Settings → General → Danger Zone → Change visibility) before adding business content you do not want
  published. Even after making it private, treat anything ever pushed while public as disclosed.

## Authorization

- Every sensitive operation is authorized on the server side at the moment it happens:
  - Components (buttons, menus, forms) pass through the router, which checks the guild, the feature's readiness,
    and the route's access level (any / staff / admin, including feature-specific staff roles).
  - Handlers then reload the target record by ID and verify it belongs to the guild and that the acting user is
    its owner or authorized staff, and that the record's current state allows the action.
  - Slash commands are hidden by `default_permissions` (convenience) and protected by explicit checks
    (`require_admin` / `require_staff`) evaluated on every invocation.
- Component IDs only carry record IDs. A forged or stale ID cannot grant access because nothing is trusted from
  the payload except the ID, which is re-validated against the database and the acting user.
- Admin = `OWNER_ID`, members with Discord's Administrator permission, or roles in `permissions.admin_roles`.
  Staff = admins plus roles in `permissions.staff_roles` (and feature-specific staff roles).

## Resetting the server (/reset)

- Only `OWNER_ID` or the server owner can reset; the router admits administrators and every reset control checks
  the owner again. The preview's fingerprint is in the confirmation form's id, so a confirmation for a server that
  changed since the preview is refused, and a forged id does no more than the owner could.
- Nothing is deleted before the owner typed `RESET <server name>`, the DM for the result was delivered, the
  database backup was written (`backups/before-reset-<time>.db`, never removed by the `/backup` rotation) and the
  private setup channel was created. The bot's own roles and the owner's Administrator roles are never deleted, so
  neither can lock itself out half-way.
- Without `wipe_shop_data` no shop data is deleted: the records of the deleted Discord objects are forgotten, and
  tickets whose channel was deleted are recorded as deleted and unfinished checkouts expire, exactly as when a
  channel is deleted by hand. With it the shop and customer tables listed in `shopbot/features/setup/reset_store.py`
  are emptied in one transaction once every deletion was attempted (the audit log, texts and counters stay). A test
  fails when a new table is not listed as wiped or kept.
- While a reset deletes, the router answers every button and form with "unavailable"; an interrupted reset is
  audited as incomplete and reported to the owner at the next start.

## Who can see what (server layout)

- Users who have not pressed Verify in #welcome see only 📌 INFORMATION (read-only). 🏬 SHOP and 🧾 ORDERS are
  `members_readonly`: hidden from `@everyone`, readable by the Member role, writable only by staff and the bot.
  🔒 STAFF is visible only to Staff and admin roles (Owner).
- Roles members give themselves (Member via "I agree", Restock Alerts via #new-arrivals) are refused as settings
  problems if they appear in any staff/admin role list or carry permissions, so a member can never self-assign power.
- Ticket, order and problem-report channels are created with explicit overwrites (customer + staff + bot only); the
  category's member access does not apply to them.
- Channels the bot does not manage are outside these rules. `/setup` (and `/setup dry_run:True`) lists every channel
  or category it does not manage that `@everyone` can see - typically Discord's default #general text channel and
  General voice channel - as a problem, so unverified users really see only the entry channels. It never changes or
  deletes them: delete them, or deny View Channel to `@everyone` on them.
- Channels that receive staff-only output must be configured with `staff` or `admin` access, or the feature is not
  ready: `audit.channel` (#bot-log), `tickets.transcript_channel` (#transcripts: whole ticket conversations),
  `operations.staff_channel`, `checkout.staff_channel`. The audit post and the transcript post also refuse, at the
  moment of posting, a channel that is not configured that way.

## Private data

- Ticket and order channels are created with explicit overwrites: `@everyone` cannot view; only the owner,
  added participants, configured staff roles, and the bot can.
- Stored personal data is limited to Discord user IDs, ticket/order records, form answers the user typed, and
  review text. Database files never leave the server through the bot (`/backup` writes to the volume only).
- Form answers and other user-typed text are escaped before being shown in embeds; messages cannot ping
  `@everyone`/roles (mentions are disabled except for explicitly targeted users/roles).
- Logs never include tokens or full form answers. Audit details are sanitized before they are stored or posted in
  #bot-log (sensitive keys masked, error text reduced to the exception type, token-shaped strings redacted).
- Configuration changes are audited by identifier only: `/setup` lists the settings keys of the roles, categories and
  channels it changed with the kind of change (e.g. `staff_chat: updated (overwrites, topic)`), `/reload` lists the
  settings sections that changed and how many content keys changed - never a setting value or a text.
- Notification failures are audited without content (the notification's internal kind and the member's ID):
  `notifications.dm_failed` when a direct message failed but the notification reached the member another way, and
  `notifications.failed` (plus a #staff-chat alert) when it could not be delivered at all.
- Staff alerts in #staff-chat and Owner DMs carry identifiers and links, never customers' form answers.
- Shipping addresses (`checkout.ask_address`) are typed only in private forms and never shown in a channel: the order
  ticket shows the name, city and country, staff see the full address privately (the record's Details button, `/order
  view`; in #orders only with `operations.answers_in_orders_channel = true`). The optional apartment details (unit,
  building or floor, entry or buzzer code, delivery instructions) are part of the address and handled exactly like it
  (with `answers_in_orders_channel` on they are posted in #orders with it); the ticket only says that they were added.
  An entry code opens a customer's door, so treat it as sensitive: it is stored with the address in the order's
  details and in the checkout session it was typed in (also one that was cancelled or expired), copied by `/backup`
  like the rest of the database, and kept and wiped like the address (until `/reset wipe_shop_data:True` empties the
  orders and checkout sessions); it is never written to logs, audit entries or staff alerts.
- Uploaded product images are stored in the database (and copied by `/backup`); announcement images are kept only
  until the announcement is posted. A review photo is stored without its metadata (Exif, XMP, IPTC, comments,
  Content Credentials, PNG text chunks: GPS position, camera, owner; a JPEG keeps only its orientation and what a
  decoder needs, a GIF is kept as it is), stripped again whenever it is attached (so a photo stored before this
  existed is posted without it too), and released once the review is published, rejected or removed
  (`docs/features/reviews.md`).

## Payments

- The bot never asks for bank or credit card details or passwords in Discord. The `manual` provider only displays
  the payment instructions you configure and lets staff confirm payment.
- Gift card payments (`gift_card`, the shipped provider): codes are entered only in a private form (a Discord modal,
  never a channel message), stored only encrypted (Fernet: AES with an HMAC signature) with a keyed fingerprint that
  refuses the same card twice, and shown only in a private, spoiler-hidden reply to `payments.gift_card.staff_roles`
  (owners and admins always). Every reveal is counted on the card and audited without the code. A code is deleted
  in the same database statement as any decision on its card (the schema refuses a decided card that still holds
  one), when its order ends unpaid, and after `keep_days`. Codes, PINs and the key never appear in logs, audit
  entries, staff alerts, transcripts or the record.
- The key: `GIFT_CARD_KEY` (recommended, kept in Railway's variables), else a `gift_card.key` file created once
  next to the database with owner-only permissions. A key file on the same volume as the database protects backups
  and database copies that leave the volume, not someone with access to the volume itself; set `GIFT_CARD_KEY` to
  keep them apart. `/backup` copies the database, never the key.
- A customer's message in their order ticket that looks like a gift card code (or a card number), also after an
  edit, is deleted and the customer is told privately to use the form (`chat_guard`); staff messages, links and
  mentions are never treated as codes. It needs `bot.message_content_intent`.
- To tell cards apart, the customer's ticket and the order record show a card's last 4 characters (codes have at
  least 10 letters and digits).
- Money is never lost to a decision: a placed order (`payment_submitted`) is never rejected or cancelled while its
  cards wait (staff may have redeemed a revealed card), and redeemed cards of an order that ends unpaid become store
  credit. The value counted is the one the customer declared; staff check it while redeeming.
- Payment methods (`[[payments.methods]]`) that are down only show a private explanation; an active `staff` method
  only posts that the customer wants to pay that way (staff send the details in the private ticket) and a `link`
  method only links to the shop's own payment page. None of them collects payment details in Discord, and staff
  still confirm the payment themselves (Mark paid).
- Payment integrations are behind a provider interface; any future provider must use the provider's hosted
  checkout or payment links and verify payment server-side (e.g. signed webhooks), never trust a user's claim.

## Abuse and consistency

- Per-user rate limits, duplicate-click protection, ticket creation cooldowns, and open-ticket/open-order limits.
- State changes use conditional database updates inside transactions, so double clicks and concurrent staff
  actions cannot apply twice (e.g. an order cannot be marked paid twice; stock cannot be oversold).
- Errors shown to customers use only configured text; stack traces and internal details are logged with an
  error reference and never displayed.

## Attachments and links

- Product images are accepted only as `https://` URLs (optionally restricted to `catalog.image_hosts`).
  Discord attachment links expire, so they are not accepted as permanent product images.
- Transcripts are generated as HTML with every value HTML-escaped. Attachments are referenced by their Discord CDN
  links (never downloaded); Discord expires those links after some time.

## Data retention

- Audit events are kept forever by default; set `audit.retention_days` to purge older events automatically.
- Completed scheduled tasks are purged after 30 days.
- Closed ticket/order channels are deleted only if `tickets.delete_after_close_minutes` /
  `checkout.delete_channel_after_close_minutes` are set; transcripts are generated first when enabled.
