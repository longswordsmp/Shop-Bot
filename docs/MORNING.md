# Morning checklist — from code to a working server

Do these in order. Each step says what to do, where, and how to check it worked. Nothing here requires changing
code. Expected time: about an hour, plus however long it takes to write your text.

---

## 1. Create the Discord application and bot

1. Open <https://discord.com/developers/applications> → **New Application** → name it → **Create**.
2. **General Information**: copy the **Application ID** (you need it in step 5). It is not secret.
3. **Bot** tab:
   - **Reset Token** → copy the token. This is a secret: never paste it into code, Discord, or chat.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent** (needed for ticket transcripts to
     contain message text). Leave **Server Members Intent** off (not needed).
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

Check: Railway shows the four variables (values hidden).

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
Keep `DB_PATH=/data/bot.db`. Without a volume, every redeploy would start with an empty database.

## 9. Deploy

Railway builds with Railpack (Python 3.12 from `.python-version`, dependencies from `requirements.txt`) and runs
`python -m shopbot` (from `railway.json`).

If the service does not pick up `railway.json` (Railway has deprecated config files for new services), set in the
service **Settings**: Start command `python -m shopbot`, Restart policy **On Failure**, **1** replica.

Check the deploy logs for, in order: `starting` → `instance_lock_acquired` → `db_ready` → `commands_synced` →
`bot_ready`. The bot should show online. `feature_not_ready` warnings are expected until you add text.
See `docs/DEPLOYMENT.md` → Troubleshooting if anything else appears.

## 10. Run `/setup`

In your server, as the owner/admin: **`/setup dry_run:True`** first. It lists what it would create.

The structure is defined in `config/settings.toml` (`[[server.roles]]`, `[[server.categories]]`,
`[[server.channels]]`). Each needs a name — set them in step 11 (e.g. `server.channel.ticket_panel.name`), or set
`id = <existing id>` in settings.toml to reuse a channel/role you already have.

Then **`/setup`**. It creates or repairs roles, categories, channels, and permissions, and publishes the panels of
every feature that is ready. It is safe to run again any time; it never duplicates or deletes anything except
withdrawing panels of features you have switched off.

## 11. Enter your business configuration and exact text

**Decisions in `config/settings.toml`** (edit on GitHub or locally, commit, push → Railway redeploys):

- `[features]` — switch on what you use.
- `checkout.currency` (e.g. `"USD"`), `payments.provider = "manual"` if you sell.
- Ticket categories (`[[tickets.categories]]`), fulfillment options, fees, forms, timers, review timing and scale —
  only if you want them. Every timer is off until you set it. Reference: `docs/CONFIGURATION.md`.

**Text in `config/content.toml`** — every key is listed there with what it is for, its Discord length limit, and
its placeholders. Fill in your exact wording. Or set text from Discord without redeploying: **`/content set key:…`**
(opens a form with the current value). Useful commands:

- **`/status`** — what is ready and what is missing, per feature.
- **`/content missing`** — the exact keys still empty.
- **`/content export`** — download everything entered so far as a `content.toml` (commit it to keep it).
- **`/reload`** — re-read the files after a change without restarting.

Locally you can also run `python -m tools.checklist --write` (writes `docs/CONTENT_CHECKLIST.md`),
`python -m tools.check_limits --strict` (fails if anything is missing or too long) and
`python -m tools.build_preview` (open `preview/out/index.html` to see every screen with your text).

When `/status` shows the features you want as ready, run **`/setup`** again to publish their panels.

## 12. Test the enabled workflows in Discord

Use a second (non-staff) Discord account as the "customer".

**Server & panels**
- [ ] `/setup` twice: the second run reports nothing new to create.
- [ ] Panels appear only for ready features; texts match exactly what you wrote.

**Tickets**
- [ ] Customer opens a ticket from the panel → private channel appears; only they and staff can see it.
- [ ] Opening a second ticket in the same category is refused with your limit text.
- [ ] Customer clicks Claim → refused (permission text). Staff Claim/Unclaim works.
- [ ] Close (with confirmation) → closed message; customer can no longer post; Reopen restores access.
- [ ] Transcript appears in the transcripts channel; Delete removes the channel only after the transcript.
- [ ] Restart the service (Railway → Restart) → old ticket buttons still work.

**Catalog**
- [ ] `/product add` (staff) → product appears in the listing channel; `/product stock` updates it live.
- [ ] Browse button shows products page by page.

**Checkout** (if enabled)
- [ ] Start → private order channel with one workflow message; add items, change quantities, review totals.
- [ ] Submit → payment instructions (your text); staff message appears in the order queue channel.
- [ ] Customer cannot press staff buttons; staff Mark paid → customer notified; stock decreases.
- [ ] Fulfill → order channel closes; (reviews on) the customer gets the review prompt.
- [ ] Two customers try to buy the last unit at once → only one succeeds.
- [ ] `/order refund` records a refund; `/order lookup` shows the history.

**Credits / Reviews** (if enabled)
- [ ] `/credits adjust` (admin) → balance button shows the new balance; credit applies at checkout.
- [ ] Review: rate + comment → appears in the reviews channel; `/review remove` deletes it.

**Admin**
- [ ] `/status`, `/content show`, `/backup` (creates a file on the volume), `/audit` (shows your actions).

Record what you tested and anything unexpected; logs in Railway include an error reference (`error_id`) that
matches the bot's reply for any failure.

---

### What has and has not been verified so far

- ✅ Implemented and tested locally: 780+ automated tests (all features, failures, concurrency, restarts),
  type checks, lint, Discord-limit and handler-coverage checks, offline preview.
- ❌ Not yet verified: running on Railway and in a real Discord server (no credentials were available while it was
  built). Steps 9–12 are that verification.
