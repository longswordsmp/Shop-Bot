# Audit logging, notification failures and entry-channel safety

Package `audit_logging` of the server specification: section 5 (`#bot-log`, `#transcripts`), section 6 (direct
message failures) and section 9 ("unverified users can access only the configured entry channels"). It adds no new
feature module, panel, button, command or content key: it extends `/setup` (`shopbot/features/setup`), `/reload`
(`shopbot/features/admin`), the notifier (`shopbot/core/notifier.py`), the audit log (`shopbot/core/audit.py`), the
operations failure handler and the tickets transcript.

| Spec item | What the bot does |
|---|---|
| #bot-log: relevant changes to permissions, settings and configuration | `setup.run` lists what `/setup` changed; `admin.reload` lists what `/reload` changed |
| #bot-log: failed operations and notification failures | `notifications.dm_failed`, `notifications.failed` (every DM and channel-post failure, once) |
| §6: if a DM fails, follow the fallback and log the failure without private data | audit entries carry the notification's internal kind and the member's ID only |
| #transcripts restricted to staff and Owner | `tickets.transcript_channel` must be a `staff`/`admin` channel; the post refuses any other |
| §9: unverified users see only the entry channels | `/setup` reports every unmanaged channel `@everyone` can see |

## #bot-log entries

### `setup.run` (every real `/setup`; dry runs are not audited)

Besides the counts (`created`, `adopted`, `updated`, `unchanged`, `skipped`, `failed`, `problems`, ...) the entry
lists **what changed**, by settings key (never names, topics or other text):

```json
{"roles": ["staff: updated (name)", "owner: created"],
 "categories": ["staff: updated (overwrites)"],
 "channels": ["welcome: adopted; updated (overwrites, topic)", "bot_log: failed (create, HTTP 403)"],
 "owner_role": {"role": "owner", "user_id": 123, "result": "assigned"},
 "public_unmanaged": 2, "created": 1, "updated": 3, ...}
```

- Entries: `created`, `recreated` (the recorded one was deleted), `adopted`, `updated (<kinds>)`, `failed (<action>,
  HTTP <status>)`. Change kinds: roles `name`, `permissions`, `color`, `hoist`, `mentionable`; categories `name`,
  `overwrites`; channels `name`, `topic`, `slowmode`, `category`, `sync` (permissions synced with the category),
  `overwrites`.
- `owner_role`: present when `/setup` gave `permissions.owner_role` to `OWNER_ID` (`assigned`) or failed to.
- At most 30 entries per section are listed (`<section>_more` counts the rest). The audit log sanitizes the details
  again (sensitive keys masked, token-shaped strings redacted).

### `admin.reload`

`{"ok": true, "errors": 0, "settings_sections_changed": ["checkout", "server"], "content_keys_changed": 4}` - the
top-level settings sections (`[checkout]`, `[server]`, ...) whose values differ after the reload, and how many content
keys (including `operator.*` overrides) were added, removed or edited. No value is ever recorded. A failed reload keeps
the running configuration, so it lists nothing.

### Notification failures

Every failure is reported exactly once, by the notifier (callers no longer report it again):

