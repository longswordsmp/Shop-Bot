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

With `preset:` (see [Presets](#presets)) there is no form: staff post a ready-made announcement after a private
preview.

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
| `allow_image` | `true` | Whether staff may attach one image (png, jpg, gif or webp, up to 10 MB minus the announcements banner picture, about 9.4 MB with the shipped banner: the per-message file limit every bot message is checked against, also on boosted servers). The option stays visible when off but is refused. |
| `[[announcements.fields]]` | none | Up to 3 extra form inputs, each posted as an embed field: `id` (internal key), `style` (`short` or `paragraph`), `required` (default `false`), `max_length` (1-1024, default 1024). |
| `[[announcements.presets]]` | `warehouse_sale`, `shipping_areas` (shipped) | Up to 25 ready-made announcements for `/announce preset:` (see [Presets](#presets)): `id` (a-z, 0-9, `_`, 1-32 characters, unique), `name` (what staff pick, plain text, at most 100 characters), `coupon` (optional discount code its claim button claims, written like `/discount` codes and upper-cased), `coupon_command` (optional: the `/discount create code:<coupon> ...` command staff are shown while the coupon does not exist), `banner` (optional banner id, default `announcements`). |

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
| `announcements.preset.<id>.title` | yes, for each preset | Title of the preset's announcement (plain text, no icons; `{excluded_areas}` works here). |
| `announcements.preset.<id>.description` | yes, for each preset | Text of the preset's announcement: Discord formatting, icons, section headings, `{channel_<key>}` links and `{excluded_areas}`. |
| `announcements.preset.<id>.claim_button` | yes, for a preset with a coupon | Label of the claim button, e.g. `{icon_gift} Claim coupon`. |
| `announcements.preset.<id>.ended_button` | yes, for a preset with a coupon | Label the button shows (disabled) once the coupon ended or expired, e.g. `Offer ended`. |
| `branding.author_name`, `branding.footer_text` | optional (shared) | Author line and footer of every announcement. |

With no extra fields configured, the feature needs no content of its own (the announcement text is typed by staff).
Like every customer-facing feature it requires the shared `core` texts (error messages) to be configured.

Everything staff see while publishing (form labels, confirmations, errors) is operator text, overridable as
`operator.announcements.*` in `config/content.toml` (see `shopbot/features/announcements/text.py`).

## Command

| Command | Access | Options |
|---|---|---|
| `/announce` | staff (`permissions.staff_roles`, `announcements.roles`, admins); hidden by default from members without Manage Messages (adjust under Server Settings → Integrations, e.g. for `announcements.roles`) | `image` (optional attachment), `mention` (optional; autocomplete lists the existing `mention_roles`; the role key or the role's name are accepted), `preset` (optional; autocomplete lists the presets by name, to staff only; the id or the name are accepted) |

Permissions are enforced by the bot on the command, on the form submission, on the retry button and on a preset's
Post announcement and Cancel buttons (the router re-checks staff access every time; a form or preview can only be
used by the staff member who opened it).

## Presets

A preset is a ready-made announcement: its texts are configured once (`announcements.preset.<id>.*`) and staff post
it with `/announce preset:<id>` (plus `mention` and `image` as usual). The shipped presets:

- `warehouse_sale` announces the warehouse sale: buy one, get one free on the old stock with the coupon `BOGO`.
- `shipping_areas` ("Shipping update: areas we can't ship to right now", no coupon, the `shipping` banner) apologises
  that we can't ship to some areas at the moment: "An update on shipping to Alaska and North Dakota", the reason (our
  shipping partners can't currently deliver there within the delivery times and prices we promise, so shipping there
  is paused), that orders everywhere else are not affected (with a link to `#shipping`), that we will announce it
  here as soon as we ship there again, and thanks. The areas are not written in the text: `{excluded_areas}` (see
  below) reads them from `[shipping.excluded_areas]`, so the post always names the areas `#shipping` and the address
  step exclude.

1. **Private preview.** The command answers privately with two messages: the announcement as it will be posted (its
   claim button shown disabled; the mention is not pinged in the copy, the card lists it), then a card with the
   channel, the mention and, for a preset with a
   coupon, the coupon's terms in plain words (for example "buy one, get one free (the cheaper item of every pair is
   free) · only <products> · no end date"), with **Post announcement** and **Cancel**. Nothing is posted yet.
2. **Problems first.** When the announcement could not be posted now, the card says why and how to fix it, and has
   no Post button (nothing is stored): the coupon does not exist, has ended or expired (with the exact command to
   create it, `coupon_command`), it is used up, discount codes are switched off (`checkout.discount_codes`) or the
   checkout feature is not ready, this preset already announced the same coupon (with a link to that post), a
   `{channel_<key>}` link points to a channel /setup has not created, the text shows `{excluded_areas}` while nothing
   is excluded in `[shipping.excluded_areas]` (the announcement would be empty: only the card is shown), plus the
   usual channel, permission, mention and image problems.
3. **Post announcement** checks everything again at that moment and posts the announcement once: one embed with the
   preset's title and text, the branding (author line, footer, timestamp), the banner inside the card at the bottom
   (above the card when an image is attached), the mention when chosen (only that role is pinged) and the claim
   button of the coupon (`sb:dc:claim:<code id>`, handled by the checkout feature: it records the claim and answers
   privately with the coupon's terms and how to use it; it keeps working after restarts). The card becomes the
   confirmation with a link to the post. If posting fails because of the server (a permission, Discord), the card
   lists the problem and keeps **Post announcement** for after the fix; if the coupon stopped being usable, staff run
   the preset again after fixing it. The preview remembers the code it showed: when the coupon was ended and created
   again in between (a new code, whose terms may differ), Post refuses it and staff run the preset again to see the
   new terms.
4. **Cancel** drops the preview; nothing was posted.
5. **When the offer ends.** `/discount end` on the coupon, or a claim press after the coupon expired (the member is
   told the offer has ended), switches the posted button to the disabled `ended_button` label; only the button
   changes, so the text still reads as it was posted (a known limit: the grey "Offer ended" button tells readers). A
   coupon that is merely used up keeps its button (a use comes back when an unpaid order is cancelled); presses then
   answer that the offer has ended. A coupon that ends while the announcement is being posted gets its button switched
   right after (the confirmation says so). After `/reset wipe_shop_data` deleted the codes, a kept post keeps its
   button and a press answers that the offer has ended.

The warehouse sale preset needs its coupon to exist first, e.g.
`/discount create code:BOGO type:Buy one, get one free once_per_customer:false products:<ids of the old stock>`
(product numbers from `/product list`). Add a preset by adding a `[[announcements.presets]]` entry with its texts
(`/content set`, or content.toml) and `/reload`. In a preset's text, `{coupon}` is its coupon's code (the shipped
text says "press **Use coupon {coupon}** on the review step"), so the text follows the `coupon` setting. A coupon has
at most 14 characters, so the customer's "Use coupon <code>" button stays short.

In any preset's title and text, `{excluded_areas}` is the list of areas we do not ship to, read from
`[shipping.excluded_areas]` at every preview and Post and written with the names `#shipping` uses: "Alaska and North
Dakota", "Alaska, North Dakota and Hawaii" for three (joined with `shipping.format.last_area`, the text `#shipping`
uses), and with the country after each country's areas when areas of more than one country are excluded ("Alaska and
North Dakota (United States) and Yukon (Canada)"). Change the areas in `config/settings.toml` and `/reload`; the next
preview shows the new list. Areas removed after a preview are refused by Post (run the preset again); areas changed
in between are posted as they are at that moment.

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
  mention role; never the text; for a preset its id and coupon), `announcements.failed` (problems caused by the server
  state or Discord, e.g. `channel_missing`, `missing_permissions`, `forbidden`, `send_failed`, `image_download`), and
  `announcements.interrupted`.
- **Presets post once:** a preset's preview is a row of the same table (`preset_id` set, no text); Post claims it like
  a form submission (a double press gets "being published" or "already posted" with the link, also after a restart:
  `sb:an:post:<token>` and `sb:an:cancel:<token>` carry only the token). The row keeps the code id its preview
  showed; the claim refuses another code (`changed`) and a second announcement of the same preset and code while the
  first is posting or posted (a unique index), so two staff members posting the same offer post it once; a post
  deleted in Discord no longer counts (the next preview checks). A preview's token never opens or submits the typed
  form (and a form's token never posts a preset).
- **Offer ended:** `/discount end` publishes `DISCOUNT_CODE_ENDED` and a claim press after expiry publishes
  `DISCOUNT_OFFER_ENDED`; both schedule a durable task (`announcements.offer_ended`, one per post, retried with backoff
  while Discord fails) that edits the button and records `offer_ended_at`. At startup every post whose code ended or
  expired while the bot was not watching is scheduled too, so the button is switched even when the event was missed
  or nobody pressed it. A deleted post or channel counts as done. A switch that failed for good (e.g. the bot may not
  edit the post) is tried again by `/discount end` and at startup, not by every later press (one failure alerts
  staff once).

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
9. Presets: `/announce preset:warehouse_sale` before the coupon exists → the card names the `/discount create`
   command and has no Post button. Create it, run the preset again → the copy and the card with the coupon's terms;
   **Post announcement** → one post with **Claim coupon**; press Post again → "already posted". As a customer press
   **Claim coupon** → the private coupon reply. `/discount end code:BOGO` → the button shows **Offer ended**.
10. `/announce preset:shipping_areas` → the copy names the areas of `[shipping.excluded_areas]` ("An update on
    shipping to Alaska and North Dakota") with a `#shipping` link and the shipping banner; **Post announcement** posts
    it. With `[shipping.excluded_areas]` empty (and `/reload`) the preview shows only a card saying nothing is
    excluded, with no Post button.

## Limitations

- The announcements are posted, not edited or deleted by the bot afterwards (delete a post with Discord's own
  message menu; the audit log keeps the record). The only edit is a preset's claim button switching to its ended
  label.
- A preset's private preview stays in the staff member's chat after Post (Discord keeps private replies until they
  are dismissed); only its card turns into the confirmation.
- A preset post whose posting was interrupted by a restart is marked interrupted like a form; if it was in fact
  posted, its button is not switched when the offer ends (a press still answers that the offer has ended; delete
  that post in Discord if it should go).
- One image per announcement, shown in the embed under the announcements banner (the banner is the message's first
  attachment, above the embed). The image and the banner together stay within 10 MB, and posting needs the bot's
  Attach Files permission in the channel whenever banners are on.
- An announcement's embed text is limited by Discord to 6000 characters in total (title, message, fields, footer and
  author line); a longer announcement is refused with a clear error, never shortened.
- The slash command's options are registered when the bot starts: the `image` option is always shown (refused when
  `allow_image` is off) and mention roles are offered by autocomplete, so both follow `/reload` without re-syncing.
- Not yet verified against a live Discord server (see docs/MORNING.md); behaviour is covered by automated tests with
  discord.py-typed fakes and the offline preview.
