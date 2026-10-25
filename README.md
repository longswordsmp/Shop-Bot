# Shop-Bot

A configurable Discord bot for running a business in a Discord server: support tickets, a product catalog,
checkout with orders and staff payment confirmation, store credit, reviews, information panels, and server setup.

**The bot contains no business content.** Every word customers see comes from your configuration
(`config/content.toml` or `/content set`); every business decision (currency, payment method, fees, timers,
policies) is a setting you choose. Features stay hidden until they are enabled and fully configured.

## Status

| Stage | State |
|---|---|
| Implemented | All features below, with persistence, permissions, recovery, and automation |
| Tested locally | 780+ automated tests, type checks, lint, Discord-limit and handler-coverage checks, offline preview |
| Deployed to Railway | Not yet — see `docs/MORNING.md` |
| Tested in Discord | Not yet — see the checklist in `docs/MORNING.md` step 12 |

## Features

- **Server setup** — `/setup` creates or repairs roles, categories, channels, permissions, and panels; idempotent.
- **Tickets** — panel, categories with optional forms, private channels, claim/close/reopen/delete, transcripts,
  limits and cooldowns, inactivity automation, restart recovery.
- **Catalog** — product management, inventory with reservations, live listing, private browsing.
- **Checkout** — private order channel with one editable workflow message, fulfillment options, forms, fees,
  store credit, atomic submit with no overselling, staff order controls, refunds, timers.
- **Store credit**, **reviews**, **information panels**, and **admin tools** (`/status`, `/content`, `/reload`,
  `/backup`, `/audit`, `/tasks`).

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
