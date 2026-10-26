# Morning checklist — from code to a working server

Do these in order. Each step says what to do, where, and how to check it worked. Nothing here requires changing
code. Expected time: about an hour. Every message, button and notification already has text (a starting text pack
written for you) and every feature is set up, so `/setup` posts everything at once; change any wording later.

---

## 1. Create the Discord application and bot

1. Open <https://discord.com/developers/applications> → **New Application** → name it → **Create**.
2. **General Information**: copy the **Application ID** (you need it in step 5). It is not secret.
3. **Bot** tab:
   - **Reset Token** → copy the token. This is a secret: never paste it into code, Discord, or chat.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent** (ticket transcripts with message text,
     the gift card chat guard) and **Server Members Intent** (the welcome mention in #welcome). If one is off, the bot
     still starts without it and `/status` says which one to switch on.
   - Optional: turn off **Public Bot** so only you can invite it.

Check: the Bot tab shows the intent enabled and you have the token saved somewhere private.

## 2. Configure secrets

**Locally (optional, for testing on your machine):**

```bash
cp .env.example .env      # then edit .env
```

Set `DISCORD_TOKEN`, `GUILD_ID`, `OWNER_ID`, `DB_PATH=./data/bot.db`, `LOG_FORMAT=text`. `.env` is git-ignored.

**On Railway (production):** service → **Variables** → add:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | the token from step 1 |
| `GUILD_ID` | your server ID (step 3) |
| `OWNER_ID` | your user ID (step 3) |
| `DB_PATH` | `/data/bot.db` |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `15` (lets in-progress clicks finish when Railway restarts the bot) |
| `GIFT_CARD_KEY` | recommended: encrypts the gift card codes customers submit. Generate it once on your computer with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste it. Keep it secret and never change it while cards wait (they would become unreadable). Without it the bot creates `gift_card.key` on the volume. |

Check: Railway shows the six variables (secret values hidden).

## 3. Get the Discord IDs

1. Discord → **User Settings → Advanced → Developer Mode** on.
2. Right-click your server icon → **Copy Server ID** → that is `GUILD_ID`.
3. Right-click your own name → **Copy User ID** → that is `OWNER_ID`.

## 4. Create or choose the server

Use the server customers will join (or a separate test server first — recommended). The bot manages one server.

## 5. Invite the bot with the right permissions

```bash
python -m tools.invite_url <APPLICATION_ID>
```

Open the printed link, choose your server, **Authorize**. The link requests only the permissions the features use
(no Administrator) plus the `applications.commands` scope for slash commands.

Check: the bot appears in the member list (offline until deployed).

## 6. Role hierarchy and permissions

Server Settings → **Roles**: drag the bot's role **above** every role it must manage (e.g. your staff role).
Discord only lets a bot edit roles below its own.

Check later: `/setup` reports any missing permission or role it cannot manage.

## 7. Connect the GitHub repository

1. **Make the repository private**: GitHub → `longswordsmp/Shop-Bot` → Settings → General → Danger Zone →
   Change visibility → Private. (It was public when the project was built.)
2. The code is on branch `claude/youthful-clarke-ns9ix2`. Merge it into `main` (open a pull request and merge),
   or point Railway at that branch.
3. Railway → **New Project → Deploy from GitHub repo** → authorize Railway's GitHub app for this repository →
   select it and the branch.

## 8. Persistent volume

Railway service → **Volumes** (or right-click the service → Attach Volume) → mount path **`/data`**.
Keep `DB_PATH=/data/bot.db`. Without a volume, every redeploy would start with an empty database, so the bot
refuses to start on Railway without one (the deploy log shows `env_invalid` … "not on persistent storage").

## 9. Deploy

Railway builds with Railpack (Python 3.12 from `.python-version`, dependencies from `requirements.txt`) and runs
`python -m shopbot` (from `railway.json`).

If the service does not pick up `railway.json` (Railway has deprecated config files for new services), set in the
service **Settings**: Start command `python -m shopbot`, Restart policy **On Failure**, **1** replica.

Check the deploy logs for, in order: `starting` → `instance_lock_acquired` → `db_ready` → `commands_synced` →
`bot_ready`. The bot should show online. A `banners_incomplete` warning means a picture in `config/banners/` is
missing or unreadable (that kind of message then shows no banner). The first start also logs `icons_uploaded`: the
bot uploads its icons (`config/emojis/`) as its own application emojis; `icon_upload_failed` names an icon Discord
refused (it then shows nothing, and the next start tries again).
See `docs/DEPLOYMENT.md` → Troubleshooting if anything else appears.

## 10. Run `/setup`

In your server, as the owner/admin: **`/setup dry_run:True`** first. It lists what it would create.

Look at its **Problems** section: it lists every channel or category the bot does not manage that `@everyone` can
see. In a new server that is Discord's default **#general** text channel and **General** voice channel (and their
"Text Channels"/"Voice Channels" categories). Unverified users must see only 📌 START HERE, so **delete them** (or
deny View Channel to `@everyone` on them); the bot never changes or deletes channels it does not manage, and every
`/setup` run reports them until they are gone.

