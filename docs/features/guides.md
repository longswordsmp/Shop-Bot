# START HERE guides: #how-to-buy, #faq, #shipping

Three independent features (package `shopbot/features/guides/`) that publish informational panels into the
START HERE channels. Each has its own feature flag, settings section, content keys and published messages, so one
guide that is not finished never blocks the others.

| Feature | Flag | Settings | Channel (shipped) | Published message keys |
|---|---|---|---|---|
| `how_to_buy` | `features.how_to_buy` | `[how_to_buy]` | `how_to_buy` | `how_to_buy:0`, `how_to_buy:1`, ... |
| `faq` | `features.faq` | `[faq]` + `[[faq.sections]]` | `faq` | `faq:0`, `faq:1`, ... |
| `shipping` | `features.shipping` | `[shipping]` + `[[shipping.regions]]` | `shipping` | `shipping:0`, ... |

## What customers see

- **#how-to-buy** — one embed: the configured title and introduction, then one field per step of the customer
  workflow (finding items, starting a private ticket, completing the steps, checking the result — you write the
  steps), and a row of **link buttons** that open the configured channels (shipped: `#catalog`, `#purchase`,
  `#faq`).
- **#faq** — an optional header (title and/or introduction), then **one embed per section** (e.g. general
  questions, transactions, order status, cancellations, problems) with its questions as fields.
- **#shipping** — a header (title, introduction), then **one card per availability status** — fully supported
  (green/success color), partially supported (warning color), unsupported (danger color) — listing its regions,
  each line written with the status indicator, plus optional information sections (delivery or fulfillment
  options, extra location information) before or after the region list. Nothing is built in: statuses, regions,
  names, notes and information all come from your configuration.

**Clickable channel links:** introductions, step texts, answers, status explanations, region notes and
information texts may contain `{channel_<key>}` (for example `{channel_catalog}`), which becomes a clickable
`#channel` link to the channel /setup created for that key. Discord does not render links in titles, field
headings, questions, region names or button labels, so those keys do not accept them: a `{channel_<key>}` written
there is reported by `/status` (it would otherwise reach customers as raw text) and the guide stays hidden until
it is removed.

**Long content is never cut.** A guide that does not fit one embed continues in the next embed (25 fields,
4096-character descriptions, 6000 characters per embed), and a message holds at most 10 embeds and 6000
characters, then further messages follow. A section that continues in another embed does not repeat its title.
The branding (author line, footer, panel thumbnail/banner from `[branding]`) appears once, on the first embed.
Link buttons are attached to the last message of the how-to-buy guide. Panels carry no timestamp, so running
/setup again with unchanged text changes nothing.

## Settings

```toml
[how_to_buy]
channel = "how_to_buy"                    # channel key from [[server.channels]]
steps = 4                                 # 1-25 numbered steps
link_channels = ["catalog", "purchase", "faq"]   # channel keys shown as link buttons (max 25, in order)

[faq]
channel = "faq"
[[faq.sections]]                          # one per topic, in display order (max 25)
id = "general"                            # internal key, never shown
questions = 1                             # 1-25

[shipping]
channel = "shipping"
statuses = ["full", "partial", "none"]    # enabled availability categories, in display order
info_sections = 0                         # 0-25 information fields
info_position = "after"                   # "before" or "after" the region list
[[shipping.regions]]                      # one per region (max 500); none are shipped
id = "region_a"                           # internal key, never shown
status = "full"                           # must be listed in `statuses`
```

Checks that keep a guide hidden (listed by `/status` and the /setup report): its channel is not set or not
defined in `server.channels`; a link channel is not defined, or is a staff/admin channel customers cannot open;
the FAQ has no sections; a region uses a status that is not enabled; a region line format lacks a required
placeholder; a region has a note but `shipping.format.region_with_note` is empty; a channel link is written where
Discord cannot show it. Wrong types, out-of-range numbers, unknown
statuses, duplicate ids and unknown settings are configuration errors (the bot refuses to load the file).

