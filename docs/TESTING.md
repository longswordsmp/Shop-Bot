# Testing and quality gates

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Run everything

```bash
scripts/quality.sh
```

Runs every gate below in order and stops at the first failure (non-zero exit). The same script runs on GitHub
Actions for every push and pull request (`.github/workflows/ci.yml`), so a broken commit is visible before it is
deployed.

## Gates (each exits non-zero on failure)

| Check | Command |
|---|---|
| Format | `.venv/bin/ruff format --check shopbot tools tests` |
| Lint (incl. security rules `S`, async rules `ASYNC`) | `.venv/bin/ruff check shopbot tools tests` |
| Types | `.venv/bin/mypy` |
| Unit + integration tests | `.venv/bin/pytest -q` |
| Configuration + Discord limits + handler coverage (your text) | `.venv/bin/python -m tools.check_limits --all-features` |
| Every state built and checked (synthetic text for anything not configured) | `.venv/bin/python -m tools.check_limits --all-features --fixtures --strict` |
| House style of every message (docs/QUALITY_STANDARDS.md) | `.venv/bin/python -m tools.check_quality --strict` |
| Secrets scan | `python -m tools.secret_scan` (scans tracked files for token-shaped strings and `.env` files) |
| Syntax/import check | `python -m compileall -q shopbot tools tests` |
| Generated docs current | `python -m tools.reference_docs` then no diff in `docs/` |

`tools.check_limits` exits 1 on any Discord limit violation, invalid configured value, or a component that no
handler would receive. With your real text, states whose text is not configured yet are reported as *incomplete*
(they are never shown to customers); add `--strict` to make those fail too, e.g. before going live. With
`--fixtures`, text that is not configured yet is replaced in memory by short synthetic tokens so that every state
your settings allow (~120 with the shipped settings, customer and staff) is built and checked; CI runs this so a
structural limit problem or a button without a handler cannot be merged even before any text exists. States that
need optional settings (fulfillment options, the order details form, ticket category forms) are not in the shipped
settings; `tests/checkout/test_config_preview.py` and `tests/tickets/test_config_preview_publish.py` build them
with full example settings and apply the same limit and handler checks.

Not a gate, but useful: `python -m tools.checklist --write` (content checklist) and `python -m tools.build_preview`
(HTML preview).

## What the automated tests cover

Tests run without Discord. `tests/fakes.py` provides fake roles, members, channels, guilds, and interactions that
subclass the real discord.py types (so `isinstance` checks behave as in production) and enforce Discord's
response rules (responding twice raises `InteractionResponded`). Interactions go through the real router, so
guild checks, rate limits, duplicate-click protection, feature readiness, and permission checks are exercised.
Each test gets a real SQLite database with migrations applied.

- Foundation (`tests/foundation`): environment validation, settings parsing, content rendering and exact text
  preservation, readiness, Discord limits, custom IDs, money math, transactions and rollback, migrations
  (idempotence, failure rollback, pre-migration backup, downgrade refusal), schema constraints, instance lock,
  scheduler (dedupe, retry/backoff, permanent failure, crash recovery), router protections, notifier fallback,
  log redaction, idempotent panel publishing.
- Feature suites (`tests/<feature>`): end-to-end flows, permission denial, invalid input, duplicate and
  concurrent submissions, missing content, restart recovery, scheduled tasks, Discord API failures, and limit
  validation of every preview state.

## What tests do NOT prove

Passing tests and a clean preview do not prove the bot works in Discord. Real verification requires a test
server: follow the manual checklist in `docs/MORNING.md` (step 12) after deploying.

## Local run against a real test server

1. Create a separate test application/bot and a test server (never test against production).
2. `cp .env.example .env`, fill in the test bot's token and IDs, `DB_PATH=./data/bot.db`, `LOG_FORMAT=text`.
3. `.venv/bin/python -m shopbot`
4. In the test server: `/status`, `/setup`, then exercise the workflows.