The structure comes from your specification and is already named (content.toml): 📌 START HERE, 📦 CATALOG,
🛒 PURCHASE and 🔒 STAFF with #welcome, #how-to-buy, #faq, #shipping, #announcements, #catalog, #new-arrivals,
#reviews, #purchase, #orders, #staff-chat, #bot-log, #transcripts, and the roles Owner, Staff, Member, Customer and
Restock Alerts. Existing channels/roles with the same names are reused instead of duplicated.

Then **`/setup`**. It creates or repairs roles, categories, channels and permissions, gives the Owner role to
`OWNER_ID`, and posts every panel: #welcome (rules + "I agree"), #how-to-buy, #faq, #shipping, #catalog,
#new-arrivals and #purchase (Start + Report a Problem), each with its banner picture at the top. It is safe to run
again any time; it never duplicates or deletes anything except withdrawing panels of features that are not ready.
`/status` should show every enabled feature as **ready**.

Check: Server Settings → Roles shows the bot's role **above** Owner, Staff, Member, Customer and Restock Alerts
(Discord only lets the bot give roles below its own). Give your staff the Staff role.

**Who sees which slash command.** Discord lists a command only for members with its default permission: the staff
commands (`/orders`, `/order`, `/ticket`, `/review`, `/announce`) need *Manage Messages*, which `/setup` gives the Staff
role (`permissions = ["manage_messages"]` in `settings.toml`; it also lets staff delete and pin messages; `/setup`
only adds it and keeps any other permissions the role already has). Admin commands need Administrator; `/product` needs Manage
Server. To fine-tune, open **Server Settings → Integrations → (the bot) → Manage** and allow or deny each command for
roles, members or channels. For example, to let the Staff role manage products: allow `/product` for Staff there and
add `"staff"` to `catalog.manager_roles` (do not give Staff Manage Server). These settings only change who *sees* a
command; the bot checks the roles itself on every use.

Check: with a staff account (Staff role only), typing `/` lists the staff commands; a customer account sees none of
them (only `/myorders`).

## 11. Change any text or setting

**Edit a message's text from Discord (easiest).** Right-click (on a phone: long-press) any message the bot posted →
**Apps** → **Edit text**. The bot lists every text in that message; pick one, change it in the form (it opens with the
current wording) and save. The posted panels update right away; other messages (tickets, notifications) use the new
text the next time the bot sends them. Only administrators see **Edit text**.

For private replies (which cannot be right-clicked) and forms: **`/content set`** and type a few words of the text
you saw: the suggestions show which text contains them.

- **`/status`** — what is ready and what is missing, per feature.
- **`/content show`** / **`/content clear`** — see a text, or undo your Discord change (the file value applies again).
- **`/content export`** — download every text as a `content.toml` (commit it to keep your Discord changes in GitHub).
- **`/reload`** — re-read the files after you changed them on GitHub (restart after changing
  `my_orders.command.description`).

**Text in `config/content.toml`** (edit on GitHub, commit, push → Railway redeploys): every key is listed with
what it is for, its Discord length limit and its placeholders (`{channel_catalog}`, `{order_number}`, ... are filled
in by the bot).

**Decisions in `config/settings.toml`** (chosen for you; change any): currency `USD`, payment with DoorDash eGift
cards for the exact total (customers submit them in a private form; only the Owner can reveal codes, redeem them and
confirm the payment: `[payments.gift_card] staff_roles`), store credit for overpayments and refunds, shipping to every country in the Americas (in the US:
all states except Alaska and North Dakota) and to Europe, 6 rules on #welcome, FAQ topics, four problem types, reviews 7 days after completion (1-5
stars), a 24-hour unpaid-order reminder, cart expiry after 24 hours, the shipping-address step, staff
reminders. The full list is in `docs/DECISIONS.md` §10a.

**Banners:** `config/banners/` holds one picture per kind of message (`welcome.png`, `faq.png`,
`selection-and-cart.png`, `error.png`, ...). Replace a file with your own picture of the same name (PNG/JPEG/GIF/WebP,
at most 2 MB, 1500×500 recommended) to change it, then `/reload` and `/setup`. To hide one kind, add its name to
`[banners] off` in `settings.toml`; `enabled = false` hides them all.

