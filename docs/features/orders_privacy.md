# Order ticket closure, #orders privacy and staff command visibility

Package `orders_privacy` of the server specification. It covers three requirements:

| Spec item | What the bot does | Where it lives |
|---|---|---|
| §4 Closing Tickets: "Tickets must support authorized manual closure" | `/ticket close` also works in an **order ticket** whose order was never submitted (an abandoned cart) | `tickets.commands` (dispatch), `checkout.service.staff_close_ticket` (the order rules) |
| §5 #orders: "Private information must not be exposed in public channels. Sensitive details should be shown only to authorized people through a properly permission-checked interaction" | the customer's typed form answers are **not** posted in #orders unless `operations.answers_in_orders_channel = true`; staff open them privately | `checkout.service.post_staff_details`, `[operations]` settings |
| §8 role-restricted staff commands | the shipped Staff role carries **Manage Messages**, so staff see `/orders`, `/order`, `/ticket`, `/review` and `/announce` in Discord's command picker | `config/settings.toml` (`[[server.roles]]` `staff`) |

## 1. Closing an order ticket (`/ticket close`)

Run `/ticket close` (optional `reason`) inside the customer's order ticket. The command is staff-only: Discord shows it
to members with Manage Messages, and the bot checks on every use that the caller is staff for order tickets
(`permissions.staff_roles`, `checkout.staff_roles` or an admin). A customer, or staff of another ticket category only,
is refused. In order tickets the command works while `features.checkout` is on, even when the support-ticket texts
are not configured yet. What happens depends on the ticket's order, re-read from the database at that moment:

| Ticket state | Result |
|---|---|
| Unfinished order (the customer never pressed Submit) | The unfinished order is **cancelled** (`checkout_sessions.status = cancelled`) in one conditional transaction that also schedules the durable `checkout.ticket_check` task; the live panel shows the staff-closed text (no controls); the ticket is closed like an automatic closure: the customer can still read it but no longer post, transcript per `checkout.transcript_on_close`, deletion per `checkout.delete_channel_after_close_minutes`. Audited as `checkout.session_closed_by_staff` (with the optional reason) and `tickets.closed`. Nothing was reserved: stock and store credit are only taken when an order is submitted. |
| A submitted order that is still open (waiting for payment, paid, in progress) | **Refused**, nothing changes. The reply links the order's record in #orders: end the order there (Reject, Cancel, Complete, ...) or with `/order cancel`; its ticket closes automatically when the order is final. |
| No unfinished order and no open order (it ended, but the channel stayed open, e.g. the process stopped before closing it; or the channel never got an order workflow) | The ticket is closed. |
| Already closed / being created | "not open" reply, nothing changes. |

The customers keep their own **Cancel** button on the live panel (unchanged).

Reliability:

- **Duplicates and races.** Every write is conditional (`active -> cancelled` once, `open -> closed` once), and close,
  reopen and delete of one ticket are serialized. Two staff members closing at once, or staff closing while the
  customer cancels, ends the order once, closes the ticket once and produces one transcript. If the customer submits
  at the same moment, whichever commits first wins: a submitted order is then refused as above.
- **Restarts.** The cancellation commits together with a `checkout.ticket_check` task; if the bot stops (or Discord
  fails) before the channel is closed, that task closes it after the restart. The staff reply then says the channel
  will be closed automatically.
- **Old buttons.** The ended panel has no controls; older copies of its buttons (also after a restart) answer with the
  configured `checkout.error.session_closed` text.
- **Missing text.** Closing never depends on content: if the panel text cannot be built, the panel is left unchanged
  (logged) and the ticket is still closed.
- The ticket's close reason is the final status (`cancelled`, or the order's/session's status), so the transcript shows
  the configured status name (`{final_status}`); the staff member's free-text reason goes to the audit log
  (`checkout.session_closed_by_staff`, or `checkout.ticket_closed_by_staff` when no unfinished order had to end).

Other `/ticket` subcommands (`add`, `remove`, `reopen`, `claim`, `transcript`, `info`) still act on support tickets
only; order tickets are handled from #orders.

## 2. Customer answers stay out of #orders

`checkout.details_form`, the fulfillment options' forms and `checkout.submission_form` collect text the customer types
(names, addresses, references ...). With `operations.answers_in_orders_channel = false` (shipped and default):

- nothing the customer typed is posted in the staff channel; the live order record shows only the reference, the
  customer mention, status, claim, order lines, totals, timeline and internal notes;
- staff see the answers, with their configured labels, **privately**: the record's **Details** button (a reply only
  the clicking staff member sees, after the server-side staff check) and `/order view` / `/order lookup` (staff-only,
  private reply);
- staff alerts in #staff-chat, Owner DMs, reminders and the audit log never contain answers (unchanged);
- restart recovery does not post them either.

