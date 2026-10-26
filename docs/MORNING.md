# Morning checklist — from code to a working server

Do these in order. Each step says what to do, where, and how to check it worked. Nothing here requires changing
code. Expected time: about an hour. Every message, button and notification already has text (a starting text pack
written for you) and every feature is set up, so `/setup` posts everything at once; change any wording later.

---

## 1. Create the Discord application and bot

1. Open <https://discord.com/developers/applications> → **New Application** → name it → **Create**.
2. **General Information**: copy the **Application ID** (you need it in step 5). It is not secret.
3. **Bot** tab:
   - **Reset Token** → copy the token. This is a secret: never paste it into code, Discord, or chat.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent** (ticket transcripts with message text,
     the gift card chat guard) and **Server Members Intent** (the welcome mention in #welcome). If one is off, the bot
     still starts without it and `/status` says which one to switch on.
   - Optional: turn off **Public Bot** so only you can invite it.

Check: the Bot tab shows the intent enabled and you have the token saved somewhere private.

## 2. Configure secrets

**Locally (optional, for testing on your machine):**

```bash
cp .env.example .env      # then edit .env
```

Set `DISCORD_TOKEN`, `GUILD_ID`, `OWNER_ID`, `DB_PATH=./data/bot.db`, `LOG_FORMAT=text`. `.env` is git-ignored.

**On Railway (production):** service → **Variables** → add:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | the token from step 1 |
| `GUILD_ID` | your server ID (step 3) |
| `OWNER_ID` | your user ID (step 3) |
| `DB_PATH` | `/data/bot.db` |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `15` (lets in-progress clicks finish when Railway restarts the bot) |
| `GIFT_CARD_KEY` | recommended: encrypts the gift card codes customers submit. Generate it once on your computer with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste it. Keep it secret and never change it while cards wait (they would become unreadable). Without it the bot creates `gift_card.key` on the volume. |

Check: Railway shows the six variables (secret values hidden).

## 3. Get the Discord IDs

1. Discord → **User Settings → Advanced → Developer Mode** on.
2. Right-click your server icon → **Copy Server ID** → that is `GUILD_ID`.
3. Right-click your own name → **Copy User ID** → that is `OWNER_ID`.

## 4. Create or choose the server

Use the server customers will join (or a separate test server first — recommended). The bot manages one server.

## 5. Invite the bot with the right permissions

```bash
python -m tools.invite_url <APPLICATION_ID>
```

Open the printed link, choose your server, **Authorize**. The link requests only the permissions the features use
(no Administrator) plus the `applications.commands` scope for slash commands.

Check: the bot appears in the member list (offline until deployed).

## 6. Role hierarchy and permissions

Server Settings → **Roles**: drag the bot's role **above** every role it must manage (e.g. your staff role).
Discord only lets a bot edit roles below its own.

Check later: `/setup` reports any missing permission or role it cannot manage.

## 7. Connect the GitHub repository

1. **Make the repository private**: GitHub → `longswordsmp/Shop-Bot` → Settings → General → Danger Zone →
   Change visibility → Private. (It was public when the project was built.)
2. The code is on branch `claude/youthful-clarke-ns9ix2`. Merge it into `main` (open a pull request and merge),
   or point Railway at that branch.
3. Railway → **New Project → Deploy from GitHub repo** → authorize Railway's GitHub app for this repository →
   select it and the branch.

## 8. Persistent volume

Railway service → **Volumes** (or right-click the service → Attach Volume) → mount path **`/data`**.
Keep `DB_PATH=/data/bot.db`. Without a volume, every redeploy would start with an empty database, so the bot
refuses to start on Railway without one (the deploy log shows `env_invalid` … "not on persistent storage").

## 9. Deploy

Railway builds with Railpack (Python 3.12 from `.python-version`, dependencies from `requirements.txt`) and runs
`python -m shopbot` (from `railway.json`).

If the service does not pick up `railway.json` (Railway has deprecated config files for new services), set in the
service **Settings**: Start command `python -m shopbot`, Restart policy **On Failure**, **1** replica.

Check the deploy logs for, in order: `starting` → `instance_lock_acquired` → `db_ready` → `commands_synced` →
`bot_ready`. The bot should show online. A `banners_incomplete` warning means a picture in `config/banners/` is
missing or unreadable (that kind of message then shows no banner). The first start also logs `icons_uploaded`: the
bot uploads its icons (`config/emojis/`) as its own application emojis; `icon_upload_failed` names an icon Discord
refused (it then shows nothing, and the next start tries again).
See `docs/DEPLOYMENT.md` → Troubleshooting if anything else appears.

**Did the deploy reach the server?** The `starting` line names the code that runs (`commit=` and 7 characters, e.g.
`commit=9f3c2b1`), and so do `/status` ("Running version") and the `/setup` report, under the summary. Compare it
with the newest commit of the branch on GitHub (the 7 characters next to the commit message). Different: Railway is
still building or the deploy failed (see its deploy log), so the server runs the old code. `unknown`: the service was
not deployed from GitHub (for example with `railway up`), so Railway did not say which commit it runs.

## 10. Run `/setup`

**Already ran the old layout in this server?** Run **`/reset`** first (owner only: `OWNER_ID` or the server owner).
It shows you privately every channel, category and role it would delete and what it keeps: `@everyone`, bot and
booster roles and every role the bot has, roles above the bot, your own Administrator roles, the channels with
history (transcripts, the bot log, published reviews: `/setup` moves them into the new layout), anything set by id
in `settings.toml`, and channels a Community server must keep. Before you confirm, **finish or cancel unpaid orders**
(customers pay in the order channel, which is deleted) and close the tickets you want a transcript of.

Press **Continue** and type `RESET <server name>`. It then makes a database backup
(`/data/backups/before-reset-<time>.db`, never rotated away), creates a private **🛠・setup** channel for you, and
deletes the listed channels, categories and roles at about one per second while every button answers "unavailable".
The progress and the result come by DM (allow DMs from server members first; without a DM it changes nothing). If
the bot restarts in the middle, it tells you by DM at the next start: run `/reset` again to finish. Orders, products,
discount codes, reviews and store credit are **kept** unless you run `/reset wipe_shop_data:True`, which also deletes
every order, product, discount code, review, ticket and customer record (they stay in the backup). Run `/setup` in 🛠・setup, then delete that
channel. `/setup` posts the staff cards of open orders again in the new #orders channel and gives customers the new
Customer role; everyone else presses **Verify** again on the new welcome panel (Restock Alerts sign-ups start over).

**After deploying an update**, check the running version first (section 9), then run **`/setup`**: it updates every
panel to the new code and text. #catalog then shows only its header card with the category menu (All items, then your
categories); item cards posted by an earlier version are removed (the report's Panels section says how many, "… N
removed"), and members see the items privately when they pick All items or a category.

**A text still shows the old wording after `/setup`?** A text you changed in Discord (**Edit text** or `/content set`)
keeps your wording: it wins over `content.toml`, so a new version of that text does not reach the server. `/content
show` names where a text comes from ("override (/content set)"); `/content clear` takes the file's wording again. This
update also renumbered the #faq payment questions (the "Why only gift cards" question is gone, the next ones moved up
by one), so a payment question you edited in Discord may now show on another question: check #faq and clear it.