| Situation | Audit entry | Staff alert (#staff-chat) |
|---|---|---|
| The DM failed, the fallback channel (ticket/order channel) delivered it | `notifications.dm_failed` `{kind, reason}` on the member | no (common: closed DMs) |
| The DM failed, the caller has its own fallback (`alert_on_failure=False`: review request posted in the order channel, problem decision shown on the report panel) | `notifications.dm_failed` | no (the caller reports it if its own way fails) |
| Nothing delivered: DM failed and no/failed fallback | `notifications.failed` `{kind}` on the member | yes |
| A channel post failed (`notify_channel`: channel missing or a Discord error) - staff alerts, auto-close warnings, order details, review requests, ... | `notifications.failed` on the member it was for, or else on the channel | yes (member shown as `-` when the post was for staff) |
| One of operations' own alerts/reminders failed | `notifications.failed` on the channel | no (alerting about alerts could loop; the alert task retries and a permanent failure is audited as `task.failed`) |

`reason` is `dm_forbidden` or `dm_failed: <exception type>`; never the message, never Discord's error text. Failure
alerts of one kind are grouped (one per 10 minutes); every failure is audited. The `FAILURE` event now carries
`channel_id` too (identifiers only).

The **transcript DM** (`tickets.transcript_dm_user`) goes through the notifier: at most once per ticket (also after a
restart), recorded, and an undeliverable one is audited and alerted like any other notification. It is a direct
message even when `features.dm_notifications` is off (the setting asks for one explicitly) and has no fallback channel
(staff have the transcript in #transcripts).

### Audit-channel post

Long details are split over up to 4 embed fields of 1000 characters (Discord: 1024 per field, 6000 per embed); the
database keeps the full sanitized details (see `/audit`).

## #transcripts is staff-only

`tickets.transcript_channel` must resolve to a channel with `staff` or `admin` access (directly or through its
category), exactly like `audit.channel`. Otherwise the tickets feature is not ready (`/status` shows
`tickets.transcript_channel: channel '<key>' must have staff or admin access`), and the transcript post itself refuses
to post there (log `tickets_transcript_channel_not_private`); the transcript stays outstanding and is posted once the
setting is fixed. The shipped configuration uses #transcripts in 🔒 STAFF.

## Entry-channel safety (`/setup` problems section)

After reconciling, `/setup` (and `/setup dry_run:True`) lists every text, voice, stage or forum channel and every
category it does **not** manage that `@everyone` can view - for example Discord's default #general text channel and
General voice channel - with a hint to delete them or deny View Channel to `@everyone`. Up to 25 are listed one by one
(the rest are counted). Channels /setup manages (everything in `server.channels`/`server.categories`, including the
public START HERE) and private ticket/order channels are never listed. **The bot never changes or deletes a channel it
does not manage.** The count is in the `setup.run` audit entry (`public_unmanaged`) and the log
(`setup_public_unmanaged_channels`). The lines are operator text (`server.setup.public_unmanaged*`,
`server.setup.channel_kind.*`, overridable with `operator.<key>` in content.toml).

## Settings and content keys

No new settings and no new content keys. The doc of `operations.failure.notification.description` now also covers
failed channel posts (`{user_mention}` is `-` when the post was for staff).

## Testing in Discord

1. In a new server that still has #general and General: run `/setup dry_run:True` → Problems lists both (and their
   categories) with the hint; nothing is audited. Run `/setup` → the same lines; #bot-log shows `setup.run` with the
   created/updated keys and `public_unmanaged`. Delete #general and General, run `/setup` again → no such lines, and
   the new `setup.run` entry lists no changes.
2. Edit #staff-chat's topic or permissions by hand, run `/setup` → `setup.run` lists `staff_chat: updated (...)`.
3. Change a value in settings.toml and a text in content.toml, push, `/reload` → `admin.reload` lists the section and
   `content_keys_changed`.
4. With a test account that has DMs from server members disabled, trigger a notification with a fallback channel
   (e.g. an order status change) → it appears in the order channel and #bot-log shows `notifications.dm_failed`;
   no #staff-chat alert.
5. Remove the bot's Send Messages permission in #staff-chat temporarily and open a ticket → #bot-log shows
   `notifications.failed` for `tickets.staff_alert` (target: the channel). Restore the permission.
6. Set `tickets.transcript_dm_user = true`, close a ticket of an account with DMs closed → the transcript is in
   #transcripts, #bot-log shows `notifications.failed` (`tickets.transcript_dm`) and #staff-chat a notification alert.
7. Point `tickets.transcript_channel` at a public channel and `/reload` → `/status` shows the problem; tickets are not
   ready. Restore it.

## Limitations

- Unmanaged public channels are only reported, never fixed: Discord requires some channels in Community servers (rules,
  community updates); choose #welcome as the rules channel or hide the others from `@everyone`.
- Each failed attempt of a retried post (e.g. an operations alert task) is audited separately.
- A verification DM failure is audited twice (`verification.dm_failed` by the verification feature and
  `notifications.failed`), because they record different facts; only one staff alert is sent.
- Not yet verified in a live Discord server (see `docs/MORNING.md`).
