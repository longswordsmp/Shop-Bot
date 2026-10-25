# New arrivals (`#new-arrivals`, Restock Alerts role)

Feature name `new_arrivals`, switched on by `features.new_arrivals`. Depends on the `catalog` feature (it announces
catalog products and writes their price and availability with the catalog's own texts).

## What it does

**Notification panel.** `/setup` posts one persistent embed in the new-arrivals channel with a single button. A click
gives the member the notification role (Restock Alerts) when they do not have it, and removes it when they do. The
member gets a private confirmation ("now subscribed" / "now unsubscribed"). The panel is kept as exactly one message
(`published_messages` key `new_arrivals:panel`): later `/setup` runs edit it in place when its text changed, re-post
it if it was deleted, and otherwise leave it untouched. While the feature is not ready the panel is withdrawn
automatically.

**Automatic product updates.** When staff change a product (`/product ...`), the catalog publishes
`PRODUCT_CHANGED`. A change qualifies when the product is listed (active and not archived) and

| Update | Catalog change | Setting |
|---|---|---|
| New product | `/product add`, or `/product toggle` switching an inactive product back on | `announce_new_products` |
| Restock | `/product stock` or `/product edit` that makes a sold-out product (limited stock, nothing left to order) orderable again (units available, or unlimited stock) | `announce_restocks` |

Price, image, position and archive changes, stock changes of products that were not sold out, and changes to
products that are switched off never qualify. Each qualifying change schedules the durable task
`new_arrivals.announce` (dedupe key `new_arrivals.announce:<product>:<new|restock>:<product version>`), so a pending
update survives a restart. The task re-reads the product and posts one embed in the new-arrivals channel with the
configured title (`{name}` = product name), the product's description, image, price and availability (exactly as on
its catalog card), an optional footer and an optional link button to the catalog channel. It mentions the
notification role only when `mention_role = true` and the role exists; `allowed_mentions` allows that one role and
nothing else.

**Never twice.** Every update is recorded in `new_arrival_announcements` (UNIQUE per product, update kind and product
version). The task claims the row before posting and records the message afterwards; a second run finds the record
and posts nothing. If the process stopped between posting and recording (or Discord created the message but its
answer was lost), the next attempt first looks through the bot's recent messages in the channel for that post and
records it instead of posting again. Rerunning `/setup` never posts updates and never adds or removes anyone's role.

## Settings (`[new_arrivals]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `channel` | `"new_arrivals"` | Channel key (from `[[server.channels]]`) of the panel and the updates. Must not be a staff/admin-only channel. |
| `role` | `"restock_alerts"` | Role key (from `[[server.roles]]`) the button adds and removes. |
| `announce_new_products` | `true` | Post an update for new products / products switched back on. |
| `announce_restocks` | `true` | Post an update when a sold-out product can be ordered again. |
| `mention_role` | `true` | Mention the role in updates. |

Checks that keep the feature disabled (listed by `/status`): `channel` and `role` set and defined in `server.*`; at
least one `announce_*` on; the channel visible to members; the role is not an owner/staff/member/manager role and grants
no permissions (members give it to themselves); with `mention_role = true` the role must be `mentionable = true` (the
shipped `restock_alerts` role is).

## Content keys (`config/content.toml`, prefix `new_arrivals.`)

| Key | Required | Shown |
|---|---|---|
| `panel.title`, `panel.description` | yes | The panel embed. The description may use `{role}` and `{channel_<key>}`. |
| `button.toggle` | yes | The panel's on/off button label. |
| `button.toggle_emoji` | no | Emoji on the button. |
| `subscribed.description` / `unsubscribed.description` | yes | Private confirmations (`{role}`, `{channel_<key>}`). |
| `subscribed.title` / `unsubscribed.title` | no | Titles of the confirmations. |
| `new_product.title` | when `announce_new_products` | Title of a new-product update (`{name}`). |
| `restock.title` | when `announce_restocks` | Title of a restock update (`{name}`). |
| `new_product.footer` / `restock.footer` | no | Footer text of the updates. |
| `button.view_catalog` | no | Link button under updates opening the catalog listing channel. |

Shared keys (owned by other features, required here): `common.money_format`, `catalog.field.price`,
`catalog.field.availability`, `catalog.availability.in_stock` / `.unlimited` / `.out_of_stock`. Errors use the
`common.error.*` texts (generic error with `{error_id}`, unavailable, busy).

## Commands and components

No slash commands. One routed button, `sb:na:toggle` (route `na`, access: anyone who can see the panel). It carries no
state: the handler reads the member's roles from the click and the role from the current settings, so it keeps
working after restarts and `/reload`.

Audit events: `new_arrivals.subscribed`, `new_arrivals.unsubscribed`, `new_arrivals.toggle_failed` (reason
`role_missing`, `role_not_manageable`, `forbidden`, `discord_error`, with the `error_id` the member was shown),
`new_arrivals.announced` (update kind, product version, message id, whether the role was mentioned). A task that
keeps failing is recorded as `task.failed` by the scheduler.

## Testing it in Discord

1. Fill the keys above (and the catalog's keys), keep `features.new_arrivals = true`, run `/setup`. The report shows
   `New arrivals panel: posted in #new-arrivals`; run `/setup` again: `up to date`, still one panel.
2. As a member: click the button → private "subscribed" confirmation, the member has Restock Alerts. Click again →
   "unsubscribed", the role is gone. Click twice quickly → the role is given once.
3. Move the bot's role below Restock Alerts and click → the generic error with a reference; `#bot-log` shows
   `new_arrivals.toggle_failed`. Move the role back.
4. `/product add` a product with stock → an update appears in #new-arrivals, pinging Restock Alerts (members who turned
   notifications off are not pinged).
5. `/product stock` set 0, then set 5 → one restock update. `/product price` / `image` / `sort` → no update.
6. Set `mention_role = false`, `/reload`, add a product → the update appears without a ping.
7. Run `/setup` again → no update is posted and nobody's roles change.

## Limitations

- Restocks are detected for staff stock changes (`/product stock`, `/product edit`). Units that become orderable
  because a pending order was cancelled or expired are not announced (they are not a restock).
- A product added with zero stock is announced as new (shown as sold out); its first stock later is announced as a
  restock.
- Each qualifying change is announced once per product version, so switching a product off and on again announces it
  again.
- A restock update whose product is sold out again by the time the task runs (normally seconds later) is skipped.
- The post-recovery search compares the update's title and description with the bot's last 50 messages in the
  channel; if the product's name or description changed between the interrupted attempt and the retry, a second copy
  can be posted (only after a crash at that exact moment).
- Product images use the product's `image_url` (the catalog's uploaded-image support is wired in by the integrator
  through `views.product_image_url`).
- Not yet verified against a live Discord server; behaviour is verified with the automated tests and the offline
  preview.
