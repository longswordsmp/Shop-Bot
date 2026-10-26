# Deployment (Railway worker)

The bot is a single long-running Python worker. It needs no web server and listens on no port.

## Moving parts

| Stage | Where | How you verify it |
|---|---|---|
| Source | GitHub repository | `git log origin/<branch>` shows the commit |
| Build | Railway (Railpack builder) | Railway build log shows `pip install -r requirements.txt` succeeded |
| Runtime | Railway service, one replica | Deploy log shows `starting` then `db_ready` then `bot_ready` |
| Discord connection | Discord gateway | `bot_ready` log line; the bot shows online in the server |
| Features | Your server | `/status` in Discord, then the manual tests in `docs/MORNING.md` |

These are separate stages: a successful push does not prove a deployment, and a running deployment does not
prove that a feature works in Discord.

## Files Railway uses

- `requirements.txt` – runtime dependencies (pinned).
- `.python-version` – `3.12` (Railpack reads it; alternatively set the `RAILPACK_PYTHON_VERSION` variable).
- `railway.json` – builder `RAILPACK`, start command `python -m shopbot`, restart policy `ON_FAILURE` (10 retries).

**Important – Railway config-as-code is deprecated.** Railway documents `railway.json`/`railway.toml` as
deprecated in favour of "Infrastructure as Code" (`.railway/railway.ts`, applied with the Railway CLI), with
existing services supported until 2026-12-01 and new services not reading these files. If your service does not
pick up `railway.json`, set the same values in the Railway dashboard (service → Settings):

| Setting | Value |
|---|---|
| Builder | Railpack (default) |
| Start command | `python -m shopbot` |
| Restart policy | On Failure (10 retries) |
| Replicas | 1 (never more – see "Single instance") |
| Volume | mounted at `/data` |

No `.railway/railway.ts` is shipped because its exact format could not be verified for this project; the
dashboard settings above are equivalent.

## Variables (Railway → service → Variables)

| Variable | Required | Value |
|---|---|---|
| `DISCORD_TOKEN` | yes | Bot token from the Developer Portal (Bot tab). Secret. |
| `GUILD_ID` | yes | Numeric ID of your server. |
| `OWNER_ID` | recommended | Your numeric user ID (always treated as admin). |
| `DB_PATH` | yes | `/data/bot.db` (on the persistent volume). |
| `LOG_FORMAT` | no | `json` (default) for Railway's log view. |
| `LOG_LEVEL` | no | `INFO` (default). |
| `APP_ENV` | no | `production` (default). |
| `CONFIG_DIR` | no | Defaults to `./config` inside the deployed source. |
| `STARTUP_FAILURE_DELAY_SECONDS` | no | Delay before exiting on a fatal startup error (default 30) to avoid tight restart loops. |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | recommended | `15` – time Railway waits between `SIGTERM` and `SIGKILL`, so clicks being processed can finish (see "Stopping"). Railway's default is short (reported as 0–3 s). |
| `ALLOW_EPHEMERAL_DB` | no | Leave unset. `true` lets the bot start on Railway without a volume – only for a throwaway test service. |
| `GIFT_CARD_KEY` | recommended | Encrypts submitted gift card codes. Generate once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep it secret. Unset: the bot creates `gift_card.key` next to the database on the volume. Changing or losing it makes codes not yet redeemed unreadable. |

Never put these values in the repository. `.env` is for local development only and is git-ignored.

## Persistent storage

- Add a volume to the service and mount it at `/data`. Set `DB_PATH=/data/bot.db`.
- On Railway the bot **refuses to start** (`env_invalid`, exit code 78) when no volume is attached or `DB_PATH` is
  outside the volume (it compares with `RAILWAY_VOLUME_MOUNT_PATH`, which Railway sets when a volume is attached).
  Without this check every redeploy would silently start with an empty database and lose all orders.
- The bot creates the directory and database on first start, applies migrations, and never recreates a
  populated database. Before applying a new migration to a database that holds data, it writes a copy next to it
  (`bot.db.pre-vNNNN-<timestamp>.bak`).
- If the database reports a schema version newer than the code (a rollback to older code), the bot refuses to
  start instead of risking data loss. Deploy the newer code again or restore a matching backup.
- A volume is **not** a backup. Use `/backup` regularly (writes a consistent copy to `/data/backups/`, keeps the
  newest 10) and copy backups off the volume (see "Backups").

## Single instance

Only one bot process may use a database. Railway does not overlap deployments of a service with a volume, and
the bot also takes an exclusive lock (`/data/bot.db.lock`): a second process waits up to two minutes and then
exits. Keep replicas at 1.

## Startup sequence (what the logs show)

1. Environment validation – a missing/invalid variable logs `env_invalid` with the variable name only (and the
   process exits with code 78 after a short delay).
2. `starting` – version, Python and discord.py versions (never the token).
3. `instance_lock_acquired`.
4. Configuration load – invalid settings log `config_invalid` with the exact setting path.
5. `db_opened`, `db_migration_applied` (when needed), `db_ready`.
6. `feature_not_ready` warnings for enabled features that still miss content or settings.
7. `routes_registered`, `commands_synced`.
8. `bot_ready` – lists ready and not-ready features.

## Stopping

On `SIGTERM` (redeploy/stop) the bot stops accepting new clicks (they get your "unavailable" text), waits up to
10 seconds for clicks already being processed to finish (`router_draining`, or `router_drain_timeout` if they did
not), stops the scheduler, closes the Discord connection, checkpoints and closes the database, releases the lock,
and exits with code 0. Railway sends `SIGKILL` after `RAILWAY_DEPLOYMENT_DRAINING_SECONDS`, so set it to 15.

