# Verification (#welcome rules and the "I agree, enter the server" button)

Specification: section 2 (Member role), section 3 (📌 START HERE → `#welcome`), section 6 ("Successful
verification" notification) and section 9 ("Unverified users can access only the configured entry channels").

## What it does

- `/setup` posts **one** welcome panel in the entry channel (`verification.channel`, shipped as `welcome`) and keeps
  it up to date: it is edited in place when its text changes, re-posted if someone deleted it, moved if the channel
  setting changes, and never duplicated (record key `verification:welcome` in `published_messages`).
- The panel has two cards and one button:
  1. **Welcome card**: configured title and short introduction with the server branding — author line
     (`branding.author_name`, with the logo as icon), optional image and thumbnail (`verification.welcome.*_url`,
     otherwise the branding banner/logo when `branding.panel_banner` / `branding.panel_thumbnail` are on).
  2. **Rules card**: optional heading and introduction, then one field per rule (`verification.rule.<n>.name` as
     the heading, `.value` as the text), in order, and the branding footer (`branding.footer_text`).
  3. **"I agree, enter the server" button** (label and optional emoji from content; color from
     `verification.button_style`).
- Clicking the button gives the clicking member the **Member role** (`permissions.member_role`). Every category or
  channel with a `members_readonly` / `members_interactive` access level becomes visible immediately (those
  overwrites are set by `/setup`); `staff`/`admin` channels and other customers' tickets stay hidden because the
  Member role is never part of those overwrites (and the settings check refuses a Member role that is also a staff
  role).
- The member gets a **private (ephemeral) confirmation embed**. If `verification.dm_confirmation = true` and
  `features.dm_notifications = true`, a **direct-message confirmation** is sent as well, at most once per acceptance.
- **Repeated clicks are safe**: a member who already has the role gets a private "already verified" embed and
  nothing is changed or audited again. Concurrent clicks by the same member are stopped by the router (only one is
  processed). Because a click's role list can be older than an earlier click, a member without the role is re-read
  from Discord before the role is granted, so a second grant, audit entry or DM cannot happen.
- **Failures never crash and never show internals**: if the role does not exist, is not below the bot's highest role
  (or is managed by an integration), or Discord refuses (missing Manage Roles, unknown role, server error), the
  failure is logged (`verification_failed`), audited (`verification.failed` with the reason and a reference), and
  the member sees the configured generic error (`common.error.generic`) with the same reference in `{error_id}`.
- **Audit**: every acceptance is recorded as `verification.accepted` (actor = the member, details: role id and how
  many times this member has been granted the role). An undeliverable confirmation DM is recorded as
  `verification.dm_failed` (no channel fallback is used: the private confirmation was already shown, and posting in
  `#welcome` would be public).
- **History**: `verification_acceptances` stores one row per member (first/last acceptance, number of acceptances —
  e.g. after leaving and rejoining). The Discord role stays the source of truth; the row only keys the DM and keeps
  history.
- Staff and the Owner do not need to verify (they see the channels through their own roles), but the button works
  for them in the same way.
- **Restarts**: the button's custom ID is `sb:verify:agree` (no state in memory, nothing per message), so the panel
  keeps working after a restart or redeploy.

## Settings (`[verification]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `channel` | `"welcome"` | Entry channel key from `server.channels`. Must be visible before the rules are accepted: its effective access must be `public_readonly` or `public_interactive` (the shipped `start_here` category is `public_readonly`). |
| `rules` | `0` | **Owner decision, required.** Number of rules on the panel (1–25). Each rule *n* gets the content keys `verification.rule.<n>.name` and `.value`; they appear in `content.toml` / the checklist once this is set (run `python -m tools.checklist --template` after changing it). |
| `dm_confirmation` | `false` | Also confirm by direct message (only when `features.dm_notifications = true`; then `verification.dm.description` is required). |
| `welcome_mention_seconds` | `30` | Mention a member who joins in the entry channel (the message appears under the panel) and delete the mention this many seconds later (0 = off). Joins within 2 seconds share one message; the deletion is a durable task. Needs `bot.members_intent` and **Server Members Intent** in the Developer Portal; without them nothing is posted. Text: `verification.welcome_mention.description`. |
| `button_style` | `"success"` | Color of the button: `primary`, `secondary`, `success` or `danger`. |

Also used: `features.verification` (on/off), `permissions.member_role` (shipped `member`, defined in
`[[server.roles]]`), `branding.*`.

Settings problems that keep the feature hidden (shown by `/status` and in the `/setup` report):
`verification.channel` missing, undefined, or not public; `permissions.member_role` missing or undefined; the
Member role listed in any staff/admin role list (`permissions.admin_roles`, `permissions.staff_roles`,
`permissions.owner_role`, `tickets.staff_roles`, a ticket category's `staff_roles`, `checkout.staff_roles`,
`catalog.manager_roles`) or carrying elevated permissions (manage/moderation permissions, `mention_everyone`,
`view_audit_log`, ...); `verification.rules` not set.

## Content keys (`verification.*`)

| Key | Required | Where |
|---|---|---|
| `verification.welcome.title` | yes | Title of the welcome card |
| `verification.welcome.description` | yes | Short introduction on the welcome card (channel links allowed) |
| `verification.welcome.image_url`, `verification.welcome.thumbnail_url` | no | Images on the welcome card |
| `verification.rules.title` | no | Heading of the rules card |
| `verification.rules.description` | no | Text above the rules (channel links allowed) |
| `verification.rule.<n>.name` / `.value` | yes (per rule) | Heading and text of rule *n* (links allowed in the text) |
| `verification.button.label` | yes | The "I agree, enter the server" button |
| `verification.button.emoji` | no | Emoji on the button |
| `verification.confirmed.title` / `.description` | no / yes | Private confirmation after the role was granted |
| `verification.already_verified.title` / `.description` | no / yes | Private reply when the member already has the role |
| `verification.dm.title` / `.description` | no / yes when `dm_confirmation` and `features.dm_notifications` are on | Direct-message confirmation |

Placeholders: descriptions and rule texts accept `{channel_<key>}` for every channel in `server.channels` (for
example `{channel_catalog}`), rendered as clickable channel links once `/setup` created the channel (a link to a
channel that does not exist yet renders empty, and the `/setup` report lists it). The replies also accept
`{user_name}` (display name, Markdown-escaped) and `{user_mention}`; their titles accept `{user_name}` (Discord does
not render mentions or links in titles and field headings). The generic error used on failures is the shared
`common.error.generic` (with `{error_id}`). All keys are listed with their limits in `docs/CONTENT_CHECKLIST.md`.

Missing required text keeps the feature not ready: the panel is not posted (and an already-posted panel is
withdrawn automatically), and the button answers with `common.error.unavailable`.

## Commands

None. The panel is published by `/setup`; the `/setup` report shows one line for the panel (posted / updated /
up to date / re-posted / moved / not posted and why) plus diagnostics when the button could not grant the role:
the Member role does not exist, the bot lacks Manage Roles, or the role is not below the bot's highest role.

## Discord limits

Every value is validated against its own limit. Discord also allows at most **6000 characters per message across
all embeds** (both cards, every rule, author line and footer). If the configured text is longer, `/setup` does not
post or edit the panel and reports `exceeds Discord limits` with the exact sizes (the previously posted panel stays
unchanged); shorten the rules. Text is never truncated. `python -m tools.check_limits --all-features` checks your
real text offline.

## Manual test in Discord

1. Set `verification.rules` and fill every required `verification.*` key (and `common.error.*`), then run
   `/status` — `verification` must be ready.
2. Run `/setup`. The report shows "Verification: welcome panel posted in #welcome" and no role diagnostics.
   Run `/setup` again: "up to date", still a single panel.
3. With a test account that has no roles: only the START HERE channels are visible; `#welcome` cannot be posted in.
4. Click the button: a private confirmation appears, the account now has the Member role and the CATALOG and
   PURCHASE categories appear immediately; STAFF stays hidden. `#bot-log` shows `verification.accepted`.
5. Click again: private "already verified" reply; no second audit entry.
6. Optional: set `dm_confirmation = true`, remove the role from the test account, click again: a DM arrives once.
   With DMs closed, the click still succeeds and `#bot-log` shows `verification.dm_failed`.
7. Move the Member role above the bot's role and click with another account: generic error with a reference that
   matches the `verification.failed` entry in `#bot-log`; `/setup` reports the role problem. Move it back.
8. Restart the bot and click with a new account: it still works.

## Limitations

- One panel message: the total text must fit Discord's 6000-character message limit (see above).
- The Member role is never removed by the bot (staff remove it manually; the member can then verify again, which
  is recorded as another acceptance).
- The DM confirmation has no channel fallback (the private confirmation already informed the member).
- Discord's own Membership Screening (rules screen) is independent of this feature and not configured by the bot.
- Not yet verified on a live Discord server; behaviour is covered by automated tests with faithful fakes
  (`tests/verification/`) and the offline preview.
