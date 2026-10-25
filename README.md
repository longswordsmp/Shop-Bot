# Shop-Bot

A configurable Discord bot for running a business in a Discord server: support tickets, a product catalog,
checkout with orders and staff payment confirmation, store credit, reviews, information panels, and server setup.

**The code contains no business content.** Every word customers see comes from configuration: `config/content.toml`
ships with a complete starting text pack, and `config/settings.toml` with a starting set of business decisions
(USD, manual payment, shipping regions, timers), so `/setup` posts every panel right away. Change any text from
Discord (right-click a bot message → Apps → **Edit text**, or `/content set`) or in the files. Each kind of message
shows its banner picture from `config/banners/`.

## Status

| Stage | State |
|---|---|
| Implemented | The owner's server specification (layout, roles, every channel's messages, tickets, staff tools) |
| Tested locally | 1,700+ automated tests, type checks, lint, Discord-limit and handler-coverage checks, offline preview |
| Deployed to Railway | Not verified from here — see `docs/MORNING.md` |
| Tested in Discord | Not yet — see the checklist in `docs/MORNING.md` step 12 |

## Server layout (created by `/setup`)

| Category | Channels | Who sees it |
|---|---|---|
| 📌 START HERE | #welcome, #how-to-buy, #faq, #shipping, #announcements | everyone (also before accepting the rules); only staff/bot post |
| 📦 CATALOG | #catalog, #new-arrivals, #reviews | Member role (after "I agree") |
| 🛒 PURCHASE | #purchase (+ private ticket/order channels) | Member role; each ticket only its customer + staff |
| 🔒 STAFF | #orders, #staff-chat, #bot-log, #transcripts | Staff and Owner |

Roles: **Owner** (given to `OWNER_ID`), **Staff**, **Member** (the #welcome "I agree" button), **Customer**
(given automatically after a qualifying order), **Restock Alerts** (members turn it on/off in #new-arrivals).

## Features

- **Server setup** — `/setup` creates or repairs roles, categories, channels, permissions and every panel; idempotent.
- **#welcome** — branding, introduction, rules and an "I agree, enter the server" button that grants Member.
- **#how-to-buy / #faq / #shipping** — step guide with channel links; FAQ sections split across embeds as needed;
  regions grouped by fully/partially/not supported.
- **#announcements** — `/announce` (staff) with title, message, configured fields, image and optional role ping.
- **#catalog** — one live embed per item with a Buy button (disabled when sold out), availability colors, uploaded
  images that stay attached; `/product add|edit|remove|list|...`.
- **#new-arrivals** — Restock Alerts opt-in button and automatic new-item / back-in-stock posts (each once).
- **#purchase** — Start (private order ticket: one live panel with reference, steps, status, timeline) and Report a
  Problem (private problem ticket with staff Resolve/Reject).
- **#orders** — one live record per order: Claim, in progress, completed, tracking, notes, correction requests,
  reject with reason, cancel, details; **#staff-chat** alerts and reminders; **#bot-log** audit; **#transcripts**.
- **Customers** — `/myorders`, DMs for every status change (with channel fallback), reviews after completion.
- **Admin tools** — `/status`, `/content`, `/reload`, `/backup`, `/audit`, `/tasks`, `/orders`, `/order view`.

## Quick start (local)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # fill in DISCORD_TOKEN, GUILD_ID, OWNER_ID, DB_PATH
.venv/bin/python -m shopbot
```

Production runs on Railway as a worker with a persistent volume at `/data` — see `docs/DEPLOYMENT.md`.

## Documentation

| Document | Contents |
|---|---|
| `docs/MORNING.md` | Step-by-step setup: Discord app, secrets, IDs, invite, Railway, `/setup`, content, testing |
| `docs/CONFIGURATION.md` | Every setting |
| `docs/CONTENT_CHECKLIST.md` | Every customer-facing text key, by feature (generated) |
| `docs/COMMANDS.md` | Slash commands (generated) |
| `docs/COMPONENTS.md` | Every button/menu/form route and handler (generated) |
| `docs/WORKFLOWS.md` | How tickets, checkout, credits, reviews, panels behave |
| `docs/features/*.md` | One page per specification feature (behaviour, settings, text keys, manual test steps) |
| `docs/DATABASE.md` | Database schema (generated) |
| `docs/DEPLOYMENT.md` | Railway, volume, variables, updates, backups, troubleshooting |
| `docs/TESTING.md` | Quality gates and what tests do and do not prove |
| `docs/SECURITY.md` | Secrets, authorization, private data, payments |
| `docs/DECISIONS.md` | Architecture, technical decisions, assumptions, limitations |

## Tools

```bash
python -m tools.check_limits --all-features   # Discord limits, invalid text, buttons without handlers
python -m tools.build_preview                 # local HTML preview of every screen (preview/out/index.html)
python -m tools.checklist --write             # docs/CONTENT_CHECKLIST.md
python -m tools.checklist --template          # refresh config/content.toml keys (keeps your text)
python -m tools.reference_docs                # regenerate generated docs
python -m tools.invite_url <APPLICATION_ID>   # invite link with the required permissions
python -m tools.secret_scan                   # check tracked files for secrets
```
