# START HERE guides: #how-to-buy, #faq, #shipping

Three independent features (package `shopbot/features/guides/`) that publish informational panels into the
START HERE channels. Each has its own feature flag, settings section, content keys and published messages, so one
guide that is not finished never blocks the others.

| Feature | Flag | Settings | Channel (shipped) | Published message keys |
|---|---|---|---|---|
| `how_to_buy` | `features.how_to_buy` | `[how_to_buy]` | `how_to_buy` | `how_to_buy:0`, `how_to_buy:1`, ... |
| `faq` | `features.faq` | `[faq]` + `[[faq.sections]]` | `faq` | `faq:0`, `faq:1`, ... |
| `shipping` | `features.shipping` | `[shipping]` + `[[shipping.groups]]` | `shipping` | `shipping:0`, ... |

## What customers see

Every guide is **one card** (one embed under its banner picture): a title, an introduction, then sections, each
a bold heading (usually starting with one of the bot's icons, e.g. `{icon_truck}`) over a few lines.

- **#how-to-buy** — the configured title and introduction, one section per step (its name in bold, usually with
  a step-number icon, over its text), an optional closing line, and a row of **link buttons** that open the
  configured channels (shipped: `#catalog`, `#purchase`, `#faq`).
- **#faq** — an optional title and introduction, then one section per topic (e.g. ordering, payment, shipping,
  tracking, problems): its heading, then each question in bold followed by its answer.
- **#shipping** — the title, introduction and an optional legend, then one section per country group (e.g. North
  America, Europe) listing its countries after a status dot: `{icon_dot_blue}` we ship everywhere in the country,
  `{icon_dot_yellow}` we ship except some areas (e.g. "United States (except Alaska and North Dakota)"), then the
  countries listed as not available (`{icon_dot_grey}`). Below: the delivery options with their prices (from
  `[[checkout.fulfillment_options]]`) and the information sections (processing, tracking). Country names are
  standard English names; the address step uses the same countries. For each country in `list_areas` (shipped:
  the US) a second card follows: "Shipping in the United States" lists all 50 states and DC one by one,
  alphabetically, in three columns, each after its dot (`{icon_dot_grey}` for the `excluded_areas`, e.g. Alaska and
  North Dakota). Text: `shipping.areas.title` (`{country}`) and the optional `shipping.areas.introduction`. The
  `shipping_areas` announcement (`/announce preset:shipping_areas`, see `announcements.md`) names the same
  `excluded_areas` with the same names and `shipping.format.last_area`.

**Clickable channel links:** introductions, step texts, answers, the closing line and information texts may
contain `{channel_<key>}` (for example `{channel_catalog}`), which becomes a clickable `#channel` link to the
channel /setup created for that key. Discord does not render links in titles, headings, questions, group names or
button labels, so those keys do not accept them: a `{channel_<key>}` written there is reported by `/status` (it
would otherwise reach customers as raw text) and the guide stays hidden until it is removed.

**Long content is never cut.** A card that does not fit one embed (4096-character descriptions, 6000 characters
per embed) continues in the next embed of the same colour without repeating its title, and a message holds at most
10 embeds and 6000 characters, then further messages follow. The branding (author line, footer) appears once, on
the first embed. Link buttons are attached to the last message of the how-to-buy guide. Panels carry no
timestamp, so running /setup again with unchanged text changes nothing.

## Settings

```toml
[how_to_buy]
channel = "how_to_buy"                    # channel key from [[server.channels]]
steps = 5                                 # 1-25 numbered steps
link_channels = ["catalog", "purchase", "faq"]   # channel keys shown as link buttons (max 25, in order)

[faq]
channel = "faq"
[[faq.sections]]                          # one per topic, in display order (max 25)
id = "ordering"                           # internal key, never shown
questions = 3                             # 1-25

[shipping]
channel = "shipping"
info_sections = 2                         # 0-10 information sections after the delivery options
not_available = ["CU", "RU", "BY"]        # countries listed as not available (any country in no group is too)
list_areas = ["US"]                       # countries whose areas get their own card, one by one (US: states + DC)
[shipping.excluded_areas]                 # parts of a country we do not ship to (US: state codes; others: names)
US = ["AK", "ND"]
[shipping.country_names]                  # optional renames, e.g. CZ = "Czech Republic"
[[shipping.groups]]                       # one per heading on #shipping, in display order (max 20)
id = "north_america"                      # internal key, never shown (heading: shipping.group.<id>.name)
countries = ["CA", "US", "MX"]            # two-letter country codes (ISO 3166-1, plus XK for Kosovo)
```

Checks that keep a guide hidden (listed by `/status` and the /setup report): its channel is not set or not
defined in `server.channels`; a link channel is not defined, or is a staff/admin channel customers cannot open;
the FAQ has no sections; #shipping has no country groups; a country format lacks a required placeholder; a channel
link is written where Discord cannot show it. Wrong types, out-of-range numbers, unknown country or state codes, a
country in two groups (or both in a group and in `not_available`), excluded areas of a country we do not ship to,
duplicate ids and unknown settings are configuration errors (the bot refuses to load the file).

## Content keys

| Keys | Kind | Required |
|---|---|---|
| `how_to_buy.title`, `how_to_buy.introduction` | title, description | required |
| `how_to_buy.step.<n>.name` / `.value` (n = 1..steps) | heading, text | required |
| `how_to_buy.closing` | description | optional |
| `how_to_buy.link.<channel>.label` / `.emoji` | button label, emoji | label required, emoji optional |
| `faq.title`, `faq.introduction` | title, description | optional |
| `faq.section.<id>.title` | heading | required |
| `faq.section.<id>.q.<n>.question` / `.answer` | question (bold), answer | required |
| `shipping.title`, `shipping.introduction` | title, description | required |
| `shipping.legend` | description | optional |
| `shipping.status.full/partial/none.indicator` | short text (the dot icon) | required while such countries are listed |
| `shipping.group.<id>.name` | heading | required |
| `shipping.format.excluded` (`{country}`, `{areas}`) | format | required once a country has excluded areas |
| `shipping.format.last_area` (`{areas}`, `{last}`) | format | required once a country has several excluded areas |
| `shipping.not_available.heading` / `.others` | heading, text | heading required when countries are listed there |
| `shipping.delivery.heading`, `shipping.format.delivery` (`{label}`, `{price}`, `{description}`) | heading, format | required with delivery options |
| `shipping.info.<n>.name` / `.value` (n = 1..info_sections) | heading, text | required |

The formats are content keys because their words are visible text (for example `{country} (except {areas})` and
`{areas} and {last}`). The full list with descriptions is generated in `config/content.toml`
(`python -m tools.checklist --template`).

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
