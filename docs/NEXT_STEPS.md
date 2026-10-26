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
4. **#shipping.** Standard "about 2 weeks", Expedited "usually 3–4 days", the US states one by one, the picture at
   the very bottom.
5. **Products with pictures.** `/product add image:<picture>`: the picture shows under the item's name in #catalog.
6. **Staff tools.** `/stats` answers privately with the week's sales, the open orders by next step and the best
   sellers. Order an item down to 3 units: one "Low stock" alert in #staff-chat; order the rest: one "Sold out" alert.

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
2. **Discount codes.** A code field on the review step (percent or fixed amount, expiry, usage limit), shown on the
   receipt and the staff record.
3. **Saved address.** Offer the customer's last address on their next order (still entered privately, never shown in
   a channel).

Done since this list was written: `/stats` for staff, and low-stock / sold-out alerts in #staff-chat
(`operations.stock_alerts`, see `docs/features/operations.md`).
