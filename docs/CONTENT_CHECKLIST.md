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
| ✅ | `branding.footer_text` | Optional footer on every customer-facing embed. | footer (2048) | optional |  |

### Shared buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `common.button.back` | Back button in multi-step workflows. | button_label (80) | required (used by an enabled feature) |  |
| ✅ | `common.button.cancel` | Cancel button on prompts and workflows. | button_label (80) | required (used by an enabled feature) |  |
| ✅ | `common.button.continue` | Continue button in multi-step workflows. | button_label (80) | required (used by an enabled feature) |  |
| ✅ | `common.button.next_page` | Next-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |
| ✅ | `common.button.previous_page` | Previous-page button in paginated lists. | button_label (80) | required (used by an enabled feature) |  |

### Error messages (shown privately to the person who clicked)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `common.error.busy` | Shown when someone clicks again while their previous click is still being processed. Optional: if empty, the duplicate click is silently ignored. | description (4096) | optional |  |
| ✅ | `common.error.cooldown` | Shown when someone acts too quickly in a row. | description (4096) | required | `{retry_after}` |
| ✅ | `common.error.generic` | Shown when an action fails unexpectedly. Internal details are never shown. You may include {error_id}: a short reference that also appears in the bot's logs (empty when there is none). | description (4096) | required | `{error_id}` |
| ✅ | `common.error.invalid_input` | Shown when a form entry is invalid (e.g. a quantity that is not a whole number). | description (4096) | required |  |
| ✅ | `common.error.not_found` | Shown when the ticket/order/item behind a control no longer exists. | description (4096) | required |  |
| ✅ | `common.error.permission_denied` | Shown when someone uses a control they are not allowed to use (e.g. a staff-only button). | description (4096) | required |  |
| ✅ | `common.error.title` | Optional title above every error message. | title (256) | optional |  |
| ✅ | `common.error.unavailable` | Shown when a feature is switched off or not fully configured. | description (4096) | required |  |