**Icons:** `config/emojis/` holds the small pictures the texts use like emojis (`{icon_truck}`, `{icon_dot_blue}`,
...). Replace a file with your own 128×128 PNG of the same name, then `/reload` and `/setup`. `docs/CONFIGURATION.md`
→ `[icons]` explains where icons can be used.

Locally you can also run `python -m tools.checklist --write`, `python -m tools.check_limits --strict` and
`python -m tools.build_preview` (open `preview/out/index.html` to see every screen with your text and banners).

## 12. Test the enabled workflows in Discord

Use a second (non-staff) Discord account as the "customer".

**Server & entry**
- [ ] `/setup` twice: the second run reports nothing new to create; the Owner account has the Owner role.
- [ ] Customer account before "I agree": sees only 📌 START HERE (read-only).
- [ ] #welcome "I agree" → private confirmation; 📦 CATALOG and 🛒 PURCHASE appear; clicking again says already verified.
- [ ] #how-to-order link buttons open #catalog, #place-order, #faq; #faq shows its topics and #shipping your countries with their dots.

**Catalog & new arrivals**
- [ ] `/product add` (staff) → its own message in #catalog with a Buy button; `/product stock 0` → button disabled, red.
- [ ] `/product image file:` (upload) → image shows, still there after `/product edit` and a bot restart.
- [ ] #new-arrivals button → Restock Alerts on, again → off. Restock a sold-out product → one update pinging the role.

**Purchase (order ticket)**
- [ ] #place-order Start → private ticket (only you + staff): intro message, then the live panel (reference, step,
      status); add items, Continue, address (region, country, the private address form), delivery and extras,
      review, Confirm order.
- [ ] Buy on a #catalog item while the ticket is open → the item is added to that ticket instead of a new one.
- [ ] Start again with an open order → private link to the existing ticket.
- [ ] Payment: the order card shows Submit gift card and Buy a gift card. Submit a card worth less than the total →
      "received … still to pay"; a second card → "Order placed" in the ticket and by DM, a payment alert in
      #staff-chat. Type a code in the ticket chat → the message disappears and you get a private note.
- [ ] In #orders as the Owner: Reveal gift card (private, spoiler-hidden codes), Payment confirmed → paid; on another
      order Card problem with a reason → the customer is told why and can submit another card. A plain Staff account
      gets "only the payment staff" for Reveal and Payment confirmed.
- [ ] Customer cannot press staff buttons; in #orders: Claim, Processing, Mark shipped (carrier, tracking number,
      optional link) → the customer gets the tracking number with Track package; Mark delivered → the ticket closes
      and the Customer role is given (on delivery by default).
- [ ] Reject with a reason and Request correction (while waiting for payment) → the customer sees the reason/request.
- [ ] Ticket closes → transcript in #transcripts with the final status; `/myorders` shows the order and timeline.
- [ ] Start a ticket, add an item, do not submit; as staff run `/ticket close` in it → the unfinished order is
      cancelled and the ticket closes (one transcript). In the ticket of a submitted open order → refused, with a link
      to its #orders record.
- [ ] With a details or submission form: #orders shows no typed answers; the record's **Details** button and
      `/order view` show them privately to staff.

**Problems, staff, admin**
- [ ] #purchase Report a Problem → choose order and type → form → private problem ticket; staff Resolve/Reject.
- [ ] #staff-chat gets new-order / paid alerts (staff pinged) and reminders once thresholds are set; the Owner gets DMs.
- [ ] `/announce` (staff) → embed in #announcements; a customer cannot use it.
- [ ] `/orders`, `/order view`, `/status`, `/content show`, `/backup`, `/audit`; #bot-log shows the actions.
- [ ] Restart the service (Railway → Restart) → every old button still works.

Record what you tested and anything unexpected. Every failure answered with the generic error text (a crash, or
an expected failure such as a ticket channel that could not be created) is logged in Railway with an error
reference (`error_id`) and alerted in #staff-chat. Staff see that reference in failed slash-command replies;
customers see it only if you include `{error_id}` in your `common.error.generic` text (e.g. "Reference:
{error_id}"), and can quote it to you.

---

### What has and has not been verified so far

- ✅ Implemented and tested locally and on GitHub Actions: 1,700+ automated tests (all features, failures, concurrency,
  restarts, banners kept/replaced on edits), type checks, lint, Discord-limit and handler-coverage checks with the
  shipped text, offline preview.
- ❌ Not yet verified by us: running in a real Discord server. In particular, check on desktop and phone that each
  banner shows **above** its message (Discord places pictures that no embed uses above the embeds; this could not be
  tested offline), and that the icons appear (the upload of application emojis was tested against a stand-in for
  Discord only). Steps 9–12 are that verification.