If the process is killed anyway (crash, `SIGKILL`), no data is half-written: every database change is a single
transaction, and the startup recovery finishes or rolls back what was in progress (tickets being created,
scheduled tasks being run). The customer of a click that was cut off may see Discord's "interaction failed" and
can click again.

## Updating

1. Run the quality gates locally (see `docs/TESTING.md`).
2. Push to the branch Railway deploys.
3. Watch the deploy log for `bot_ready`. Run `/status`.
4. If the update changed panels or text, run `/setup` (idempotent) to republish panels.

## Rollback

Redeploy the previous successful deployment from Railway's deployment list. A rollback across a schema
migration is refused by design (see above); restore the pre-migration backup only if you accept losing the
data written since.

## Backups

- In Discord: `/backup` (admin) creates `/data/backups/bot-<timestamp>.db` (the newest 10 are kept).
- `/reset` first writes `/data/backups/before-reset-<timestamp>.db`; the `/backup` rotation never removes these, so
  delete old ones yourself when you no longer need them.
- To copy a backup off Railway, use the Railway CLI or a temporary shell on the service and download the file;
  never post database files in Discord (they contain private customer data).
- To restore: stop the service, replace `/data/bot.db` with the backup file (and delete `bot.db-wal`/`bot.db-shm`
  if present), start the service.

## Troubleshooting

| Symptom / log event | Cause | Fix |
|---|---|---|
| `env_invalid` | Variable missing or malformed | Set it in Railway Variables (the log names it). |
| `env_invalid` "DB_PATH is not on persistent storage" / "not inside the Railway volume" | No volume, or `DB_PATH` outside it | Attach a volume at `/data` and set `DB_PATH=/data/bot.db`. |
| `discord_login_failed` | Token wrong or reset | Reset the token in the Developer Portal and update `DISCORD_TOKEN`. |
| `portal_intents_unknown` (warning) | The Developer Portal switches could not be read before connecting (timeout after 15 s, Discord error); the bot connects as configured | Nothing, unless `privileged_intents_required` follows: then switch the intents on in the Developer Portal. |
| `welcome_mention_off` (warning) | `verification.welcome_mention_seconds` is set but the Server Members intent is off | Set `bot.members_intent = true` and switch on Server Members Intent in the Developer Portal, then restart. |
| `privileged_intent_not_enabled` (warning) | An intent in `[bot]` is not switched on in the Developer Portal; the bot runs without it | Developer Portal → Bot → Privileged Gateway Intents: switch on the named intent, then restart. |
| `privileged_intents_required` | Discord refused an intent (the portal could not be read before connecting) | Developer Portal → Bot → enable Message Content Intent and Server Members Intent (or switch them off in `[bot]`). |
| `guild_not_available` | Bot not in the server | Invite it with `python -m tools.invite_url <APPLICATION_ID>`. |
| `commands_sync_failed` | Bot invited without `applications.commands`, or wrong `GUILD_ID` | Re-invite with the generated URL; check `GUILD_ID`. |
| `config_invalid` | Typo or wrong type in settings.toml | Fix the path named in the log. |
| `db_init_failed` | Volume missing / read-only / downgrade | Check the `/data` volume and `DB_PATH`. |
| `instance_locked` | Another process uses the database | Ensure replicas = 1; wait for the old deployment to stop. |
| `server_reset` (warning) | The owner ran `/reset` (counts of what was deleted, the backup file) | Run `/setup` in the 🛠・setup channel it created. |
| `reset_delete_failed` | `/reset` could not delete a channel or role (HTTP status and Discord code in the log) | Delete it by hand, or give the bot Manage Channels / move its role higher, and run `/reset` again. |
| `reset_dm_failed` / `reset_backup_failed` / `reset_setup_channel_failed` | `/reset` stopped before deleting anything (DMs closed, the backup could not be written, or the private setup channel was refused) | Allow DMs from server members / check the volume / give the bot Manage Channels, then run `/reset` again. |
| `reset_interrupted` (warning, at startup) | The bot restarted while `/reset` was deleting; the owner gets a DM | Run `/reset` again to delete what is left, then `/setup`. |
| Buttons answer "unavailable" | Feature not ready | Run `/status`, fill missing content, `/reload` or `/content set`, then `/setup`. |
| `/setup` reports missing permissions | Bot role lacks permissions or is too low | Give the bot role the permissions in the invite URL and drag it above managed roles. |
| `/setup` problems: "not managed by /setup, and `@everyone` can see it" (log `setup_public_unmanaged_channels`) | A channel/category the bot does not manage (usually Discord's default #general and General voice) is visible to users who have not accepted the rules | Delete it, or deny View Channel to `@everyone` on it, then run `/setup dry_run:True` again. The bot never changes channels it does not manage. |
| `tickets.transcript_channel ... must have staff or admin access` (`/status`), log `tickets_transcript_channel_not_private` | The transcript channel is visible to members | Point `tickets.transcript_channel` at a channel in a `staff`/`admin` category (shipped: #transcripts in STAFF); outstanding transcripts are posted once it is fixed. |
| #bot-log shows `notifications.dm_failed` | A member has DMs closed; the notification was delivered in their ticket/order channel (or another way) | Nothing to do (common). |
| #bot-log shows `notifications.failed` and #staff-chat a notification alert | A notification or bot post could not be delivered at all (DM closed and no fallback, channel deleted, missing permissions) | Check the channel named in the audit entry (target) and the bot's permissions there; contact the member if needed. |