In your server, as the owner/admin: **`/setup dry_run:True`** first. It lists what it would create.

Look at its **Problems** section: it lists every channel or category the bot does not manage that `@everyone` can
see. In a new server that is Discord's default **#general** text channel and **General** voice channel (and their
"Text Channels"/"Voice Channels" categories). Unverified users must see only 📌 INFORMATION, so **delete them** (or
deny View Channel to `@everyone` on them); `/setup` never changes or deletes channels it does not manage, and every
run reports them until they are gone.

The structure comes from your specification and is already named (content.toml): 📌 INFORMATION, 🏬 SHOP,
🧾 ORDERS and 🔒 STAFF with 📜・welcome, 🧭・how-to-order, ❔・faq, 🚚・shipping, 📣・announcements, 📦・catalog,
✨・new-arrivals, ⭐・reviews, 🛒・place-order, 📋・orders, 💬・staff-chat, 📒・bot-log and 📁・transcripts, and the
roles Owner, Staff, Member, Customer and Restock Alerts. Existing channels/roles with the same names are reused
instead of duplicated.

Then **`/setup`**. It creates or repairs roles, categories, channels and permissions, gives the Owner role to
`OWNER_ID`, and posts every panel: 📜・welcome (rules + Verify), 🧭・how-to-order, ❔・faq, 🚚・shipping, 📦・catalog,
✨・new-arrivals, 🛒・place-order (Start + Report a Problem) and the intro at the top of ⭐・reviews, each with its
banner picture at the bottom of its card. It is safe to run again any time; it never duplicates or deletes anything except withdrawing panels of features
that are not ready.
`/status` should show every enabled feature as **ready**.

