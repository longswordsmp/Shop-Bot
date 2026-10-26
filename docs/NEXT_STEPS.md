# Next steps

What is left to do before customers use the bot, the decisions only the owner can make, and the next features worth
building. Updated as work lands; the step-by-step setup is in `docs/MORNING.md`.

## 1. Check it on the real server

Everything is tested offline (2,000+ automated tests, the house-style checker and the HTML preview), but nothing has
been tried in a real Discord server yet. Do step 12 of `docs/MORNING.md` with a second (customer) account. The parts
only a real server can confirm:

1. **Banners inside the cards.** Each picture sits at the bottom of its card, and is still there after a button
   press or a status change on the order ticket and the #orders record.
2. **Icons.** Small icons show in front of button labels, in section headings and in field names; no card title shows
   raw text such as `<:truck:123…>`.
3. **Payment methods.** In an order ticket, press *Credit or debit card*: the private "Dear customer, we're sorry …"
   message appears with *Submit gift card* (opens the gift card form) and *Buy a gift card*.
4. **#shipping.** One message, one card: Standard "about 2 weeks", Expedited "usually 3–4 days", the US states by
   name at the bottom of the same card (one line per dot), the picture at the very bottom.
5. **Products with pictures.** `/product add image:<picture>`: the picture shows under the item's name in #catalog.
6. **Staff tools.** `/stats` answers privately with the week's sales, the open orders by next step and the best
   sellers. Order an item down to 3 units: one "Low stock" alert in #staff-chat; order the rest: one "Sold out" alert.
7. **Discount codes.** `/discount create` a code, then in an order ticket press **Add discount code** on the review
   step: the discount shows under the subtotal and stays on the order card, the #orders record and `/order view`
   (the checks are in step 12 of `docs/MORNING.md`).
8. **Reviews.** #reviews starts with the intro panel and its **Write a review** button (members cannot type in
   #reviews, so the button is the way there; `/review` works in an open ticket). Write a review → stars → the form
   with a photo upload → *Approve* in #staff-chat → #reviews shows the stars, the text and the photo without the
   member's name (step 12 of `docs/MORNING.md`). The staff tools are now `/reviews` (they were `/review`). A text changed earlier with
   `/content set` that uses `{user_name}` in `reviews.published.title`, or `{order_number}` in a decision notice
   (`reviews.notice.*`), is refused now (published reviews never name the member; a review may have no order):
   `/status` names it, and reviews stay hidden until it is changed. A review with a photo now always waits for
   *Approve* (`reviews.photo_requires_approval`, shipped on). The photo upload inside the form is Discord's newer
   file upload field: try it once from a phone and a computer (step 12 of `docs/MORNING.md`).
9. **Categories.** `/category create name:Old stock`, `/product categories product_id:<id> add:Old stock`; pick Old
   stock in the #catalog menu: a private message with its items and their Buy buttons; the same pick works twice in a
   row. Then set up the shop's real categories (at most 24, besides the built-in All items).

## 2. Decisions only the owner can make

- **Which payment methods to list.** `config/settings.toml` → `[[payments.methods]]` lists credit/debit card, Apple
  Pay, Google Pay, PayPal and Cash App as *temporarily down*. Keep only the ones the shop really offers, and say
  "temporarily down" only while that is true. When one works again, set its `status = "active"` (see
  `docs/CONFIGURATION.md`, `[[payments.methods]]`).
- **Gift cards as the only way to pay.** Many buyers see gift-card-only payment as a warning sign (the US FTC tells
  consumers that real businesses do not ask to be paid with gift cards), and DoorDash's terms restrict reselling and
  redeeming cards for others. Bringing a card option back (section 3) removes that risk.
- **Amazon's logo on the banners.** The banner pictures show the "amazon" wordmark. A version without it avoids
  customers thinking the shop is Amazon or partnered with Amazon, and the server being reported.
- **Delivery times.** "About 2 weeks" and "usually 3–4 days" are promises to customers; change the texts if they
  differ by region.
- **#catalog with many items.** Discord opens a channel at its newest message, so with one public card per item
  members land at the bottom of #catalog while the category menu is on the first message at the top. Either pin
  that header message (the menu is then one click away in Pins) or set `product_cards = false` under `[catalog]` so
  #catalog is one card with the menu and the items are browsed privately (then consider emptying
  `catalog.picker.description`, which repeats the header's own text).
- **Bulk orders and the package coupon.** The FAQ says bulk orders cost nothing extra and get a percentage off
  (staff send a discount code in the ticket): decide the percentage and from what quantity. The warehouse post
  promises a 20% off coupon in every package (old stock only, can be given to a friend): create it before posting
  (`docs/MORNING.md`, step 12) and choose whether one customer may use it more than once (`once_per_customer`).

- **Reviews without an order.** `/review` from a member without a delivered order writes an unverified review (no
  "Verified purchase" marker), always checked by staff first, one per member every 30 days
  (`reviews.allow_unverified`, `reviews.unverified_cooldown_days`). Turn it off once the old customers have reviewed
  again, if only buyers should review.

## 3. Bringing card payments back

- **Quick: a payment page.** A Stripe Payment Link, PayPal.me or Square page can be offered as an active method today
  (`kind = "link"`, `url = "https://…"`). Customers pay there and staff press **Mark paid** after checking the
  payment. Limits: the customer types the amount, and staff match payments to orders by hand.
- **Proper: card checkout per order.** A payment provider that creates a checkout page for the exact total of each
  order and is told by a signed webhook when it is paid, so the order is marked paid automatically (refunds through
  the provider). It needs a Stripe (or similar) account, two secrets in Railway's variables, and a public HTTPS
  address for the webhook (Railway gives the service one). The extension point is ready
  (`shopbot/features/checkout/payments/__init__.py`); building it takes one focused session once the account exists
  (test mode first).

## 4. Features worth building next

1. **Order-specific payment links.** For a `link` method, add the order number and total to the page link, so staff
   can match payments without asking.
2. **Saved address.** Offer the customer's last address on their next order (still entered privately, never shown in
   a channel).
3. **A tip without an order.** Tips are added to an order on its review step and paid with it (`[checkout.tips]`,
   `docs/DECISIONS.md` §11g). A tip on its own (for example a "Tip our team" button in a support ticket) needs its own
   payment: a gift card checked by staff like an order's, or a payment page once card payments are back. With it, a
   refund could be marked as "the tip" (today `/stats` counts a refund against the items first, so a refunded tip
   still shows as kept).

Done since this list was written: `/stats` for staff, low-stock / sold-out alerts in #staff-chat
(`operations.stock_alerts`, see `docs/features/operations.md`), and discount codes: `/discount` (percent off, amount
off, buy one get one free; product limits, minimum, uses, end date, once per customer), the code field and claimed
coupons on the review step, the discount on the order card, the #orders record and `/order view` (see
`docs/DECISIONS.md` §11e), and catalog categories: a menu on the #catalog header that shows a category's items in a
private message, `/category` and `/product categories` (see `docs/DECISIONS.md` §11j).
