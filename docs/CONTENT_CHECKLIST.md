# Content checklist

Every piece of text customers can see, by feature. Fill values in `config/content.toml`
(or with `/content set` in Discord). Empty means not configured: the related panel or message
is not shown until its required keys are set. Configured text is used exactly as written.
Placeholders such as `{number}` are replaced with data; other text, including braces, is kept as-is.

Regenerate with `python -m tools.checklist --write` after changing settings.toml.

## Shared (errors, buttons, formats, branding)

Feature `core` — enabled.

### Branding text

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `branding.author_name` | Optional author line at the top of every customer-facing embed (uses the logo as icon). | author (256) | optional |  |
| ▫️ | `branding.footer_text` | Optional footer on every customer-facing embed. | footer (2048) | optional |  |

### Shared buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `common.button.back` | Back button in multi-step workflows. | button_label (80) | optional |  |
| ❌ | `common.button.cancel` | Cancel button on prompts and workflows. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.confirm` | Confirm button on confirmation prompts. | button_label (80) | required (used by an enabled feature) |  |
| ▫️ | `common.button.continue` | Continue button in multi-step workflows. | button_label (80) | optional |  |
| ❌ | `common.button.next_page` | Next-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.previous_page` | Previous-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |

### Error messages (shown privately to the person who clicked)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `common.error.busy` | Shown when someone clicks again while their previous click is still being processed. Optional: if empty, the duplicate click is silently ignored. | description (4096) | optional |  |
| ❌ | `common.error.cooldown` | Shown when someone acts too quickly in a row. | description (4096) | required | `{retry_after}` |
| ❌ | `common.error.generic` | Shown when an action fails unexpectedly. Internal details are never shown. | description (4096) | required |  |
| ❌ | `common.error.invalid_input` | Shown when a form entry is invalid (e.g. a quantity that is not a whole number). | description (4096) | required |  |
| ❌ | `common.error.not_found` | Shown when the ticket/order/item behind a control no longer exists. | description (4096) | required |  |
| ❌ | `common.error.permission_denied` | Shown when someone uses a control they are not allowed to use (e.g. a staff-only button). | description (4096) | required |  |
| ▫️ | `common.error.title` | Optional title above every error message. | title (256) | optional |  |
| ❌ | `common.error.unavailable` | Shown when a feature is switched off or not fully configured. | description (4096) | required |  |

### Formats

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `common.money_format` | How prices are written, e.g. '{amount} {currency}'. {amount} is formatted with the separators from settings.checkout. Required by catalog and checkout. | format (100) | required (used by an enabled feature) | `{amount}`, `{currency}` |
| ▫️ | `common.page_indicator` | Optional page indicator added to the footer of paginated lists. | format (100) | optional | `{page}`, `{pages}` |
| ▫️ | `common.progress_format` | Optional step indicator shown in multi-step workflows. {bar} uses the progress symbols from settings.branding. | format (100) | optional | `{current}`, `{total}`, `{bar}` |

## Server structure (role, category, channel names)

Feature `server` — enabled.

### Category names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `server.category.admin.name` | Name of the 'admin' channel category. | category_name (100) | required |  |
| ❌ | `server.category.orders.name` | Name of the 'orders' channel category. | category_name (100) | required |  |
| ❌ | `server.category.staff.name` | Name of the 'staff' channel category. | category_name (100) | required |  |
| ❌ | `server.category.support.name` | Name of the 'support' channel category. | category_name (100) | required |  |
| ❌ | `server.category.tickets.name` | Name of the 'tickets' channel category. | category_name (100) | required |  |

### Channel names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `server.channel.audit_log.name` | Name of the 'audit_log' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.catalog.name` | Name of the 'catalog' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.order_queue.name` | Name of the 'order_queue' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.reviews.name` | Name of the 'reviews' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.shop.name` | Name of the 'shop' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.staff_alerts.name` | Name of the 'staff_alerts' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.ticket_panel.name` | Name of the 'ticket_panel' channel. | channel_name (100) | required |  |
| ❌ | `server.channel.transcripts.name` | Name of the 'transcripts' channel. | channel_name (100) | required |  |

