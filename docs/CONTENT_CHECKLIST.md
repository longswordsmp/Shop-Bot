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
| ❌ | `common.button.back` | Back button in multi-step workflows. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.cancel` | Cancel button on prompts and workflows. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.confirm` | Confirm button on confirmation prompts. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.continue` | Continue button in multi-step workflows. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.next_page` | Next-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |
| ❌ | `common.button.previous_page` | Previous-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |

### Error messages (shown privately to the person who clicked)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `common.error.busy` | Shown when someone clicks again while their previous click is still being processed. Optional: if empty, the duplicate click is silently ignored. | description (4096) | optional |  |
| ❌ | `common.error.cooldown` | Shown when someone acts too quickly in a row. | description (4096) | required | `{retry_after}` |
| ❌ | `common.error.generic` | Shown when an action fails unexpectedly. Internal details are never shown. You may include {error_id}: a short reference that also appears in the bot's logs (empty when there is none). | description (4096) | required | `{error_id}` |
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
| ❌ | `common.progress_format` | Optional step indicator shown in multi-step workflows. {bar} uses the progress symbols from settings.branding. | format (100) | required (used by an enabled feature) | `{current}`, `{total}`, `{bar}` |

## Server structure (role, category, channel names)

Feature `server` — enabled.

### Category names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `server.category.catalog.name` | Name of the 'catalog' channel category. | category_name (100) | required |  |
| ✅ | `server.category.purchase.name` | Name of the 'purchase' channel category. | category_name (100) | required |  |
| ✅ | `server.category.staff.name` | Name of the 'staff' channel category. | category_name (100) | required |  |
| ✅ | `server.category.start_here.name` | Name of the 'start_here' channel category. | category_name (100) | required |  |

### Channel names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `server.channel.announcements.name` | Name of the 'announcements' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.bot_log.name` | Name of the 'bot_log' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.catalog.name` | Name of the 'catalog' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.faq.name` | Name of the 'faq' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.how_to_buy.name` | Name of the 'how_to_buy' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.new_arrivals.name` | Name of the 'new_arrivals' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.orders.name` | Name of the 'orders' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.purchase.name` | Name of the 'purchase' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.reviews.name` | Name of the 'reviews' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.shipping.name` | Name of the 'shipping' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.staff_chat.name` | Name of the 'staff_chat' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.transcripts.name` | Name of the 'transcripts' channel. | channel_name (100) | required |  |
| ✅ | `server.channel.welcome.name` | Name of the 'welcome' channel. | channel_name (100) | required |  |