Check: Server Settings → Roles shows the bot's role **above** Owner, Staff, Member, Customer and Restock Alerts
(Discord only lets the bot give roles below its own). Give your staff the Staff role.

**Who sees which slash command.** Discord lists a command only for members with its default permission: the staff
commands (`/orders`, `/order`, `/stats`, `/ticket`, `/reviews`, `/announce`) need *Manage Messages*, which `/setup` gives the Staff
role (`permissions = ["manage_messages"]` in `settings.toml`; it also lets staff delete and pin messages; `/setup`
only adds it and keeps any other permissions the role already has). Admin commands need Administrator; `/product`,
`/category` and `/discount` need Manage Server. To fine-tune, open **Server Settings → Integrations → (the bot) → Manage** and allow or deny each command for
roles, members or channels. For example, to let the Staff role manage products: allow `/product` for Staff there and
add `"staff"` to `catalog.manager_roles` (do not give Staff Manage Server). These settings only change who *sees* a
command; the bot checks the roles itself on every use.

Check: with a staff account (Staff role only), typing `/` lists the staff commands; a customer account sees none of
them (only `/myorders`).

## 11. Change any text or setting

**Edit a message's text from Discord (easiest).** Right-click (on a phone: long-press) any message the bot posted →
**Apps** → **Edit text**. The bot lists every text in that message; pick one, change it in the form (it opens with the
current wording) and save. The posted panels update right away; other messages (tickets, notifications) use the new
text the next time the bot sends them. Only administrators see **Edit text**.

For private replies (which cannot be right-clicked) and forms: **`/content set`** and type a few words of the text
you saw: the suggestions show which text contains them.

- **`/status`** — what is ready and what is missing, per feature.
- **`/content show`** / **`/content clear`** — see a text, or undo your Discord change (the file value applies again).
- **`/content export`** — download every text as a `content.toml` (commit it to keep your Discord changes in GitHub).
- **`/reload`** — re-read the files after you changed them on GitHub (restart after changing
  `my_orders.command.description`).

**Text in `config/content.toml`** (edit on GitHub, commit, push → Railway redeploys): every key is listed with
what it is for, its Discord length limit and its placeholders (`{channel_catalog}`, `{order_number}`, ... are filled
in by the bot).

**Decisions in `config/settings.toml`** (chosen for you; change any): currency `USD`, payment with DoorDash eGift
cards for the exact total (customers submit them in a private form; only the Owner can reveal codes, redeem them and
confirm the payment: `[payments.gift_card] staff_roles`), store credit for overpayments and refunds, shipping to every country in the Americas (in the US:
all states except Alaska and North Dakota) and to Europe, 6 rules on #welcome, FAQ topics, four problem types, reviews 7 days after completion (1-5
stars), a 24-hour unpaid-order reminder, cart expiry after 24 hours, the shipping-address step, staff
reminders. The full list is in `docs/DECISIONS.md` §10a.

**Banners:** `config/banners/` holds one picture per kind of message (`welcome.png`, `faq.png`,
`selection-and-cart.png`, `error.png`, ...), shown at the bottom of that message's card. Replace a file with your own
picture of the same name (PNG/JPEG/GIF/WebP, at most 2 MB, 1500×500 recommended) to change it, then `/reload` and
`/setup`. To hide one kind, add its name to `[banners] off` in `settings.toml`; `enabled = false` hides them all.

**Icons:** `config/emojis/` holds the small pictures the texts use like emojis (`{icon_truck}`, `{icon_dot_blue}`,
...). Replace a file with your own 128×128 PNG of the same name, then `/reload` and `/setup`. `docs/CONFIGURATION.md`
→ `[icons]` explains where icons can be used.