## Content keys

| Keys | Kind | Required |
|---|---|---|
| `how_to_buy.title`, `how_to_buy.introduction` | title, description | required |
| `how_to_buy.step.<n>.name` / `.value` (n = 1..steps) | field name, field value | required |
| `how_to_buy.link.<channel>.label` / `.emoji` | button label, emoji | label required, emoji optional |
| `faq.title`, `faq.introduction` | title, description | optional |
| `faq.section.<id>.title` | title | required |
| `faq.section.<id>.q.<n>.question` / `.answer` | field name, field value | required |
| `shipping.title`, `shipping.introduction` | title, description | required |
| `shipping.status.<status>.label` (placeholder `{indicator}`) / `.indicator` | title, emoji | required for every enabled status |
| `shipping.status.<status>.description` | description | optional (a status card with no regions is shown only when it has one) |
| `shipping.format.region` (`{indicator}`, `{name}`) | format | required once regions exist; must contain both placeholders |
| `shipping.format.region_with_note` (`{indicator}`, `{name}`, `{note}`) | format | required once any region has a note |
| `shipping.region.<id>.name` / `.note` | short text (256) | name required, note optional |
| `shipping.info.<n>.name` / `.value` (n = 1..info_sections) | field name, field value | required |

The region line formats are content keys because their separators are visible text (for example
`{indicator} {name}` and `{indicator} {name} ({note})`). The full list with descriptions is generated in
`config/content.toml` (`python -m tools.checklist --template`).

## Commands

No commands of their own. `/setup` publishes the guides of every ready feature (and withdraws the guides of
features that are disabled or not fully configured); `/status` lists what is missing; `/content set` changes a
text (run `/setup` afterwards to update the published messages).

## Publishing behaviour

- Idempotent: each message is recorded (`published_messages`) and edited in place when its text changed;
  unchanged messages are not touched.
- Order: if a message was deleted in Discord (or the guide moved to another channel), it is posted again and the
  messages after it are posted again too, so the guide stays in order.
- A shorter configuration removes the surplus messages of the earlier, longer one.
- Nothing incomplete is posted: missing required text, a `{channel_<key>}` link or link button to a channel that
  does not exist, missing bot permissions (`view_channel`, `send_messages`, `embed_links`,
  `read_message_history`) or a message over Discord's limits (only possible when branding, title and description
  are all near their maximum together) are reported in the /setup report and nothing changes.
- Discord errors are reported with the HTTP status and Discord error code; running /setup again retries.
- Publishing one guide runs one at a time (a lock), so concurrent /setup runs cannot duplicate messages.

## How to test manually in Discord

1. Fill the keys listed above in `config/content.toml` (or `/content set`), e.g. write `{channel_catalog}` in a
   step text, and run `/status`: `how_to_buy`, `faq` and `shipping` should be ready.
2. Run `/setup`. Check #how-to-buy (one embed, numbered steps, three link buttons that open #catalog, #purchase
   and #faq; the `{channel_catalog}` text is a clickable link), #faq (one embed per section) and #shipping
   (status cards in three colors, region lines with indicators).
3. Run `/setup` again: the report says "up to date" and no message is edited or re-posted.
4. Change one answer with `/content set` and run `/setup`: the same message is edited.
5. Raise `questions` of a section to 25 with long answers, `/reload`, `/setup`: the FAQ continues in further
   messages. Lower it again: the extra messages are removed.
6. Delete the first #faq message by hand and run `/setup`: the FAQ is posted again in order.
7. Clear a required key with `/content clear`: the guide is withdrawn and `/status` names the key.

## Limitations

- Text changes appear after running `/setup` (like every panel).
- A `{channel_<key>}` link to a staff channel renders, but customers cannot open it (choose customer channels).
- A section or status card that needs several embeds does not repeat its title; the continuation keeps the
  card's color and follows directly below (no "continued" wording is invented).
- Not yet verified against a live Discord server (see `docs/MORNING.md`).