### Channel topics

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `server.channel.audit_log.topic` | Optional topic (description) of the 'audit_log' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.catalog.topic` | Optional topic (description) of the 'catalog' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.order_queue.topic` | Optional topic (description) of the 'order_queue' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.reviews.topic` | Optional topic (description) of the 'reviews' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.shop.topic` | Optional topic (description) of the 'shop' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.staff_alerts.topic` | Optional topic (description) of the 'staff_alerts' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.ticket_panel.topic` | Optional topic (description) of the 'ticket_panel' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.transcripts.topic` | Optional topic (description) of the 'transcripts' channel. | channel_topic (1024) | optional |  |

### Role names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `server.role.staff.name` | Name of the 'staff' role. | role_name (100) | required |  |

## Tickets

Feature `tickets` — enabled.

### Ticket status messages (posted in the ticket channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `tickets.auto_close_warning.description` | Posted in an inactive ticket (and pings the customer) tickets.auto_close_warning_minutes before it closes automatically. {minutes} is that number of minutes, {user_mention} mentions the customer. Needed when both auto-close settings are above 0. | description (4096) | optional | `{minutes}`, `{user_mention}` |
| ▫️ | `tickets.auto_closed.description` | Text of the ticket-closed message when the ticket was closed automatically for inactivity (used instead of tickets.closed.description). {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.auto_close_inactive_minutes is above 0. | description (4096) | optional | `{number}` |
| ❌ | `tickets.claimed.description` | Posted in the ticket when a staff member claims it. {staff_mention} mentions that staff member. | description (4096) | required | `{staff_mention}` |
| ❌ | `tickets.closed.description` | Text of the ticket-closed message when someone closed it. {closer_mention} mentions who closed it. {number} is the ticket number, zero-padded to tickets.number_padding digits. | description (4096) | required | `{closer_mention}`, `{number}` |
| ▫️ | `tickets.closed.reason_label` | Optional field name under which the close reason given by staff (/ticket close reason) is shown on the ticket-closed message. If empty, the reason is not shown to the customer. | field_name (256) | optional |  |
| ❌ | `tickets.closed.title` | Title of the message posted in the ticket when it is closed (manually or automatically); this message carries the reopen/transcript/delete buttons. {number} is the ticket number, zero-padded to tickets.number_padding digits. | title (256) | required | `{number}` |
| ▫️ | `tickets.deletion_scheduled.description` | Posted in the ticket after closing when the channel will be deleted automatically. {minutes} is tickets.delete_after_close_minutes. Needed when that setting is above 0. | description (4096) | optional | `{minutes}` |
| ❌ | `tickets.reopened.description` | Posted in the ticket when it is reopened. {actor_mention} mentions who reopened it. Needed when tickets.allow_reopen = true. | description (4096) | required | `{actor_mention}` |
| ❌ | `tickets.unclaimed.description` | Posted in the ticket when it is unclaimed. | description (4096) | required |  |
| ❌ | `tickets.user_added.description` | Posted in the ticket when staff add someone to it (/ticket add). {user_mention} mentions (and pings) the added member. | description (4096) | required | `{user_mention}` |
| ❌ | `tickets.user_removed.description` | Posted in the ticket when staff remove someone from it (/ticket remove). {user_mention} mentions the removed member. | description (4096) | required | `{user_mention}` |

### Ticket buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.button.claim` | Claim button on the ticket welcome message (staff take ownership of the ticket). | button_label (80) | required |  |
| ❌ | `tickets.button.close` | Close button on the ticket welcome message. | button_label (80) | required |  |
| ❌ | `tickets.button.delete` | Delete button on the ticket-closed message (staff only; asks for confirmation). | button_label (80) | required |  |
| ❌ | `tickets.button.reopen` | Reopen button on the ticket-closed message. Needed when tickets.allow_reopen = true. | button_label (80) | required |  |
| ❌ | `tickets.button.transcript` | Transcript button on the ticket-closed message (staff only; posts a transcript to the transcript channel). Needed when tickets.transcript_channel is set. | button_label (80) | required |  |
| ❌ | `tickets.button.unclaim` | Unclaim button that replaces the claim button while a staff member has claimed the ticket. | button_label (80) | required |  |