Locally you can also run `python -m tools.checklist --write`, `python -m tools.check_limits --strict` and
`python -m tools.build_preview` (open `preview/out/index.html` to see every screen with your text and banners).

## 12. Test the enabled workflows in Discord

Use a second (non-staff) Discord account as the "customer".

**Server & entry**
- [ ] `/reset` as a staff or admin account that is not the owner → refused. As the owner: the private list matches
      the server; Cancel changes nothing; Continue with a wrong phrase changes nothing; the right phrase → DM with the
      progress and the result, only the kept roles and 🛠・setup remain.
- [ ] `/setup` in 🛠・setup, then `/setup` again: the second run reports nothing new to create; the Owner account has
      the Owner role.
- [ ] Customer account joins → mentioned in #welcome, the mention disappears after 30 seconds.
- [ ] Customer account before Verify: sees only 📌 INFORMATION (read-only).
- [ ] #welcome Verify → private confirmation; 🏬 SHOP and 🧾 ORDERS appear; clicking again says already verified.
- [ ] #how-to-order link buttons open #catalog, #place-order, #faq; #faq shows its topics and #shipping your countries with their dots.
- [ ] #shipping: Standard says "about 2 weeks" and Expedited "usually 3–4 days"; it is one message with one card:
      the US states are listed at the bottom of that card on one line per status like the countries (the blue dot,
      then Alaska and North Dakota after the grey dot; no gaps, readable on desktop and phone) and the banner
      closes it under the states. The legend at the top shows one dot per line. No separate states message
      is left (on a server that still had one, the next `/setup` reports "1 old message(s) removed").
- [ ] #faq is one card (nothing continues in a second card) and answers the bulk-order questions: no extra cost, a
      percentage off on request.
- [ ] Icons: buttons show their small icon in front of the label (e.g. Start order, Back, Continue), section headings
      and field names show theirs, and no card title shows raw text like `<:truck:123…>`.

**Catalog & new arrivals**
- [ ] #catalog is one message: the header card with the banner and a menu (All items first, also before any category
      exists). No item cards are posted in the channel (`catalog.product_cards = false`, as shipped).
- [ ] `/product add image:<a picture>` (staff); as the customer pick All items → a private message with its card, the
      picture under its name and a Buy button; `/product stock 0`, pick All items again → button disabled, red.
- [ ] `/product image file:` (upload) → image shows, still there after `/product edit` and a bot restart.
- [ ] Before any category the #catalog menu offers All items only. `/category create name:Old stock emoji:tag`
      (staff) → private reply; the menu on the header card now lists All items, then Old stock with its icon.
      `/product categories product_id:<id> add:Old stock` for two items (`/category list` shows "2 item(s)").
- [ ] As the customer, pick Old stock in the #catalog menu → a private message only you see ("Only you can see
      this"): its two items as cards with their pictures and a **Buy <item>** button each; nothing new in #catalog.
      Pick Old stock again → it answers again (the menu resets). All items with more than 5 items → Next / Previous
      edit that private message ("Page 2 of 2"). Buy there → the item lands in your order ticket. Check on a phone too.
- [ ] #new-arrivals button → Restock Alerts on, again → off. Restock a sold-out product → one update pinging the role.

**Purchase (order ticket)**
- [ ] #place-order Start → private ticket (only you + staff): intro message, then the live panel (reference, step,
      status); add items, Continue, address (region, country, the private address form), delivery and extras,
      review, Confirm order.
- [ ] On the address step (Change address and Add apartment (optional) on one row, Back / Continue / Cancel on the
      next) press **Add apartment (optional)** → a private form with four optional fields (apartment, building or
      floor, entry or buzzer code, delivery instructions). Fill two → SHIP TO only adds "Apartment details added"
      (never what you typed), the button reads **Edit apartment details** and opens with your answers; empty every
      field → the line is gone. The house icon shows on the button and the line. Place the order with them: the
      record's **Details** button in #orders shows them privately (apartment and building under the street, the entry
      code and instructions after the country); the ticket and #orders never show them (with
      `operations.answers_in_orders_channel` off, as shipped). Check on a phone too.
