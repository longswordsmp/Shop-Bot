"""Operator text of the catalog: /product and /category replies, the staff-only product editor form, the staff
product and category lists, and listing refresh reports. Shown only to catalog managers and admins (ephemerally) and
in the admin-only audit channel; never to customers. Each entry can be overridden in content.toml under
``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # product editor form (staff-only modal)
    "catalog.form.add_title": "New product",
    "catalog.form.edit_title": "Edit product #{product_id}",
    "catalog.form.name": "Name",
    "catalog.form.name_hint": "1-100 characters, shown as the product title.",
    "catalog.form.price": "Price ({currency})",
    "catalog.form.price_hint": "For example {example}",
    "catalog.form.stock": "Stock on hand",
    "catalog.form.stock_hint": "Whole number. Leave empty for unlimited stock.",
    "catalog.form.max_per_order": "Maximum per order",
    "catalog.form.max_per_order_hint": "Whole number. Leave empty for no limit.",
    "catalog.form.description": "Description",
    "catalog.form.description_hint": "Optional, up to 4000 characters.",
    # results
    "catalog.cmd.created": "Product #{product_id} created",
    "catalog.cmd.already_created": "This form was already submitted: product #{product_id} exists",
    "catalog.cmd.updated": "Product #{product_id} updated",
    "catalog.cmd.unchanged": "Nothing changed for product #{product_id}",
    "catalog.cmd.stale": "Product #{product_id} changed after the editor was opened (another edit or an order). "
    "Nothing was saved. Run /product edit again to see the current values.",
    "catalog.cmd.not_found": "Product #{product_id} does not exist.",
    "catalog.cmd.archived_product": "Product #{product_id} is archived and can no longer be changed.",
    "catalog.cmd.invalid": "Invalid input",
    "catalog.cmd.price_set": "Price of product #{product_id} set to {price}",
    "catalog.cmd.stock_set": "Stock of product #{product_id} set to {stock}",
    "catalog.cmd.stock_unlimited": "Product #{product_id} now has unlimited stock",
    "catalog.cmd.stock_added": "Stock of product #{product_id} changed by {delta} to {stock}",
    "catalog.cmd.stock_amount_required": "An amount is required for this stock mode.",
    "catalog.cmd.stock_add_zero": "The amount to add must not be 0 (use a negative amount to remove units).",
    "catalog.cmd.stock_set_negative": "The stock cannot be negative.",
    "catalog.cmd.stock_below_reserved": "The stock cannot be lower than the {reserved} units held by pending orders "
    "(requested: {requested}).",
    "catalog.cmd.stock_is_unlimited": "Product #{product_id} has unlimited stock. Use mode 'set' to give it a "
    "limited stock first.",
    "catalog.cmd.stock_too_large": "The stock must be at most {max}.",
    "catalog.cmd.image_set": "Image of product #{product_id} updated",
    "catalog.cmd.add_image_saved": "Its picture is saved and shows on its card in the catalog.",
    "catalog.cmd.add_image_failed": "Its picture was not saved: {reason} Add one with /product image "
    "product_id:{product_id} file:(your picture).",
    "catalog.cmd.add_image_lost": "The picture you attached was not received (the bot restarted, or the form was "
    "open longer than 30 minutes). Add it with /product image product_id:{product_id} file:(your picture).",
    "catalog.cmd.image_cleared": "Image of product #{product_id} removed",
    "catalog.cmd.image_invalid": "The image link must be a public https:// link to an image (at most 2048 "
    "characters, no spaces). To use a file from your computer, upload it with the file option instead (links to "
    "Discord attachments expire).",
    "catalog.cmd.image_host_not_allowed": "Images must be hosted on one of: {hosts}",
    "catalog.cmd.image_both": "Give either an image link (url) or an uploaded file (file), not both.",
    "catalog.cmd.image_type": "The uploaded file must be a PNG, JPEG, GIF or WebP image.",
    "catalog.cmd.image_size": "The uploaded image must be at most {max_mb} MB.",
    "catalog.cmd.image_empty": "The uploaded file is empty.",
    "catalog.cmd.image_download": "The uploaded file could not be downloaded from Discord. Nothing was changed; "
    "please try again.",
    "catalog.cmd.activated": "Product #{product_id} is now active (listed)",
    "catalog.cmd.deactivated": "Product #{product_id} is now inactive (hidden, cannot be ordered)",
    "catalog.cmd.archive_confirm": "Archiving hides product #{product_id} for good and it cannot be ordered again. "
    "Run the command again with confirm set to True.",
    "catalog.cmd.archive_reserved": "Product #{product_id} has {reserved} units held by pending orders. Archive it "
    "after those orders are completed or cancelled (you can deactivate it now with /product toggle).",
    "catalog.cmd.archived": "Product #{product_id} archived",
    # /product remove (confirmation buttons)
    "catalog.remove.title": "Remove product #{product_id}?",
    "catalog.remove.confirm": "{name} is removed from the listing for good and can no longer be ordered. Its order "
    "history is kept. This cannot be undone.",
    "catalog.remove.button_remove": "Remove",
    "catalog.remove.button_keep": "Keep",
    "catalog.cmd.removed": "Product #{product_id} removed",
    "catalog.cmd.remove_kept": "Product #{product_id} was kept",
    "catalog.cmd.remove_reserved": "Product #{product_id} has {reserved} units held by pending orders. Remove it "
    "after those orders are completed or cancelled (you can deactivate it now with /product toggle).",
    "catalog.cmd.sorted": "Product #{product_id} moved to position {position}",
    # validation reasons
    "catalog.invalid.name": "Name: must be 1-100 characters.",
    "catalog.invalid.description": "Description: must be at most 4000 characters.",
    "catalog.invalid.price": "Price: {reason}.",
    "catalog.invalid.currency": "Prices cannot be entered until settings.checkout.currency is set.",
    "catalog.invalid.stock": "Stock: must be empty (unlimited) or a whole number from 0 to {max}.",
    "catalog.invalid.max_per_order": "Maximum per order: must be empty (no limit) or a whole number from 1 to {max}.",
    "catalog.invalid.position": "Position: must be between {min} and {max}.",
    # product details (/product view)
    "catalog.view.title": "Product #{product_id}",
    "catalog.view.name": "Name",
    "catalog.view.price": "Price",
    "catalog.view.stock": "Stock on hand",
    "catalog.view.reserved": "Held by pending orders",
    "catalog.view.available": "Available",
    "catalog.view.max_per_order": "Maximum per order",
    "catalog.view.status": "Status",
    "catalog.view.position": "Position",
    "catalog.view.image": "Image",
    "catalog.view.version": "Version",
    "catalog.view.description": "Description",
    "catalog.view.unlimited": "unlimited",
    "catalog.view.none": "none",
    "catalog.view.active": "active",
    "catalog.view.inactive": "inactive",
    "catalog.view.archived": "archived",
    "catalog.view.amount": "{amount} {currency}",
    "catalog.view.image_long": "set (too long to show here; see the thumbnail)",
    "catalog.view.image_uploaded": "uploaded file {filename} ({kilobytes} KB, {type})",
    "catalog.view.categories": "Categories",
    "catalog.view.customer_preview_missing": "The customer card cannot be shown yet: some catalog text is not "
    "configured (run /status).",
    # staff product list (/product list)
    "catalog.list.title": "Products ({total})",
    "catalog.list.title_archived": "Products including archived ({total})",
    "catalog.list.line": "`#{product_id}` {name} | {price} | {stock} | {status}",
    "catalog.list.empty": "No products yet. Use /product add.",
    "catalog.list.page": "Page {page} of {pages}",
    "catalog.list.previous": "Previous",
    "catalog.list.next": "Next",
    "catalog.list.stock_unlimited": "unlimited",
    "catalog.list.stock": "{available} available / {stock} on hand",
    # autocomplete suggestion for product_id options
    "catalog.choice": "#{product_id} {name}",
    # /product categories
    "catalog.cmd.categories_title": "Categories of product #{product_id}",
    "catalog.cmd.category_added": "Added to {name}.",
    "catalog.cmd.category_already_in": "Already in {name}.",
    "catalog.cmd.category_removed": "Taken out of {name}.",
    "catalog.cmd.category_not_in": "Was not in {name}.",
    "catalog.cmd.categories_now": "Now in: {names}. Members find it there, and under All.",
    "catalog.cmd.categories_none": "Not in any category: members find it under All.",
    # /category (the categories of the #catalog menu)
    "catalog.category.choice": "#{category_id} {name}",
    "catalog.category.created": "Category #{category_id} {name} created",
    "catalog.category.created_hint": "It is in the #catalog menu now. Put items in it with /product categories "
    "product_id:(number) add:{name}.",
    "catalog.category.updated": "Category #{category_id} {name} updated",
    "catalog.category.unchanged": "Nothing changed for category #{category_id} {name}",
    "catalog.category.edit_nothing": "Give at least one value to change: name, emoji, description or position.",
    "catalog.category.delete_title": "Delete category #{category_id}?",
    "catalog.category.delete_confirm": "{name} leaves the #catalog menu and its {items} item(s) leave the category; "
    "they stay in the catalog and under All. Run the command again with confirm set to True.",
    "catalog.category.deleted": "Category {name} deleted ({items} item(s) taken out of it; they stay in the catalog)",
    "catalog.category.not_found": "Category #{category_id} does not exist (see /category list).",
    "catalog.category.too_many": "The menu holds at most {max} categories besides All. Delete one first.",
    "catalog.category.invalid.name": "Name: must be 1-{max} characters.",
    "catalog.category.invalid.name_all": "Name: {name} is the menu's built-in choice that shows every item. Pick "
    "another name.",
    "catalog.category.invalid.duplicate": "Name: category #{category_id} is already called {name} (upper and lower "
    "case count as the same). Pick another name.",
    "catalog.category.invalid.emoji": "Emoji: give one of the bot's icons by name (for example tag; the names are the "
    "files in config/emojis), one emoji or one country flag. Emoji with a skin tone or joined from several are not "
    "accepted.",
    "catalog.category.invalid.description": "Description: must be at most {max} characters.",
    "catalog.category.invalid.position": "Position: must be between {min} and {max}.",
    # /category list
    "catalog.category.list.title": "Categories ({count}, at most {max})",
    "catalog.category.list.all": "**All** (built in, always first): {items} item(s)",
    "catalog.category.list.line": "`#{category_id}` {emoji} **{name}**: {items} item(s), position {position}",
    "catalog.category.list.hidden": ", {hidden} more hidden (inactive)",
    "catalog.category.list.description": "_{description}_",
    "catalog.category.list.no_emoji": "·",
    "catalog.category.list.empty": "No categories yet. Create one with /category create.",
    # listing refresh reports
    "catalog.refresh.field": "Product listing",
    "catalog.refresh.done": "Listing updated in {channel}: {messages} message(s), {products} product(s) "
    "({changed} changed, {unchanged} unchanged, {removed} removed).",
    "catalog.refresh.not_ready": "Listing not refreshed: the catalog is not ready.",
    "catalog.refresh.disabled": "No listing channel is configured; {removed} old listing message(s) removed.",
    "catalog.refresh.no_guild": "Listing not refreshed: the server is not available to the bot.",
    "catalog.refresh.channel_missing": "Listing not refreshed: channel '{channel}' does not exist yet. Run /setup.",
    "catalog.refresh.missing_permissions": "Listing not refreshed: the bot lacks {permissions} in {channel}.",
    "catalog.refresh.missing_content": "Listing not refreshed: content key {content_key} is not configured.",
    "catalog.refresh.too_large": "Listing not refreshed: listing message {index} exceeds Discord limits ({problems}).",
    "catalog.refresh.forbidden": "Listing not refreshed: Discord denied access to {channel} (missing permissions).",
    "catalog.refresh.not_found": "Listing not refreshed: {channel} no longer exists. Run /setup.",
    "catalog.refresh.http_error": "Listing refresh failed (Discord error {status}); it will be retried.",
    "catalog.refresh.emoji_refused": "Discord refused a category's emoji, so the #catalog menu shows without emoji "
    "for now. Give that category another emoji or one of the bot's icons with /category edit (see /category list).",
    "catalog.refresh.image_changed": "Listing refresh stopped: a product image changed while it ran; it will be "
    "retried.",
    "catalog.refresh.error": "Listing refresh failed unexpectedly. Reference: {error_id}.",
}
