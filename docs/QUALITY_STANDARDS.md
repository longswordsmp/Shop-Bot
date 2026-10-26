# Quality standards for every message the bot sends

These standards describe what a professional shop server looks like. They apply to every message, form, button
and menu the bot shows, for customers first and for staff and operators too. Rules marked **[checked]** are
enforced by `python -m tools.check_quality` (part of `scripts/quality.sh`) on every preview state with the shipped
texts; the others are reviewed by eye with `python -m tools.build_preview` before a change is pushed.

## 1. Structure

- **L1 One card per message.** The owner's rule: keep everything in one embed. A message is one embed, and what
  belongs together stays in that card as sections or fields, never in a second embed or a follow-up message
  (#shipping lists the US states at the bottom of its own card; the FAQ is one card). No stacks of small embeds, no
  splitting a card into several. When text does not fit, tighten the wording rather than add a card. The one
  exception in the shipped setup is the private catalog results after choosing a category (several product cards,
  shown only to that person). A long order's continuation card (a second embed of the same colour) stays only as
  the fallback when Discord's limits (4096 characters per description, 6000 per embed) leave no other way. Staff
  replies follow the same rule: a list longer than one card continues in a further message, one card each.
  [checked: one embed per customer message, except the catalog listing's product cards when
  `catalog.products_per_listing_message` is more than 1, a category's private view, and a long order's continuation
  card]
- **L2 Title says what this is.** Short (at most 60 characters), plain text, no ending period. [checked]
- **L3 Lead first.** One or two short sentences under the title: what happened and what to do next.
- **L4 Sections.** Each part of a card starts with a heading line: an icon and an UPPERCASE label in bold
  (`{icon_receipt} ORDER SUMMARY`), followed by short lines. Lists are lines in a section, not grids of fields.
- **L5 Few fields.** Fields are for key facts only (ticket, order number, status). At most 3 inline fields per row
  and at most 6 inline fields per card. [checked: at most 6 inline fields per customer embed]
- **L6 The number that matters is bold.** Total, order number, ticket number, amount due.
- **L7 Banner.** Every customer message keeps its banner picture, shown inside the card at the bottom. [checked by
  tests/foundation/test_banner_coverage.py]

## 2. Icons

- **I1 Only where Discord shows them.** Custom icons render in descriptions, field names and field values, and as
  the emoji of buttons and menu options (a label that starts with `{icon_x} `). They never go into embed titles,
  author lines, footers, form (modal) titles, labels and placeholders, or menu placeholders, where Discord shows
  raw text such as `<:cart:123>`. [checked]
- **I2 One meaning, one icon.** The same thing always has the same icon (see the dictionary below). Two different
  actions never share an icon in the same message. [checked: a button label always has the same icon]
- **I3 One icon per line, at its start.** Icons mark headings, statuses and key facts. Not every line gets one;
  icons never sit inside a sentence, a price or customer data.
- **I4 Status dots.** `dot_blue` = yes / available / active, `dot_yellow` = partly / pending, `dot_grey` = no /
  not available / not yet.

Icon dictionary: cart = items and cart; receipt = order, order summary, total; wallet = payment, payment methods, store
credit; card = card payments; gift = gift cards and claimed coupons; discount = discount codes; truck = delivery and
shipping; st_submitted, st_confirmed, st_shipped, st_delivered = the order statuses (payment submitted, payment
confirmed, shipped, delivered; the FAQ's legend); map = tracking; location = address, ship to; home = apartment details;
clock = time, processing, progress; calendar = dates; ticket = ticket; shield = protection; lock = privacy; box =
packaging; check = done, confirmed; cross = cancelled, refused, close; door = a ticket closing; alert = warning,
problem; info = information; chat = questions, talk to us; headset = support; hourglass = waiting (the same picture as
st_submitted, so never for an order that still waits for its payment); tag = price, catalog; bell = restock alerts;
coins = amounts and store credit balance; help = FAQ; star = reviews; verified = verified purchase; photo = photos and
screenshots; heart = thanks, and tips for our team; list = steps and details; compass = how it works; user = customer;
flag = report a problem; arrow_right = continue; arrow_left = back; plus = add; minus = take off (remove store credit, a
discount, no tip); trash = delete; edit = change (and typing your own amount); link = open elsewhere; brand_apple,
brand_google, brand_paypal, brand_cashapp, brand_doordash, brand_stripe, brand_visa, brand_mastercard = that brand's
payment method, each in the brand's own colour (payment methods only, set by `icon` in `[[payments.methods]]`; the
card method uses card, the DoorDash gift card method brand_doordash; gift stays for gift cards elsewhere).

## 3. Words

- **W1 Plain and friendly.** Second person ("your order"), active voice, short sentences (at most about 25
  words), paragraphs of at most about 300 characters. [checked: no line over 400 characters of the shop's own text
  in a customer message; what people type (comments, answers, reasons) and the country lists on #shipping are not
  counted]
- **W2 One term per thing.** order ticket, order number, store credit, DoorDash eGift card, Standard shipping,
  Expedited shipping, Shipping protection, Discreet packaging. Status names are the same everywhere.
- **W3 No internal words.** Never show ids, content keys, custom ids, snake_case names, `None`, `True`/`False`,
  error class names or stack traces to customers. A short reference code for an error is fine. [checked]
- **W4 No leftovers.** No unreplaced `{placeholder}`, no `{icon_...}` text, no double spaces, no trailing spaces, no
  space before punctuation, no empty lines at the start or end. [checked]
- **W5 Money and dates.** Amounts always through the shop's currency format; dates as Discord timestamps, one
  format per date.
- **W6 Honest.** Texts promise only what the shop does. Before a customer presses a payment method, texts present
  the shop's methods as the ways to pay and say the customer chooses when they pay ("we'll show you what's available
  then"); they never say which work right now ("gift cards only", "temporarily down") and never claim that every
  method works ("any payment method"). Pressing a method that does not work today says so privately and offers the
  ones that do (`docs/DECISIONS.md` §11d).

## 4. Buttons and menus

- **B1 Short and clear, sentence case**, at most 25 visible characters. Action buttons start with a verb
  ("Submit gift card", "Open ticket"); choice buttons name the choice ("PayPal", "Expedited shipping"). [checked:
  length]
- **B2 Styles.** The main action is success or primary; destructive actions are danger; everything else is
  secondary; link buttons open pages outside Discord. At most one success/primary-styled main action per row.
- **B3 Rows.** At most 5 buttons per row; related buttons together; Cancel last.

## 5. Errors, warnings and pop-ups

- **E1 Say what happened and what to do next**, and offer the button that does it.
- **E2 Never blame the customer**; never show technical details.
- **E3 Private.** Replies meant for one person are private (ephemeral).

## 6. Privacy and safety

- **P1** Addresses, gift card codes and payment details are only entered in private forms, never in a channel,
  and are shown only to the people who need them.
- **P2** Texts never ask for card numbers, passwords or codes in chat.

## How the checker works

`tools/check_quality.py` builds every preview state with the shipped texts (the same states as the HTML preview)
and checks the rules marked [checked]. A message counts as a customer message when its first card carries the
shop's footer (`branding.footer_text`) or has no timestamp (a later message of a long card); a private operator
reply (no branded footer, a timestamp) is only checked for icons Discord cannot show and for leftover placeholders.
Stress-test states named `example_*` (filled with synthetic text) are only checked for icons. Accepted exceptions
are listed, each with its reason, in `EXCEPTIONS` at the top of the tool; keep that list short.

## How to check

1. `python -m tools.check_quality` lists every violation of a [checked] rule per preview state (`--strict` exits
   non-zero; `scripts/quality.sh` runs it).
2. `python -m tools.build_preview --out preview.html` and look at the changed states: one design system, readable at
   a glance, nothing cramped, the banner at the bottom of the card.
3. After a deploy, the live checks in docs/MORNING.md (icons in field names and buttons, the banner inside the
   card after an edit) are done on the real server.