### Ticket category 'general'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `tickets.category.general.button_emoji` | Optional emoji on the 'general' panel button / selection option. | emoji (100) | optional |  |
| ❌ | `tickets.category.general.button_label` | Label of the 'general' button on the ticket panel (also the option label when the panel uses a selection menu, and {category_label} in the staff alert). | button_label (80) | required |  |
| ❌ | `tickets.category.general.channel_prefix` | Channel-name prefix for 'general' tickets ({prefix} in tickets.channel_name_pattern). Discord lower-cases channel names and replaces spaces with dashes. | channel_prefix (60) | required |  |
| ▫️ | `tickets.category.general.channel_topic` | Optional topic of each 'general' ticket channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. {user_name} is the customer's display name. | channel_topic (1024) | optional | `{number}`, `{user_name}` |
| ▫️ | `tickets.category.general.select_description` | Optional small text under the 'general' option when the panel uses a selection menu. | option_description (100) | optional |  |
| ❌ | `tickets.category.general.welcome_description` | Text of the welcome message posted in a new 'general' ticket. {user_mention} mentions the customer, {user_name} is their display name. {number} is the ticket number, zero-padded to tickets.number_padding digits. Form answers (if the category has a form) are added below as fields named by the form labels. | description (4096) | required | `{user_mention}`, `{user_name}`, `{number}` |
| ❌ | `tickets.category.general.welcome_title` | Title of the welcome message posted in a new 'general' ticket (the message that carries the close/claim buttons). {number} is the ticket number, zero-padded to tickets.number_padding digits. Mentions do not render in titles; use {user_name}. | title (256) | required | `{user_mention}`, `{user_name}`, `{number}` |

### Ticket confirmation prompts (private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.close_confirm.description` | Text of the private prompt asking to confirm closing a ticket (uses the shared confirm/cancel buttons). Needed when tickets.close_confirmation = true. | description (4096) | required |  |
| ▫️ | `tickets.close_confirm.title` | Optional title of the private prompt asking to confirm closing a ticket. | title (256) | optional |  |
| ❌ | `tickets.delete_confirm.description` | Text of the private prompt asking staff to confirm deleting a ticket channel (uses the shared confirm/cancel buttons). | description (4096) | required |  |
| ▫️ | `tickets.delete_confirm.title` | Optional title of the private prompt asking staff to confirm deleting a ticket channel. | title (256) | optional |  |

### Ticket private replies

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.created.description` | Private reply after a ticket was created. {channel_mention} links the new ticket channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. | description (4096) | required | `{channel_mention}`, `{number}` |
| ❌ | `tickets.error.not_open` | Private error when a ticket button cannot be used in the ticket's current state (e.g. closing an already closed ticket, claiming an already claimed ticket, deleting an open ticket). | description (4096) | required |  |
| ❌ | `tickets.limit_reached.description` | Private error when the customer already has the maximum number of open tickets in that category. {limit} is that maximum. | description (4096) | required | `{limit}` |

### Ticket panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.panel.description` | Text of the ticket panel. | description (4096) | required |  |
| ▫️ | `tickets.panel.select_placeholder` | Greyed-out text of the category selection menu (used when tickets.panel_style = "select"). | select_placeholder (150) | optional |  |
| ❌ | `tickets.panel.title` | Title of the ticket panel (the message with the open buttons). | title (256) | required |  |

