# Catalog: `#catalog` listings, buy buttons, product images, `/product`

Specification: section 3 (CATALOG / `#catalog` and the `/product` commands) and section 4 (a listing's button opens or
continues the customer's private ticket and carries the selected listing into that workflow).

## What it does

- **One embed per item.** The listing channel (`catalog.listing_channel`, shipped: `catalog`) shows the header card in
  its own first message (with the private-browsing button when `catalog.browse_enabled = true`), then one message
  per product (`catalog.products_per_listing_message = 1`, shipped). With a value above 1 a message holds that many
  product cards and one buy button per card (5 per row, in card order). A message is split earlier when it would
  exceed 10 cards, 6000 embed characters, 10 attached files or 10 MiB of attached images.
- **Card content.** Name (markdown-escaped), description (as written), image, price (`common.money_format`),
  availability and the optional per-order limit. Inactive and archived products are not listed.
- **Availability states are visually distinct.** The card color follows the state, in addition to the configured
  availability text: `success` = available (unlimited, or more than the low-stock threshold left), `warning` = low
  stock (only when `catalog.low_stock_threshold` > 0), `danger` = out of stock. Colors come from `branding.colors`.
- **Buy button.** While the checkout feature is switched on, every card has a buy button (`sb:co:buy:<product id>`,
  handled by checkout). It is **disabled** while the product is out of stock. A click:
  1. re-loads the product (unknown → not-found text; inactive/archived → `checkout.error.product_unavailable`;
     sold out → `checkout.error.out_of_stock`; nothing is created);
  2. if the member has an unfinished order channel (the one the `#purchase` Start button opens), puts the product
     in its cart (one unit; a product already in the cart keeps its quantity, so a repeated click never adds a
     second unit), shows the cart step, edits the channel's live workflow message, and replies privately with
     `checkout.buy_added.description` (falls back to `checkout.session_exists.description`) linking the channel;
  3. otherwise applies every rule of the Start button (open-order limit, cooldown, channel limits), opens the
     private order channel with the product already in the cart, and replies with `checkout.started.description`.
     If the product sold out in the moment between the check and the cart, the channel still opens with an empty
     cart and the reply adds the configured out-of-stock error.
  Every cart rule applies (`checkout.max_cart_lines`, per-product `max_per_order`, `checkout.max_line_quantity`,
  stock, amount limits); stock is reserved only when the order is submitted, as before. One member's Start and buy
  clicks are serialized, so concurrent clicks open at most one channel and every product lands in it. If checkout is
  not ready, the button stays visible and answers with the configured "unavailable" text.
- **Updates and removals.** Every product change (form edit, price, stock, image, toggle, archive/remove, sort, and
  stock movements from orders) refreshes the listing: changed messages are edited in place, removed products' messages
  are deleted (with one product per message, removing product 2 of 3 edits message 2 to show product 3 and deletes
  the surplus last message, so order is kept and nothing stale remains). Deleted listing messages are re-posted.
- **Uploaded images.** `/product image file:<upload>` downloads the file (PNG, JPEG, GIF or WebP, at most 8 MB; the
  type is detected from the bytes, not trusted from Discord), stores it in the database (`product_images`, one row
  per product), and clears the product's image link. Listing messages, private browsing and `/product view` attach
  the file and show it with `attachment://product-<id>-<hash>.<ext>`. Every edit attaches it again, a new image
  gets a new name (the content hash), and after a restart the bytes come from the database, so images keep
  displaying. A message that is unchanged is never re-sent and never loads image bytes. Setting a link removes the
  upload; giving neither removes the image; archiving deletes the stored bytes.

## Commands (catalog managers and admins; replies are private operator text)

| Command | What it does |
|---|---|
| `/product add` | Opens the product form (name, price, stock, per-order limit, description). |
| `/product edit product_id` | Opens the form prefilled; stale edits are refused. |
| `/product price` / `stock` / `toggle` / `sort` | Single-field changes. |
| `/product image product_id [url] [file]` | Image link **or** uploaded file; neither = remove the image. |
| `/product remove product_id` | Asks for confirmation with Remove / Keep buttons; Remove archives the product (refused while pending orders hold units; idempotent). |
| `/product archive product_id confirm` | Same as remove, confirmed with the option. |
| `/product view product_id` | Staff details (incl. the uploaded file's name/size/type) and the customer card. |
| `/product list [include_archived]` | Paginated staff list. |

## Settings (`[catalog]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `listing_channel` | `catalog` | Channel key of the listing; `""` removes the listing. |
| `products_per_listing_message` | `1` | Product cards per listing message (1-10). |
| `manager_roles` | `[]` | Extra role keys that may use `/product` (admins always can). |
| `browse_enabled` | `false` | Private page-by-page browsing button on the header (and the shop panel). |
| `image_hosts` | `[]` | Allowed hosts for image **links** (`[]` = any https host). Uploads are not affected. |
| `low_stock_threshold` | `0` | 1..N units left shows the low-stock text in the warning color; 0 = off (business decision). |

## Content keys (all wording is configured; see `docs/CONTENT_CHECKLIST.md`)

- Listing: `catalog.listing.title`, `catalog.listing.description`, `catalog.listing.empty`.
- Cards: `catalog.field.price`, `catalog.field.availability`, `catalog.field.max_per_order` (optional),
  `catalog.availability.in_stock` / `.unlimited` / `.out_of_stock`, `catalog.availability.low_stock` (optional,
  only with a threshold; falls back to `in_stock`).
- Buttons: `catalog.button.browse`, `catalog.button.buy` (required while checkout is on; `{name}` = product name —
  use it when several products share a message), `catalog.button.buy_short` (optional: used when `{name}` makes the
  label longer than Discord's 80 characters; the listing reports it as missing when such a product exists),
  `catalog.button.buy_emoji` (optional).
- Checkout replies: `checkout.started.description`, `checkout.buy_added.description` (optional),
  `checkout.limit_reached.description`, `checkout.error.*`.

Missing required text keeps the catalog "not ready": its listing is withdrawn and its controls answer with the
configured "unavailable" text. Optional keys are simply omitted.

## Database

Migration `0006_catalog_images.sql`: `product_images(product_id PRIMARY KEY → products, guild_id, filename, content_type,
size, sha256, data BLOB, uploaded_by, created_at)` with CHECKs on the type list, the 8 MB size, the hex digest and
`length(data) = size`. Products are never deleted (orders reference them); archiving removes the image row.

## Reuse by other features

`shopbot.features.catalog.images.image_for(app, product) -> (embed image URL or None, files)` returns what a product
card shows (an `attachment://` URL plus the `FileSpec` to attach, or the image link). Put the files in
`MessageSpec.files` (a tuple, also when empty, for a message that is edited later). `views.product_embed(...,
image_url=..., low_stock_threshold=settings.catalog.low_stock_threshold)` builds the same card. Generic support:
`MessageSpec.files` (`None` = the message does not manage attachments; a tuple = exactly these, replaced on edit),
`ui.limits` checks file count/size/names and `attachment://` references, `core.panels.upsert_message(prepare=...)`
loads file bytes only for messages that are actually sent or edited (hashes use the sha256).

## Testing it in Discord (manual)

1. Configure the catalog and checkout text (`/status` lists what is missing), set the currency and payment provider,
   run `/setup`. `#catalog` shows the header message, then one message per product.
2. `/product add` a product with stock 2; check its card is green (success) with an enabled buy button.
3. `/product image product_id:<id> file:<a PNG>`; the card shows the image. `/product stock product_id:<id> mode:add
   amount:1`; the image is still shown after the edit. Restart the bot; the image is still shown.
4. As a member without an order channel, click Buy: a private order channel opens with the product in the cart and
   you get a private link. Click Buy on another product: the same channel's cart gains it; the reply links it.
5. Submit an order for the remaining stock; the card turns red (danger) and its buy button is disabled.
6. `/product remove product_id:<id>` → Remove; the product's message disappears and the others keep their order.

## Limitations

- Not yet verified in a live Discord server (tested with automated fakes and the offline preview).
- Every edit of a message with an uploaded image uploads the image again (Discord replaces attachments on edit);
  with one product per message only the changed product's message is edited.
- Servers without boosts accept 10 MiB per message upload; one image is at most 8 MB, and messages with several
  images are split to stay under 10 MiB.
- Uploaded images live in the SQLite database, so they count toward the database size on the volume and toward
  every `/backup` copy. Archiving or removing a product deletes its stored image.
- Accepted formats are PNG, JPEG, GIF and WebP, recognized from the file's own bytes (SVG and other formats are
  refused).
- The buy button does not offer a quantity: it adds one unit; the customer changes the quantity in the cart step.
- The listing shows the buy button whenever checkout is switched on, even while checkout misses text (the click then
  answers "unavailable"), so the listing does not have to be re-posted when checkout becomes ready.