### Formats

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `common.money_format` | How prices are written, e.g. '{amount} {currency}'. {amount} is formatted with the separators from settings.checkout. Required by catalog and checkout. | format (100) | required (used by an enabled feature) | `{amount}`, `{currency}` |
| ✅ | `common.page_indicator` | Optional page indicator added to the footer of paginated lists. | format (100) | optional | `{page}`, `{pages}` |
| ✅ | `common.progress_format` | Optional step indicator shown in multi-step workflows. {bar} uses the progress symbols from settings.branding. | format (100) | required (used by an enabled feature) | `{current}`, `{total}`, `{bar}` |

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
| ✅ | `server.channel.announcements.topic` | Optional topic (description) of the 'announcements' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.bot_log.topic` | Optional topic (description) of the 'bot_log' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.catalog.topic` | Optional topic (description) of the 'catalog' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.faq.topic` | Optional topic (description) of the 'faq' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.how_to_buy.topic` | Optional topic (description) of the 'how_to_buy' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.new_arrivals.topic` | Optional topic (description) of the 'new_arrivals' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.orders.topic` | Optional topic (description) of the 'orders' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.purchase.topic` | Optional topic (description) of the 'purchase' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.reviews.topic` | Optional topic (description) of the 'reviews' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.shipping.topic` | Optional topic (description) of the 'shipping' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.staff_chat.topic` | Optional topic (description) of the 'staff_chat' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.transcripts.topic` | Optional topic (description) of the 'transcripts' channel. | channel_topic (1024) | optional |  |
| ✅ | `server.channel.welcome.topic` | Optional topic (description) of the 'welcome' channel. | channel_topic (1024) | optional |  |

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

### Verification: welcome panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `verification.after.heading` | Optional heading of the closing section of the welcome panel that /setup posts in the 'welcome' channel (what to do after verifying), shown in bold. | title (256) | optional |  |
| ✅ | `verification.after.text` | Optional closing section of the welcome panel that /setup posts in the 'welcome' channel, below the rules (e.g. where to go after verifying). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.button.emoji` | Optional emoji shown on the 'I agree' button. | emoji (100) | optional |  |
| ✅ | `verification.button.label` | Label of the button under the welcome panel that /setup posts in the 'welcome' channel; it accepts the rules and grants the Member role (the specification calls it "I agree, enter the server"). | button_label (80) | required |  |
| ✅ | `verification.welcome.description` | Short introduction to the server on the first card of the welcome panel that /setup posts in the 'welcome' channel. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.welcome.image_url` | Optional large image (https:// URL) at the bottom of the first card of the welcome panel that /setup posts in the 'welcome' channel. If empty, the branding banner is used when branding.panel_banner is on. | url (2048) | optional |  |
| ▫️ | `verification.welcome.thumbnail_url` | Optional small image (https:// URL) in the top-right corner of the first card of the welcome panel that /setup posts in the 'welcome' channel. If empty, the branding logo is used when branding.panel_thumbnail is on. | url (2048) | optional |  |
| ✅ | `verification.welcome.title` | Title of the first card of the welcome panel that /setup posts in the 'welcome' channel, for example a welcome line. | title (256) | required |  |

### Verification: private replies after the 'I agree' button

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `verification.already_verified.description` | Private reply to a member who clicks the 'I agree' button although they already have the Member role (nothing changes). {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.already_verified.title` | Optional title of the private reply to a member who clicks the 'I agree' button although they already have the Member role. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |
| ✅ | `verification.confirmed.description` | Private confirmation (only the member sees it) after they accepted the rules and received the Member role; the member-only channels are visible from now on. {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.confirmed.title` | Optional title of the private confirmation shown after the member accepted the rules and received the Member role. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |

### Verification: direct message (verification.dm_confirmation)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `verification.dm.description` | Direct message confirming a successful verification, sent in addition to the private confirmation when verification.dm_confirmation and features.dm_notifications are on (required then). {user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | optional | `{user_name}`, `{user_mention}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.dm.title` | Optional title of the direct message confirming a successful verification. {user_name} is the member's display name. | title (256) | optional | `{user_name}` |

### Verification: server rules (on the welcome panel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `verification.rule.1.name` | Name of rule 1 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.1.value` | Text of rule 1 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rule.2.name` | Name of rule 2 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.2.value` | Text of rule 2 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rule.3.name` | Name of rule 3 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.3.value` | Text of rule 3 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rule.4.name` | Name of rule 4 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.4.value` | Text of rule 4 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rule.5.name` | Name of rule 5 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.5.value` | Text of rule 5 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rule.6.name` | Name of rule 6 of 6, shown in bold before its text (for example a number and a short title). Links cannot be shown here. | field_name (256) | required |  |
| ✅ | `verification.rule.6.value` | Text of rule 6 of 6, after its name on the same line. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ▫️ | `verification.rules.description` | Optional text above the list of rules. You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `verification.rules.title` | Optional heading of the rules on the welcome panel that /setup posts in the 'welcome' channel, shown in bold (it may start with an icon, e.g. {icon_shield}). | title (256) | optional |  |

### Verification: welcome mention

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `verification.welcome_mention.description` | Short text under the mention of members who just joined (the mention itself comes first and pings them), posted in the verification channel (under the welcome panel) and deleted after verification.welcome_mention_seconds (required while that is above 0). You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from server.channels. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

## #how-to-buy guide

Feature `how_to_buy` — enabled.

### How to buy guide

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `how_to_buy.closing` | Optional closing line at the bottom of the step-by-step guide posted in the how_to_buy channel, below the steps (e.g. where to find answers). You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.introduction` | Introduction at the top of the step-by-step guide posted in the how_to_buy channel, above the steps. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.title` | Title of the step-by-step guide posted in the how_to_buy channel. | title (256) | required |  |

### How to buy guide: channel link buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `how_to_buy.link.catalog.emoji` | Optional emoji on the link button that opens the 'catalog' channel. | emoji (100) | optional |  |
| ✅ | `how_to_buy.link.catalog.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'catalog' channel. | button_label (80) | required |  |
| ✅ | `how_to_buy.link.faq.emoji` | Optional emoji on the link button that opens the 'faq' channel. | emoji (100) | optional |  |
| ✅ | `how_to_buy.link.faq.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'faq' channel. | button_label (80) | required |  |
| ✅ | `how_to_buy.link.purchase.emoji` | Optional emoji on the link button that opens the 'purchase' channel. | emoji (100) | optional |  |
| ✅ | `how_to_buy.link.purchase.label` | Label of the link button under the step-by-step guide posted in the how_to_buy channel that opens the 'purchase' channel. | button_label (80) | required |  |

### How to buy guide: steps

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `how_to_buy.step.1.name` | Heading of step 1 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_1} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.1.value` | Explanation of step 1 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.step.2.name` | Heading of step 2 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_2} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.2.value` | Explanation of step 2 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.step.3.name` | Heading of step 3 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_3} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.3.value` | Explanation of step 3 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.step.4.name` | Heading of step 4 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_4} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.4.value` | Explanation of step 4 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.step.5.name` | Heading of step 5 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_5} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.5.value` | Explanation of step 5 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `how_to_buy.step.6.name` | Heading of step 6 of 6 in the step-by-step guide posted in the how_to_buy channel, shown in bold. Start it with a step-number icon if you want one shown, e.g. {icon_step_6} (channel links do not work in headings). | field_name (256) | required |  |
| ✅ | `how_to_buy.step.6.value` | Explanation of step 6 of 6 in the step-by-step guide posted in the how_to_buy channel. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

## #faq

Feature `faq` — enabled.

### FAQ header

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.introduction` | Optional introduction at the top of the FAQ posted in the faq channel, above the first section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.title` | Optional title at the top of the FAQ posted in the faq channel. | title (256) | optional |  |

### FAQ section 'ordering'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.section.ordering.q.1.answer` | Answer to question 1 of 3 in the 'ordering' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.ordering.q.1.question` | Question 1 of 3 in the 'ordering' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.ordering.q.2.answer` | Answer to question 2 of 3 in the 'ordering' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.ordering.q.2.question` | Question 2 of 3 in the 'ordering' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.ordering.q.3.answer` | Answer to question 3 of 3 in the 'ordering' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.ordering.q.3.question` | Question 3 of 3 in the 'ordering' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.ordering.title` | Heading of the 'ordering' section of the FAQ posted in the faq channel, shown in bold (e.g. the topic: ordering, payment...). It may start with one of the bot's icons, e.g. {icon_cart}. | title (256) | required |  |

### FAQ section 'payment'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.section.payment.q.1.answer` | Answer to question 1 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.1.question` | Question 1 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.q.2.answer` | Answer to question 2 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.2.question` | Question 2 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.q.3.answer` | Answer to question 3 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.3.question` | Question 3 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.q.4.answer` | Answer to question 4 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.4.question` | Question 4 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.q.5.answer` | Answer to question 5 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.5.question` | Question 5 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.q.6.answer` | Answer to question 6 of 6 in the 'payment' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.payment.q.6.question` | Question 6 of 6 in the 'payment' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.payment.title` | Heading of the 'payment' section of the FAQ posted in the faq channel, shown in bold (e.g. the topic: ordering, payment...). It may start with one of the bot's icons, e.g. {icon_cart}. | title (256) | required |  |

### FAQ section 'problems'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.section.problems.q.1.answer` | Answer to question 1 of 2 in the 'problems' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.problems.q.1.question` | Question 1 of 2 in the 'problems' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.problems.q.2.answer` | Answer to question 2 of 2 in the 'problems' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.problems.q.2.question` | Question 2 of 2 in the 'problems' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.problems.title` | Heading of the 'problems' section of the FAQ posted in the faq channel, shown in bold (e.g. the topic: ordering, payment...). It may start with one of the bot's icons, e.g. {icon_cart}. | title (256) | required |  |

### FAQ section 'shipping'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.section.shipping.q.1.answer` | Answer to question 1 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.1.question` | Question 1 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.q.2.answer` | Answer to question 2 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.2.question` | Question 2 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.q.3.answer` | Answer to question 3 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.3.question` | Question 3 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.q.4.answer` | Answer to question 4 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.4.question` | Question 4 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.q.5.answer` | Answer to question 5 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.5.question` | Question 5 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.q.6.answer` | Answer to question 6 of 6 in the 'shipping' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.shipping.q.6.question` | Question 6 of 6 in the 'shipping' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.shipping.title` | Heading of the 'shipping' section of the FAQ posted in the faq channel, shown in bold (e.g. the topic: ordering, payment...). It may start with one of the bot's icons, e.g. {icon_cart}. | title (256) | required |  |

### FAQ section 'tracking'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `faq.section.tracking.q.1.answer` | Answer to question 1 of 2 in the 'tracking' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.tracking.q.1.question` | Question 1 of 2 in the 'tracking' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.tracking.q.2.answer` | Answer to question 2 of 2 in the 'tracking' section. You can link channels with {channel_<key>} (e.g. {channel_catalog}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `faq.section.tracking.q.2.question` | Question 2 of 2 in the 'tracking' section (channel links do not work in questions). | field_name (256) | required |  |
| ✅ | `faq.section.tracking.title` | Heading of the 'tracking' section of the FAQ posted in the faq channel, shown in bold (e.g. the topic: ordering, payment...). It may start with one of the bot's icons, e.g. {icon_cart}. | title (256) | required |  |

## #shipping

Feature `shipping` — enabled.

### Shipping areas one by one

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.areas.introduction` | Optional text under that title, e.g. 'Find your state: {icon_dot_blue} we ship there · {icon_dot_grey} not available.' {country} is the country's name. The areas follow in three columns, each with shipping.status.full.indicator or shipping.status.none.indicator. | description (4096) | optional | `{country}` |
| ✅ | `shipping.areas.title` | Title of the card that lists every area of a country one by one (shipping.list_areas, e.g. every US state), e.g. 'Shipping in the {country}'. {country} is the country's name. | title (256) | required | `{country}` |

### Shipping delivery options

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.delivery.heading` | Heading of the delivery options and their prices (checkout.fulfillment_options). It may start with one of the bot's icons, e.g. {icon_truck}. | field_name (256) | required |  |
| ✅ | `shipping.extras.heading` | Heading of the optional extras and their prices (checkout.extras, written with shipping.format.delivery). It may start with one of the bot's icons, e.g. {icon_truck}. | field_name (256) | required |  |
| ✅ | `shipping.format.delivery` | How one delivery option or extra is written, e.g. '**{label}** · {price}'. {label} is its name (checkout.fulfillment.<id>.label or checkout.extra.<id>.label), {price} its price, {description} its description (empty when it has none). | format (100) | required | `{label}`, `{price}`, `{description}` |

### Shipping countries

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.format.excluded` | How a country with excluded areas is written, e.g. '{country} (except {areas})'. Must contain {country} and {areas}. | format (100) | required | `{country}`, `{areas}` |
| ✅ | `shipping.format.last_area` | How the last of several excluded areas is joined, e.g. '{areas} and {last}' (gives 'Alaska and North Dakota'; other areas are separated by commas). Must contain {areas} and {last}. | format (100) | required | `{areas}`, `{last}` |
| ✅ | `shipping.group.caribbean.name` | Heading of the country group 'caribbean' (AG, BS, BB, DM, DO, GD, ...), shown in bold above its countries. | field_name (256) | required |  |
| ✅ | `shipping.group.central_america.name` | Heading of the country group 'central_america' (BZ, CR, SV, GT, HN, NI, ...), shown in bold above its countries. | field_name (256) | required |  |
| ✅ | `shipping.group.europe.name` | Heading of the country group 'europe' (AL, AD, AT, BE, BA, BG, ...), shown in bold above its countries. | field_name (256) | required |  |
| ✅ | `shipping.group.north_america.name` | Heading of the country group 'north_america' (CA, US, MX), shown in bold above its countries. | field_name (256) | required |  |
| ✅ | `shipping.group.south_america.name` | Heading of the country group 'south_america' (AR, BO, BR, CL, CO, EC, ...), shown in bold above its countries. | field_name (256) | required |  |
| ✅ | `shipping.not_available.heading` | Heading of the countries we do not ship to (shipping.not_available), e.g. 'Not available'. Required when countries are listed there or shipping.not_available.others is set. | field_name (256) | required |  |
| ✅ | `shipping.not_available.others` | Optional last item of the not-available list, e.g. 'every country not listed above'. | field_value (1024) | optional |  |

### Shipping information

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.info.1.name` | Heading of information section 1 of 2 (e.g. processing times or tracking), shown in bold below the delivery options. It may start with one of the bot's icons, e.g. {icon_truck}. | field_name (256) | required |  |
| ✅ | `shipping.info.1.value` | Text of information section 1 of 2. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `shipping.info.2.name` | Heading of information section 2 of 2 (e.g. processing times or tracking), shown in bold below the delivery options. It may start with one of the bot's icons, e.g. {icon_truck}. | field_name (256) | required |  |
| ✅ | `shipping.info.2.value` | Text of information section 2 of 2. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | field_value (1024) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

### Shipping header

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.introduction` | Introduction at the top of the shipping card posted in the shipping channel. You can link channels with {channel_<key>} (e.g. {channel_faq}); they become clickable #channel links. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `shipping.legend` | Optional line under the introduction explaining the status dots, e.g. '{icon_dot_blue} We ship everywhere · {icon_dot_yellow} Some areas excluded · {icon_dot_grey} Not available'. | description (4096) | optional |  |
| ✅ | `shipping.title` | Title of the shipping card posted in the shipping channel. | title (256) | required |  |

### Shipping status dots

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `shipping.status.full.indicator` | Dot shown before the countries where we ship everywhere in the country, e.g. {icon_dot_blue}. Required while such countries are listed. | field_name (256) | required |  |
| ✅ | `shipping.status.none.indicator` | Dot shown before the countries where we do not ship to the country, e.g. {icon_dot_blue}. Required while such countries are listed. | field_name (256) | required |  |
| ✅ | `shipping.status.partial.indicator` | Dot shown before the countries where we ship to the country except some areas, e.g. {icon_dot_blue}. Required while such countries are listed. | field_name (256) | required |  |

## Tickets

Feature `tickets` — enabled.

### Ticket status messages (posted in the ticket channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.auto_close_warning.description` | Posted in an inactive ticket (and pings the customer) tickets.auto_close_warning_minutes before it closes automatically. {minutes} is that number of minutes, {user_mention} mentions the customer. Needed when both auto-close settings are above 0. | description (4096) | optional | `{minutes}`, `{user_mention}` |
| ✅ | `tickets.auto_close_warning.title` | Optional title of the message posted in the ticket before it closes automatically for inactivity. | title (256) | optional |  |
| ✅ | `tickets.auto_closed.description` | Text of the ticket-closed message when the ticket was closed automatically for inactivity (used instead of tickets.closed.description). {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.auto_close_inactive_minutes is above 0. | description (4096) | optional | `{number}` |
| ✅ | `tickets.claimed.description` | Posted in the ticket when a staff member claims it. {staff_mention} mentions that staff member. | description (4096) | required | `{staff_mention}` |
| ✅ | `tickets.claimed.title` | Optional title of the message posted in the ticket when a staff member claims it. | title (256) | optional |  |
| ✅ | `tickets.closed.description` | Text of the ticket-closed message when someone closed it. {closer_mention} mentions who closed it. {number} is the ticket number, zero-padded to tickets.number_padding digits. | description (4096) | required | `{closer_mention}`, `{number}` |
| ✅ | `tickets.closed.reason_label` | Optional field name under which the close reason given by staff (/ticket close reason) is shown on the ticket-closed message. If empty, the reason is not shown to the customer. | field_name (256) | optional |  |
| ✅ | `tickets.closed.title` | Title of the message posted in the ticket when it is closed (manually or automatically); this message carries the reopen/transcript/delete buttons. {number} is the ticket number, zero-padded to tickets.number_padding digits. | title (256) | required | `{number}` |
| ✅ | `tickets.deletion_scheduled.description` | Posted in the ticket after closing when the channel will be deleted automatically. {minutes} is tickets.delete_after_close_minutes. Needed when that setting is above 0. | description (4096) | optional | `{minutes}` |
| ✅ | `tickets.deletion_scheduled.title` | Optional title of the message posted in the ticket after closing when the channel will be deleted automatically. | title (256) | optional |  |
| ✅ | `tickets.reopened.description` | Posted in the ticket when it is reopened. {actor_mention} mentions who reopened it. Needed when tickets.allow_reopen = true. | description (4096) | required | `{actor_mention}` |
| ✅ | `tickets.reopened.title` | Optional title of the message posted in the ticket when it is reopened. | title (256) | optional |  |
| ✅ | `tickets.unclaimed.description` | Posted in the ticket when it is unclaimed. | description (4096) | required |  |
| ✅ | `tickets.unclaimed.title` | Optional title of the message posted in the ticket when it is unclaimed. | title (256) | optional |  |
| ✅ | `tickets.user_added.description` | Posted in the ticket when staff add someone to it (/ticket add). {user_mention} mentions (and pings) the added member. | description (4096) | required | `{user_mention}` |
| ✅ | `tickets.user_added.title` | Optional title of the message posted in the ticket when staff add someone to it (/ticket add). | title (256) | optional |  |
| ✅ | `tickets.user_removed.description` | Posted in the ticket when staff remove someone from it (/ticket remove). {user_mention} mentions the removed member. | description (4096) | required | `{user_mention}` |
| ✅ | `tickets.user_removed.title` | Optional title of the message posted in the ticket when staff remove someone from it (/ticket remove). | title (256) | optional |  |

### Ticket buttons

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.button.claim` | Claim button on the ticket welcome message (staff take ownership of the ticket). | button_label (80) | required |  |
| ✅ | `tickets.button.close` | Close button on the ticket welcome message (and on a report's panel); also the button that confirms closing on the private close prompt. | button_label (80) | required |  |
| ✅ | `tickets.button.delete` | Delete button on the ticket-closed message (staff only; asks for confirmation), and the button that confirms the deletion on that private prompt. | button_label (80) | required |  |
| ✅ | `tickets.button.reopen` | Reopen button on the ticket-closed message. Needed when tickets.allow_reopen = true. | button_label (80) | required |  |
| ✅ | `tickets.button.transcript` | Transcript button on the ticket-closed message (staff only; posts a transcript to the transcript channel). Needed when tickets.transcript_channel is set. | button_label (80) | required |  |
| ✅ | `tickets.button.unclaim` | Unclaim button that replaces the claim button while a staff member has claimed the ticket. | button_label (80) | required |  |

### Ticket confirmation prompts (private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.close_confirm.description` | Text of the private prompt asking to confirm closing a ticket (buttons: tickets.button.close confirms, the shared Cancel button keeps it open). Needed when tickets.close_confirmation = true. | description (4096) | required |  |
| ✅ | `tickets.close_confirm.title` | Optional title of the private prompt asking to confirm closing a ticket. | title (256) | optional |  |
| ✅ | `tickets.delete_confirm.description` | Text of the private prompt asking staff to confirm deleting a ticket channel (buttons: tickets.button.delete confirms, the shared Cancel button keeps it). | description (4096) | required |  |
| ✅ | `tickets.delete_confirm.title` | Optional title of the private prompt asking staff to confirm deleting a ticket channel. | title (256) | optional |  |

### Ticket private replies

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.created.description` | Private reply after a ticket was created. {channel_mention} links the new ticket channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. | description (4096) | required | `{channel_mention}`, `{number}` |
| ✅ | `tickets.created.title` | Optional title of the private reply after a ticket was created. {number} is the ticket number, zero-padded to tickets.number_padding digits. | title (256) | optional | `{number}` |
| ✅ | `tickets.error.not_open` | Private error when a ticket button cannot be used in the ticket's current state (e.g. closing an already closed ticket, claiming an already claimed ticket, deleting an open ticket). | description (4096) | required |  |
| ✅ | `tickets.limit_reached.description` | Private error when the customer reached a ticket limit: the maximum number of open tickets in that category (also checked when they reopen their own closed ticket), or the maximum number of tickets created in 24 hours (tickets.max_created_per_user_per_day). {limit} is that maximum. | description (4096) | required | `{limit}` |

### Ticket panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.panel.description` | Text of the ticket panel. Needed when tickets.panel_channel is set. | description (4096) | optional |  |
| ✅ | `tickets.panel.select_placeholder` | Greyed-out text of the category selection menu (used when tickets.panel_style = "select" and tickets.panel_channel is set). | select_placeholder (150) | optional |  |
| ✅ | `tickets.panel.title` | Title of the ticket panel (the message with the open buttons). Needed when tickets.panel_channel is set. | title (256) | optional |  |

### New-ticket staff alert

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.staff_alert.description` | Text of the new-ticket staff alert. {user_mention} mentions the customer, {channel_mention} links the ticket, {category_label} is the category's button label. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | description (4096) | required | `{user_mention}`, `{channel_mention}`, `{number}`, `{category_label}` |
| ✅ | `tickets.staff_alert.title` | Title of the alert posted in the staff alert channel for each new ticket. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.staff_alert_channel is set. | title (256) | required | `{number}`, `{category_label}` |

### Ticket transcripts

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `tickets.transcript.description` | Text of the transcript message in the transcript channel. {user_mention} mentions the customer; {closer_mention} mentions who closed the ticket (the bot for automatic closes, or the staff member who requested the transcript while the ticket was open); {closed_at} is when it was closed (shown in each reader's time zone; empty while it is open); {final_status} is the order's final status name (checkout.status_label.*) for order channels, empty for support tickets; {close_reason} is the recorded close reason (the reason staff gave, or the automatic reason). {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | description (4096) | required | `{number}`, `{user_mention}`, `{closer_mention}`, `{closed_at}`, `{final_status}`, `{close_reason}` |
| ✅ | `tickets.transcript.title` | Title of the message (with the transcript file) posted in the transcript channel. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_channel is set. | title (256) | required | `{number}` |
| ✅ | `tickets.transcript_dm.description` | Text of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | description (4096) | optional | `{number}` |
| ✅ | `tickets.transcript_dm.title` | Title of the direct message that sends the customer their transcript file. {number} is the ticket number, zero-padded to tickets.number_padding digits. Needed when tickets.transcript_dm_user = true and a transcript channel is set. | title (256) | optional | `{number}` |

## Catalog

Feature `catalog` — enabled.

### Catalog: product cards (listing and private browsing)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `catalog.availability.in_stock` | Availability of a product with limited stock that can still be ordered (its card uses the success color). {count} is the number of units that can still be ordered (stock minus units held by pending orders). | field_value (1024) | required | `{count}` |
| ✅ | `catalog.availability.low_stock` | Optional: availability of a product with only 1 to catalog.low_stock_threshold units left (its card uses the warning color). {count} is the number of units that can still be ordered. If empty, catalog.availability.in_stock is shown. | field_value (1024) | optional (falls back to `catalog.availability.in_stock`) | `{count}` |
| ✅ | `catalog.availability.out_of_stock` | Availability of a product whose stock is used up (still listed, cannot be ordered; its buy button is disabled and its card uses the danger color). | field_value (1024) | required |  |
| ✅ | `catalog.availability.unlimited` | Availability of a product without a stock limit (its card uses the success color). | field_value (1024) | required |  |
| ✅ | `catalog.field.availability` | Name of the availability field on every product card. The value is one of the catalog.availability.* texts. | field_name (256) | required |  |
| ✅ | `catalog.field.max_per_order` | Optional: name of a field showing the maximum quantity per order (the value is the number). Shown only on products that have a limit. If empty, the limit is not shown. | field_name (256) | optional |  |
| ✅ | `catalog.field.price` | Name of the price field on every product card. The value is the price written with common.money_format. | field_name (256) | required |  |

### Catalog: private browsing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `catalog.button.browse` | Button under the listing's header card that opens a private, page-by-page view of the products (only when catalog.browse_enabled is true). | button_label (80) | optional |  |

### Catalog: buy button on product cards

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `catalog.button.buy` | Button under each product card (listing and private browsing) that opens the customer's private order channel, or continues the one they already have, with this product in the cart. {name} is the product name (use it when catalog.products_per_listing_message is more than 1, so each button names its product). Disabled while the product is out of stock. | button_label (80) | required | `{name}` |
| ✅ | `catalog.button.buy_emoji` | Optional emoji shown on the buy button. | emoji (100) | optional |  |
| ✅ | `catalog.button.buy_short` | Optional: label used instead of catalog.button.buy for a product whose name makes that label longer than Discord's 80 characters. Required only when such a product exists (the listing reports it). | button_label (80) | optional |  |

### Catalog: live product listing

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `catalog.listing.description` | Text of the header card at the top of the product listing channel. | description (4096) | required |  |
| ✅ | `catalog.listing.empty` | Shown below the listing header, and in private browsing, when no product is currently offered. | description (4096) | required |  |
| ✅ | `catalog.listing.title` | Title of the header card: the first message of the product listing channel (each product follows in its own message). | title (256) | required |  |

## #new-arrivals (Restock Alerts)

Feature `new_arrivals` — enabled.

### New arrivals: notification panel (posted by /setup in the new-arrivals channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `new_arrivals.button.toggle` | Label of the panel's single button. A click gives the member the notification role, or removes it when they already have it (so the label should describe both, e.g. turning notifications on or off). | button_label (80) | required |  |
| ✅ | `new_arrivals.button.toggle_emoji` | Optional emoji shown on the panel's button. | emoji (100) | optional |  |
| ✅ | `new_arrivals.panel.description` | Text of the notification panel: what members receive when they turn notifications on, and that the same button turns them off again. {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `new_arrivals.panel.title` | Title of the persistent panel in the new-arrivals channel that lets members turn product notifications on or off. | title (256) | required |  |

### New arrivals: automatic product updates (posted in the new-arrivals channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `new_arrivals.button.view_catalog` | Optional label of a link button under each product update that opens the catalog listing channel (catalog.listing_channel). If empty, the updates have no button. | button_label (80) | optional |  |
| ✅ | `new_arrivals.new_product.footer` | Optional small text at the bottom of a new-product update. | footer (2048) | optional |  |
| ✅ | `new_arrivals.new_product.title` | Title of the update posted automatically when a product is added to the catalog or switched back on (new_arrivals.announce_new_products). {name} is the product name. Below it the update shows the product's description, image, price and availability exactly like its catalog card. | title (256) | required | `{name}` |
| ✅ | `new_arrivals.restock.footer` | Optional small text at the bottom of a restock update. | footer (2048) | optional |  |
| ✅ | `new_arrivals.restock.title` | Title of the update posted automatically when a sold-out product can be ordered again (new_arrivals.announce_restocks). {name} is the product name. Below it the update shows the product's description, image, price and availability exactly like its catalog card. | title (256) | required | `{name}` |

### New arrivals: private confirmations (shown only to the member who clicked)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `new_arrivals.subscribed.description` | Private confirmation shown after a member turned notifications on (they now have the notification role and are mentioned in product updates). {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `new_arrivals.subscribed.title` | Optional title of the private confirmation shown after a member turned notifications on. | title (256) | optional |  |
| ✅ | `new_arrivals.unsubscribed.description` | Private confirmation shown after a member turned notifications off (the notification role was removed; clicking the button again turns them back on). {role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links such as {channel_catalog} become clickable #channel links once /setup created the channel. | description (4096) | required | `{role}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `new_arrivals.unsubscribed.title` | Optional title of the private confirmation shown after a member turned notifications off. | title (256) | optional |  |

## Checkout, orders and payment

Feature `checkout` — enabled.

### Checkout: shipping address (checkout.ask_address)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.address.added_line` | Line on the shipping address step once the address is entered, e.g. '**Address:** {name}, {city}'. The street is never shown in the channel. | format (100) | required | `{name}`, `{city}`, `{region}`, `{postal}` |
| ✅ | `checkout.address.button` | Button on the shipping address step that opens the address form. | button_label (80) | required |  |
| ✅ | `checkout.address.button_change` | The same button once an address was entered. | button_label (80) | required |  |
| ✅ | `checkout.address.city.label` | Label of the address form field for the city or town; also its name in the address shown to staff. | input_label (45) | required |  |
| ▫️ | `checkout.address.city.placeholder` | Optional greyed-out example in the field for the city or town. | input_placeholder (100) | optional |  |
| ✅ | `checkout.address.country_label` | Label of the country in the address shown to staff (e.g. 'Country'). | field_name (256) | required |  |
| ✅ | `checkout.address.country_line` | Line on the shipping address step showing the chosen country, e.g. '**Country:** {country}'. | format (100) | required | `{country}` |
| ✅ | `checkout.address.country_none` | Shown as {country} before a country is chosen, e.g. 'not chosen yet'. | field_value (1024) | required |  |
| ✅ | `checkout.address.country_placeholder` | Greyed-out text of a country menu on the shipping address step. {first} and {last} are the first and last country in it (a region with more than 25 countries has several menus), e.g. 'Choose your country ({first} to {last})'. | select_placeholder (150) | required | `{first}`, `{last}` |
| ✅ | `checkout.address.heading` | Heading of the address section of the shipping address step, shown in bold (it may start with an icon, e.g. {icon_location}). | title (256) | required |  |
| ✅ | `checkout.address.menu.caribbean` | Name of the region 'caribbean' ([[shipping.groups]]) in the address step's region menu (e.g. North America). | option_label (100) | required |  |
| ✅ | `checkout.address.menu.central_america` | Name of the region 'central_america' ([[shipping.groups]]) in the address step's region menu (e.g. North America). | option_label (100) | required |  |
| ✅ | `checkout.address.menu.europe` | Name of the region 'europe' ([[shipping.groups]]) in the address step's region menu (e.g. North America). | option_label (100) | required |  |
| ✅ | `checkout.address.menu.north_america` | Name of the region 'north_america' ([[shipping.groups]]) in the address step's region menu (e.g. North America). | option_label (100) | required |  |
| ✅ | `checkout.address.menu.south_america` | Name of the region 'south_america' ([[shipping.groups]]) in the address step's region menu (e.g. North America). | option_label (100) | required |  |
| ✅ | `checkout.address.missing_line` | Line on the shipping address step before the address is entered, e.g. '**Address:** not added yet'. | field_value (1024) | required |  |
| ✅ | `checkout.address.modal_title` | Title of the private address form. | modal_title (45) | required |  |
| ✅ | `checkout.address.name.label` | Label of the address form field for the recipient's full name; also its name in the address shown to staff. | input_label (45) | required |  |
| ✅ | `checkout.address.name.placeholder` | Optional greyed-out example in the field for the recipient's full name. | input_placeholder (100) | optional |  |
| ✅ | `checkout.address.postal.label` | Label of the address form field for the postal or ZIP code; also its name in the address shown to staff. | input_label (45) | required |  |
| ▫️ | `checkout.address.postal.placeholder` | Optional greyed-out example in the field for the postal or ZIP code. | input_placeholder (100) | optional |  |
| ✅ | `checkout.address.region.label` | Label of the address form field for the state, province or region (required for a country with excluded areas, e.g. US states); also its name in the address shown to staff. | input_label (45) | required |  |
| ✅ | `checkout.address.region.placeholder` | Optional greyed-out example in the field for the state, province or region (required for a country with excluded areas, e.g. US states). | input_placeholder (100) | optional |  |
| ✅ | `checkout.address.region_placeholder` | Greyed-out text of the region menu on the shipping address step (e.g. 'Choose your region'). | select_placeholder (150) | required |  |
| ✅ | `checkout.address.ship_to` | How the destination is written on the review, the order and the staff record, e.g. '{name}, {city}, {country}' (the full address is shown to staff only). | format (100) | required | `{name}`, `{city}`, `{region}`, `{postal}`, `{country}` |
| ✅ | `checkout.address.street.label` | Label of the address form field for the street and house number (and apartment, if any); also its name in the address shown to staff. | input_label (45) | required |  |
| ✅ | `checkout.address.street.placeholder` | Optional greyed-out example in the field for the street and house number (and apartment, if any). | input_placeholder (100) | optional |  |
| ✅ | `checkout.error.area_excluded` | Private reply when the address is in an area we do not ship to (shipping.excluded_areas). {area} is its name (e.g. Alaska), {country} the country. The answers are kept so the customer can correct them. | description (4096) | required | `{area}`, `{country}` |
| ✅ | `checkout.error.country_first` | Private reply when the address form is opened before a country is chosen. | description (4096) | required |  |

### Checkout: order workflow - choosing products

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.browse.description` | Optional text of the product-choosing step (e.g. how to pick products and quantities). | description (4096) | optional |  |
| ✅ | `checkout.browse.empty_catalog` | Shown in the product-choosing step when no product is offered right now. | description (4096) | required |  |
| ✅ | `checkout.browse.select_placeholder` | Greyed-out text of the product selection menu (if empty, Discord would show its own default text). The options are the product names; the small text under each is its price. | select_placeholder (150) | required |  |
| ✅ | `checkout.browse.title` | Title of the order workflow message while the customer chooses products (first step). | title (256) | required |  |

### Checkout: order workflow - review and submit

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.button.apply_credits` | Button in the review step that applies the customer's store credit. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ✅ | `checkout.button.remove_credits` | Button in the review step that removes the applied store credit. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ✅ | `checkout.button.submit` | Button in the review step that submits the order. | button_label (80) | required |  |
| ✅ | `checkout.review.credits_available_field` | Optional name of the line under the review step's total showing how much store credit the customer could apply (shown while it is not applied). | field_name (256) | optional |  |
| ✅ | `checkout.review.credits_field` | Name of the store credit applied to the order, in the summary (checkout.summary.credit_line) and on the staff order record. Needed when credits are enabled and credits.allow_at_checkout = true. | field_name (256) | required |  |
| ✅ | `checkout.review.description` | Optional text of the review step. | description (4096) | optional |  |
| ✅ | `checkout.review.price_changed` | Shown in the review step when the total changed since the customer looked at it (prices, fees or their credit balance changed); nothing was submitted yet. {total} is the new total. | description (4096) | required | `{total}` |
| ✅ | `checkout.review.title` | Title of the final step where the customer reviews the order and submits it. | title (256) | required |  |
| ✅ | `checkout.review.total_field` | Name of the total to pay: the last line of the summary (review step and the placed order) and on the staff order record. | field_name (256) | required |  |

### Checkout: cancelling

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.button.cancel_order` | Button under a submitted order that lets the customer cancel it while it is not paid yet. | button_label (80) | required |  |
| ✅ | `checkout.button.cancel_yes` | Button of the cancel question (an unfinished order or an unpaid one) that cancels it. | button_label (80) | required |  |
| ✅ | `checkout.button.keep_order` | Button of the cancel question that keeps the order and goes back to it. | button_label (80) | required |  |
| ✅ | `checkout.cancel_confirm.description` | Question asking the customer to confirm cancelling an unfinished order (shared confirm/cancel buttons). | description (4096) | required |  |
| ✅ | `checkout.cancel_confirm.title` | Optional title of the question asking the customer to confirm cancelling an unfinished order. | title (256) | optional |  |
| ✅ | `checkout.cancelled.description` | Text of the workflow message after the customer cancelled an unfinished order. Also used after staff closed the ticket of an unfinished order (/ticket close) when checkout.staff_closed.description is empty. | description (4096) | required |  |
| ✅ | `checkout.cancelled.title` | Optional title of the workflow message after the customer cancelled an unfinished order (the channel is then closed). Also used after staff closed the ticket of an unfinished order when checkout.staff_closed.title is empty. | title (256) | optional |  |
| ✅ | `checkout.expired.description` | Text of the workflow message when an unfinished order expired. Needed when checkout.session_expiry_minutes is above 0. | description (4096) | required |  |
| ✅ | `checkout.expired.title` | Optional title of the workflow message when an unfinished order expired after checkout.session_expiry_minutes without activity. | title (256) | optional |  |
| ✅ | `checkout.order_cancel_confirm.description` | Question asking the customer to confirm cancelling a submitted, unpaid order (shared confirm/back buttons). {order_number} is the order number. | description (4096) | required | `{order_number}` |
| ✅ | `checkout.order_cancel_confirm.title` | Optional title of the question asking the customer to confirm cancelling a submitted, unpaid order. {order_number} is the order number. | title (256) | optional | `{order_number}` |
| ✅ | `checkout.staff_closed.description` | Optional text of the workflow message after staff closed the ticket of an unfinished (never submitted) order with /ticket close (the order is cancelled, the channel closed). Empty = checkout.cancelled.description. | description (4096) | optional (falls back to `checkout.cancelled.description`) |  |
| ✅ | `checkout.staff_closed.title` | Optional title of the workflow message after staff closed the ticket of an unfinished (never submitted) order with /ticket close. Empty = checkout.cancelled.title. | title (256) | optional (falls back to `checkout.cancelled.title`) |  |

### Checkout: order workflow - order details form

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.button.enter_details` | Button that opens the order details form. Needed when a form is defined. | button_label (80) | optional |  |
| ✅ | `checkout.button.enter_more_details` | Button that opens the second part of the details form (Discord forms hold at most 5 fields; used when checkout.details_form plus an option's form have more than 5 fields together). | button_label (80) | optional |  |
| ✅ | `checkout.details.description` | Optional text of the details step. | description (4096) | optional |  |
| ✅ | `checkout.details.modal_title` | Title of the order details form. Needed when a form is defined. | modal_title (45) | optional |  |
| ✅ | `checkout.details.title` | Title of the step where the customer enters order details (checkout.details_form and the chosen option's form). The answers are shown below as fields named by the field labels. Needed when a form is defined. | title (256) | optional |  |

### Checkout: shop panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.button.start` | Button on the shop panel that opens a private order channel for the customer. The panel also shows the catalog's browse button (catalog.button.browse) and the credits balance button when those are configured. | button_label (80) | required |  |
| ✅ | `checkout.panel.description` | Text of the shop panel. | description (4096) | required |  |
| ✅ | `checkout.panel.title` | Title of the shop panel (the message with the start button). | title (256) | required |  |

### Checkout: private replies to the start button

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.buy_added.description` | Optional: private reply when the customer clicks a product's buy button (catalog.button.buy) while they already have an unfinished order channel; the product is now in its cart (one unit, or the quantity already chosen). {name} is the product name, {channel_mention} links the channel. If empty, checkout.session_exists.description is shown. A buy button that opens a new order channel answers with checkout.started.description. | description (4096) | optional (falls back to `checkout.session_exists.description`) | `{name}`, `{channel_mention}` |
| ✅ | `checkout.limit_reached.description` | Private reply when the customer already has the maximum number of open orders (waiting for payment, paid, or in progress). {limit} is checkout.max_open_orders_per_user. | description (4096) | required | `{limit}` |
| ✅ | `checkout.order_exists.description` | Private reply when the customer clicks start while they already have the maximum number of open orders (checkout.max_open_orders_per_user) and one of them still has its order ticket open: no new ticket is created, the reply links the existing one. {channel_mention} links that ticket, {order_number} is its order number, {limit} the maximum (checkout.limit_reached.description is used when no open ticket can be linked). | description (4096) | required | `{channel_mention}`, `{order_number}`, `{limit}` |
| ✅ | `checkout.session_exists.description` | Private reply when the customer clicks start while they already have an unfinished order channel; no second channel is created. {channel_mention} links the existing channel. | description (4096) | required | `{channel_mention}` |
| ✅ | `checkout.started.description` | Private reply after the start button created the customer's order channel. {channel_mention} links the channel. | description (4096) | required | `{channel_mention}` |
| ✅ | `checkout.ticket_link.button` | Optional link button under the private replies to the start button (new ticket, unfinished ticket, existing open order) that opens the customer's order ticket. Omitted when empty. | button_label (80) | optional |  |

### Checkout: order workflow - cart

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.cart.description` | Optional text of the cart step. | description (4096) | optional |  |
| ✅ | `checkout.cart.edit_option` | Optional small text under each product in the change-quantity menu. {quantity} is the quantity in the cart, {line_total} the line price. | option_description (100) | optional | `{quantity}`, `{line_total}` |
| ✅ | `checkout.cart.edit_placeholder` | Greyed-out text of the menu that picks a cart line to change its quantity (if empty, Discord would show its own default text). | select_placeholder (150) | required |  |
| ✅ | `checkout.cart.empty` | Shown under the items heading while the cart is empty. | field_value (1024) | required |  |
| ✅ | `checkout.cart.field` | Name of the field listing the order lines on the staff order record (the customer's live panel lists them under checkout.cart.heading). Each line is written with checkout.cart.line. | field_name (256) | required |  |
| ✅ | `checkout.cart.line` | How one cart/order line is written (the customer's items and the staff order record), e.g. '`{quantity}×` **{name}** · {line_total}'. {name} is the product name, {quantity} the quantity, {unit_price} the price of one unit and {line_total} the line price (both written with common.money_format). | format (100) | required | `{name}`, `{quantity}`, `{unit_price}`, `{line_total}` |
| ✅ | `checkout.cart.minimum_not_met` | Shown in the cart step while the subtotal is below checkout.min_order_total_minor (the continue button stays disabled). {minimum} is that minimum written with common.money_format. Needed when checkout.min_order_total_minor is above 0. | description (4096) | optional | `{minimum}` |
| ✅ | `checkout.cart.subtotal_field` | Name of the subtotal (sum of the cart lines before fees and credits) in the summary and on the staff order record. | field_name (256) | required |  |
| ✅ | `checkout.cart.title` | Title of the cart step (the customer checks and edits quantities). | title (256) | required |  |

### Checkout: order ticket - items, summary and progress (live panel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.cart.heading` | Heading of the items section of the live panel (every step and every status), shown in bold, e.g. '{icon_cart} ITEMS'. Each item is written with checkout.cart.line. | title (256) | required |  |
| ✅ | `checkout.progress.current` | The stage the order is in now, e.g. '{icon_check} **{stage}** · {date}'. {stage} is the stage name (checkout.ticket.stage.*, or checkout.progress.start for the first one), {date} the date it was reached, {time} the date and time, {relative} the same as a relative time (e.g. '2 hours ago'); use one of them. | field_value (1024) | required | `{stage}`, `{date}`, `{time}`, `{relative}` |
| ✅ | `checkout.progress.done` | A stage the order went through, e.g. '{icon_check} {stage} · {date}'. {stage} is the stage name (checkout.ticket.stage.*, or checkout.progress.start for the first one), {date} the date it was reached, {time} the date and time, {relative} the same as a relative time (e.g. '2 hours ago'); use one of them. | field_value (1024) | required | `{stage}`, `{date}`, `{time}`, `{relative}` |
| ✅ | `checkout.progress.heading` | Heading of the progress tracker on the live panel once the order was placed, shown in bold, e.g. '{icon_clock} PROGRESS'. It lists the stages reached (with their date), then the ones still to come. | title (256) | required |  |
| ✅ | `checkout.progress.next` | A stage still to come, e.g. '{icon_dot_grey} {stage}'. {stage} is the stage name. | field_value (1024) | required | `{stage}` |
| ✅ | `checkout.progress.start` | Name of the first stage in the progress tracker, when the customer pressed Confirm order and the items were reserved for them, e.g. 'Items reserved'. The later stages use their stage names (checkout.ticket.stage.*). | format (100) | required |  |
| ✅ | `checkout.progress.stopped` | The stage that ended the order (cancelled, rejected, expired or refunded), e.g. '{icon_cross} **{stage}** · {date}'. {stage} is the stage name (checkout.ticket.stage.*, or checkout.progress.start for the first one), {date} the date it was reached, {time} the date and time, {relative} the same as a relative time (e.g. '2 hours ago'); use one of them. | field_value (1024) | required | `{stage}`, `{date}`, `{time}`, `{relative}` |
| ✅ | `checkout.summary.credit_line` | How the store credit applied to the order is written in the summary (it is taken off the total), e.g. '{label} · -{amount}'. {label} is checkout.review.credits_field, {amount} the credit applied. Needed when credits are enabled and credits.allow_at_checkout = true. | format (100) | required | `{label}`, `{amount}` |
| ✅ | `checkout.summary.free` | Optional: shown as the amount of a delivery option without a fee, e.g. 'Free' (when empty, the option is listed without an amount). | format (100) | optional |  |
| ✅ | `checkout.summary.heading` | Heading of the amounts section of the live panel, shown in bold, e.g. '{icon_receipt} SUMMARY': the subtotal, the delivery option and extras with their prices, fees, store credit and the total (what is known so far while the order is put together). | title (256) | required |  |
| ✅ | `checkout.summary.line` | How one amount of the summary is written, e.g. '{label} · {amount}'. {label} is its name (the subtotal, the delivery option, an extra or fee), {amount} the amount (common.money_format). | format (100) | required | `{label}`, `{amount}` |
| ✅ | `checkout.summary.total_line` | Last line of the summary: the total (or the subtotal while nothing else is known), e.g. '**{label} · {amount}**'. {label} is checkout.review.total_field (or checkout.cart.subtotal_field), {amount} the amount. | format (100) | required | `{label}`, `{amount}` |

### Checkout: private order channel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.channel_prefix` | Channel-name prefix of private order channels ({prefix} in tickets.channel_name_pattern; the number is the shared ticket/order-channel counter). Discord lower-cases channel names. | channel_prefix (60) | required |  |
| ✅ | `checkout.channel_topic` | Optional topic of each order channel. {user_name} is the customer's display name. | channel_topic (1024) | optional | `{user_name}` |

### Checkout: discount codes (review step)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.discount.button_add` | Button on the review step that opens the private discount code form, e.g. '{icon_discount} Add discount code'. Shown while the shop has at least one discount code that can be used (/discount). | button_label (80) | required |  |
| ✅ | `checkout.discount.button_remove` | Button on the review step that takes the applied discount code off the order (it replaces the add and use buttons while a code is applied). | button_label (80) | required |  |
| ✅ | `checkout.discount.button_use` | Button on the review step for a coupon the customer claimed (at most two, newest first): pressing it applies the code, e.g. '{icon_gift} Use coupon {code}'. {code} is the code. | button_label (80) | required | `{code}` |
| ✅ | `checkout.discount.error.already_used` | Private reply when the customer's discount code cannot be used because the code is once per customer and this customer already placed an order with it; also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.ended` | Private reply when the customer's discount code cannot be used because staff ended the code (/discount end); also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.expired` | Private reply when the customer's discount code cannot be used because the code's end date has passed; also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.minimum` | Private reply when the customer's discount code cannot be used because the items in the cart are worth less than the code's minimum ({minimum}); also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}`, `{minimum}` |
| ✅ | `checkout.discount.error.needs_two_items` | Private reply when the customer's discount code cannot be used because the code is buy one, get one free and fewer than two eligible items are in the cart; also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.no_eligible_items` | Private reply when the customer's discount code cannot be used because the code is limited to some items and none of them is in the cart; also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.too_many_tries` | Private reply after 5 codes that could not be used within 10 minutes (protection against guessing codes): the customer waits a few minutes before trying again. | description (4096) | required |  |
| ✅ | `checkout.discount.error.unknown` | Private reply when the customer's discount code cannot be used because the shop has no code with this name (or it uses characters codes never have); also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.error.used_up` | Private reply when the customer's discount code cannot be used because the code was used as many times as it allows; also shown in the summary of the review step (checkout.summary.discount_note) when a code the customer applied stops applying. {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.input_label` | Label of the code field in the form. | input_label (45) | required |  |
| ✅ | `checkout.discount.input_placeholder` | Optional greyed-out example inside the empty code field. | input_placeholder (100) | optional |  |
| ✅ | `checkout.discount.modal_title` | Title of the discount code form. | modal_title (45) | required |  |
| ✅ | `checkout.summary.bogo_line` | How a buy one, get one free discount is written in the summary, e.g. 'Buy one, get one free ({code}) · -{amount}'. {code} is the code, {amount} the price of the free items. | format (100) | required | `{code}`, `{amount}` |
| ✅ | `checkout.summary.discount_line` | How a percent or amount discount is written in the summary, right under the subtotal (review step, order ticket, staff record), e.g. 'Discount ({code}) · -{amount}'. {code} is the code, {amount} the amount it takes off. | format (100) | required | `{code}`, `{amount}` |
| ✅ | `checkout.summary.discount_note` | Shown under the total of the review step when a code the customer applied stops applying, e.g. '{icon_alert} {reason} You can also press **Remove discount**.'. {reason} is the reason's text (checkout.discount.error.*). | field_value (1024) | required | `{reason}` |
| ✅ | `checkout.summary.discount_off_line` | Shown in the summary in the discount's place when a code the customer applied stops applying (the cart changed, the code expired, was used up or ended), e.g. 'Discount ({code}) · not applied'. The total is shown without the discount; the note under it says why (checkout.summary.discount_note). | format (100) | required | `{code}` |

### Checkout: claimed coupons (private replies to an announcement's claim button)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.discount.claim.description` | First text of the private reply after a coupon was claimed (claiming it again shows the same reply). {code} is the code. | description (4096) | required | `{code}` |
| ✅ | `checkout.discount.claim.ended` | Text of that reply. | description (4096) | required |  |
| ✅ | `checkout.discount.claim.ended_title` | Title of the private reply to a claim button whose offer has ended (the code ended or expired, or discount codes are switched off). | title (256) | required |  |
| ✅ | `checkout.discount.claim.heading` | Heading of the coupon's terms in the claim reply, shown in bold, e.g. '{icon_gift} YOUR COUPON'. The terms follow, one per line (checkout.discount.terms.*). | title (256) | required |  |
| ✅ | `checkout.discount.claim.howto` | How to use the coupon (under checkout.discount.claim.howto_heading). {code} is the code; {channel_catalog} links the catalog channel. | description (4096) | required | `{code}`, `{channel_catalog}` |
| ✅ | `checkout.discount.claim.howto_heading` | Heading of how to use the coupon in the claim reply, shown in bold, e.g. '{icon_info} HOW TO USE IT'. | title (256) | required |  |
| ✅ | `checkout.discount.claim.title` | Title of the private reply after a coupon was claimed. | title (256) | required |  |
| ✅ | `checkout.discount.terms.amount` | The terms of an amount coupon, e.g. '**{amount} off** your items'. {amount} is the amount. | field_value (1024) | required | `{amount}` |
| ✅ | `checkout.discount.terms.bogo` | The terms of a buy one, get one free coupon, e.g. '**Buy one, get one free:** the cheaper item of every pair is free'. | field_value (1024) | required |  |
| ✅ | `checkout.discount.terms.expires` | Shown when the coupon has an end date: {date} is that date (each reader sees it in their own time zone). | field_value (1024) | required | `{date}` |
| ✅ | `checkout.discount.terms.minimum` | Shown when the coupon needs a minimum: {minimum} is the items subtotal the cart needs. | field_value (1024) | required | `{minimum}` |
| ✅ | `checkout.discount.terms.once` | Shown when each customer can use the coupon once. | field_value (1024) | required |  |
| ✅ | `checkout.discount.terms.percent` | The terms of a percent coupon, e.g. '**{percent}% off** your items'. {percent} is the percentage. | field_value (1024) | required | `{percent}` |
| ✅ | `checkout.discount.terms.products` | Shown when the coupon counts only some items: {products} lists their names. | field_value (1024) | required | `{products}` |

### Checkout: error messages (private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.error.cart_full` | Shown when the customer adds a product but the cart already has checkout.max_cart_lines different products. {max} is that number. | description (4096) | required | `{max}` |
| ✅ | `checkout.error.order_not_cancellable` | Shown when the customer tries to cancel an order that can no longer be cancelled by them (it was already paid, cancelled or handled). | description (4096) | required |  |
| ✅ | `checkout.error.out_of_stock` | Shown when the customer asks for more units than are available (when adding to the cart, or when submitting if someone else bought them first). {name} is the product name, {available} the units still available. | description (4096) | required | `{name}`, `{available}` |
| ✅ | `checkout.error.product_unavailable` | Shown when a product in the cart is no longer offered (deactivated or removed). {name} is the product name. | description (4096) | required | `{name}` |
| ✅ | `checkout.error.quantity_invalid` | Shown when a quantity is above the allowed maximum (checkout.max_line_quantity or the product's per-order limit). {max} is that maximum. | description (4096) | required | `{max}` |
| ✅ | `checkout.error.session_closed` | Shown when the customer uses a control of an order workflow that was already submitted, cancelled or expired. | description (4096) | required |  |

### Checkout: extras (delivery step)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.extra.discreet.description` | Optional short explanation of the extra 'discreet' on the delivery step (what it covers). | field_value (1024) | optional |  |
| ✅ | `checkout.extra.discreet.label` | Name of the extra 'discreet' (e.g. Shipping protection), on the delivery step, the review, the order and the staff record. | field_name (256) | required |  |
| ✅ | `checkout.extra.protection.description` | Optional short explanation of the extra 'protection' on the delivery step (what it covers). | field_value (1024) | optional |  |
| ✅ | `checkout.extra.protection.label` | Name of the extra 'protection' (e.g. Shipping protection), on the delivery step, the review, the order and the staff record. | field_name (256) | required |  |
| ✅ | `checkout.extras.button_off` | Label of an extra's button while it is not added (pressing it adds the extra), e.g. '{label}: Off'. | button_label (80) | required | `{label}`, `{price}` |
| ✅ | `checkout.extras.button_on` | Label of an added extra's button (pressing it removes the extra), e.g. '{label}: On'. | button_label (80) | required | `{label}`, `{price}` |
| ✅ | `checkout.extras.heading` | Heading of the extras on the delivery step, shown in bold (it may start with an icon, e.g. {icon_shield}). | title (256) | required |  |
| ✅ | `checkout.extras.line` | How one extra is listed on the delivery step, e.g. '{status} **{label}** · {price}'. {status} is checkout.extras.status_on or status_off, {label} the extra's name, {price} its price, {description} its description (empty when it has none). | format (100) | required | `{status}`, `{label}`, `{price}`, `{description}` |
| ✅ | `checkout.extras.status_off` | Marker of an extra not added (as {status} in checkout.extras.line), e.g. {icon_dot_grey}. | field_name (256) | required |  |
| ✅ | `checkout.extras.status_on` | Marker of an extra the customer added (as {status} in checkout.extras.line), e.g. {icon_check}. | field_name (256) | required |  |

### Checkout: fulfillment option 'expedited'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.fulfillment.expedited.description` | Optional small text under the 'expedited' option in the menu (e.g. its fee or delivery time). | option_description (100) | optional |  |
| ✅ | `checkout.fulfillment.expedited.label` | Name of the 'expedited' fulfillment option (menu option, and the chosen-option field). | option_label (100) | required |  |

### Checkout: fulfillment option 'standard'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.fulfillment.standard.description` | Optional small text under the 'standard' option in the menu (e.g. its fee or delivery time). | option_description (100) | optional |  |
| ✅ | `checkout.fulfillment.standard.label` | Name of the 'standard' fulfillment option (menu option, and the chosen-option field). | option_label (100) | required |  |

### Checkout: order workflow - fulfillment option

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.fulfillment_step.description` | Optional text of the fulfillment step. | description (4096) | optional |  |
| ✅ | `checkout.fulfillment_step.fee_field` | Name of the delivery fee in the summary and on the staff order record when the chosen option's name is not configured any more (an order placed before the option was removed); otherwise the option's name is shown with its fee. Needed when an option has a fee. | field_name (256) | required |  |
| ✅ | `checkout.fulfillment_step.select_placeholder` | Greyed-out text of the fulfillment option menu (if empty, Discord would show its own default text). Needed when checkout.fulfillment_options are defined. | select_placeholder (150) | required |  |
| ✅ | `checkout.fulfillment_step.title` | Title of the step where the customer chooses a fulfillment option (delivery, pickup, ...). Needed when checkout.fulfillment_options are defined. | title (256) | required |  |

### Checkout: customer notifications (direct message, or the order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.notify.cancelled.description` | Notification sent to the customer when their order becomes 'cancelled' (the order was cancelled (by the customer before paying, or by staff)). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}`, `{channel_purchase}` |
| ✅ | `checkout.notify.cancelled.title` | Optional title of the notification sent when an order becomes 'cancelled'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.expired.description` | Notification sent to the customer when their order becomes 'expired' (the order was not paid within checkout.payment_expiry_minutes). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.payment_expiry_minutes is above 0. | description (4096) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.expired.title` | Optional title of the notification sent when an order becomes 'expired'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.fulfilled.description` | Notification sent to the customer when their order becomes 'fulfilled' (the order was delivered). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}`, `{channel_purchase}` |
| ✅ | `checkout.notify.fulfilled.title` | Optional title of the notification sent when an order becomes 'fulfilled'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.paid.description` | Notification sent to the customer when their order becomes 'paid' (staff confirmed the payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.paid.title` | Optional title of the notification sent when an order becomes 'paid'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.payment_reminder.description` | Reminder sent once when an order is still unpaid checkout.payment_reminder_minutes after it was submitted (the payment instructions are attached below it). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.payment_reminder_minutes is above 0. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.payment_reminder.title` | Optional title of the payment reminder. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.payment_submitted.description` | Notification sent to the customer when their order becomes 'payment_submitted' (the customer submitted their payment; staff are verifying it). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.payment_submitted.title` | Optional title of the notification sent when an order becomes 'payment_submitted'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.pending_payment.description` | 'Submission received' notification, sent once when the customer submitted the order (it now waits for payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.pending_payment.title` | Optional title of the 'submission received' notification. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.processing.description` | Notification sent to the customer when their order becomes 'processing' (staff are preparing the order). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.processing.title` | Optional title of the notification sent when an order becomes 'processing'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.refund.description` | Notification sent to the customer when staff record a partial refund (a refund that completes the full refund sends the 'refunded' notification instead). {order_number} is the order number, {amount} the refunded amount (common.money_format), {channel_mention} links the order channel. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | required | `{order_number}`, `{amount}`, `{channel_mention}`, `{channel_purchase}` |
| ✅ | `checkout.notify.refund.title` | Optional title of the notification sent when staff record a partial refund. {order_number} is the order number, {amount} the refunded amount, {channel_mention} links the order channel. | title (256) | optional | `{order_number}`, `{amount}`, `{channel_mention}` |
| ✅ | `checkout.notify.refunded.description` | Notification sent to the customer when their order becomes 'refunded' (the order was fully refunded). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}`, `{channel_purchase}` |
| ✅ | `checkout.notify.refunded.title` | Optional title of the notification sent when an order becomes 'refunded'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.rejected.description` | Notification sent to the customer when their order becomes 'rejected' (staff rejected the order before payment). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.rejected.title` | Optional title of the notification sent when an order becomes 'rejected'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.session_expiry_warning.description` | Warning posted in the order ticket (mentioning the customer) checkout.session_expiry_warning_minutes before an unfinished order expires for inactivity (any change to the order resets the timer; after a later idle period the warning is sent again). {minutes} is that number of minutes, {expires} when the order expires (relative time shown by Discord, e.g. 'in 10 minutes'). Needed when checkout.session_expiry_warning_minutes is above 0. | description (4096) | required | `{minutes}`, `{expires}` |
| ✅ | `checkout.notify.session_expiry_warning.title` | Optional title of the warning posted in the order ticket before an unfinished order expires. {minutes} and {expires} as in the text. | title (256) | optional | `{minutes}`, `{expires}` |
| ✅ | `checkout.notify.shipped.description` | Notification sent to the customer when their order becomes 'shipped' (the order was shipped (carrier and tracking number entered with Mark shipped)). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.shipped.title` | Optional title of the notification sent when an order becomes 'shipped'. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |

### Checkout: gift card payment replies

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.notify.card_problem.description` | Text of that notification; the cards with the reasons follow. {remaining} is still to pay, {channel_mention} the order ticket. | description (4096) | required | `{order_number}`, `{total}`, `{remaining}`, `{channel_mention}` |
| ✅ | `checkout.notify.card_problem.title` | Title of the notification when our team could not accept a card (the order waits for payment again). | title (256) | required | `{order_number}`, `{total}`, `{remaining}`, `{channel_mention}` |
| ✅ | `checkout.payment.gift_card.chat_guard` | Sent to the customer (by DM, else in the ticket) when their message was deleted because it looked like a gift card code (payments.gift_card.chat_guard). {channel_mention} is their order ticket. | description (4096) | required | `{channel_mention}` |
| ✅ | `checkout.payment.gift_card.error.amount` | The amount is not a valid amount; {example} shows one. | description (4096) | required | `{example}` |
| ✅ | `checkout.payment.gift_card.error.code` | The code is not valid: {min}-{max} letters and digits. | description (4096) | required | `{min}`, `{max}` |
| ✅ | `checkout.payment.gift_card.error.duplicate` | This card was already submitted (it is waiting or was redeemed). | description (4096) | required |  |
| ✅ | `checkout.payment.gift_card.error.state` | The order no longer waits for a payment (placed, paid or closed). | description (4096) | required |  |
| ✅ | `checkout.payment.gift_card.error.too_many` | The order already has {max} cards (payments.gift_card.max_cards). | description (4096) | required | `{max}` |
| ✅ | `checkout.payment.gift_card.error.unavailable` | Cards cannot be accepted right now (the bot cannot store them safely); the customer should try again later or ask our team. | description (4096) | required |  |
| ✅ | `checkout.payment.gift_card.reply.complete` | Private reply after the card that completes the payment (the order is placed): {amount} (this card), {received} (all cards), {order_number}. | description (4096) | required | `{amount}`, `{received}`, `{order_number}` |
| ✅ | `checkout.payment.gift_card.reply.partial` | Private reply after a card that does not cover the total yet: {amount} (this card), {received} (all cards), {remaining} (still to pay), {total}. | description (4096) | required | `{amount}`, `{received}`, `{remaining}`, `{total}` |

### Checkout: customer notifications sent by staff actions

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.notify.correction.description` | Notification sent to the customer when staff ask them to correct information on an order waiting for payment (the staff message follows in a field). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | description (4096) | required | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.correction.message_field` | Name of the field in the correction notification that shows the staff member's message. | field_name (256) | required |  |
| ✅ | `checkout.notify.correction.title` | Optional title of the notification asking the customer to correct information. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.rejected.reason_field` | Name of the field in the customer's rejection notification (checkout.notify.rejected.*) that shows the reason staff entered when rejecting the order. | field_name (256) | required |  |
| ✅ | `checkout.notify.shipped.carrier_field` | Name of the field showing the carrier in the shipped notification (Mark shipped) and on the customer's order ticket card while the order is shipped, e.g. '{icon_truck} Carrier'. | field_name (256) | required |  |
| ✅ | `checkout.notify.shipped.number_field` | Name of the field showing the tracking number in the shipped notification and on the customer's order ticket card while the order is shipped, e.g. '{icon_location} Tracking number'. | field_name (256) | required |  |
| ✅ | `checkout.notify.shipped.track_button` | Label of the link button under the shipped notification and on the customer's order ticket card while the order is shipped, that opens the carrier's tracking page (shown when staff entered a tracking link), e.g. 'Track package'. | button_label (80) | required |  |
| ✅ | `checkout.notify.tracking.description` | Notification sent to the customer when staff add or change tracking details of an order that is already completed (the details follow in a field). {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. Needed when checkout.staff.button.tracking is configured. | description (4096) | optional | `{order_number}`, `{total}`, `{channel_mention}` |
| ✅ | `checkout.notify.tracking.details_field` | Name of the field showing the tracking details in the customer's completion notification and in the tracking notification. Needed when checkout.staff.button.tracking is configured. | field_name (256) | optional |  |
| ✅ | `checkout.notify.tracking.title` | Optional title of the notification sent when staff add or change tracking details of a completed order. {order_number} is the order number, {total} the order total, {channel_mention} links the order channel. Sent by direct message when features.dm_notifications is on, otherwise (or when the customer's direct messages are closed) posted in the order channel with a mention. | title (256) | optional | `{order_number}`, `{total}`, `{channel_mention}` |

### Checkout: payment method pop-up (a method that is down)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.payment.down.active_heading` | Heading of the pop-up's list of the methods that work right now (it may start with an icon). | title (256) | required |  |
| ✅ | `checkout.payment.down.active_line` | How each method that works right now is listed in the pop-up. {method} is its name, {description} its description (empty when it has none). | field_value (1024) | required | `{method}`, `{description}` |
| ✅ | `checkout.payment.down.description` | Text of that pop-up: the method is temporarily down (and when it comes back, if known). {method} is the method's name. The methods that work right now and their buttons follow. | description (4096) | required | `{method}` |
| ✅ | `checkout.payment.down.gift_card_button` | Button in the pop-up that opens the private gift card form (as Submit gift card does). | button_label (80) | required |  |
| ✅ | `checkout.payment.down.hint` | Optional last line of the pop-up, e.g. 'Press a button below to use one of them.' | description (4096) | optional |  |
| ✅ | `checkout.payment.down.title` | Title of the private pop-up shown when a customer presses a payment method that is temporarily down. {method} is the method's name. | title (256) | required | `{method}` |

### Checkout: gift card payment

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.payment.gift_card.buy_button` | Link button to payments.gift_card.buy_url (where customers buy a card). | button_label (80) | required |  |
| ✅ | `checkout.payment.gift_card.card_line` | One submitted card: {number} (1, 2...), {amount}, {last4} (the code's last characters) and {status} (one of the card_status texts). | format (100) | required | `{number}`, `{amount}`, `{last4}`, `{status}` |
| ✅ | `checkout.payment.gift_card.card_status.redeemed` | Status of a card our team redeemed. | format (100) | required |  |
| ✅ | `checkout.payment.gift_card.card_status.refused` | Status of a card our team could not accept; {reason} is what staff wrote. | format (100) | required | `{reason}` |
| ✅ | `checkout.payment.gift_card.card_status.waiting` | Status of a card our team has not checked yet. | format (100) | required |  |
| ✅ | `checkout.payment.gift_card.cards_field` | Heading of the section listing the cards the customer submitted for the order (under the instructions on the order card and in the card problem notification), shown in bold, e.g. '{icon_gift} GIFT CARDS'. | field_name (256) | required |  |
| ✅ | `checkout.payment.gift_card.instructions` | How to pay with gift cards, shown in the order ticket and in the payment reminder. {total} is the amount to pay. | description (4096) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.payment.gift_card.remaining` | Last line of the cards section while part of the total is still to pay: {remaining} (still to pay) and {received} (the cards that count). | format (100) | required | `{remaining}`, `{received}` |
| ✅ | `checkout.payment.gift_card.submit_button` | Button under the instructions that opens the private gift card form. | button_label (80) | required |  |
| ✅ | `checkout.payment.gift_card.title` | Optional title of the payment instructions shown in the order ticket while the order waits for payment. | title (256) | optional | `{order_number}`, `{total}` |

### Checkout: gift card payment form

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.payment.gift_card.form.amount_label` | Label of the card amount input. | input_label (45) | required |  |
| ✅ | `checkout.payment.gift_card.form.amount_placeholder` | Optional hint in the amount input; {remaining} is what is still to pay. | input_placeholder (100) | optional | `{remaining}` |
| ✅ | `checkout.payment.gift_card.form.code_label` | Label of the gift card code input. | input_label (45) | required |  |
| ✅ | `checkout.payment.gift_card.form.code_placeholder` | Optional example in the code input. | input_placeholder (100) | optional |  |
| ✅ | `checkout.payment.gift_card.form.pin_label` | Label of the optional PIN input. | input_label (45) | required |  |
| ✅ | `checkout.payment.gift_card.form.pin_placeholder` | Optional hint in the PIN input. | input_placeholder (100) | optional |  |
| ✅ | `checkout.payment.gift_card.form.title` | Title of the private gift card form. | modal_title (45) | required | `{order_number}` |

### Checkout: payment methods

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `checkout.payment.method.apple_pay.description` | Optional one-line description of the payment method 'apple_pay' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.apple_pay.label` | Name of the payment method 'apple_pay' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ✅ | `checkout.payment.method.card.description` | Optional one-line description of the payment method 'card' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.card.label` | Name of the payment method 'card' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ▫️ | `checkout.payment.method.cash_app.description` | Optional one-line description of the payment method 'cash_app' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.cash_app.label` | Name of the payment method 'cash_app' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ✅ | `checkout.payment.method.gift_card.description` | Optional one-line description of the payment method 'gift_card' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.gift_card.label` | Name of the payment method 'gift_card' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ▫️ | `checkout.payment.method.google_pay.description` | Optional one-line description of the payment method 'google_pay' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.google_pay.label` | Name of the payment method 'google_pay' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ▫️ | `checkout.payment.method.paypal.description` | Optional one-line description of the payment method 'paypal' (e.g. the cards it takes), shown in the pop-up's list of methods that work right now. | field_value (1024) | optional |  |
| ✅ | `checkout.payment.method.paypal.label` | Name of the payment method 'paypal' on its button in the order ticket (it may start with an icon) and in the payment-methods list. | button_label (80) | required |  |
| ✅ | `checkout.payment.methods.error_state` | Private reply when a payment-method button is pressed for an order that no longer waits for payment. | description (4096) | required |  |
| ✅ | `checkout.payment.methods.error_unavailable` | Private reply when a payment-method button is pressed that is no longer offered (the settings changed). | description (4096) | required |  |
| ✅ | `checkout.payment.methods.heading` | Heading of the payment-methods section on the order card while the order waits for payment (it may start with an icon). | title (256) | required |  |
| ✅ | `checkout.payment.methods.line_active` | How a method that works right now is listed, e.g. '{icon_dot_blue} **{method}** · available now'. | field_value (1024) | required | `{method}` |
| ✅ | `checkout.payment.methods.line_down` | How the methods that are temporarily down are listed (one line for all of them), e.g. '{icon_dot_grey} {method} · temporarily down'. {method} is their names, separated by commas. | field_value (1024) | required | `{method}` |
| ✅ | `checkout.payment.methods.note` | Optional text under the list of payment methods, e.g. why only some work right now. | description (4096) | optional |  |

### Checkout: payment by a method our team handles

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.payment.staff_method.already` | Private reply when the customer chooses the same method again. {method} is the method's name. | description (4096) | required | `{method}` |
| ✅ | `checkout.payment.staff_method.posted` | Text of that message: our team will send the payment details in this ticket. {method} is the method's name, {user_mention} mentions the customer. | description (4096) | required | `{method}`, `{user_mention}` |
| ✅ | `checkout.payment.staff_method.reply` | Private reply to the customer after choosing such a method. {method} is the method's name. | description (4096) | required | `{method}` |
| ✅ | `checkout.payment.staff_method.title` | Title of the message posted in the order ticket when the customer chooses a method our team handles (kind = "staff"). {method} is the method's name. | title (256) | required | `{method}` |

### Checkout: quantity form

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.quantity.description` | Optional help text under the quantity label (e.g. that 0 removes the product). | input_description (100) | optional |  |
| ✅ | `checkout.quantity.label` | Label of the quantity field (a whole number; 0 removes the product from the cart). | input_label (45) | required |  |
| ✅ | `checkout.quantity.placeholder` | Optional greyed-out example inside the empty quantity field. | input_placeholder (100) | optional |  |
| ✅ | `checkout.quantity.title` | Title of the form that asks for a product's quantity. | modal_title (45) | required |  |

### Checkout: staff order controls (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.staff.amount_field` | Name of the field on the staff order record with the amounts, one per line as in the customer's summary (checkout.summary.line, .total_line): items, delivery, extras and other fees, store credit, the total, and refunds. | field_name (256) | required |  |
| ✅ | `checkout.staff.button.cancel` | Staff button: cancel the order (unpaid: stock and credit are returned; paid: nothing is restocked, refunded or returned automatically - use the refund buttons and the return-credit button). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.claim` | Staff button: claim an open order (records who handles it; a second claim is refused). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.correction` | Staff button on an order waiting for payment: ask the customer to correct information (opens a form for the message; the customer is notified privately and the order keeps waiting). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.details` | Staff button: show the full order record privately to the staff member who clicks it. | button_label (80) | required |  |
| ✅ | `checkout.staff.button.fulfilled` | Staff button: a shipped order was delivered (the order ticket is closed). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.mark_paid` | Staff button: the payment was received (unpaid -> paid; stock is then taken). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.note` | Staff button: add an internal note (opens a staff-only form; notes are never shown to the customer). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.processing` | Staff button: mark a paid order as being prepared (optional stage). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.refund` | Staff button: record a (partial) refund. With the manual payment method the money is returned outside the bot. | button_label (80) | required |  |
| ✅ | `checkout.staff.button.refund_credit` | Staff button: refund (part of) the order as store credit. Needed when the credits feature is on. | button_label (80) | required |  |
| ✅ | `checkout.staff.button.reject` | Staff button: reject an unpaid order (reserved stock and applied credit are returned). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.return_credit` | Staff button: give back the store credit the customer applied to a paid order (all of it at once; shown until it was given back, e.g. after cancelling a paid order). Cancelling a paid order returns neither money nor credit automatically. Needed when credits are enabled and credits.allow_at_checkout = true. | button_label (80) | required |  |
| ✅ | `checkout.staff.button.shipped` | Staff button: the order was shipped. Opens a form for the carrier, the tracking number and an optional tracking link, which the customer receives in the ticket and by direct message. | button_label (80) | required |  |
| ✅ | `checkout.staff.button.tracking` | Optional staff button on a paid, in-progress or completed order: add or change tracking details (included in the customer's completion notification, or sent to them when the order is already completed). Leave empty if your workflow does not use tracking details. | button_label (80) | optional |  |
| ✅ | `checkout.staff.button.unclaim` | Staff button: release the claim (only the member holding it, or an admin). | button_label (80) | required |  |
| ✅ | `checkout.staff.channel_field` | Optional name of the field on the staff order record that links the customer's order channel. | field_name (256) | optional |  |
| ✅ | `checkout.staff.claimed_field` | Name of the field on the staff order record showing which staff member claimed the order and since when (while nobody has claimed an open order, it shows checkout.staff.not_claimed). | field_name (256) | required |  |
| ✅ | `checkout.staff.credit_returned_field` | Optional name of the field on the staff order controls showing the applied store credit that was given back to the customer (written with common.money_format). | field_name (256) | optional |  |
| ✅ | `checkout.staff.customer_field` | Name of the field on the staff order record that mentions the customer (no ping). | field_name (256) | required |  |
| ✅ | `checkout.staff.delivery_field` | Name of the field on the staff order record showing the delivery option and the extras the customer chose (their labels; shown when the order has one). | field_name (256) | required |  |
| ✅ | `checkout.staff.description` | Text of the staff order controls. {user_mention} mentions the customer (no ping), {channel_mention} links the order channel, {order_number}/{total}/{status} as in the title. The order lines, totals and buttons follow. | description (4096) | required | `{order_number}`, `{user_mention}`, `{channel_mention}`, `{total}`, `{status}` |
| ✅ | `checkout.staff.details_title` | Optional title of the message posted under the staff order controls with the customer's form answers (only when a form is defined and operations.answers_in_orders_channel = true; otherwise the answers are shown only privately in the record's details). {order_number} is the order number. | title (256) | optional | `{order_number}` |
| ✅ | `checkout.staff.not_claimed` | Shown under checkout.staff.claimed_field while nobody has claimed an open order. | field_value (1024) | required |  |
| ✅ | `checkout.staff.refunded_field` | Name of the field on the staff order controls showing how much was refunded so far. | field_name (256) | required |  |
| ✅ | `checkout.staff.ship_to_field` | Name of the field on the staff order record showing where the order goes: the country (with operations.answers_in_orders_channel: checkout.address.ship_to, never the street). The record's details button shows the full address privately. | field_name (256) | required |  |
| ✅ | `checkout.staff.title` | Title of the order controls posted for staff in checkout.staff_channel for every submitted order (edited on every status change). {order_number} is the order number, {total} the total, {status} the status name (checkout.status_label.*). | title (256) | required | `{order_number}`, `{total}`, `{status}` |
| ✅ | `checkout.staff.tracking_field` | Name of the field on the staff order record showing the tracking details staff entered. Needed when checkout.staff.button.tracking is configured. | field_name (256) | optional |  |

### Checkout: staff gift card controls

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.staff.button.card_problem` | Staff button: the waiting cards could not be redeemed (asks for the reason the customer is shown). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.payment_confirmed` | Staff button: every waiting card was redeemed (the order is paid). | button_label (80) | required |  |
| ✅ | `checkout.staff.button.reveal_gift_card` | Staff button: show the submitted codes privately (payment roles only; every reveal is logged). | button_label (80) | required |  |
| ✅ | `checkout.staff.gift_card.field` | Field on the order record listing the submitted cards (never their codes). | field_name (256) | required |  |
| ✅ | `checkout.staff.gift_card.line` | One card on the order record: {index}, {amount}, {last4} and {state} (one of the state texts, followed by the revealed text once someone revealed it). | format (100) | required | `{index}`, `{amount}`, `{last4}`, `{state}` |
| ✅ | `checkout.staff.gift_card.revealed` | Added to a card once its code was revealed: {count} (times) and {staff} (who revealed it last). | format (100) | required | `{count}`, `{staff}` |
| ✅ | `checkout.staff.gift_card.state.erased` | State of a card no one decided on whose code was deleted after payments.gift_card.keep_days (shown instead of the waiting state). | format (100) | required |  |
| ✅ | `checkout.staff.gift_card.state.redeemed` | State of a redeemed card. | format (100) | required |  |
| ✅ | `checkout.staff.gift_card.state.refused` | State of a refused card; {reason} is what the customer was told. | format (100) | required | `{reason}` |
| ✅ | `checkout.staff.gift_card.state.waiting` | State of a card no one decided on yet. | format (100) | required |  |
| ✅ | `checkout.staff.gift_card.state.withdrawn` | State of a card given up because the order ended before anyone decided. | format (100) | required |  |
| ✅ | `checkout.staff.gift_card.total` | Last line of the payment field: {covered} (cards waiting or redeemed) of {total}. | format (100) | required | `{covered}`, `{total}` |

### Checkout: staff order controls - timeline and notes

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.staff.note_line` | How one internal note is written, e.g. '{time} {actor}: {note}'. {time} is when it was added, {actor} mentions who added it, {note} is the note. | format (100) | required | `{time}`, `{actor}`, `{note}` |
| ✅ | `checkout.staff.notes_field` | Name of the field on the staff order record listing the internal staff notes (the latest ones; never shown to the customer). | field_name (256) | required |  |
| ✅ | `checkout.staff.stage.claimed` | Stage name in the timeline when a staff member claimed the order. | format (100) | required |  |
| ✅ | `checkout.staff.stage.correction` | Stage name in the timeline when staff asked the customer to correct information. | format (100) | required |  |
| ✅ | `checkout.staff.stage.tracking` | Stage name in the timeline when staff added or changed the tracking details. Needed when checkout.staff.button.tracking is configured. | format (100) | optional |  |
| ✅ | `checkout.staff.stage.unclaimed` | Stage name in the timeline when the claim on the order was released. | format (100) | required |  |
| ✅ | `checkout.staff.timeline_field` | Name of the field on the staff order record listing the order's stages with their times (submitted, status changes, claims, correction requests, tracking details; the latest ones when there are many - the details button shows the full history). | field_name (256) | required |  |
| ✅ | `checkout.staff.timeline_line` | How one stage is written in the timeline, e.g. '{time} {stage} {actor}'. {time} is the time (shown in each reader's time zone), {stage} the stage name (checkout.status_label.* for statuses, checkout.staff.stage.* for the other stages), {actor} mentions who did it (empty for automatic changes). | format (100) | required | `{time}`, `{stage}`, `{actor}` |

### Checkout: order status views (order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.status.cancelled.description` | Optional text of the order workflow message once the order is 'cancelled' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | optional | `{order_number}`, `{total}`, `{channel_purchase}` |
| ✅ | `checkout.status.cancelled.title` | Title of the order workflow message once the order is 'cancelled' (the order was cancelled (by the customer before paying, or by staff)). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.expired.description` | Optional text of the order workflow message once the order is 'expired' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.expired.title` | Title of the order workflow message once the order is 'expired' (the order was not paid within checkout.payment_expiry_minutes). {order_number} is the order number, {total} the order total written with common.money_format. Needed when checkout.payment_expiry_minutes is above 0. | title (256) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.fulfilled.description` | Optional text of the order workflow message once the order is 'fulfilled' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | optional | `{order_number}`, `{total}`, `{channel_purchase}` |
| ✅ | `checkout.status.fulfilled.title` | Title of the order workflow message once the order is 'fulfilled' (the order was delivered). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.paid.description` | Optional text of the order workflow message once the order is 'paid' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.paid.title` | Title of the order workflow message once the order is 'paid' (staff confirmed the payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.payment_submitted.description` | Optional text of the order workflow message once the order is 'payment_submitted' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.payment_submitted.title` | Title of the order workflow message once the order is 'payment_submitted' (the customer submitted their payment; staff are verifying it). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.processing.description` | Optional text of the order workflow message once the order is 'processing' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.processing.title` | Title of the order workflow message once the order is 'processing' (staff are preparing the order). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.refunded.description` | Optional text of the order workflow message once the order is 'refunded' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. {channel_purchase} links the place-order channel (where Report a problem is). | description (4096) | optional | `{order_number}`, `{total}`, `{channel_purchase}` |
| ✅ | `checkout.status.refunded.title` | Title of the order workflow message once the order is 'refunded' (the order was fully refunded). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.rejected.description` | Optional text of the order workflow message once the order is 'rejected' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.rejected.title` | Title of the order workflow message once the order is 'rejected' (staff rejected the order before payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |
| ✅ | `checkout.status.shipped.description` | Optional text of the order workflow message once the order is 'shipped' (under the ticket, order number and status line; the items, summary and progress follow). {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.status.shipped.title` | Title of the order workflow message once the order is 'shipped' (the order was shipped (carrier and tracking number entered with Mark shipped)). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |

### Checkout: order status names

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.status_label.cancelled` | Name of the order status 'cancelled' (the order was cancelled (by the customer before paying, or by staff)), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.expired` | Name of the order status 'expired' (the order was not paid within checkout.payment_expiry_minutes), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.fulfilled` | Name of the order status 'fulfilled' (the order was delivered), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.paid` | Name of the order status 'paid' (staff confirmed the payment), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.payment_submitted` | Name of the order status 'payment_submitted' (the customer submitted their payment; staff are verifying it), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.pending_payment` | Name of the order status 'pending_payment' (waiting for payment), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.processing` | Name of the order status 'processing' (staff are preparing the order), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.refunded` | Name of the order status 'refunded' (the order was fully refunded), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.rejected` | Name of the order status 'rejected' (staff rejected the order before payment), used as {status} in the staff order controls. | format (100) | required |  |
| ✅ | `checkout.status_label.shipped` | Name of the order status 'shipped' (the order was shipped (carrier and tracking number entered with Mark shipped)), used as {status} in the staff order controls. | format (100) | required |  |

### Checkout: submitted order (order channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.submitted.description` | Optional text of the submitted order. The payment instructions follow below it. {order_number} is the order number, {total} the order total written with common.money_format. | description (4096) | optional | `{order_number}`, `{total}` |
| ✅ | `checkout.submitted.title` | Title of the workflow message after the order was submitted (waiting for payment). {order_number} is the order number, {total} the order total written with common.money_format. | title (256) | required | `{order_number}`, `{total}` |

### Checkout: order ticket (introduction, and the ticket, order number and status on the live panel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.ticket.draft_line` | First line of every step of the live panel while the customer puts the order together, e.g. '{icon_ticket} Ticket **#{ticket_number}** · {status}'. {ticket_number} is the ticket number, {status} checkout.ticket.status_in_progress. | field_value (1024) | required | `{ticket_number}`, `{status}` |
| ✅ | `checkout.ticket.intro_description` | First message in every order ticket, posted before the live panel: identifies the ticket and explains that only the customer and authorized staff can see it. {ticket_number} is the ticket number (it is also shown in checkout.ticket.reference_line above the text), {user_mention} mentions the customer (no ping). | description (4096) | required | `{ticket_number}`, `{user_mention}` |
| ✅ | `checkout.ticket.intro_title` | Optional title of the first message in every order ticket. {ticket_number} is the ticket number. | title (256) | optional | `{ticket_number}` |
| ✅ | `checkout.ticket.order_line` | First line of the live panel once the order was placed (every status), e.g. '{icon_ticket} Ticket **#{ticket_number}** · Order **#{order_number}** · {status}'. {ticket_number} is the ticket number, {order_number} the order number, {status} the current stage name (checkout.ticket.stage.*). | field_value (1024) | required | `{ticket_number}`, `{order_number}`, `{status}` |
| ✅ | `checkout.ticket.reference_line` | First line of the ticket introduction and of the closed panel of an order that was never placed: the ticket's unique reference, e.g. '{icon_ticket} Ticket **#{ticket_number}**'. {ticket_number} is the ticket number (as in the channel name). | field_value (1024) | required | `{ticket_number}` |
| ✅ | `checkout.ticket.status_in_progress` | Status shown on the live panel while the customer is still putting the order together (not submitted yet), as {status} in checkout.ticket.draft_line. | field_value (1024) | required |  |

### Checkout: order stage names (live panel, progress tracker, /myorders)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `checkout.ticket.stage.cancelled` | Name of the stage 'cancelled' (the order was cancelled (by the customer before paying, or by staff)) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.expired` | Name of the stage 'expired' (the order was not paid within checkout.payment_expiry_minutes) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.fulfilled` | Name of the stage 'fulfilled' (the order was delivered) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.paid` | Name of the stage 'paid' (staff confirmed the payment) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.payment_submitted` | Name of the stage 'payment_submitted' (the customer submitted their payment; staff are verifying it) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.pending_payment` | Name of the stage 'pending_payment' (waiting for payment) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.processing` | Name of the stage 'processing' (staff are preparing the order) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.refunded` | Name of the stage 'refunded' (the order was fully refunded) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.rejected` | Name of the stage 'rejected' (staff rejected the order before payment) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |
| ✅ | `checkout.ticket.stage.shipped` | Name of the stage 'shipped' (the order was shipped (carrier and tracking number entered with Mark shipped)) shown to the customer: the status on the live panel, the progress tracker and /myorders. | format (100) | required |  |

## /myorders

Feature `my_orders` — enabled.

### My orders (/myorders, private)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `my_orders.command.description` | Description of the /myorders command shown in Discord's command list (at most 100 characters). The command is registered only when features.my_orders is on and this text is set; Discord receives it when the bot starts (a change needs a restart: /reload does not update it). | command_description (100) | required |  |
| ✅ | `my_orders.empty.description` | Text of the /myorders reply when the member has no order yet. | description (4096) | required |  |
| ✅ | `my_orders.list.description` | Optional text under the /myorders title when the member has orders. {count} is their number. The orders follow (newest first), with previous/next buttons when there is more than one page. | description (4096) | optional | `{count}` |
| ✅ | `my_orders.list.title` | Title of the private /myorders reply (also when the member has no order yet). {count} is the number of the member's orders. | title (256) | required | `{count}` |
| ✅ | `my_orders.order.summary` | Line under each order's heading in /myorders, e.g. '{total} · placed {date}'. {order_number} is the order number, {status} its current stage name (checkout.ticket.stage.*), {total} the total (common.money_format), {date} the date it was submitted (shown by Discord in the reader's time zone). | description (4096) | required | `{order_number}`, `{status}`, `{total}`, `{date}` |
| ✅ | `my_orders.order.ticket_field` | Optional label of the line linking the order's ticket while it is still open, e.g. 'Ticket' (the line is omitted when empty). | field_name (256) | optional |  |
| ✅ | `my_orders.order.title` | Heading of each order in /myorders, shown in bold. {order_number} is the order number, {status} its current stage name (checkout.ticket.stage.*), {total} the total (common.money_format), {date} the date it was submitted (shown by Discord in the reader's time zone). | title (256) | required | `{order_number}`, `{status}`, `{total}`, `{date}` |
| ✅ | `my_orders.order.tracking_field` | Optional label of the line showing the tracking details staff recorded for the order, e.g. 'Tracking' (shown only when there are tracking details; omitted when empty). | field_name (256) | optional |  |

## Store credit

Feature `credits` — enabled.

### Credits: notice to a member after staff changed their balance (direct message)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `credits.adjusted_notice.description` | Optional text of the direct message sent to a member after an admin added or removed credits (/credits adjust). {amount} is the change written with common.money_format (negative for a removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not notified. Requires features.dm_notifications. | description (4096) | optional | `{amount}`, `{balance}` |
| ✅ | `credits.adjusted_notice.title` | Optional title of the direct message sent to a member after an admin added or removed credits (/credits adjust). {amount} is the change written with common.money_format (negative for a removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not notified. | title (256) | optional | `{amount}`, `{balance}` |

### Credits: 'my balance' button and the private balance reply

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `credits.balance.description` | Text of the private reply to the 'my balance' button. {amount} is the member's current credit balance written with common.money_format. | description (4096) | required | `{amount}` |
| ✅ | `credits.balance.title` | Optional title of the private reply to the 'my balance' button. If empty, the reply has no title. | title (256) | optional |  |
| ✅ | `credits.button.balance` | Label of the 'my balance' button that other panels (for example the shop panel) can show. Clicking it shows the member their own credit balance privately. | button_label (80) | required |  |

## Reviews

Feature `reviews` — enabled.

### Reviews: replies to the customer

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.already_reviewed.description` | Shown privately when the customer tries to rate or comment on an order they already reviewed. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ✅ | `reviews.declined.description` | Replaces the review request after the customer chose not to leave a review. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ✅ | `reviews.expired.description` | Replaces the review request when the order can no longer be reviewed (for example it was refunded, or staff closed the review). {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ✅ | `reviews.thanks.description` | Replaces the review request after the customer submitted their review (the rating menu and buttons are removed). {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ✅ | `reviews.thanks_pending.description` | Optional text that replaces the review request after the customer submitted their review while reviews.require_approval is on (the review is published only after a staff member approves it). If empty, reviews.thanks.description is shown instead. {order_number} is the number of the reviewed order. | description (4096) | optional (falls back to `reviews.thanks.description`) | `{order_number}` |

### Reviews: approval request (staff only, posted in reviews.approval_channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.approval.button.approve` | Label of the Approve button on the approval message (staff only; publishes the review in the reviews channel). Required when reviews.require_approval is on. | button_label (80) | optional |  |
| ✅ | `reviews.approval.button.reject` | Label of the Reject button on the approval message (staff only; opens a form for an optional reason; the review is not published). Required when reviews.require_approval is on. | button_label (80) | optional |  |
| ✅ | `reviews.approval.customer_field` | Heading of the reviewer (a mention of the customer) on the approval message. Required when reviews.require_approval is on. | field_name (256) | optional |  |
| ✅ | `reviews.approval.order_field` | Heading of the reviewed order's number on the approval message. Required when reviews.require_approval is on. | field_name (256) | optional |  |
| ✅ | `reviews.approval.rating_field` | Heading of the rating on the approval message (the rating is shown with reviews.rating.<n>.display or the rating's option label). Required when reviews.require_approval is on. | field_name (256) | optional |  |
| ✅ | `reviews.approval.reason_field` | Optional heading of the rejection reason on the approval message (the reason is shown exactly as the staff member wrote it in the reject form). If empty, the reason is not shown there. | field_name (256) | optional |  |
| ✅ | `reviews.approval.status.approved` | Approval status shown on the approval message when a staff member approved the review (it is then published in the reviews channel). {staff_mention} mentions the staff member who approved it. Required when reviews.require_approval is on. | field_value (1024) | optional | `{staff_mention}` |
| ✅ | `reviews.approval.status.pending` | Approval status shown on the approval message when the review waits for a staff decision (the Approve and Reject buttons are shown). Required when reviews.require_approval is on. | field_value (1024) | optional |  |
| ✅ | `reviews.approval.status.rejected` | Approval status shown on the approval message when a staff member rejected the review (it is not published). {staff_mention} mentions the staff member who rejected it. Required when reviews.require_approval is on. | field_value (1024) | optional | `{staff_mention}` |
| ✅ | `reviews.approval.status.removed` | Approval status shown on the approval message when the review was removed with /review remove (it is not or no longer published). Required when reviews.require_approval is on. | field_value (1024) | optional |  |
| ✅ | `reviews.approval.status_field` | Heading of the approval status on the approval message (its value is one of reviews.approval.status.*). Required when reviews.require_approval is on. | field_name (256) | optional |  |
| ✅ | `reviews.approval.title` | Title of the message that asks staff to approve or reject a submitted review (posted in the staff channel reviews.approval_channel; the customer's comment is shown below it exactly as written). {rating} is the chosen rating as a number, {user_name} the reviewer's display name, {order_number} the number of the reviewed order. Required when reviews.require_approval is on. | title (256) | optional | `{rating}`, `{user_name}`, `{order_number}` |

### Reviews: rating request (sent to the customer after an order is fulfilled)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.button.decline` | Label of the button on the review request that lets the customer decline to leave a review. | button_label (80) | required |  |
| ✅ | `reviews.prompt.description` | Text of the review request asking the customer to rate their order. {order_number} is the number of the reviewed order. | description (4096) | required | `{order_number}` |
| ✅ | `reviews.prompt.select_placeholder` | Placeholder of the rating selection menu on the review request (shown before a rating is picked). | select_placeholder (150) | required |  |
| ✅ | `reviews.prompt.title` | Optional title of the review request sent to the customer (by direct message or in the order channel, see reviews.prompt_via) after their order was fulfilled. {order_number} is the number of the reviewed order. | title (256) | optional | `{order_number}` |

### Reviews: comment form (opens after the customer picks a rating)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.comment.description` | Optional help text under the label of the comment box. | input_description (100) | optional |  |
| ✅ | `reviews.comment.label` | Label of the comment box in the comment form. Required when reviews.allow_comment is on. | input_label (45) | required |  |
| ✅ | `reviews.comment.placeholder` | Optional greyed-out example text inside the empty comment box. | input_placeholder (100) | optional |  |
| ✅ | `reviews.comment.title` | Title of the form that asks for a written comment after the customer picked a rating. Required when reviews.allow_comment is on. | modal_title (45) | required |  |

### Reviews: decision notices (private, to the customer, when reviews.require_approval is on)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.notice.approved.description` | Optional text of the private message telling the customer their review was approved; it is sent once the review is published in the reviews channel. Sent by direct message when features.dm_notifications is on, otherwise (or when direct messages are closed) in the order channel with a mention (notifications.fallback_to_channel). If empty, no message is sent. {order_number} is the number of the reviewed order. {review_link} is a link to the published review. | description (4096) | optional | `{order_number}`, `{review_link}` |
| ✅ | `reviews.notice.approved.title` | Optional title of the private message telling the customer their review was approved and published. Needs reviews.notice.approved.description. {order_number} is the number of the reviewed order. | title (256) | optional | `{order_number}` |
| ✅ | `reviews.notice.rejected.description` | Optional text of the private message telling the customer their review was not approved (it is not published). Sent by direct message when features.dm_notifications is on, otherwise (or when direct messages are closed) in the order channel with a mention (notifications.fallback_to_channel). If empty, no message is sent. {order_number} is the number of the reviewed order. | description (4096) | optional | `{order_number}` |
| ✅ | `reviews.notice.rejected.reason_field` | Optional heading under which the reason the staff member gave in the reject form is shown in that message (exactly as written). If empty, or when no reason was given, the reason is not shown to the customer. | field_name (256) | optional |  |
| ✅ | `reviews.notice.rejected.title` | Optional title of the private message telling the customer their review was not approved. Needs reviews.notice.rejected.description. {order_number} is the number of the reviewed order. | title (256) | optional | `{order_number}` |

### Reviews: published review (posted in the reviews channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.published.rating_field` | Heading of the rating on a published review; the rating itself is shown with reviews.rating.<n>.display (or the rating's option label). | field_name (256) | required |  |
| ✅ | `reviews.published.title` | Title of a review posted in the reviews channel. {rating} is the chosen rating as a number (1 to reviews.rating_scale), {user_name} the reviewer's display name, {order_number} the number of the reviewed order. The customer's comment (if any) is shown below the title exactly as written. | title (256) | required | `{rating}`, `{user_name}`, `{order_number}` |
| ✅ | `reviews.published.verified_field` | Optional heading of the verification marker shown on every published review (every review comes from a completed order, so every published review is a verified purchase). Set it together with reviews.published.verified_value, or leave both empty to show no marker. | field_name (256) | optional |  |
| ✅ | `reviews.published.verified_value` | Optional verification marker text shown under reviews.published.verified_field on every published review (for example that the reviewer bought the order). Set it together with reviews.published.verified_field. {order_number} is the number of the reviewed order. | field_value (1024) | optional | `{order_number}` |

### Reviews: rating options

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `reviews.rating.1.description` | Optional small text under the option for rating 1 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ✅ | `reviews.rating.1.display` | Optional text shown as the rating on a published review with rating 1 of 5 (for example a row of stars). If empty, the option label (reviews.rating.1.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.1.label`) |  |
| ✅ | `reviews.rating.1.emoji` | Optional emoji shown next to the option for rating 1 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ✅ | `reviews.rating.1.label` | Option label for rating 1 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.1.display is empty. | option_label (100) | required |  |
| ✅ | `reviews.rating.2.description` | Optional small text under the option for rating 2 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ✅ | `reviews.rating.2.display` | Optional text shown as the rating on a published review with rating 2 of 5 (for example a row of stars). If empty, the option label (reviews.rating.2.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.2.label`) |  |
| ✅ | `reviews.rating.2.emoji` | Optional emoji shown next to the option for rating 2 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ✅ | `reviews.rating.2.label` | Option label for rating 2 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.2.display is empty. | option_label (100) | required |  |
| ✅ | `reviews.rating.3.description` | Optional small text under the option for rating 3 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ✅ | `reviews.rating.3.display` | Optional text shown as the rating on a published review with rating 3 of 5 (for example a row of stars). If empty, the option label (reviews.rating.3.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.3.label`) |  |
| ✅ | `reviews.rating.3.emoji` | Optional emoji shown next to the option for rating 3 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ✅ | `reviews.rating.3.label` | Option label for rating 3 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.3.display is empty. | option_label (100) | required |  |
| ✅ | `reviews.rating.4.description` | Optional small text under the option for rating 4 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ✅ | `reviews.rating.4.display` | Optional text shown as the rating on a published review with rating 4 of 5 (for example a row of stars). If empty, the option label (reviews.rating.4.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.4.label`) |  |
| ✅ | `reviews.rating.4.emoji` | Optional emoji shown next to the option for rating 4 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ✅ | `reviews.rating.4.label` | Option label for rating 4 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.4.display is empty. | option_label (100) | required |  |
| ✅ | `reviews.rating.5.description` | Optional small text under the option for rating 5 of 5 in the rating selection menu. | option_description (100) | optional |  |
| ✅ | `reviews.rating.5.display` | Optional text shown as the rating on a published review with rating 5 of 5 (for example a row of stars). If empty, the option label (reviews.rating.5.label) is shown instead. | field_value (1024) | optional (falls back to `reviews.rating.5.label`) |  |
| ✅ | `reviews.rating.5.emoji` | Optional emoji shown next to the option for rating 5 of 5 in the rating selection menu. | emoji (100) | optional |  |
| ✅ | `reviews.rating.5.label` | Option label for rating 5 of 5 in the rating selection menu of the review request (options are listed from the highest rating to the lowest). Also shown on the published review when reviews.rating.5.display is empty. | option_label (100) | required |  |

## #announcements (/announce)

Feature `announcements` — enabled.

### Announcements: presets (posted with /announce preset:)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `announcements.preset.warehouse_sale.claim_button` | Button under the 'warehouse_sale' preset announcement (/announce preset:warehouse_sale, staff name 'Warehouse sale: buy one, get one free') that claims the coupon BOGO (e.g. '{icon_gift} Claim coupon'). Pressing it answers privately with the coupon's terms (checkout.discount.claim.*). | button_label (80) | required |  |
| ✅ | `announcements.preset.warehouse_sale.description` | Text of the 'warehouse_sale' preset announcement (/announce preset:warehouse_sale, staff name 'Warehouse sale: buy one, get one free'). Discord formatting, icons ({icon_gift}), section headings and channel links ({channel_catalog}) work here. {coupon} is the coupon's code (BOGO), e.g. 'press **Use coupon {coupon}** on the review step'. | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}`, `{coupon}` |
| ✅ | `announcements.preset.warehouse_sale.ended_button` | Label the button under the 'warehouse_sale' preset announcement (/announce preset:warehouse_sale, staff name 'Warehouse sale: buy one, get one free') shows (disabled) once the coupon BOGO has ended or expired, e.g. 'Offer ended'. | button_label (80) | required |  |
| ✅ | `announcements.preset.warehouse_sale.title` | Title of the 'warehouse_sale' preset announcement (/announce preset:warehouse_sale, staff name 'Warehouse sale: buy one, get one free'), posted in the 'announcements' channel. Plain text (no icons). | title (256) | required |  |

## Report a Problem

Feature `problems` — enabled.

### Report a Problem: private choice step (shown only to the customer)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.attachments_note` | Tells the customer that screenshots or files can be uploaded in the private ticket once it is open (the ticket channel allows attachments). Shown below the confirmation after the report was created and on the report's ticket panel while the report is open. | description (4096) | required |  |
| ✅ | `problems.choose.description` | Text of the private message in which the customer chooses the order the problem is about (their orders in problems.eligible_statuses, newest first) and/or the issue type, then presses the shared Continue button to describe the problem. Shown only when there is something to choose. You can link server channels with {channel_<key>} (for example {channel_faq}). | description (4096) | required | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `problems.choose.no_order_option` | Label of the last option of the order menu, for a problem that is not about one of the listed orders. Needed when problems.allow_without_order = true and problems.eligible_statuses is not empty. | option_label (100) | required |  |
| ✅ | `problems.choose.no_order_option_description` | Optional small text under the 'not about an order' option. | option_description (100) | optional |  |
| ✅ | `problems.choose.order_option` | Label of each order in the order menu. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name as the customer's order ticket writes it (checkout.ticket.stage.<status>). Needed when problems.eligible_statuses is not empty. | option_label (100) | required | `{order_number}`, `{total}`, `{status}` |
| ✅ | `problems.choose.order_option_description` | Optional small text under each order in the order menu. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name as the customer's order ticket writes it (checkout.ticket.stage.<status>). | option_description (100) | optional | `{order_number}`, `{total}`, `{status}` |
| ✅ | `problems.choose.order_placeholder` | Greyed-out text of the order menu in the private choice step. Needed when problems.eligible_statuses is not empty. | select_placeholder (150) | required |  |
| ✅ | `problems.choose.title` | Optional title of the private message in which the customer chooses the order the problem is about and/or the issue type. | title (256) | optional |  |
| ✅ | `problems.choose.type_placeholder` | Greyed-out text of the issue-type menu in the private choice step. Needed when more than one [[problems.types]] is configured. | select_placeholder (150) | required |  |

### Report a Problem: the report's private ticket

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.button.reject` | Reject button on the report panel (staff only; opens a form for the reason, required when problems.require_reason_on_reject = true). | button_label (80) | required |  |
| ✅ | `problems.button.resolve` | Resolve button on the report panel (staff only; opens a form for an optional note to the customer). | button_label (80) | required |  |
| ✅ | `problems.channel_prefix` | Channel-name prefix of problem-report tickets ({prefix} in tickets.channel_name_pattern). Discord lower-cases channel names and replaces spaces with dashes. | channel_prefix (60) | required |  |
| ✅ | `problems.channel_topic` | Optional topic of each problem-report ticket channel. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {user_name} is the customer's display name. | channel_topic (1024) | optional | `{number}`, `{user_name}` |
| ✅ | `problems.details.heading` | Optional heading of the card with the customer's form answers, posted below the report panel, shown in bold (it may start with an icon, e.g. {icon_file}). Each question follows in bold with the answer under it. If empty, the answers are shown without a heading. | title (256) | optional |  |
| ✅ | `problems.ticket.decided_by_field` | Optional field name under which the staff member who decided is mentioned on the report panel. If empty, it is not shown. | field_name (256) | optional |  |
| ✅ | `problems.ticket.description` | Text of the report panel, for example that only the customer and authorized staff can see this ticket. {user_mention} mentions the customer, {user_name} is their display name. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). You can link server channels with {channel_<key>} (for example {channel_faq}). The form answers are posted below the panel (problems.details.heading). | description (4096) | required | `{user_mention}`, `{user_name}`, `{number}`, `{type_label}`, `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |
| ✅ | `problems.ticket.order_field` | Field name of the order the report is about (report panel, staff alert, decision message; omitted when the report is not about an order). Needed when problems.eligible_statuses is not empty. | field_name (256) | required |  |
| ✅ | `problems.ticket.order_value` | The order the report is about, shown under problems.ticket.order_field. {order_number} is the order number, {total} the order total written with common.money_format, {status} the order's status name as the customer's order ticket writes it (checkout.ticket.stage.<status>). Needed when problems.eligible_statuses is not empty. | field_value (1024) | required | `{order_number}`, `{total}`, `{status}` |
| ✅ | `problems.ticket.reason_field` | Field name of the reason (rejection) or note (resolution) staff gave, shown exactly as staff wrote it on the report panel and in the decision message (omitted when staff gave none). | field_name (256) | required |  |
| ✅ | `problems.ticket.status_field` | Field name of the report status on the report panel and in the decision message. | field_name (256) | required |  |
| ✅ | `problems.ticket.title` | Title of the report panel posted in the new ticket (the live message that shows the report and its status). {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). {user_name} is the customer's display name. | title (256) | required | `{number}`, `{type_label}`, `{user_name}` |
| ✅ | `problems.ticket.type_field` | Field name of the issue type on the report panel. | field_name (256) | required |  |

### Report a Problem: button on the #purchase panel

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.button.report` | Label of the Report a Problem button on the #purchase panel (next to the Start button). The button appears once the Report a Problem feature is fully configured (run /setup to update the panel). | button_label (80) | required |  |
| ▫️ | `problems.button.report_emoji` | Optional emoji on the Report a Problem button. | emoji (100) | optional |  |

### Report a Problem: private replies

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.created.description` | Private confirmation after the report's ticket was created. {channel_mention} links the ticket; {number} is the report's ticket number (zero-padded to tickets.number_padding digits). Also shown when the same report form is submitted again. | description (4096) | required | `{channel_mention}`, `{number}` |
| ✅ | `problems.created.title` | Optional title of the private confirmation after the report's ticket was created. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). | title (256) | optional | `{number}` |
| ✅ | `problems.limit_reached.description` | Private reply when the customer already has problems.max_open_per_user undecided reports with an open ticket. {limit} is that number; {channel_mention} links the ticket of their newest open report (a line with {channel_mention} is left out when that ticket cannot be linked). | description (4096) | required | `{limit}`, `{channel_mention}` |
| ✅ | `problems.limit_reached.title` | Optional title of the private reply when the customer already has the maximum number of open reports. | title (256) | optional |  |
| ✅ | `problems.no_orders.description` | Private reply when the customer has no order a report may refer to (statuses in problems.eligible_statuses). Needed when problems.allow_without_order = false. You can link server channels with {channel_<key>} (for example {channel_faq}). | description (4096) | optional | `{channel_welcome}`, `{channel_how_to_buy}`, `{channel_faq}`, `{channel_shipping}`, `{channel_announcements}`, `{channel_catalog}`, `{channel_new_arrivals}`, `{channel_reviews}`, `{channel_purchase}`, `{channel_orders}`, `{channel_staff_chat}`, `{channel_bot_log}`, `{channel_transcripts}` |

### Report a Problem: decision messages to the customer

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.notify.rejected.description` | Text of the private message telling the customer their report was rejected (staff's reason/note follows under problems.ticket.reason_field). Sent by direct message when features.dm_notifications is on; the report panel in the ticket always shows the decision, and this message is posted in the ticket when the panel cannot be updated. {channel_mention} links the ticket (a line with it is left out when the ticket cannot be linked). {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | description (4096) | required | `{number}`, `{type_label}`, `{channel_mention}` |
| ✅ | `problems.notify.rejected.title` | Optional title of the private message telling the customer their report was rejected. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | title (256) | optional | `{number}`, `{type_label}` |
| ✅ | `problems.notify.resolved.description` | Text of the private message telling the customer their report was resolved (staff's reason/note follows under problems.ticket.reason_field). Sent by direct message when features.dm_notifications is on; the report panel in the ticket always shows the decision, and this message is posted in the ticket when the panel cannot be updated. {channel_mention} links the ticket (a line with it is left out when the ticket cannot be linked). {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | description (4096) | required | `{number}`, `{type_label}`, `{channel_mention}` |
| ✅ | `problems.notify.resolved.title` | Optional title of the private message telling the customer their report was resolved. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). | title (256) | optional | `{number}`, `{type_label}` |

### Report a Problem: staff alert

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.staff_alert.description` | Text of the new-report staff alert (the report details stay in the private ticket). {user_mention} mentions the customer, {channel_mention} links the ticket. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). Needed when tickets.staff_alert_channel is set. | description (4096) | required | `{user_mention}`, `{channel_mention}`, `{number}`, `{type_label}` |
| ✅ | `problems.staff_alert.title` | Title of the alert posted in the staff alert channel (tickets.staff_alert_channel) for each new report. {number} is the report's ticket number (zero-padded to tickets.number_padding digits). {type_label} is the label of the chosen issue type (problems.type.<id>.label). Needed when tickets.staff_alert_channel is set. | title (256) | required | `{number}`, `{type_label}` |

### Report a Problem: report statuses and decisions

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.status.open` | Name of the report status 'open' (waiting for a staff decision), shown on the report panel (and in the decision message). It may start with an icon, e.g. {icon_check}. | field_value (1024) | required |  |
| ✅ | `problems.status.rejected` | Name of the report status 'rejected' (staff rejected the report), shown on the report panel (and in the decision message). It may start with an icon, e.g. {icon_check}. | field_value (1024) | required |  |
| ✅ | `problems.status.resolved` | Name of the report status 'resolved' (staff resolved the report), shown on the report panel (and in the decision message). It may start with an icon, e.g. {icon_check}. | field_value (1024) | required |  |

### Report a Problem: issue type 'damaged'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.type.damaged.description` | Optional small text under the 'damaged' option in the issue-type menu (shown when several types exist). | option_description (100) | optional |  |
| ✅ | `problems.type.damaged.emoji` | Optional emoji of the 'damaged' option in the issue-type menu. | emoji (100) | optional |  |
| ✅ | `problems.type.damaged.form.details.description` | Optional help text under the label of the 'details' field. | input_description (100) | optional |  |
| ✅ | `problems.type.damaged.form.details.label` | Label of the 'details' field of the 'damaged' form (also the question shown in bold above the answer on the report's ticket). | input_label (45) | required |  |
| ✅ | `problems.type.damaged.form.details.placeholder` | Optional greyed-out example text inside the empty 'details' field. | input_placeholder (100) | optional |  |
| ✅ | `problems.type.damaged.form_title` | Title of the form in which the customer describes a 'damaged' problem, before the ticket is created (opened by the choice step's Continue button, or right away when there is nothing to choose). | modal_title (45) | required |  |
| ✅ | `problems.type.damaged.label` | Name of the 'damaged' issue type: the option in the issue-type menu of the private report step, and {type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision message. | option_label (100) | required |  |

### Report a Problem: issue type 'general'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.type.general.description` | Optional small text under the 'general' option in the issue-type menu (shown when several types exist). | option_description (100) | optional |  |
| ✅ | `problems.type.general.emoji` | Optional emoji of the 'general' option in the issue-type menu. | emoji (100) | optional |  |
| ✅ | `problems.type.general.form.details.description` | Optional help text under the label of the 'details' field. | input_description (100) | optional |  |
| ✅ | `problems.type.general.form.details.label` | Label of the 'details' field of the 'general' form (also the question shown in bold above the answer on the report's ticket). | input_label (45) | required |  |
| ✅ | `problems.type.general.form.details.placeholder` | Optional greyed-out example text inside the empty 'details' field. | input_placeholder (100) | optional |  |
| ✅ | `problems.type.general.form_title` | Title of the form in which the customer describes a 'general' problem, before the ticket is created (opened by the choice step's Continue button, or right away when there is nothing to choose). | modal_title (45) | required |  |
| ✅ | `problems.type.general.label` | Name of the 'general' issue type: the option in the issue-type menu of the private report step, and {type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision message. | option_label (100) | required |  |

### Report a Problem: issue type 'not_received'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.type.not_received.description` | Optional small text under the 'not_received' option in the issue-type menu (shown when several types exist). | option_description (100) | optional |  |
| ✅ | `problems.type.not_received.emoji` | Optional emoji of the 'not_received' option in the issue-type menu. | emoji (100) | optional |  |
| ✅ | `problems.type.not_received.form.details.description` | Optional help text under the label of the 'details' field. | input_description (100) | optional |  |
| ✅ | `problems.type.not_received.form.details.label` | Label of the 'details' field of the 'not_received' form (also the question shown in bold above the answer on the report's ticket). | input_label (45) | required |  |
| ✅ | `problems.type.not_received.form.details.placeholder` | Optional greyed-out example text inside the empty 'details' field. | input_placeholder (100) | optional |  |
| ✅ | `problems.type.not_received.form_title` | Title of the form in which the customer describes a 'not_received' problem, before the ticket is created (opened by the choice step's Continue button, or right away when there is nothing to choose). | modal_title (45) | required |  |
| ✅ | `problems.type.not_received.label` | Name of the 'not_received' issue type: the option in the issue-type menu of the private report step, and {type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision message. | option_label (100) | required |  |

### Report a Problem: issue type 'wrong_item'

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `problems.type.wrong_item.description` | Optional small text under the 'wrong_item' option in the issue-type menu (shown when several types exist). | option_description (100) | optional |  |
| ✅ | `problems.type.wrong_item.emoji` | Optional emoji of the 'wrong_item' option in the issue-type menu. | emoji (100) | optional |  |
| ✅ | `problems.type.wrong_item.form.details.description` | Optional help text under the label of the 'details' field. | input_description (100) | optional |  |
| ✅ | `problems.type.wrong_item.form.details.label` | Label of the 'details' field of the 'wrong_item' form (also the question shown in bold above the answer on the report's ticket). | input_label (45) | required |  |
| ✅ | `problems.type.wrong_item.form.details.placeholder` | Optional greyed-out example text inside the empty 'details' field. | input_placeholder (100) | optional |  |
| ✅ | `problems.type.wrong_item.form_title` | Title of the form in which the customer describes a 'wrong_item' problem, before the ticket is created (opened by the choice step's Continue button, or right away when there is nothing to choose). | modal_title (45) | required |  |
| ✅ | `problems.type.wrong_item.label` | Name of the 'wrong_item' issue type: the option in the issue-type menu of the private report step, and {type_label} / the type shown on the report's ticket panel, in the staff alert and in the decision message. | option_label (100) | required |  |

## Staff operations (#staff-chat alerts, reminders, Owner alerts, Customer role)

Feature `operations` — enabled.

### Operations: staff alerts (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `operations.alert.low_stock.description` | Staff alert when an order left an item with catalog.low_stock_threshold units or fewer (once per drop; operations.ping_roles are mentioned). {name} is the item's name, {count} the units that can still be ordered. | description (4096) | required | `{name}`, `{count}` |
| ✅ | `operations.alert.low_stock.title` | Optional title of the Staff alert when an order left an item with catalog.low_stock_threshold units or fewer (once per drop; operations.ping_roles are mentioned). {name} is the item's name, {count} the units that can still be ordered. | title (256) | optional | `{name}`, `{count}` |
| ✅ | `operations.alert.new_order.description` | Staff alert when a customer submitted a new order (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.new_order.title` | Optional title of the Staff alert when a customer submitted a new order (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.paid_order.description` | Staff alert when an order's payment was confirmed and it waits to be shipped (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.paid_order.title` | Optional title of the Staff alert when an order's payment was confirmed and it waits to be shipped (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.payment_method.description` | Staff alert when a customer chose a payment method our team handles (payments.methods kind "staff") and waits for the payment details in their order ticket ({method} is the method's name) (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{method}` |
| ✅ | `operations.alert.payment_method.title` | Optional title of the Staff alert when a customer chose a payment method our team handles (payments.methods kind "staff") and waits for the payment details in their order ticket ({method} is the method's name) (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{method}` |
| ✅ | `operations.alert.payment_submitted.description` | Staff alert when a customer submitted their payment (gift cards covering the total) and it waits to be verified (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.payment_submitted.title` | Optional title of the Staff alert when a customer submitted their payment (gift cards covering the total) and it waits to be verified (operations.ping_roles are mentioned). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ✅ | `operations.alert.sold_out.description` | Staff alert when an order sold an item out (once per drop; operations.ping_roles are mentioned). {name} is the item's name, {count} the units that can still be ordered. | description (4096) | required | `{name}`, `{count}` |
| ✅ | `operations.alert.sold_out.title` | Optional title of the Staff alert when an order sold an item out (once per drop; operations.ping_roles are mentioned). {name} is the item's name, {count} the units that can still be ordered. | title (256) | optional | `{name}`, `{count}` |
| ✅ | `operations.button.open_record` | Optional link button under order alerts and order reminders that jumps to the order's live record in checkout.staff_channel. | button_label (80) | optional |  |

### Operations: staff reminders (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `operations.button.open_ticket` | Optional link button that jumps to the ticket: under unanswered-ticket reminders and under the order alerts (next to operations.button.open_record). | button_label (80) | optional |  |
| ✅ | `operations.reminder.unanswered.description` | Staff reminder about a support ticket whose customer waits for a staff reply, posted once it waited operations.unanswered_ticket_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {ticket_number} is the ticket number, {customer_mention} mentions the customer (no ping), {ticket_channel} links the ticket; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unanswered_ticket_minutes is above 0. | description (4096) | required | `{ticket_number}`, `{customer_mention}`, `{ticket_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unanswered.title` | Optional title of the Staff reminder about a support ticket whose customer waits for a staff reply, posted once it waited operations.unanswered_ticket_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {ticket_number} is the ticket number, {customer_mention} mentions the customer (no ping), {ticket_channel} links the ticket; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{ticket_number}`, `{customer_mention}`, `{ticket_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unclaimed.description` | Staff reminder about an open order nobody claimed, posted once it waited operations.unclaimed_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unclaimed_order_minutes is above 0. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unclaimed.title` | Optional title of the Staff reminder about an open order nobody claimed, posted once it waited operations.unclaimed_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unfulfilled.description` | Staff reminder about a paid order not shipped yet, posted once it waited operations.unfulfilled_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unfulfilled_order_minutes is above 0. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unfulfilled.title` | Optional title of the Staff reminder about a paid order not shipped yet, posted once it waited operations.unfulfilled_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unpaid.description` | Staff reminder about an order still waiting for payment, posted once it waited operations.unpaid_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. Needed when operations.unpaid_order_minutes is above 0. | description (4096) | required | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |
| ✅ | `operations.reminder.unpaid.title` | Optional title of the Staff reminder about an order still waiting for payment, posted once it waited operations.unpaid_order_minutes minutes (again every operations.reminder_repeat_minutes while it still waits, when set). {order_number} is the order number, {customer_mention} mentions the customer (no ping), {total} is the order total, {status} the status name (checkout.status_label.*), {order_channel} links the customer's order channel; {waiting_since} is when it started waiting (shown in each reader's time zone), {minutes} the threshold in minutes. | title (256) | optional | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{waiting_since}`, `{minutes}` |

### Operations: failure alerts (staff channel)

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ✅ | `operations.failure.error.description` | Staff alert when a button, form or command failed unexpectedly (the member got the configured error message; details are in the bot logs). {source} is the internal name of the control or command, {error_id} the reference the member saw, {error} the error type. | description (4096) | required | `{source}`, `{error_id}`, `{error}` |
| ✅ | `operations.failure.error.title` | Optional title of the Staff alert when a button, form or command failed unexpectedly (the member got the configured error message; details are in the bot logs). {source} is the internal name of the control or command, {error_id} the reference the member saw, {error} the error type. | title (256) | optional | `{source}`, `{error_id}`, `{error}` |
| ✅ | `operations.failure.notification.description` | Staff alert when a notification could not be delivered: a private notification to a member, neither by direct message nor in the fallback channel, or a message the bot posts in a channel (e.g. a ticket's staff alert or an order's details). {user_mention} mentions the member it was for (no ping; '-' when it was for staff), {notification} is the notification's internal name (e.g. checkout.paid). | description (4096) | required | `{user_mention}`, `{notification}` |
| ✅ | `operations.failure.notification.title` | Optional title of the Staff alert when a notification could not be delivered: a private notification to a member, neither by direct message nor in the fallback channel, or a message the bot posts in a channel (e.g. a ticket's staff alert or an order's details). {user_mention} mentions the member it was for (no ping; '-' when it was for staff), {notification} is the notification's internal name (e.g. checkout.paid). | title (256) | optional | `{user_mention}`, `{notification}` |
| ✅ | `operations.failure.role.description` | Staff alert when the customer role could not be given or removed (missing role, role above the bot's highest role, or missing permission). {user_mention} mentions the member (no ping), {role_mention} the role. | description (4096) | required | `{user_mention}`, `{role_mention}` |
| ✅ | `operations.failure.role.title` | Optional title of the Staff alert when the customer role could not be given or removed (missing role, role above the bot's highest role, or missing permission). {user_mention} mentions the member (no ping), {role_mention} the role. | title (256) | optional | `{user_mention}`, `{role_mention}` |
| ✅ | `operations.failure.task.description` | Staff alert when a scheduled task failed permanently after its retries (details are in the audit log). {task} is the task's internal name, {task_id} its number, {attempts} the attempts made. | description (4096) | required | `{task}`, `{task_id}`, `{attempts}` |
| ✅ | `operations.failure.task.title` | Optional title of the Staff alert when a scheduled task failed permanently after its retries (details are in the audit log). {task} is the task's internal name, {task_id} its number, {attempts} the attempts made. | title (256) | optional | `{task}`, `{task_id}`, `{attempts}` |

### Operations: the Owner's private alerts

| | Key | Where it appears | Kind (max chars) | Required | Placeholders |
|---|---|---|---|---|---|
| ▫️ | `operations.owner.failure_task.description` | Optional text of the Owner's private alert about the same failure (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.failure.task.description`) | `{task}`, `{task_id}`, `{attempts}` |
| ▫️ | `operations.owner.failure_task.title` | Optional title of the Owner's private alert when a scheduled task failed permanently after its retries (details are in the audit log) (empty = the staff alert's title). | title (256) | optional (falls back to `operations.failure.task.title`) | `{task}`, `{task_id}`, `{attempts}` |
| ▫️ | `operations.owner.new_order.description` | Optional text of the Owner's private alert when a customer submitted a new order (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.new_order.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.new_order.title` | Optional title of the Owner's private alert when a customer submitted a new order (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.new_order.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.paid_order.description` | Optional text of the Owner's private alert when an order's payment was confirmed and it waits to be shipped (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.paid_order.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.paid_order.title` | Optional title of the Owner's private alert when an order's payment was confirmed and it waits to be shipped (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.paid_order.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.payment_method.description` | Optional text of the Owner's private alert when a customer chose a payment method our team handles (payments.methods kind "staff") and waits for the payment details in their order ticket ({method} is the method's name) (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.payment_method.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{method}` |
| ▫️ | `operations.owner.payment_method.title` | Optional title of the Owner's private alert when a customer chose a payment method our team handles (payments.methods kind "staff") and waits for the payment details in their order ticket ({method} is the method's name) (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.payment_method.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}`, `{method}` |
| ▫️ | `operations.owner.payment_submitted.description` | Optional text of the Owner's private alert when a customer submitted their payment (gift cards covering the total) and it waits to be verified (empty = the staff alert's text). Placeholders as in the staff alert. | description (4096) | optional (falls back to `operations.alert.payment_submitted.description`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |
| ▫️ | `operations.owner.payment_submitted.title` | Optional title of the Owner's private alert when a customer submitted their payment (gift cards covering the total) and it waits to be verified (empty = the staff alert's title). Placeholders as in the staff alert. | title (256) | optional (falls back to `operations.alert.payment_submitted.title`) | `{order_number}`, `{customer_mention}`, `{total}`, `{status}`, `{order_channel}` |

## Business decisions and settings still open

- None reported for the listed features.

## Summary

- Required keys (for the listed features): 508
- Required keys still missing: 0

Legend: ✅ configured · ❌ required and missing · ▫️ optional and empty
