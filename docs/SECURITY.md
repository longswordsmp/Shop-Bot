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

## Private data

- Ticket and order channels are created with explicit overwrites: `@everyone` cannot view; only the owner,
  added participants, configured staff roles, and the bot can.
- Stored personal data is limited to Discord user IDs, ticket/order records, form answers the user typed, and
  review text. Database files never leave the server through the bot (`/backup` writes to the volume only).
- Form answers and other user-typed text are escaped before being shown in embeds; messages cannot ping
  `@everyone`/roles (mentions are disabled except for explicitly targeted users/roles).
- Logs never include tokens or full form answers.

## Payments

- The bot never asks for or accepts card numbers or other payment credentials in Discord. The implemented
  `manual` provider only displays the payment instructions you configure and lets staff confirm payment.
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