- [ ] Buy on an item (All items in #catalog) while the ticket is open → the item is added to that ticket instead of a
      new one.
- [ ] Start again with an open order → private link to the existing ticket.
- [ ] Payment methods: #place-order, #how-to-order and #faq list every method one per line with its brand icon
      (card, black Apple, blue Google, navy PayPal, green Cash App, red DoorDash) and "You choose when you pay, and
      we'll show you what's available then"; nothing there says gift cards only or temporarily down. Check the icons
      after the restart (the changed and new brand pictures are uploaded) and on a phone.
- [ ] The order card says **Choose how to pay** with a button per method (its brand icon as the button's emoji, three
      per row) and Cancel order alone last; no gift card steps yet. Press Credit or debit card (or PayPal, Cash App,
      Apple Pay, Google Pay) → a private "We're sorry! **Credit or debit card** isn't available at the moment. For now
      we only accept payment by DoorDash eGift card." with **Pay with gift card** and **Buy a gift card**; Pay with
      gift card → the private HOW TO PAY steps
      with the exact total, **Submit gift card** and **Buy a gift card**; Submit gift card opens the gift card form.
      Pressing DoorDash eGift card on the order card shows the same steps.
- [ ] Payment: submit a card worth less than the total → "received … still to pay", and the order card now shows the
      steps for the rest and the GIFT CARDS received with Submit gift card and Buy a gift card (no method buttons);
      a second card → "Order placed" in the ticket and by DM, a payment alert in #staff-chat. Type a code in the ticket
      chat → the message disappears and you get a private note.
- [ ] In #orders as the Owner: Reveal gift card (private, spoiler-hidden codes), Payment confirmed → paid; on another
      order Card problem with a reason → the customer is told why and can submit another card. A plain Staff account
      gets "only the payment staff" for Reveal and Payment confirmed.
- [ ] Customer cannot press staff buttons; in #orders: Claim, Processing, Mark shipped (carrier, tracking number,
      optional link) → the customer gets the tracking number with Track package; Mark delivered → the ticket closes
      and the Customer role is given (on delivery by default).
- [ ] Reject with a reason and Request correction (while waiting for payment) → the customer sees the reason/request.
- [ ] Ticket closes → transcript in #transcripts with the final status; `/myorders` shows the order and timeline.
- [ ] Start a ticket, add an item, do not submit; as staff run `/ticket close` in it → the unfinished order is
      cancelled and the ticket closes (one transcript). In the ticket of a submitted open order → refused, with a link
      to its #orders record.
- [ ] With a details or submission form: #orders shows no typed answers; the record's **Details** button and
      `/order view` show them privately to staff.

**Discount codes**
- [ ] `/discount create code:SAVE10 type:Percent off value:10` (as an admin or a catalog manager) → a private reply
      with the terms; a plain Staff account or a customer is refused. `/discount list` shows it with "Orders: 0".
- [ ] In an order ticket on the review step: **Add discount code** → type `save10` → the summary shows "Discount
      (SAVE10) · -…" under the subtotal, the total drops, and **Remove discount** replaces the button. Type a code that
      does not exist → a private "We couldn't find the code" reply; after 5 wrong codes → "please wait a few minutes".
- [ ] `/discount create code:BOGO type:Buy one, get one free` → with two different items in the cart, the cheaper
      one is free ("Buy one, get one free (BOGO) · -…"); with one item → the reply says it needs two items.
- [ ] Confirm the order with a code: the order card, the #orders record and `/order view` show the discount line; the
      gift card amount to pay is the total after the discount.
- [ ] `/discount end code:SAVE10` → an unfinished order that applied it shows "has ended" in its summary, the order
      already placed keeps its discount; `/discount list` lists it under "Ended or expired".
- [ ] Tips: on the review step the menu above Back / Confirm order reads "Add a tip for our team (optional)" (after
      the store credit and coupon buttons) and the card's last line says tips are optional and "100% of every tip
      goes to our team". Pick **$5.00 tip** → the closed menu shows "$5.00 tip" selected, the summary shows "Tip for
      our team · $5.00" after the extras and the total grows by $5.00. Pick **Other amount**, close the form without
      sending, then pick Other amount again (the form opens again); type 7.50 → "$7.50 tip" is selected; type €5 or
      500 → a private reply says why. With a discount code applied, the discount does not change with the tip.
      Confirm the order: the order card, the #orders record and `/order view` show the tip on its own line, and the
      gift card amount to pay includes it. Once staff mark it paid, `/stats` shows "Tips for the team: $7.50" and the
      revenue without it.

