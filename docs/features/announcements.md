# Announcements (`/announce`)

Specification: section 3 (`📌 START HERE` → `#announcements`) and section 8 (`/announce`).

## What it does

Authorized staff publish announcements in the announcements channel with `/announce`:

1. `/announce` (optionally with an `image` and a `mention`) opens a private form with **Title** (up to 256
   characters), **Message** (up to 4000) and up to three configured extra fields.
2. Submitting the form posts **one embed** in the announcements channel:
   - the staff member's title and message (exactly as typed; only surrounding whitespace is removed; Discord
     formatting such as bold, lists and links works),
   - every configured extra field that was filled in, as an embed field whose heading is the configured content key
     `announcements.field.<id>.name` (empty optional fields are left out),
   - the uploaded image, re-attached to the message as a file (`attachment://announcement.<ext>`), so it keeps
     displaying after Discord's temporary upload links expire,
   - the shared branding (author line `branding.author_name`, footer `branding.footer_text`, logo/banner if
     `branding.panel_thumbnail` / `panel_banner` are on; an uploaded image takes the banner's place), the primary
     color and a timestamp.
3. The staff member gets a private confirmation (operator text) with a link to the post.

Nobody is mentioned unless a mention role was chosen; then only that role (Discord `allowed_mentions` is limited to
it, so `@everyone`, `@here` or role mentions typed into the text never ping). The bot never posts an announcement on
its own: every post comes from a submitted form.

If anything prevents posting (empty or too long text, the 6000-character embed total exceeded, the channel missing,
missing bot permissions, Discord refusing the message, an image that cannot be downloaded, a role that cannot be
mentioned), **nothing is posted** and the staff member gets a private operator error. Most errors offer an
**Edit and try again** button that reopens the form prefilled with what was typed.

## Settings (`[announcements]` in `config/settings.toml`)

| Setting | Default | Meaning |
|---|---|---|
| `channel` | `"announcements"` (shipped) | Channel key (from `[[server.channels]]`) the announcements are posted in. Required. |
| `roles` | `[]` | Extra role keys allowed to use `/announce`. `permissions.staff_roles` and admins (Owner, `permissions.admin_roles`, Discord Administrators) can always announce. |
| `mention_roles` | `[]` | Role keys staff may choose to mention. Empty: announcements never mention anyone. At most 25. The role must be mentionable (`mentionable = true` in `[[server.roles]]`, applied by `/setup`) unless the bot has the Mention @everyone permission in the channel. |
| `allow_image` | `true` | Whether staff may attach one image (png, jpg, gif or webp, up to the server's bot upload limit: 10 MB without boosts). The option stays visible when off but is refused. |
| `[[announcements.fields]]` | none | Up to 3 extra form inputs, each posted as an embed field: `id` (internal key), `style` (`short` or `paragraph`), `required` (default `false`), `max_length` (1-1024, default 1024). |

Example:

```toml
[announcements]
channel = "announcements"
roles = []
mention_roles = ["restock_alerts"]
allow_image = true

[[announcements.fields]]
id = "valid_until"
style = "short"
required = false
max_length = 100
```

Settings are re-read by `/reload`; a form opened before a change is validated against the new settings when it is
submitted.

## Content keys

| Key | Required | Where it is shown |
|---|---|---|
| `announcements.field.<id>.name` | yes, for each configured field | Heading of that field in posted announcements; also the field's label in the staff form when it fits (45 characters), otherwise the form shows `Field '<id>'`. |
| `branding.author_name`, `branding.footer_text` | optional (shared) | Author line and footer of every announcement. |

With no extra fields configured, the feature needs no content of its own (the announcement text is typed by staff).
Like every customer-facing feature it requires the shared `core` texts (error messages) to be configured.

Everything staff see while publishing (form labels, confirmations, errors) is operator text, overridable as
`operator.announcements.*` in `config/content.toml` (see `shopbot/features/announcements/text.py`).

## Command

| Command | Access | Options |
|---|---|---|
| `/announce` | staff (`permissions.staff_roles`, `announcements.roles`, admins); hidden by default from members without Manage Messages (adjust under Server Settings → Integrations, e.g. for `announcements.roles`) | `image` (optional attachment), `mention` (optional; autocomplete lists the existing `mention_roles`; the role key or the role's name are accepted) |

Permissions are enforced by the bot on the command, on the form submission and on the retry button (the router
re-checks staff access every time; a form can only be submitted by the staff member who opened it).

## Reliability

- **Restart-safe form:** the form's custom id is `sb:an:submit:<token>` (24 random hex characters). The token refers
  to a row in the `announcements` table holding the command's choices (image reference, mention). A form opened
  before a restart is published normally after it; the retry button (`sb:an:retry:<token>`) also keeps working.
- **No duplicates:** a submission claims the row with a conditional update (`draft` → `posting`) inside a transaction,
  so the same form can never post twice (double submissions get "already published" with the link; concurrent ones
  "being published").
- **Interrupted posts:** if the process stops while posting, the row is marked `interrupted` at the next start and
  an `announcements.interrupted` audit entry is written. It is never retried automatically (it may already have been
  posted); staff are told to check the channel.
- **Expiry:** a form (and the text typed into it for retrying) is usable for 6 hours after `/announce`; expired
  unpublished rows are deleted at the next `/announce`, publish or restart. Published rows keep only identifiers
  (channel and message id), never the text.
- **Image:** the upload is validated when the command runs (type, size, Discord CDN link) and downloaded again when
  the form is submitted; the real format is detected from the file's first bytes.
- **Audit:** `announcements.published` (actor, channel, message id, title/message length, filled field ids, image yes/no,
  mention role; never the text), `announcements.failed` (problems caused by the server state or Discord, e.g.
  `channel_missing`, `missing_permissions`, `forbidden`, `send_failed`, `image_download`), and
  `announcements.interrupted`.

## Testing it manually in Discord

1. Enable `features.announcements` (shipped on), run `/setup` so the `#announcements` channel exists, and make sure
   the core texts are configured (`/status` shows the feature as ready).
2. As a member without a staff role, `/announce` must be refused (permission error, nothing posted).
3. As staff: `/announce` → fill title and message → submit. One embed appears in `#announcements` with your text,
   the footer/author branding and a timestamp; you get a private confirmation with a link.
4. Configure a field (settings + `announcements.field.<id>.name`), `/reload`, and publish again: the field appears
   under the message when filled in and is left out when empty.
5. `/announce image:<a png>` → the picture is shown inside the embed. Edit the message later or wait a day: the image
   still displays (it is attached to the message).
6. Add a mentionable role to `mention_roles`, `/reload`, `/announce mention:<role>`: the role is pinged once; text
   such as `@everyone` typed into the message does not ping.
7. Errors: submit a message of spaces (form error with **Edit and try again**), remove the bot's Send Messages
   permission in the channel (permission error, nothing posted), delete the channel (channel missing; `/setup`
   recreates it, then **Edit and try again** publishes).
8. Restart the bot while a form is open, then submit it: it is published.

## Limitations

- The announcements are posted, not edited or deleted by the bot afterwards (delete a post with Discord's own
  message menu; the audit log keeps the record).
- One image per announcement. Discord limits the file to the server's bot upload limit (10 MB without boosts).
- An announcement's embed text is limited by Discord to 6000 characters in total (title, message, fields, footer and
  author line); a longer announcement is refused with a clear error, never shortened.
- The slash command's options are registered when the bot starts: the `image` option is always shown (refused when
  `allow_image` is off) and mention roles are offered by autocomplete, so both follow `/reload` without re-syncing.
- Not yet verified against a live Discord server (see docs/MORNING.md); behaviour is covered by automated tests with
  discord.py-typed fakes and the offline preview.