### Channel topics

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `server.channel.announcements.topic` | Optional topic (description) of the 'announcements' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.bot_log.topic` | Optional topic (description) of the 'bot_log' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.catalog.topic` | Optional topic (description) of the 'catalog' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.faq.topic` | Optional topic (description) of the 'faq' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.how_to_buy.topic` | Optional topic (description) of the 'how_to_buy' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.new_arrivals.topic` | Optional topic (description) of the 'new_arrivals' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.orders.topic` | Optional topic (description) of the 'orders' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.purchase.topic` | Optional topic (description) of the 'purchase' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.reviews.topic` | Optional topic (description) of the 'reviews' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.shipping.topic` | Optional topic (description) of the 'shipping' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.staff_chat.topic` | Optional topic (description) of the 'staff_chat' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.transcripts.topic` | Optional topic (description) of the 'transcripts' channel. | channel_topic (1024) | optional |  |
| ▫️ | `server.channel.welcome.topic` | Optional topic (description) of the 'welcome' channel. | channel_topic (1024) | optional |  |

### Role names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `server.role.customer.name` | Name of the 'customer' role. | role_name (100) | required |  |
| ✅ | `server.role.member.name` | Name of the 'member' role. | role_name (100) | required |  |
| ✅ | `server.role.owner.name` | Name of the 'owner' role. | role_name (100) | required |  |
| ✅ | `server.role.restock_alerts.name` | Name of the 'restock_alerts' role. | role_name (100) | required |  |
| ✅ | `server.role.staff.name` | Name of the 'staff' role. | role_name (100) | required |  |

## #welcome rules and the "I agree" button (Member role)

Feature `verification` — enabled.

### Verification: private replies after the 'I agree' button

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `verification.already_verified.description` | Private reply to a member who clicks the 'I agree' button although they already have the Member role (nothing changes). {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.already_verified.title` | Optional title of the private reply to a member who clicks the 'I agree' button although they already have the Member role. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |
| ❌ | `verification.confirmed.description` | Private confirmation (only the member sees it) after they accepted the rules and received the Member role; the member-only channels are visible from now on. {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.confirmed.title` | Optional title of the private confirmation shown after the member accepted the rules and received the Member role. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |

### Verification: welcome panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `verification.button.emoji` | Optional emoji shown on the 'I agree' button. | emoji (100) | optional |  |
| ❌ | `verification.button.label` | Label of the button under the welcome panel that /setup posts in the 'welcome' channel; it accepts the rules and grants the Member role (the specification calls it "I agree, enter the server"). | button_label (80) | required |  |
| ❌ | `verification.welcome.description` | Short introduction to the server on the first card of the welcome panel that /setup posts in the 'welcome' channel. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.welcome.image_url` | Optional large image (https:// URL) at the bottom of the first card of the welcome panel that /setup posts in the 'welcome' channel. If empty, the branding banner is used when branding.panel_banner is on. | url (2048) | optional |  |
| ▫️ | `verification.welcome.thumbnail_url` | Optional small image (https:// URL) in the top-right corner of the first card of the welcome panel that /setup posts in the 'welcome' channel. If empty, the branding logo is used when branding.panel_thumbnail is on. | url (2048) | optional |  |
| ❌ | `verification.welcome.title` | Title of the first card of the welcome panel that /setup posts in the 'welcome' channel, for example a welcome line. | title (256) | required |  |

### Verification: direct message (verification.dm_confirmation)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `verification.dm.description` | Direct message confirming a successful verification, sent in addition to the private confirmation when verification.dm_confirmation and features.dm_notifications are on (required then). {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | optional | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.dm.title` | Optional title of the direct message confirming a successful verification. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |

### Verification: server rules (on the welcome panel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `verification.rules.description` | Optional text above the list of rules on the rules card. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.rules.title` | Optional heading of the rules card (the second card of the welcome panel that /setup posts in the 'welcome' channel). | title (256) | optional |  |

## #how-to-buy guide

Feature `how_to_buy` — enabled.

### How to buy guide

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `how_to_buy.introduction` | Introduction at the top of the step-by-step guide posted in the how_to_buy channel, above the steps. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `how_to_buy.title` | Title of the step-by-step guide posted in the how_to_buy channel. | title (256) | required |  |

### How to buy guide: channel link buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `how_to_buy.link.catalog.emoji` | Optional emoji on the link button that opens the 'catalog' channel. | emoji (100) | optional |  |
| ❌ | `how_to_buy.link.catalog.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'catalog' channel. | button_label (80) | required |  |
| ▫️ | `how_to_buy.link.faq.emoji` | Optional emoji on the link button that opens the 'faq' channel. | emoji (100) | optional |  |
| ❌ | `how_to_buy.link.faq.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'faq' channel. | button_label (80) | required |  |
| ▫️ | `how_to_buy.link.purchase.emoji` | Optional emoji on the link button that opens the 'purchase' channel. | emoji (100) | optional |  |
| ❌ | `how_to_buy.link.purchase.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'purchase' channel. | button_label (80) | required |  |

### How to buy guide: steps

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `how_to_buy.step.1.name` | Heading of step 1 of 4 in the step-by-step guide posted in the how_to_buy channel (write the step number yourself if you want one shown; channel links do not work in headings). | field_name (256) | required |  |
| ❌ | `how_to_buy.step.1.value` | Explanation of step 1 of 4 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `how_to_buy.step.2.name` | Heading of step 2 of 4 in the step-by-step guide posted in the how_to_buy channel (write the step number yourself if you want one shown; channel links do not work in headings). | field_name (256) | required |  |
| ❌ | `how_to_buy.step.2.value` | Explanation of step 2 of 4 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `how_to_buy.step.3.name` | Heading of step 3 of 4 in the step-by-step guide posted in the how_to_buy channel (write the step number yourself if you want one shown; channel links do not work in headings). | field_name (256) | required |  |
| ❌ | `how_to_buy.step.3.value` | Explanation of step 3 of 4 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `how_to_buy.step.4.name` | Heading of step 4 of 4 in the step-by-step guide posted in the how_to_buy channel (write the step number yourself if you want one shown; channel links do not work in headings). | field_name (256) | required |  |
| ❌ | `how_to_buy.step.4.value` | Explanation of step 4 of 4 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

## #faq

Feature `faq` — enabled.

### FAQ header

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `faq.introduction` | Optional introduction at the top of the FAQ posted in the faq channel, above the first section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `faq.title` | Optional title at the top of the FAQ posted in the faq channel. | title (256) | optional |  |

### FAQ section 'cancellations'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `faq.section.cancellations.q.1.answer` | Answer to question 1 of 1 in the 'cancellations' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `faq.section.cancellations.q.1.question` | Question 1 of 1 in the 'cancellations' section (channel links do not work in questions). | field_name (256) | required |  |
| ❌ | `faq.section.cancellations.title` | Title of the 'cancellations' section of the FAQ posted in the faq channel (e.g. the topic: general questions, order status...). | title (256) | required |  |

### FAQ section 'general'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `faq.section.general.q.1.answer` | Answer to question 1 of 1 in the 'general' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `faq.section.general.q.1.question` | Question 1 of 1 in the 'general' section (channel links do not work in questions). | field_name (256) | required |  |
| ❌ | `faq.section.general.title` | Title of the 'general' section of the FAQ posted in the faq channel (e.g. the topic: general questions, order status...). | title (256) | required |  |

### FAQ section 'order_status'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `faq.section.order_status.q.1.answer` | Answer to question 1 of 1 in the 'order_status' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `faq.section.order_status.q.1.question` | Question 1 of 1 in the 'order_status' section (channel links do not work in questions). | field_name (256) | required |  |
| ❌ | `faq.section.order_status.title` | Title of the 'order_status' section of the FAQ posted in the faq channel (e.g. the topic: general questions, order status...). | title (256) | required |  |

### FAQ section 'problems'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `faq.section.problems.q.1.answer` | Answer to question 1 of 1 in the 'problems' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `faq.section.problems.q.1.question` | Question 1 of 1 in the 'problems' section (channel links do not work in questions). | field_name (256) | required |  |
| ❌ | `faq.section.problems.title` | Title of the 'problems' section of the FAQ posted in the faq channel (e.g. the topic: general questions, order status...). | title (256) | required |  |

### FAQ section 'transactions'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `faq.section.transactions.q.1.answer` | Answer to question 1 of 1 in the 'transactions' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `faq.section.transactions.q.1.question` | Question 1 of 1 in the 'transactions' section (channel links do not work in questions). | field_name (256) | required |  |
| ❌ | `faq.section.transactions.title` | Title of the 'transactions' section of the FAQ posted in the faq channel (e.g. the topic: general questions, order status...). | title (256) | required |  |

## #shipping

Feature `shipping` — enabled.

### Shipping region lines

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `shipping.format.region` | How one region line is written, e.g. '{indicator} {name}'. {indicator} is the status indicator, {name} the region name. Must contain {indicator} and {name}. Required once regions are configured. | format (100) | optional | `{indicator}`, `{name}` |
| ▫️ | `shipping.format.region_with_note` | How a region line with a note is written, e.g. '{indicator} {name} ({note})'. Must contain {indicator}, {name} and {note}. Required once any region has a note. | format (100) | optional | `{indicator}`, `{name}`, `{note}` |

### Shipping header

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `shipping.introduction` | Introduction at the top of the shipping information posted in the shipping channel. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `shipping.title` | Title of the shipping information posted in the shipping channel. | title (256) | required |  |

### Shipping availability statuses

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `shipping.status.full.description` | Optional explanation at the top of the 'full' card (fully supported), above its regions. The card is shown when it has regions or this explanation. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `shipping.status.full.indicator` | Indicator (emoji) of the 'full' status (fully supported), written at the start of each of its regions (as {indicator} in shipping.format.region). | emoji (100) | required |  |
| ❌ | `shipping.status.full.label` | Title of the card listing the regions that are 'full' (fully supported). {indicator} is replaced with this status's indicator. Required while 'full' is listed in shipping.statuses. | title (256) | required | `{indicator}` |
| ▫️ | `shipping.status.none.description` | Optional explanation at the top of the 'none' card (not supported), above its regions. The card is shown when it has regions or this explanation. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `shipping.status.none.indicator` | Indicator (emoji) of the 'none' status (not supported), written at the start of each of its regions (as {indicator} in shipping.format.region). | emoji (100) | required |  |
| ❌ | `shipping.status.none.label` | Title of the card listing the regions that are 'none' (not supported). {indicator} is replaced with this status's indicator. Required while 'none' is listed in shipping.statuses. | title (256) | required | `{indicator}` |
| ▫️ | `shipping.status.partial.description` | Optional explanation at the top of the 'partial' card (partially supported), above its regions. The card is shown when it has regions or this explanation. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `shipping.status.partial.indicator` | Indicator (emoji) of the 'partial' status (partially supported), written at the start of each of its regions (as {indicator} in shipping.format.region). | emoji (100) | required |  |
| ❌ | `shipping.status.partial.label` | Title of the card listing the regions that are 'partial' (partially supported). {indicator} is replaced with this status's indicator. Required while 'partial' is listed in shipping.statuses. | title (256) | required | `{indicator}` |

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
| ❌ | `tickets.limit_reached.description` | Private error when the customer reached a ticket limit: the maximum number of open tickets in that category (also checked when they reopen their own closed ticket), or the maximum number of tickets created in 24 hours (tickets.max_created_per_user_per_day). {limit} is that maximum. | description (4096) | required | `{limit}` |

### Ticket panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `tickets.panel.description` | Text of the ticket panel. Needed when tickets.panel_channel is set. | description (4096) | optional |  |
| ▫️ | `tickets.panel.select_placeholder` | Greyed-out text of the category selection menu (used when tickets.panel_style = "select" and tickets.panel_channel is set). | select_placeholder (150) | optional |  |
| ▫️ | `tickets.panel.title` | Title of the ticket panel (the message with the open buttons). Needed when tickets.panel_channel is set. | title (256) | optional |  |

### New-ticket staff alert

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.staff_alert.description` | Text of the new-ticket staff alert. {user_mention} mentions the customer, {channel_mention} links the ticket, {category_label} is the category's button label. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | description (4096) | required | `{user_mention}`, `{channel_mention}`, `{number}`, `{category_label}` |
| ❌ | `tickets.staff_alert.title` | Title of the alert posted in the staff alert channel for each new ticket. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | title (256) | required | `{number}`, `{category_label}` |

### Ticket transcripts

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `tickets.transcript.description` | Text of the transcript message in the transcript channel. {user_mention} mentions the customer; {closer_mention} mentions who closed the ticket (the bot for automatic closes, or the staff member who requested the transcript while the ticket was open); {closed_at} is when it was closed (shown in each reader's time zone; empty while it is open); {final_status} is the order's final status name (checkout.status_label.*) for order channels, empty for support tickets; {close_reason} is the recorded close reason (the reason staff gave, or the automatic reason). {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | description (4096) | required | `{number}`, `{user_mention}`, `{closer_mention}`, `{closed_at}`, `{final_status}`, `{close_reason}` |
| ❌ | `tickets.transcript.title` | Title of the message (with the transcript file) posted in the transcript channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | title (256) | required | `{number}` |
| ▫️ | `tickets.transcript_dm.description` | Text of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | description (4096) | optional | `{number}` |
| ▫️ | `tickets.transcript_dm.title` | Title of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | title (256) | optional | `{number}` |

## Catalog

Feature `catalog` — enabled.

### Catalog: product cards (listing and private browsing)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.availability.in_stock` | Availability of a product with limited stock that can still be ordered (its card uses the success color). {count} is the number of units that can still be ordered (stock minus units held by pending orders). | field_value (1024) | required | `{count}` |
| ❌ | `catalog.availability.out_of_stock` | Availability of a product whose stock is used up (still listed, cannot be ordered; its buy button is disabled and its card uses the danger color). | field_value (1024) | required |  |
| ❌ | `catalog.availability.unlimited` | Availability of a product without a stock limit (its card uses the success color). | field_value (1024) | required |  |
| ❌ | `catalog.field.availability` | Name of the availability field on every product card. The value is one of the catalog.availability.* texts. | field_name (256) | required |  |
| ▫️ | `catalog.field.max_per_order` | Optional: name of a field showing the maximum quantity per order (the value is the number). Shown only on products that have a limit. If empty, the limit is not shown. | field_name (256) | optional |  |
| ❌ | `catalog.field.price` | Name of the price field on every product card. The value is the price written with common.money_format. | field_name (256) | required |  |

### Catalog: private browsing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `catalog.button.browse` | Button under the listing's header card that opens a private, page-by-page view of the products (only when catalog.browse_enabled is true). | button_label (80) | optional |  |

### Catalog: buy button on product cards

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.button.buy` | Button under each product card (listing and private browsing) that opens the customer's private order channel, or continues the one they already have, with this product in the cart. {name} is the product name (use it when catalog.products_per_listing_message is more than 1, so each button names its product). Disabled while the product is out of stock. | button_label (80) | required | `{name}` |
| ▫️ | `catalog.button.buy_emoji` | Optional emoji shown on the buy button. | emoji (100) | optional |  |
| ▫️ | `catalog.button.buy_short` | Optional: label used instead of catalog.button.buy for a product whose name makes that label longer than Discord's 80 characters. Required only when such a product exists (the listing reports it). | button_label (80) | optional |  |

### Catalog: live product listing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `catalog.listing.description` | Text of the header card at the top of the product listing channel. | description (4096) | required |  |
| ❌ | `catalog.listing.empty` | Shown below the listing header, and in private browsing, when no product is currently offered. | description (4096) | required |  |
| ❌ | `catalog.listing.title` | Title of the header card: the first message of the product listing channel (each product follows in its own message). | title (256) | required |  |

## #new-arrivals (Restock Alerts)

Feature `new_arrivals` — enabled.

### New arrivals: notification panel (posted by /setup in the new-arrivals channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `new_arrivals.button.toggle` | Label of the panel's single button. A click gives the member the notification role, or removes it when they already have it (so the label should describe both, e.g. turning notifications on or off). | button_label (80) | required |  |
| ▫️ | `new_arrivals.button.toggle_emoji` | Optional emoji shown on the panel's button. | emoji (100) | optional |  |
| ❌ | `new_arrivals.panel.description` | Text of the notification panel: what members receive when they turn notifications on, and that the same button turns them off again. {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `new_arrivals.panel.title` | Title of the persistent panel in the new-arrivals channel that lets members turn product notifications on or off. | title (256) | required |  |

### New arrivals: automatic product updates (posted in the new-arrivals channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `new_arrivals.button.view_catalog` | Optional label of a link button under each product update that opens the catalog listing channel (catalog.listing_channel). If empty, the updates have no button. | button_label (80) | optional |  |
| ▫️ | `new_arrivals.new_product.footer` | Optional small text at the bottom of a new-product update. | footer (2048) | optional |  |
| ❌ | `new_arrivals.new_product.title` | Title of the update posted automatically when a product is added to the catalog or switched back on (new_arrivals.announce_new_products). {name} is the product name. Below it the update shows the product's description, image, price and availability exactly like its catalog card. | title (256) | required | `{name}` |
| ▫️ | `new_arrivals.restock.footer` | Optional small text at the bottom of a restock update. | footer (2048) | optional |  |
| ❌ | `new_arrivals.restock.title` | Title of the update posted automatically when a sold-out product can be ordered again (new_arrivals.announce_restocks). {name} is the product name. Below it the update shows the product's description, image, price and availability exactly like its catalog card. | title (256) | required | `{name}` |

### New arrivals: private confirmations (shown only to the member who clicked)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `new_arrivals.subscribed.description` | Private confirmation shown after a member turned notifications on (they now have the notification role and are mentioned in product updates). {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `new_arrivals.subscribed.title` | Optional title of the private confirmation shown after a member turned notifications on. | title (256) | optional |  |
| ❌ | `new_arrivals.unsubscribed.description` | Private confirmation shown after a member turned notifications off (the notification role was removed; clicking the button again turns them back on). {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `new_arrivals.unsubscribed.title` | Optional title of the private confirmation shown after a member turned notifications off. | title (256) | optional |  |

## Checkout, orders and payment

Feature `checkout` — enabled.

### Checkout: order workflow - choosing products

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.browse.description` | Optional text of the product-choosing step (e.g. how to pick products and quantities). | description (4096) | optional |  |
| ❌ | `checkout.browse.empty_catalog` | Shown in the product-choosing step when no product is offered right now. | description (4096) | required |  |
| ❌ | `checkout.browse.select_placeholder` | Greyed-out text of the product selection menu (if empty, Discord would show its own default text). The options are the product names; the small text under each is its price. | select_placeholder (150) | required |  |
| ❌ | `checkout.browse.title` | Title of the order workflow message while the customer chooses products (first step). | title (256) | required |  |

### Checkout: order workflow - review and submit

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.button.apply_credits` | Button in the review step that applies the customer's store credit. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ❌ | `checkout.button.remove_credits` | Button in the review step that removes the applied store credit. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ❌ | `checkout.button.submit` | Button in the review step that submits the order. | button_label (80) | required |  |
| ▫️ | `checkout.review.credits_available_field` | Optional name of a field showing how much store credit the customer could apply (shown while credits are not applied). | field_name (256) | optional |  |
| ❌ | `checkout.review.credits_field` | Name of the field showing the store credit applied to the order (written with common.money_format). Needed when credits are enabled and credits.allow_at_checkout = true. | field_name (256) | required |  |
| ▫️ | `checkout.review.description` | Optional text of the review step. | description (4096) | optional |  |
| ❌ | `checkout.review.price_changed` | Shown in the review step when the total changed since the customer looked at it (prices, fees or their credit balance changed); nothing was submitted yet. {total} is the new total. | description (4096) | required | `{total}` |
| ❌ | `checkout.review.title` | Title of the final step where the customer reviews the order and submits it. | title (256) | required |  |
| ❌ | `checkout.review.total_field` | Name of the field showing the total to pay (review step, submitted order, staff order controls). | field_name (256) | required |  |

### Checkout: cancelling

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.button.cancel_order` | Button under a submitted order that lets the customer cancel it while it is not paid yet. | button_label (80) | required |  |
| ❌ | `checkout.cancel_confirm.description` | Question asking the customer to confirm cancelling an unfinished order (shared confirm/cancel buttons). | description (4096) | required |  |
| ▫️ | `checkout.cancel_confirm.title` | Optional title of the question asking the customer to confirm cancelling an unfinished order. | title (256) | optional |  |
| ❌ | `checkout.cancelled.description` | Text of the workflow message after the customer cancelled an unfinished order. | description (4096) | required |  |
| ▫️ | `checkout.cancelled.title` | Optional title of the workflow message after the customer cancelled an unfinished order (the channel is then closed). | title (256) | optional |  |
| ▫️ | `checkout.expired.description` | Text of the workflow message when an unfinished order expired. Needed when checkout.session_expiry_minutes is above 0. | description (4096) | optional |  |
| ▫️ | `checkout.expired.title` | Optional title of the workflow message when an unfinished order expired after checkout.session_expiry_minutes without activity. | title (256) | optional |  |
| ❌ | `checkout.order_cancel_confirm.description` | Question asking the customer to confirm cancelling a submitted, unpaid order (shared confirm/back buttons). {order_number} is the order number. | description (4096) | required | `{order_number}` |
| ▫️ | `checkout.order_cancel_confirm.title` | Optional title of the question asking the customer to confirm cancelling a submitted, unpaid order. {order_number} is the order number. | title (256) | optional | `{order_number}` |

### Checkout: order workflow - order details form

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.button.enter_details` | Button that opens the order details form. Needed when a form is defined. | button_label (80) | optional |  |
| ▫️ | `checkout.button.enter_more_details` | Button that opens the second part of the details form (Discord forms hold at most 5 fields; used when checkout.details_form plus an option's form have more than 5 fields together). | button_label (80) | optional |  |
| ▫️ | `checkout.details.description` | Optional text of the details step. | description (4096) | optional |  |
| ▫️ | `checkout.details.modal_title` | Title of the order details form. Needed when a form is defined. | modal_title (45) | optional |  |
| ▫️ | `checkout.details.title` | Title of the step where the customer enters order details (checkout.details_form and the chosen option's form). The answers are shown below as fields named by the field labels. Needed when a form is defined. | title (256) | optional |  |

### Checkout: shop panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.button.start` | Button on the shop panel that opens a private order channel for the customer. The panel also shows the catalog's browse button (catalog.button.browse) and the credits balance button when those are configured. | button_label (80) | required |  |
| ❌ | `checkout.panel.description` | Text of the shop panel. | description (4096) | required |  |
| ❌ | `checkout.panel.title` | Title of the shop panel (the message with the start button). | title (256) | required |  |

### Checkout: private replies to the start button

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.buy_added.description` | Optional: private reply when the customer clicks a product's buy button (catalog.button.buy) while they already have an unfinished order channel; the product is now in its cart (one unit, or the quantity already chosen). {name} is the product name, {channel_mention} links the channel. If empty, checkout.session_exists.description is shown. A buy button that opens a new order channel answers with checkout.started.description. | description (4096) | optional (falls back to `checkout.session_exists.description`) | `{name}`, `{channel_mention}` |
| ❌ | `checkout.limit_reached.description` | Private reply when the customer already has the maximum number of open orders (waiting for payment, paid, or in progress). {limit} is checkout.max_open_orders_per_user. | description (4096) | required | `{limit}` |
| ❌ | `checkout.order_exists.description` | Private reply when the customer clicks start while they already have the maximum number of open orders (checkout.max_open_orders_per_user) and one of them still has its order ticket open: no new ticket is created, the reply links the existing one. {channel_mention} links that ticket, {order_number} is its order number, {limit} the maximum (checkout.limit_reached.description is used when no open ticket can be linked). | description (4096) | required | `{channel_mention}`, `{order_number}`, `{limit}` |
| ❌ | `checkout.session_exists.description` | Private reply when the customer clicks start while they already have an unfinished order channel; no second channel is created. {channel_mention} links the existing channel. | description (4096) | required | `{channel_mention}` |
| ❌ | `checkout.started.description` | Private reply after the start button created the customer's order channel. {channel_mention} links the channel. | description (4096) | required | `{channel_mention}` |
| ▫️ | `checkout.ticket_link.button` | Optional link button under the private replies to the start button (new ticket, unfinished ticket, existing open order) that opens the customer's order ticket. Omitted when empty. | button_label (80) | optional |  |

### Checkout: order workflow - cart

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.cart.description` | Optional text of the cart step. | description (4096) | optional |  |
| ▫️ | `checkout.cart.edit_option` | Optional small text under each product in the change-quantity menu. {quantity} is the quantity in the cart, {line_total} the line price. | option_description (100) | optional | `{quantity}`, `{line_total}` |
| ❌ | `checkout.cart.edit_placeholder` | Greyed-out text of the menu that picks a cart line to change its quantity (if empty, Discord would show its own default text). | select_placeholder (150) | required |  |
| ❌ | `checkout.cart.empty` | Shown in the cart field while the cart is empty. | field_value (1024) | required |  |
| ❌ | `checkout.cart.field` | Name of the field listing the cart lines (product steps, cart, review, the submitted order and the staff order controls). Each line is written with checkout.cart.line. | field_name (256) | required |  |
| ❌ | `checkout.cart.line` | How one cart/order line is written, e.g. '{quantity} x {name}: {line_total}'. {name} is the product name, {quantity} the quantity, {unit_price} the price of one unit and {line_total} the line price (both written with common.money_format). | format (100) | required | `{name}`, `{quantity}`, `{unit_price}`, `{line_total}` |
| ▫️ | `checkout.cart.minimum_not_met` | Shown in the cart step while the subtotal is below checkout.min_order_total_minor (the continue button stays disabled). {minimum} is that minimum written with common.money_format. Needed when checkout.min_order_total_minor is above 0. | description (4096) | optional | `{minimum}` |
| ❌ | `checkout.cart.subtotal_field` | Name of the subtotal field (sum of the cart lines before fees and credits). | field_name (256) | required |  |
| ❌ | `checkout.cart.title` | Title of the cart step (the customer checks and edits quantities). | title (256) | required |  |

### Checkout: private order channel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.channel_prefix` | Channel-name prefix of private order channels ({prefix} in tickets.channel_name_pattern; the number is the shared ticket/order-channel counter). Discord lower-cases channel names. | channel_prefix (60) | required |  |
| ▫️ | `checkout.channel_topic` | Optional topic of each order channel. {user_name} is the customer's display name. | channel_topic (1024) | optional | `{user_name}` |

### Checkout: error messages (private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.error.cart_full` | Shown when the customer adds a product but the cart already has checkout.max_cart_lines different products. {max} is that number. | description (4096) | required | `{max}` |
| ❌ | `checkout.error.order_not_cancellable` | Shown when the customer tries to cancel an order that can no longer be cancelled by them (it was already paid, cancelled or handled). | description (4096) | required |  |
| ❌ | `checkout.error.out_of_stock` | Shown when the customer asks for more units than are available (when adding to the cart, or when submitting if someone else bought them first). {name} is the product name, {available} the units still available. | description (4096) | required | `{name}`, `{available}` |
| ❌ | `checkout.error.product_unavailable` | Shown when a product in the cart is no longer offered (deactivated or removed). {name} is the product name. | description (4096) | required | `{name}` |
| ❌ | `checkout.error.quantity_invalid` | Shown when a quantity is above the allowed maximum (checkout.max_line_quantity or the product's per-order limit). {max} is that maximum. | description (4096) | required | `{max}` |
| ❌ | `checkout.error.session_closed` | Shown when the customer uses a control of an order workflow that was already submitted, cancelled or expired. | description (4096) | required |  |

### Checkout: order workflow - fulfillment option

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.fulfillment_step.chosen_field` | Name of the field showing the chosen fulfillment option (its label). Needed when checkout.fulfillment_options are defined. | field_name (256) | optional |  |
| ▫️ | `checkout.fulfillment_step.description` | Optional text of the fulfillment step. | description (4096) | optional |  |
| ▫️ | `checkout.fulfillment_step.fee_field` | Name of the field showing the chosen option's fee (fee_minor, written with common.money_format; shown only when that fee is above 0). Needed when an option has a fee. | field_name (256) | optional |  |
| ▫️ | `checkout.fulfillment_step.select_placeholder` | Greyed-out text of the fulfillment option menu (if empty, Discord would show its own default text). Needed when checkout.fulfillment_options are defined. | select_placeholder (150) | optional |  |
| ▫️ | `checkout.fulfillment_step.title` | Title of the step where the customer chooses a fulfillment option (delivery, pickup, ...). Needed when checkout.fulfillment_options are defined. | title (256) | optional |  |

### Checkout: customer notifications (direct message, or the order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.notify.cancelled.description` | Notification sent to the customer when their order becomes 'cancelled' (the order was cancelled (by the customer before paying, or by staff)). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.cancelled.title` | Optional title of the notification sent when an order becomes 'cancelled'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.expired.description` | Notification sent to the customer when their order becomes 'expired' (the order was not paid within checkout.payment_expiry_minutes). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.payment_expiry_minutes is above 0. | description (4096) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.expired.title` | Optional title of the notification sent when an order becomes 'expired'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.fulfilled.description` | Notification sent to the customer when their order becomes 'fulfilled' (the order was delivered/completed). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.fulfilled.title` | Optional title of the notification sent when an order becomes 'fulfilled'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.paid.description` | Notification sent to the customer when their order becomes 'paid' (staff confirmed the payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.paid.title` | Optional title of the notification sent when an order becomes 'paid'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.payment_reminder.description` | Reminder sent once when an order is still unpaid checkout.payment_reminder_minutes after it was submitted (the payment instructions are attached below it). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.payment_reminder_minutes is above 0. | description (4096) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.payment_reminder.title` | Optional title of the payment reminder. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.pending_payment.description` | 'Submission received' notification, sent once when the customer submitted the order (it now waits for payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.pending_payment.title` | Optional title of the 'submission received' notification. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.processing.description` | Notification sent to the customer when their order becomes 'processing' (staff are preparing the order). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.processing.title` | Optional title of the notification sent when an order becomes 'processing'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.refund.description` | Notification sent to the customer when staff record a partial refund (a refund that completes the full refund sends the 'refunded' notification instead). {order_number} is the order number, {amount} the refunded amount (common.money_format), {channel_mention} links the order channel. | description (4096) | required | `{order_number}`, `{amount}`, `{channel_mention}` |
| ▫️ | `checkout.notify.refund.title` | Optional title of the notification sent when staff record a partial refund. {order_number} is the order number, {amount} the refunded amount, {channel_mention} links the order channel. | title (256) | optional | `{order_number}`, `{amount}`, `{channel_mention}` |
| ❌ | `checkout.notify.refunded.description` | Notification sent to the customer when their order becomes 'refunded' (the order was fully refunded). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.refunded.title` | Optional title of the notification sent when an order becomes 'refunded'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.rejected.description` | Notification sent to the customer when their order becomes 'rejected' (staff rejected the order before payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.rejected.title` | Optional title of the notification sent when an order becomes 'rejected'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.session_expiry_warning.description` | Warning posted in the order ticket (mentioning the customer) checkout.session_expiry_warning_minutes before an unfinished order expires for inactivity (any change to the order resets the timer; after a later idle period the warning is sent again). {minutes} is that number of minutes, {expires} when the order expires (relative time shown by Discord, e.g. 'in 10 minutes'). Needed when checkout.session_expiry_warning_minutes is above 0. | description (4096) | optional | `{minutes}`, `{expires}` |
| ▫️ | `checkout.notify.session_expiry_warning.title` | Optional title of the warning posted in the order ticket before an unfinished order expires. {minutes} and {expires} as in the text. | title (256) | optional | `{minutes}`, `{expires}` |

### Checkout: customer notifications sent by staff actions

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.notify.correction.description` | Notification sent to the customer when staff ask them to correct information on an order waiting for payment (the staff message follows in a field). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.correction.message_field` | Name of the field in the correction notification that shows the staff member's message. | field_name (256) | required |  |
| ▫️ | `checkout.notify.correction.title` | Optional title of the notification asking the customer to correct information. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ❌ | `checkout.notify.rejected.reason_field` | Name of the field in the customer's rejection notification (checkout.notify.rejected.*) that shows the reason staff entered when rejecting the order. | field_name (256) | required |  |
| ▫️ | `checkout.notify.tracking.description` | Notification sent to the customer when staff add or change tracking details of an order that is already completed (the details follow in a field). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.staff.button.tracking is configured. | description (4096) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ▫️ | `checkout.notify.tracking.details_field` | Name of the field showing the tracking details in the customer's completion notification and in the tracking notification. Needed when checkout.staff.button.tracking is configured. | field_name (256) | optional |  |
| ▫️ | `checkout.notify.tracking.title` | Optional title of the notification sent when staff add or change tracking details of a completed order. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |

### Checkout: payment instructions (manual payment)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.payment.manual.instructions` | How the customer pays (your own payment instructions), shown in the order channel after the customer submits an order and in the payment reminder. Staff confirm receipt with the mark-paid button. Never ask customers to send card details or passwords in Discord. {order_number} is the order number, {total} the amount to pay written with common.money_format. | description (4096) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.payment.manual.title` | Optional title of the payment instructions shown in the order channel after the customer submits an order (payments.provider = "manual"). {order_number} is the order number, {total} the amount to pay written with common.money_format. | title (256) | optional | `{order_number}`, `{total}` |

### Checkout: quantity form

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.quantity.description` | Optional help text under the quantity label (e.g. that 0 removes the product). | input_description (100) | optional |  |
| ❌ | `checkout.quantity.label` | Label of the quantity field (a whole number; 0 removes the product from the cart). | input_label (45) | required |  |
| ▫️ | `checkout.quantity.placeholder` | Optional greyed-out example inside the empty quantity field. | input_placeholder (100) | optional |  |
| ❌ | `checkout.quantity.title` | Title of the form that asks for a product's quantity. | modal_title (45) | required |  |

### Checkout: staff order controls (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.staff.button.cancel` | Staff button: cancel the order (unpaid: stock and credit are returned; paid: nothing is restocked, refunded or returned automatically - use the refund buttons and the return-credit button). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.claim` | Staff button: claim an open order (records who handles it; a second claim is refused). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.correction` | Staff button on an order waiting for payment: ask the customer to correct information (opens a form for the message; the customer is notified privately and the order keeps waiting). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.details` | Staff button: show the full order record privately to the staff member who clicks it. | button_label (80) | required |  |
| ❌ | `checkout.staff.button.fulfilled` | Staff button: the order was delivered/completed (the order channel is closed). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.mark_paid` | Staff button: the payment was received (unpaid -> paid; stock is then taken). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.note` | Staff button: add an internal note (opens a staff-only form; notes are never shown to the customer). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.processing` | Staff button: mark a paid order as being prepared (optional stage). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.refund` | Staff button: record a (partial) refund. With the manual payment method the money is returned outside the bot. | button_label (80) | required |  |
| ❌ | `checkout.staff.button.refund_credit` | Staff button: refund (part of) the order as store credit. Needed when the credits feature is on. | button_label (80) | required |  |
| ❌ | `checkout.staff.button.reject` | Staff button: reject an unpaid order (reserved stock and applied credit are returned). | button_label (80) | required |  |
| ❌ | `checkout.staff.button.return_credit` | Staff button: give back the store credit the customer applied to a paid order (all of it at once; shown until it was given back, e.g. after cancelling a paid order). Cancelling a paid order returns neither money nor credit automatically. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ▫️ | `checkout.staff.button.tracking` | Optional staff button on a paid, in-progress or completed order: add or change tracking details (included in the customer's completion notification, or sent to them when the order is already completed). Leave empty if your workflow does not use tracking details. | button_label (80) | optional |  |
| ❌ | `checkout.staff.button.unclaim` | Staff button: release the claim (only the member holding it, or an admin). | button_label (80) | required |  |
| ▫️ | `checkout.staff.channel_field` | Optional name of the field on the staff order record that links the customer's order channel. | field_name (256) | optional |  |
| ❌ | `checkout.staff.claimed_field` | Name of the field on the staff order record showing which staff member claimed the order and since when (shown only while it is claimed). | field_name (256) | required |  |
| ▫️ | `checkout.staff.credit_returned_field` | Optional name of the field on the staff order controls showing the applied store credit that was given back to the customer (written with common.money_format). | field_name (256) | optional |  |
| ❌ | `checkout.staff.customer_field` | Name of the field on the staff order record that mentions the customer (no ping). | field_name (256) | required |  |
| ❌ | `checkout.staff.description` | Text of the staff order controls. {user_mention} mentions the customer (no ping), {channel_mention} links the order channel, {order_number}/{total}/{status} as in the title. The order lines, totals and buttons follow. | description (4096) | required | `{order_number}`, `{user_mention}`, `{channel_mention}`, `{total}`, `{status}` |
| ▫️ | `checkout.staff.details_title` | Optional title of the message posted under the staff order controls with the customer's form answers (only when a form is defined). {order_number} is the order number. | title (256) | optional | `{order_number}` |
| ❌ | `checkout.staff.reference_field` | Name of the field on the staff order record showing the order's unique reference (its number). | field_name (256) | required |  |
| ❌ | `checkout.staff.refunded_field` | Name of the field on the staff order controls showing how much was refunded so far. | field_name (256) | required |  |
| ❌ | `checkout.staff.status_field` | Name of the field on the staff order record showing the current status (its name from checkout.status_label.*). | field_name (256) | required |  |
| ❌ | `checkout.staff.title` | Title of the order controls posted for staff in checkout.staff_channel for every submitted order (edited on every status change). {order_number} is the order number, {total} the total, {status} the status name (checkout.status_label.*). | title (256) | required | `{order_number}`, `{total}`, `{status}` |
| ▫️ | `checkout.staff.tracking_field` | Name of the field on the staff order record showing the tracking details staff entered. Needed when checkout.staff.button.tracking is configured. | field_name (256) | optional |  |

### Checkout: staff order controls - timeline and notes

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.staff.note_line` | How one internal note is written, e.g. '{time} {actor}: {note}'. {time} is when it was added, {actor} mentions who added it, {note} is the note. | format (100) | required | `{time}`, `{actor}`, `{note}` |
| ❌ | `checkout.staff.notes_field` | Name of the field on the staff order record listing the internal staff notes (the latest ones; never shown to the customer). | field_name (256) | required |  |
| ❌ | `checkout.staff.stage.claimed` | Stage name in the timeline when a staff member claimed the order. | format (100) | required |  |
| ❌ | `checkout.staff.stage.correction` | Stage name in the timeline when staff asked the customer to correct information. | format (100) | required |  |
| ▫️ | `checkout.staff.stage.tracking` | Stage name in the timeline when staff added or changed the tracking details. Needed when checkout.staff.button.tracking is configured. | format (100) | optional |  |
| ❌ | `checkout.staff.stage.unclaimed` | Stage name in the timeline when the claim on the order was released. | format (100) | required |  |
| ❌ | `checkout.staff.timeline_field` | Name of the field on the staff order record listing the order's stages with their times (submitted, status changes, claims, correction requests, tracking details; the latest ones when there are many - the details button shows the full history). | field_name (256) | required |  |
| ❌ | `checkout.staff.timeline_line` | How one stage is written in the timeline, e.g. '{time} {stage} {actor}'. {time} is the time (shown in each reader's time zone), {stage} the stage name (checkout.status_label.* for statuses, checkout.staff.stage.* for the other stages), {actor} mentions who did it (empty for automatic changes). | format (100) | required | `{time}`, `{stage}`, `{actor}` |

### Checkout: order status views (order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.status.cancelled.description` | Optional text of the order workflow message once the order is 'cancelled'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.cancelled.title` | Title of the order workflow message once the order is 'cancelled' (the order was cancelled (by the customer before paying, or by staff)). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.expired.description` | Optional text of the order workflow message once the order is 'expired'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.expired.title` | Title of the order workflow message once the order is 'expired' (the order was not paid within checkout.payment_expiry_minutes). {order_number} is the order number, {total} the order total written with common.money_format. Needed when checkout.payment_expiry_minutes is above 0. | title (256) | optional | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.fulfilled.description` | Optional text of the order workflow message once the order is 'fulfilled'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.fulfilled.title` | Title of the order workflow message once the order is 'fulfilled' (the order was delivered/completed). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.paid.description` | Optional text of the order workflow message once the order is 'paid'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.paid.title` | Title of the order workflow message once the order is 'paid' (staff confirmed the payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.processing.description` | Optional text of the order workflow message once the order is 'processing'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.processing.title` | Title of the order workflow message once the order is 'processing' (staff are preparing the order). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.refunded.description` | Optional text of the order workflow message once the order is 'refunded'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.refunded.title` | Title of the order workflow message once the order is 'refunded' (the order was fully refunded). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ▫️ | `checkout.status.rejected.description` | Optional text of the order workflow message once the order is 'rejected'. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.status.rejected.title` | Title of the order workflow message once the order is 'rejected' (staff rejected the order before payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |

### Checkout: order status names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.status_label.cancelled` | Name of the order status 'cancelled' (the order was cancelled (by the customer before paying, or by staff)), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.expired` | Name of the order status 'expired' (the order was not paid within checkout.payment_expiry_minutes), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.fulfilled` | Name of the order status 'fulfilled' (the order was delivered/completed), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.paid` | Name of the order status 'paid' (staff confirmed the payment), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.pending_payment` | Name of the order status 'pending_payment' (waiting for payment), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.processing` | Name of the order status 'processing' (staff are preparing the order), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.refunded` | Name of the order status 'refunded' (the order was fully refunded), used as {status} in the staff order controls. | format (100) | required |  |
| ❌ | `checkout.status_label.rejected` | Name of the order status 'rejected' (staff rejected the order before payment), used as {status} in the staff order controls. | format (100) | required |  |

### Checkout: submitted order (order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.submitted.description` | Optional text of the submitted order. The payment instructions follow below it. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ❌ | `checkout.submitted.title` | Title of the workflow message after the order was submitted (waiting for payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |

### Checkout: order ticket (introduction, reference, status and timeline on the live panel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.ticket.intro_description` | First message in every order ticket, posted before the live panel: identifies the ticket and explains that only the customer and authorized staff can see it. {ticket_number} is the ticket number (it is also shown in the reference field under the text), {user_mention} mentions the customer (no ping). | description (4096) | required | `{ticket_number}`, `{user_mention}` |
| ▫️ | `checkout.ticket.intro_title` | Optional title of the first message in every order ticket. {ticket_number} is the ticket number. | title (256) | optional | `{ticket_number}` |
| ❌ | `checkout.ticket.order_number_field` | Name of the field showing the order number on the live panel once the order was submitted. | field_name (256) | required |  |
| ❌ | `checkout.ticket.reference_field` | Name of the field showing the ticket's unique reference (the ticket number, as in the channel name) on the ticket introduction and on every state of the live panel. | field_name (256) | required |  |
| ❌ | `checkout.ticket.status_field` | Name of the field showing the current status on every state of the live panel (the value is checkout.ticket.status_in_progress before submission, then the order's stage name). | field_name (256) | required |  |
| ❌ | `checkout.ticket.status_in_progress` | Status shown on the live panel while the customer is still putting the order together (not submitted yet). | field_value (1024) | required |  |
| ❌ | `checkout.ticket.timeline_entry` | How one stage of the timeline is written, e.g. '{time} {stage}'. {stage} is the stage name (checkout.ticket.stage.*), {time} the date and time it was reached and {relative} the same as a relative time (both shown by Discord in each reader's own time zone and language). | format (100) | required | `{stage}`, `{time}`, `{relative}` |
| ❌ | `checkout.ticket.timeline_field` | Name of the field listing the order's stages so far (the timeline) on the live panel after submission (also in /myorders). Each stage is written with checkout.ticket.timeline_entry. | field_name (256) | required |  |

### Checkout: order stage names (live panel, timeline, /myorders)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `checkout.ticket.stage.cancelled` | Name of the stage 'cancelled' (the order was cancelled (by the customer before paying, or by staff)) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.expired` | Name of the stage 'expired' (the order was not paid within checkout.payment_expiry_minutes) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.fulfilled` | Name of the stage 'fulfilled' (the order was delivered/completed) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.paid` | Name of the stage 'paid' (staff confirmed the payment) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.pending_payment` | Name of the stage 'pending_payment' (waiting for payment) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.processing` | Name of the stage 'processing' (staff are preparing the order) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.refunded` | Name of the stage 'refunded' (the order was fully refunded) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |
| ❌ | `checkout.ticket.stage.rejected` | Name of the stage 'rejected' (staff rejected the order before payment) shown to the customer: the status field of the live panel, the order timeline and /myorders. | format (100) | required |  |

## /myorders

Feature `my_orders` — enabled.

### My orders (/myorders, private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `my_orders.command.description` | Description of the /myorders command shown in Discord's command list (at most 100 characters). The command is registered only when features.my_orders is on and this text is set; Discord receives it when the bot starts (a change needs a restart: /reload does not update it). | command_description (100) | required |  |
| ❌ | `my_orders.empty.description` | Text of the /myorders reply when the member has no order yet. | description (4096) | required |  |
| ▫️ | `my_orders.list.description` | Optional text under the /myorders title when the member has orders. {count} is their number. One card per order follows (newest first), with previous/next buttons when there is more than one page. | description (4096) | optional | `{count}` |
| ❌ | `my_orders.list.title` | Title of the private /myorders reply (also when the member has no order yet). {count} is the number of the member's orders. | title (256) | required | `{count}` |
| ❌ | `my_orders.order.number_field` | Name of the field showing the order number (the order's reference) on each order card. | field_name (256) | required |  |
| ❌ | `my_orders.order.status_field` | Name of the field showing the order's current stage (checkout.ticket.stage.*) on each order card. | field_name (256) | required |  |
| ▫️ | `my_orders.order.ticket_field` | Optional name of a field linking the order's ticket while it is still open (omitted when empty). | field_name (256) | optional |  |
| ❌ | `my_orders.order.timeline_field` | Name of the field listing the order's stages so far on each order card (each written with checkout.ticket.timeline_entry). | field_name (256) | required |  |
| ❌ | `my_orders.order.title` | Title of each order's card in /myorders. {order_number} is the order number, {status} its current stage name (checkout.ticket.stage.*), {total} the total (common.money_format), {date} the date it was submitted (shown by Discord in the reader's time zone). | title (256) | required | `{order_number}`, `{status}`, `{total}`, `{date}` |
| ❌ | `my_orders.order.total_field` | Name of the field showing the order total (common.money_format) on each order card. | field_name (256) | required |  |
| ▫️ | `my_orders.order.tracking_field` | Optional name of the field showing the tracking details staff recorded for the order (shown only when there are tracking details; omitted when empty). | field_name (256) | optional |  |

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

## Reviews

Feature `reviews` — enabled.

### Reviews: replies to the customer

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `reviews.already_reviewed.description` | Shown privately when the customer tries to rate or comment on an order they already reviewed. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ❌ | `reviews.declined.description` | Replaces the review request after the customer chose not to leave a review. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ❌ | `reviews.expired.description` | Replaces the review request when the order can no longer be reviewed (for example it was refunded, or staff closed the review). {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ❌ | `reviews.thanks.description` | Replaces the review request after the customer submitted their review (the rating menu and buttons are removed). {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |

### Reviews: rating request (sent to the customer after an order is fulfilled)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `reviews.button.decline` | Label of the button on the review request that lets the customer decline to leave a review. | button_label (80) | required |  |
| ❌ | `reviews.prompt.description` | Text of the review request asking the customer to rate their order. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ❌ | `reviews.prompt.select_placeholder` | Placeholder of the rating selection menu on the review request (shown before a rating is picked). | select_placeholder (150) | required |  |
| ▫️ | `reviews.prompt.title` | Optional title of the review request sent to the customer (by direct message or in the order channel, see reviews.prompt_via) after their order was fulfilled. {order_number} is the number of the reviewed order. | title (256) | optional | `{order_number}` |

### Reviews: comment form (opens after the customer picks a rating)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `reviews.comment.description` | Optional help text under the label of the comment box. | input_description (100) | optional |  |
| ❌ | `reviews.comment.label` | Label of the comment box in the comment form. Required when reviews.allow_comment is on. | input_label (45) | required |  |
| ▫️ | `reviews.comment.placeholder` | Optional greyed-out example text inside the empty comment box. | input_placeholder (100) | optional |  |
| ❌ | `reviews.comment.title` | Title of the form that asks for a written comment after the customer picked a rating. Required when reviews.allow_comment is on. | modal_title (45) | required |  |

### Reviews: published review (posted in the reviews channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `reviews.published.rating_field` | Heading of the rating on a published review; the rating itself is shown with reviews.rating.<n>.display (or the rating's option label). | field_name (256) | required |  |
| ❌ | `reviews.published.title` | Title of a review posted in the reviews channel. {rating} is the chosen rating as a number (1 to reviews.rating_scale), {user_name} the reviewer's display name, {order_number} the number of the reviewed order. The customer's comment (if any) is shown below the title exactly as written. | title (256) | required | `{rating}`, `{user_name}`, `{order_number}` |

### Reviews: rating options

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `reviews.rating.1.description` | Optional small text under the option for rating 1 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ▫️ | `reviews.rating.1.display` | Optional text shown as the rating on a published review with rating 1 of 5 (for example a row of stars). If empty, the option label (reviews.rating.1.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.1.label`) |  |
| ▫️ | `reviews.rating.1.emoji` | Optional emoji shown next to the option for rating 1 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ❌ | `reviews.rating.1.label` | Option label for rating 1 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.1.display is empty. | option_label (100) | required |  |
| ▫️ | `reviews.rating.2.description` | Optional small text under the option for rating 2 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ▫️ | `reviews.rating.2.display` | Optional text shown as the rating on a published review with rating 2 of 5 (for example a row of stars). If empty, the option label (reviews.rating.2.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.2.label`) |  |
| ▫️ | `reviews.rating.2.emoji` | Optional emoji shown next to the option for rating 2 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ❌ | `reviews.rating.2.label` | Option label for rating 2 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.2.display is empty. | option_label (100) | required |  |
| ▫️ | `reviews.rating.3.description` | Optional small text under the option for rating 3 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ▫️ | `reviews.rating.3.display` | Optional text shown as the rating on a published review with rating 3 of 5 (for example a row of stars). If empty, the option label (reviews.rating.3.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.3.label`) |  |
| ▫️ | `reviews.rating.3.emoji` | Optional emoji shown next to the option for rating 3 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ❌ | `reviews.rating.3.label` | Option label for rating 3 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.3.display is empty. | option_label (100) | required |  |
| ▫️ | `reviews.rating.4.description` | Optional small text under the option for rating 4 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ▫️ | `reviews.rating.4.display` | Optional text shown as the rating on a published review with rating 4 of 5 (for example a row of stars). If empty, the option label (reviews.rating.4.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.4.label`) |  |
| ▫️ | `reviews.rating.4.emoji` | Optional emoji shown next to the option for rating 4 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ❌ | `reviews.rating.4.label` | Option label for rating 4 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.4.display is empty. | option_label (100) | required |  |
| ▫️ | `reviews.rating.5.description` | Optional small text under the option for rating 5 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ▫️ | `reviews.rating.5.display` | Optional text shown as the rating on a published review with rating 5 of 5 (for example a row of stars). If empty, the option label (reviews.rating.5.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.5.label`) |  |
| ▫️ | `reviews.rating.5.emoji` | Optional emoji shown next to the option for rating 5 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ❌ | `reviews.rating.5.label` | Option label for rating 5 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.5.display is empty. | option_label (100) | required |  |

## Report a Problem

Feature `problems` — enabled.

### Report a Problem: private choice step (shown only to the customer)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.attachments_note` | Tells the customer that screenshots or files can be uploaded in the private ticket once it is open (the ticket channel allows attachments). Shown below the choice step, below the confirmation after the report was created, and on the report's ticket panel while the report is open. | description (4096) | required |  |
| ❌ | `problems.choose.description` | Text of the private message in which the customer chooses the order the problem is about (their orders in problems.eligible_statuses, newest first) and/or the issue type, then presses the shared Continue button to describe the problem. Shown only when there is something to choose. You can link server channels with {channel_<key>} (for example {channel_faq}). | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `problems.choose.no_order_option` | Label of the last option of the order menu, for a problem that is not about one of the listed orders. Needed when problems.allow_without_order = true and problems.eligible_statuses is not empty. | option_label (100) | required |  |
| ▫️ | `problems.choose.no_order_option_description` | Optional small text under the 'not about an order' option. | option_description (100) | optional |  |
| ❌ | `problems.choose.order_option` | Label of each order in the order menu. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name (checkout.status_label.<status>). Needed when problems.eligible_statuses is not empty. | option_label (100) | required | `{order_number}`, `{total}`, `{status}` |
| ▫️ | `problems.choose.order_option_description` | Optional small text under each order in the order menu. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name (checkout.status_label.<status>). | option_description (100) | optional | `{order_number}`, `{total}`, `{status}` |
| ❌ | `problems.choose.order_placeholder` | Greyed-out text of the order menu in the private choice step. Needed when problems.eligible_statuses is not empty. | select_placeholder (150) | required |  |
| ▫️ | `problems.choose.title` | Optional title of the private message in which the customer chooses the order the problem is about and/or the issue type. | title (256) | optional |  |
| ▫️ | `problems.choose.type_placeholder` | Greyed-out text of the issue-type menu in the private choice step. Needed when more than one [[problems.types]] is configured. | select_placeholder (150) | optional |  |

### Report a Problem: the report's private ticket

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.button.reject` | Reject button on the report panel (staff only; opens a form for the reason, required when problems.require_reason_on_reject = true). | button_label (80) | required |  |
| ❌ | `problems.button.resolve` | Resolve button on the report panel (staff only; opens a form for an optional note to the customer). | button_label (80) | required |  |
| ❌ | `problems.channel_prefix` | Channel-name prefix of problem-report tickets ({prefix} in tickets.channel_name_pattern). Discord lower-cases channel names and replaces spaces with dashes. | channel_prefix (60) | required |  |
| ▫️ | `problems.channel_topic` | Optional topic of each problem-report ticket channel. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {user_name} is the customer's display name. | channel_topic (1024) | optional | `{number}`, `{user_name}` |
| ▫️ | `problems.ticket.decided_by_field` | Optional field name under which the staff member who decided is mentioned on the report panel. If empty, it is not shown. | field_name (256) | optional |  |
| ❌ | `problems.ticket.description` | Text of the report panel, for example that only the customer and authorized staff can see this ticket. {user_mention} mentions the customer, {user_name} is their display name. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). You can link server channels with {channel_<key>} (for example {channel_faq}). The form answers are posted below the panel. | description (4096) | required | `{user_mention}`, `{user_name}`, `{number}`, `{type_label}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ❌ | `problems.ticket.order_field` | Field name of the order the report is about (report panel, staff alert, decision message; omitted when the report is not about an order). Needed when problems.eligible_statuses is not empty. | field_name (256) | required |  |
| ❌ | `problems.ticket.order_value` | The order the report is about, shown under problems.ticket.order_field. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name (checkout.status_label.<status>). Needed when problems.eligible_statuses is not empty. | field_value (1024) | required | `{order_number}`, `{total}`, `{status}` |
| ❌ | `problems.ticket.reason_field` | Field name of the reason (rejection) or note (resolution) staff gave, shown exactly as staff wrote it on the report panel and in the decision message (omitted when staff gave none). | field_name (256) | required |  |
| ❌ | `problems.ticket.status_field` | Field name of the report status on the report panel. | field_name (256) | required |  |
| ❌ | `problems.ticket.title` | Title of the report panel posted in the new ticket (the live message that shows the report and its status). {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). {user_name} is the customer's display name. | title (256) | required | `{number}`, `{type_label}`, `{user_name}` |
| ❌ | `problems.ticket.type_field` | Field name of the issue type on the report panel. | field_name (256) | required |  |

### Report a Problem: button on the #purchase panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.button.report` | Label of the Report a Problem button on the #purchase panel (next to the Start button). The button appears once the Report a Problem feature is fully configured (run /setup to update the panel). | button_label (80) | required |  |
| ▫️ | `problems.button.report_emoji` | Optional emoji on the Report a Problem button. | emoji (100) | optional |  |

### Report a Problem: private replies

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.created.description` | Private confirmation after the report's ticket was created. {channel_mention} links the ticket; {number} is the report's ticket number (zero-padded to tickets.number_padding digits). Also shown when the same report form is submitted again. | description (4096) | required | `{channel_mention}`, `{number}` |
| ❌ | `problems.limit_reached.description` | Private reply when the customer already has problems.max_open_per_user undecided reports with an open ticket. {limit} is that number; {channel_mention} links the ticket of their newest open report. | description (4096) | required | `{limit}`, `{channel_mention}` |
| ▫️ | `problems.no_orders.description` | Private reply when the customer has no order a report may refer to (statuses in problems.eligible_statuses). Needed when problems.allow_without_order = false. You can link server channels with {channel_<key>} (for example {channel_faq}). | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

### Report a Problem: decision messages to the customer

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.notify.rejected.description` | Text of the private message telling the customer their report was rejected (staff's reason/note follows under problems.ticket.reason_field). Sent by direct message when features.dm_notifications is on; the report panel in the ticket always shows the decision, and this message is posted in the ticket when the panel cannot be updated. {channel_mention} links the ticket. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | description (4096) | required | `{number}`, `{type_label}`, `{channel_mention}` |
| ▫️ | `problems.notify.rejected.title` | Optional title of the private message telling the customer their report was rejected. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | title (256) | optional | `{number}`, `{type_label}` |
| ❌ | `problems.notify.resolved.description` | Text of the private message telling the customer their report was resolved (staff's reason/note follows under problems.ticket.reason_field). Sent by direct message when features.dm_notifications is on; the report panel in the ticket always shows the decision, and this message is posted in the ticket when the panel cannot be updated. {channel_mention} links the ticket. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | description (4096) | required | `{number}`, `{type_label}`, `{channel_mention}` |
| ▫️ | `problems.notify.resolved.title` | Optional title of the private message telling the customer their report was resolved. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | title (256) | optional | `{number}`, `{type_label}` |

### Report a Problem: staff alert

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.staff_alert.description` | Text of the new-report staff alert (the report details stay in the private ticket). {user_mention} mentions the customer, {channel_mention} links the ticket. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). Needed when tickets.staff_alert_channel is set. | description (4096) | required | `{user_mention}`, `{channel_mention}`, `{number}`, `{type_label}` |
| ❌ | `problems.staff_alert.title` | Title of the alert posted in the staff alert channel (tickets.staff_alert_channel) for each new report. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). Needed when tickets.staff_alert_channel is set. | title (256) | required | `{number}`, `{type_label}` |

### Report a Problem: report statuses and decisions

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `problems.status.open` | Name of the report status 'open' (waiting for a staff decision), shown on the report panel. | field_value (1024) | required |  |
| ❌ | `problems.status.rejected` | Name of the report status 'rejected' (staff rejected the report), shown on the report panel. | field_value (1024) | required |  |
| ❌ | `problems.status.resolved` | Name of the report status 'resolved' (staff resolved the report), shown on the report panel. | field_value (1024) | required |  |

### Report a Problem: issue type 'general'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `problems.type.general.description` | Optional small text under the 'general' option in the issue-type menu (shown when several types exist). | option_description (100) | optional |  |
| ▫️ | `problems.type.general.emoji` | Optional emoji of the 'general' option in the issue-type menu. | emoji (100) | optional |  |
| ▫️ | `problems.type.general.form.details.description` | Optional help text under the label of the 'details' field. | input_description (100) | optional |  |
| ❌ | `problems.type.general.form.details.label` | Label of the 'details' field of the 'general' form (also the field name of the answer on the report's ticket). | input_label (45) | required |  |
| ▫️ | `problems.type.general.form.details.placeholder` | Optional greyed-out example text inside the empty 'details' field. | input_placeholder (100) | optional |  |
| ❌ | `problems.type.general.form_title` | Title of the form in which the customer describes a 'general' problem, before the ticket is created (opened by the choice step's Continue button, or right away when there is nothing to choose). | modal_title (45) | required |  |
| ❌ | `problems.type.general.label` | Name of the 'general' issue type: the option in the issue-type menu of the private report step, and {type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision message. | option_label (100) | required |  |

## Staff operations (#staff-chat alerts, reminders, Owner alerts, Customer role)

Feature `operations` — enabled.

### Operations: staff alerts (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `operations.alert.new_order.description` | Staff alert when a customer submitted a new order (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.alert.new_order.title` | Optional title of the Staff alert when a customer submitted a new order (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ❌ | `operations.alert.paid_order.description` | Staff alert when an order's payment was confirmed and it waits to be completed (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.alert.paid_order.title` | Optional title of the Staff alert when an order's payment was confirmed and it waits to be completed (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.button.open_record` | Optional link button under order alerts and order reminders that jumps to the order's live record in checkout.staff_channel. | button_label (80) | optional |  |

### Operations: staff reminders (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `operations.button.open_ticket` | Optional link button under unanswered-ticket reminders that jumps to the ticket. | button_label (80) | optional |  |
| ▫️ | `operations.reminder.unanswered.description` | Staff reminder about a support ticket whose customer waits for a staff reply, posted once it waited operations.unanswered_ticket_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {ticket_number} is the ticket number, {customer_mention} mentions the customer (no ping), {ticket_channel} links the ticket; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unanswered_ticket_minutes is above 0. | description (4096) | optional | `{ticket_number}`, `{customer_mention}`, `{ticket_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unanswered.title` | Optional title of the Staff reminder about a support ticket whose customer waits for a staff reply, posted once it waited operations.unanswered_ticket_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {ticket_number} is the ticket number, {customer_mention} mentions the customer (no ping), {ticket_channel} links the ticket; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{ticket_number}`, `{customer_mention}`, `{ticket_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unclaimed.description` | Staff reminder about an open order nobody claimed, posted once it waited operations.unclaimed_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unclaimed_order_minutes is above 0. | description (4096) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unclaimed.title` | Optional title of the Staff reminder about an open order nobody claimed, posted once it waited operations.unclaimed_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unfulfilled.description` | Staff reminder about a paid order not completed yet, posted once it waited operations.unfulfilled_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unfulfilled_order_minutes is above 0. | description (4096) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unfulfilled.title` | Optional title of the Staff reminder about a paid order not completed yet, posted once it waited operations.unfulfilled_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unpaid.description` | Staff reminder about an order still waiting for payment, posted once it waited operations.unpaid_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unpaid_order_minutes is above 0. | description (4096) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ▫️ | `operations.reminder.unpaid.title` | Optional title of the Staff reminder about an order still waiting for payment, posted once it waited operations.unpaid_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |

### Operations: failure alerts (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ❌ | `operations.failure.error.description` | Staff alert when a button, form or command failed unexpectedly (the member got the configured error message; details are in the bot logs). {source} is the internal name of the control or command, {error_id} the reference the member saw, {error} the error type. | description (4096) | required | `{source}`, `{error_id}`, `{error}` |
| ▫️ | `operations.failure.error.title` | Optional title of the Staff alert when a button, form or command failed unexpectedly (the member got the configured error message; details are in the bot logs). {source} is the internal name of the control or command, {error_id} the reference the member saw, {error} the error type. | title (256) | optional | `{source}`, `{error_id}`, `{error}` |
| ❌ | `operations.failure.notification.description` | Staff alert when a notification could not be delivered: a private notification to a member, neither by direct message nor in the fallback channel, or a message the bot posts in a channel (e.g. a ticket's staff alert or an order's details). {user_mention} mentions the member it was for (no ping; '-' when it was for staff), {notification} is the notification's internal name (e.g. checkout.paid). | description (4096) | required | `{user_mention}`, `{notification}` |
| ▫️ | `operations.failure.notification.title` | Optional title of the Staff alert when a notification could not be delivered: a private notification to a member, neither by direct message nor in the fallback channel, or a message the bot posts in a channel (e.g. a ticket's staff alert or an order's details). {user_mention} mentions the member it was for (no ping; '-' when it was for staff), {notification} is the notification's internal name (e.g. checkout.paid). | title (256) | optional | `{user_mention}`, `{notification}` |
| ❌ | `operations.failure.role.description` | Staff alert when the customer role could not be given or removed (missing role, role above the bot's highest role, or missing permission). {user_mention} mentions the member (no ping), {role_mention} the role. | description (4096) | required | `{user_mention}`, `{role_mention}` |
| ▫️ | `operations.failure.role.title` | Optional title of the Staff alert when the customer role could not be given or removed (missing role, role above the bot's highest role, or missing permission). {user_mention} mentions the member (no ping), {role_mention} the role. | title (256) | optional | `{user_mention}`, `{role_mention}` |
| ❌ | `operations.failure.task.description` | Staff alert when a scheduled task failed permanently after its retries (details are in the audit log). {task} is the task's internal name, {task_id} its number, {attempts} the attempts made. | description (4096) | required | `{task}`, `{task_id}`, `{attempts}` |
| ▫️ | `operations.failure.task.title` | Optional title of the Staff alert when a scheduled task failed permanently after its retries (details are in the audit log). {task} is the task's internal name, {task_id} its number, {attempts} the attempts made. | title (256) | optional | `{task}`, `{task_id}`, `{attempts}` |

### Operations: the Owner's private alerts

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `operations.owner.failure_task.description` | Optional text of the Owner's private alert about the same failure (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.failure.task.description`) | `{task}`, `{task_id}`, `{attempts}` |
| ▫️ | `operations.owner.failure_task.title` | Optional title of the Owner's private alert when a scheduled task failed permanently after its retries (details are in the audit log) (empty = the staff alert's title). | title (256) | optional (falls back to `operations.failure.task.title`) | `{task}`, `{task_id}`, `{attempts}` |
| ▫️ | `operations.owner.new_order.description` | Optional text of the Owner's private alert when a customer submitted a new order (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.new_order.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.new_order.title` | Optional title of the Owner's private alert when a customer submitted a new order (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.new_order.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.paid_order.description` | Optional text of the Owner's private alert when an order's payment was confirmed and it waits to be completed (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.paid_order.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.paid_order.title` | Optional title of the Owner's private alert when an order's payment was confirmed and it waits to be completed (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.paid_order.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |

## Business decisions and settings still open

- **verification**: verification.rules is not set (the number of server rules shown on the welcome panel, 1-25; each rule then needs verification.rule.<n>.name and .value)

## Summary

- Required keys (for the listed features): 280
- Required keys still missing: 258

Legend: ✅ configured · ❌ required and missing · ▫️ optional and empty