**Reviews (Write a review, `/review`, #reviews)**
- [ ] ⭐・reviews starts with the intro panel (the server was rebuilt, older reviews will come back, a welcome, how to
      leave a review with **Write a review**, "your name is never shown") with the reviews banner at the bottom and the
      **Write a review** button (star icon) under it; check it on desktop and phone. In a #reviews that already showed
      reviews it comes after them.
- [ ] **Write a review as a member.** With an account that has only the Member role (no order, no ticket), open
      #reviews and press **Write a review** → a private card "How many stars do you give us?" with a stars menu → pick
      5 → the form "Your review" shows the description box and a **Photo (optional)** upload field → write a
      description, add a photo, Submit → "Our team checks it ...". #staff-chat shows the approval message with the
      photo → Approve → #reviews shows the stars, the text and the photo without the name. Press Write a review again
      → a private reply with the date you can share another one (no menu). Upload a PDF in the form instead of a photo
      → "We could not use this photo" with your text shown under "Your review text", nothing written.
- [ ] As a member without a delivered order: `/review` lists the stars as "5 stars - Excellent ★★★★★" ...; run
      `/review stars:5 photo:<a photo>` → the form "Your review" opens → write a description → private reply "Our team
      checks it ...". #staff-chat shows the approval message with your name, "No delivered order: unverified review"
      and the photo. Press **Approve** → #reviews shows "★★★★★ 5/5", the description and the photo, with **no name, no
      mention and no avatar**, and no "Verified purchase"; you get "Your review is published" by DM. Run `/review`
      again → a private reply with the date you can share another one.
- [ ] With a delivered order (staff: Mark delivered): `/review stars:4` → "It will appear in our reviews channel" →
      #reviews shows the review with "Verified purchase / Order #N" and still no name. The order's review request, if
      it came, answers "already reviewed". With a second delivered order, `/review stars:5 photo:<a photo>` → "Our
      team checks it ..." (a photo is always checked first) → the approval message shows "#N" under Order → Approve.
- [ ] `/review photo:` with a photo that is not a picture (e.g. a PDF) or an iPhone HEIC photo → a private "We could
      not use this photo" reply ("up to 9 MB" or the server's limit), no form.
- [ ] Review with a photo taken in portrait on a phone (a JPEG) → the approval message and #reviews show it upright
      (the bot removes the photo's metadata but keeps its orientation).
- [ ] **Where members can type `/review`.** With the Member-only account, open #reviews and type `/review`. Expected:
      the message box is locked ("You do not have permission to send messages"), which is why the panel has the
      button; if Discord does offer the command there, it works too. In an open order or support ticket `/review`
      works (the intro says so).
- [ ] After the upgrade, **Server Settings → Integrations → (the bot)**: `/review` has no leftover permission override
      (overrides set for the old staff `/review` command may stay on the members' `/review`, which keeps its command
      id, and hide it from members); the staff tools are now `/reviews`.
- [ ] `/reviews list` shows the review numbers; `/reviews remove review:<n>` deletes a published review; a member
      cannot use `/reviews`. Restart the bot with a `/review` form open, then send it → it still works.

**Warehouse sale announcement (`/announce preset:warehouse_sale`)**
- [ ] `/announce preset:warehouse_sale` before the coupon exists → a private card says the coupon `BOGO` does not
      exist, shows the command to create it and has no Post button.
- [ ] `/discount create code:BOGO type:Buy one, get one free once_per_customer:false products:<ids of the old stock>`
      (product numbers from `/product list`).
- [ ] The post promises a 20% off coupon in every package (old stock only, can be given to a friend): create that
      printed code, e.g. `/discount create code:THANKYOU20 type:Percent off value:20 once_per_customer:false
      products:<ids of the old stock>`, and print it on a card in each package. As a customer, `THANKYOU20` on the
      review step takes 20% off the old-stock items only.
- [ ] `/announce preset:warehouse_sale` (optionally `mention:`) → two private messages: the announcement exactly as
      it will be posted (icons in the text, the banner at the bottom of the card, **Claim coupon** greyed out) and a
      card with the channel, the mention and the coupon's terms; check the text on desktop and phone.
- [ ] **Post announcement** → one post in #announcements with the green **Claim coupon** button (its gift icon in
      front of the label); the card turns into "Announcement published" with a link. Press Post again → "already
      posted"; run the preset again → "already announced" with the link.
- [ ] As a customer press **Claim coupon** → a private "Coupon claimed" reply with the terms; order two old-stock
      items → on the review step press **Use coupon BOGO** → the cheaper item is free; confirm → the receipt shows
      the free item ("Buy one, get one free (BOGO) · -…").
- [ ] `/discount end code:BOGO` → within a few seconds the announcement's button shows **Offer ended** (greyed out),
      the text and the banner unchanged; pressing it does nothing.

**Shipping areas announcement (`/announce preset:shipping_areas`)**
- [ ] First confirm with the owner which areas are right: the shipped `[shipping.excluded_areas]` is
      `US = ["AK", "ND"]` (Alaska and North Dakota); the owner's message said "South Dakota" (`SD`). Change it in
      `config/settings.toml` if needed and `/reload`; #shipping (after `/setup`), the address step and this
      announcement all follow it.
- [ ] `/announce preset:shipping_areas` → the private copy reads "An update on shipping to Alaska and North Dakota"
      (the same names #shipping shows), the areas in bold in the first line, icons before each heading, a working
      #shipping link and the shipping banner at the bottom; no button. The card has **Post announcement**; check the
      text on desktop and phone, then post it → one post in #announcements.
- [ ] With `[shipping.excluded_areas]` emptied and `/reload` (put it back afterwards), the preset answers with only a
      card saying nothing is excluded and no Post button.

**Problems, staff, admin**
- [ ] #place-order Report a Problem → choose order and type → form → private problem ticket; staff Resolve/Reject.
- [ ] #staff-chat gets new-order / paid alerts (staff pinged) and reminders once thresholds are set; the Owner gets DMs.
- [ ] `/announce` (staff) → embed in #announcements; a customer cannot use it, nor see the presets in
      `/announce preset:`.
- [ ] `/orders`, `/order view`, `/stats`, `/discount list`, `/status`, `/content show`, `/backup`, `/audit`; #bot-log
      shows the actions.
- [ ] Restart the service (Railway → Restart) → every old button still works.

Record what you tested and anything unexpected. Every failure answered with the generic error text (a crash, or
an expected failure such as a ticket channel that could not be created) is logged in Railway with an error
reference (`error_id`) and alerted in #staff-chat. Staff see that reference in failed slash-command replies;
customers see it only if you include `{error_id}` in your `common.error.generic` text (e.g. "Reference:
{error_id}"), and can quote it to you.

---

### What has and has not been verified so far

- ✅ Implemented and tested locally and on GitHub Actions: 2,000+ automated tests (all features, failures, concurrency,
  restarts, banners kept/replaced on edits, `/reset` refusals, interruptions and the rebuild with the shipped
  settings), type checks, lint, Discord-limit and handler-coverage checks with the shipped text, offline preview.
- ❌ Not yet verified by us: running in a real Discord server. In particular, check on desktop and phone that each
  banner shows **inside its card, at the bottom** (the card's image), and above the card only where the card shows a
  picture of its own (a product or announcement image, the welcome image when one is set); that a banner still
  shows after its message is edited (a button click, a status change on the order ticket or #orders record: the bot
  keeps the uploaded picture and refers to it again, which could not be tested offline); that the icons appear (the
  upload of application emojis was tested against a stand-in for Discord only), in particular in field names and as
  button emojis (Discord's documentation does not say whether field names show custom emoji; titles do not, so the
  bot leaves icons out of titles), the welcome mention and its removal
  after 30 seconds, `/reset` on your real server (Discord's own refusals, e.g. for Onboarding channels, only show
  up there; they are reported and skipped), and that editing only the buttons of the warehouse sale announcement
  (**Offer ended**) keeps its text and banner as posted. Steps 9–12 are that verification.