### New-ticket staff alert

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.staff_alert.description` | Text of the new-ticket staff alert. {user_mention} mentions the customer, {channel_mention} links the ticket, {category_label} is the category's button label. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | description (4096) | required | `{user_mention}`, `{channel_mention}`, `{number}`, `{category_label}` |
| ❌ | `tickets.staff_alert.title` | Title of the alert posted in the staff alert channel for each new ticket. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | title (256) | required | `{number}`, `{category_label}` |

### Ticket transcripts

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.transcript.description` | Text of the transcript message in the transcript channel. {user_mention} mentions the customer; {closer_mention} mentions who closed the ticket (the bot for automatic closes, or the staff member who requested the transcript while the ticket was open). {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | description (4096) | required | `{number}`, `{user_mention}`, `{closer_mention}` |
| ❌ | `tickets.transcript.title` | Title of the message (with the transcript file) posted in the transcript channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | title (256) | required | `{number}` |
| ▫️ | `tickets.transcript_dm.description` | Text of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | description (4096) | optional | `{number}` |
| ▫️ | `tickets.transcript_dm.title` | Title of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | title (256) | optional | `{number}` |

## Catalog

Feature `catalog` — enabled.

### Catalog: product cards (listing and private browsing)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.availability.in_stock` | Availability of a product with limited stock that can still be ordered. {count} is the number of units that can still be ordered (stock minus units held by pending orders). | field_value (1024) | required | `{count}` |
| ❌ | `catalog.availability.out_of_stock` | Availability of a product whose stock is used up (still listed, cannot be ordered). | field_value (1024) | required |  |
| ❌ | `catalog.availability.unlimited` | Availability of a product without a stock limit. | field_value (1024) | required |  |
| ❌ | `catalog.field.availability` | Name of the availability field on every product card. The value is one of the catalog.availability.* texts. | field_name (256) | required |  |
| ▫️ | `catalog.field.max_per_order` | Optional: name of a field showing the maximum quantity per order (the value is the number). Shown only on products that have a limit. If empty, the limit is not shown. | field_name (256) | optional |  |
| ❌ | `catalog.field.price` | Name of the price field on every product card. The value is the price written with common.money_format. | field_name (256) | required |  |

### Catalog: private browsing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.button.browse` | Button under the last listing message that opens a private, page-by-page view of the products (only when catalog.browse_enabled is true). | button_label (80) | required |  |

### Catalog: live product listing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.listing.description` | Text of the header card at the top of the product listing channel. | description (4096) | required |  |
| ❌ | `catalog.listing.empty` | Shown below the listing header, and in private browsing, when no product is currently offered. | description (4096) | required |  |
| ❌ | `catalog.listing.title` | Title of the header card at the top of the product listing channel. | title (256) | required |  |

## Store credit

Feature `credits` — enabled.

### Credits: notice to a member after staff changed their balance (direct message)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `credits.adjusted_notice.description` | Optional text of the direct message sent to a member after an admin added or removed credits (/credits adjust). {amount} is the change written with common.money_format (negative for a removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not notified. Requires features.dm_notifications. | description (4096) | optional | `{amount}`, `{balance}` |
| ▫️ | `credits.adjusted_notice.title` | Optional title of the direct message sent to a member after an admin added or removed credits (/credits adjust). {amount} is the change written with common.money_format (negative for a removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not notified. | title (256) | optional | `{amount}`, `{balance}` |

### Credits: 'my balance' button and the private balance reply

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `credits.balance.description` | Text of the private reply to the 'my balance' button. {amount} is the member's current credit balance written with common.money_format. | description (4096) | required | `{amount}` |
| ▫️ | `credits.balance.title` | Optional title of the private reply to the 'my balance' button. If empty, the reply has no title. | title (256) | optional |  |
| ❌ | `credits.button.balance` | Label of the 'my balance' button that other panels (for example the shop panel) can show. Clicking it shows the member their own credit balance privately. | button_label (80) | required |  |

## Business decisions and settings still open

- None reported for the listed features.

## Summary

- Required keys (for the listed features): 64
- Required keys still missing: 64

Legend: ✅ configured · ❌ required and missing · ▫️ optional and empty