With `true`, the previous behaviour returns: the answers are also posted as messages under each order record in the
staff channel (once per message part, deduplicated across restarts), visible to everyone who can read #orders.

The customer still sees their own answers in their private order ticket (the details and review steps).

## 3. Staff command visibility

Discord shows a slash command only to members holding its *default member permission*. The staff commands use
Manage Messages (`/orders`, `/order`, `/ticket`, `/review`, `/announce`); the admin commands use Administrator; `/product`
(and `/credits`) use Manage Server. The shipped Staff role is therefore:

```toml
[[server.roles]]
key = "staff"
permissions = ["manage_messages"]
```

`/setup` applies it (the bot holds Manage Messages itself, which Discord requires to grant it). Consequences:

- Staff can also delete and pin messages in every channel they can see, including the bot's panels; running `/setup`
  posts a deleted panel again.
- `/setup` sets **exactly** the listed permissions on the role: an existing Staff role you adopted loses other
  server-wide permissions unless you list them too. With `permissions = []` an adopted role is left untouched.
- The permission only controls visibility. The bot checks the Staff role on every use; a member with Manage Messages
  but without a staff role is refused.

Fine-tuning instead (or in addition): **Server Settings → Integrations → (the bot) → Manage**: allow or deny each
command for specific roles, members or channels. Discord's overrides only change who sees and can start a command;
the bot's own checks still apply.

`/product` (catalog managers): the bot allows admins and roles in `catalog.manager_roles`. To let the Staff role manage
products, add `"staff"` to `catalog.manager_roles` **and** allow `/product` for the Staff role under Integrations (do
not give Staff Manage Server: it would let them change the server settings).

## Settings

| Setting | Shipped | Meaning |
|---|---|---|
| `operations.answers_in_orders_channel` | `false` | `true` = also post the customer's form answers under each order record in the staff channel. |
| `server.roles` `staff` `permissions` | `["manage_messages"]` | Shows the staff commands to the Staff role; also lets staff delete/pin messages. |

A value other than `true`/`false` for `answers_in_orders_channel` is a settings error, reported like every invalid
setting (at startup, or by `/reload`, which then keeps the previous configuration).

## Content keys

- `checkout.staff_closed.title`, `checkout.staff_closed.description` (both optional): the live panel after staff
  closed the ticket of an unfinished order. Empty = the cancelled texts (`checkout.cancelled.title` /
  `.description`), so nothing new is required.
- `checkout.staff.details_title` (optional, existing): only used when `answers_in_orders_channel = true`.

Operator text (staff replies, overridable with `operator.<key>`): `checkout.op.ticket_closed`,
`checkout.op.ticket_session_closed`, `checkout.op.ticket_close_pending`, `checkout.op.ticket_order_open` (+
`_hint`), `checkout.op.ticket_not_open`, `checkout.op.ticket_busy`.

## Commands

- `/ticket close [reason]` — in a support ticket as before; in an order ticket as described above.

## Testing manually in Discord

1. Run `/setup`; Server Settings → Roles → Staff shows *Manage Messages*. With a staff account (Staff role only), type
   `/` in a channel: `/orders`, `/order`, `/ticket`, `/review`, `/announce` are listed; `/setup` and `/product` are not.
   With a customer account none of them are listed (only `/myorders`, once its description is configured).
2. As a customer, press Start in #purchase and add an item, but do not submit. As staff, run `/ticket close` in that
   ticket: the reply says the unfinished order was cancelled; the customer's panel shows the staff-closed (or
   cancelled) text without buttons; the customer can read but not write; #transcripts gets one transcript; #bot-log
   shows the closure. Run `/ticket close` again: "not open".
3. Place and submit an order, then run `/ticket close` in its ticket: refused with a link to the order's record. Reject
   or cancel it from #orders: the ticket closes.
4. Configure a details or submission form, place an order: #orders shows only the record; press **Details** (staff) to
   see the answers privately; `/order view <number>` shows them too. A customer pressing an old Details button is
   refused. Set `operations.answers_in_orders_channel = true`, `/reload`, place another order: the answers appear under
   its record.

## Limitations

- Switching `answers_in_orders_channel` off does not delete answer messages already posted in #orders; delete them by
  hand. Switching it on posts the answers of open orders at the next restart (recovery) or the next follow-up.
- Transcripts in #transcripts (staff-only, a file per closed ticket, required by the specification) contain the whole
  ticket conversation, including what the customer entered on the live panel.
- The staff member's `/ticket close` reason is recorded in the audit log only (order tickets have no closed message).
- No direct message is sent to the customer when staff close an abandoned cart; the ticket's panel shows it.
- Not verified in a live Discord server yet (automated tests with fakes only).
