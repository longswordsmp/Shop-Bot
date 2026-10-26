"""Operator text of ``/announce``: the staff-only form, confirmations and error replies.

Shown only to the staff member who runs the command (ephemeral replies and their own form), never to customers. Each
entry can be overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # the form (Discord limits: title and labels 45 characters, descriptions 100)
    "announcements.form.title": "Announcement",
    "announcements.form.title_label": "Title",
    "announcements.form.title_description": "Heading of the announcement (up to 256 characters).",
    "announcements.form.message_label": "Message",
    "announcements.form.message_description": "Main text. Discord formatting (bold, lists, links) is supported.",
    "announcements.form.field_label": "Field '{field_id}'",
    "announcements.form.field_optional": "Optional. Leave it empty to leave this field out.",
    "announcements.form.field_required": "Required.",
    # confirmation
    "announcements.done.title": "Announcement published",
    "announcements.done.description": "Posted in {channel}: {url}",
    "announcements.done.mention": "Mentioned: {role}",
    "announcements.done.image": "Image attached.",
    "announcements.done.open": "Open announcement",
    "announcements.done.already": "This form was already published: {url}",
    # errors (nothing is posted when any of these is shown)
    "announcements.error.title": "Announcement not published",
    "announcements.error.retry_hint": "Nothing was posted. Use the button below to correct the form and try again.",
    "announcements.error.rerun_hint": "Nothing was posted. Run /announce again.",
    "announcements.button.retry": "Edit and try again",
    "announcements.error.title_empty": "The title is empty.",
    "announcements.error.message_empty": "The message is empty.",
    "announcements.error.title_too_long": "The title has {length} characters; at most {limit} are allowed.",
    "announcements.error.message_too_long": "The message has {length} characters; at most {limit} are allowed.",
    "announcements.error.field_required": "The field '{field}' is required.",
    "announcements.error.field_too_long": "The field '{field}' has {length} characters; at most {limit} are allowed.",
    "announcements.error.total_too_long": (
        "The announcement has {length} characters in total (title, message, fields, footer and author line); "
        "Discord allows at most {limit}. Shorten it."
    ),
    "announcements.error.limits": "The announcement does not fit Discord's limits: {violations}",
    "announcements.error.no_guild": "The configured server is not available right now. Try again in a moment.",
    "announcements.error.channel_missing": (
        "The announcements channel (`{channel}`) does not exist yet. Run /setup first."
    ),
    "announcements.error.missing_permissions": "The bot lacks {permissions} in {channel}.",
    "announcements.error.forbidden": "Discord refused the message in {channel} (missing access or permissions).",
    "announcements.error.send_failed": "Posting failed (HTTP {http_status}, Discord code {code}). Try again.",
    "announcements.error.mention_none": (
        "No role can be mentioned: announcements.mention_roles is empty in the settings."
    ),
    "announcements.error.mention_not_allowed": (
        "'{role}' is not one of the roles announcements may mention (announcements.mention_roles)."
    ),
    "announcements.error.mention_missing": "The role `{role}` does not exist yet. Run /setup first.",
    "announcements.error.mention_not_pingable": (
        "{role} cannot be mentioned by the bot: make the role mentionable (mentionable = true in server.roles, then "
        "/setup) or give the bot the Mention @everyone permission in {channel}."
    ),
    "announcements.error.image_disabled": "Images are turned off for announcements (announcements.allow_image).",
    "announcements.error.image_type": "`{filename}` is not a supported image (png, jpg, gif or webp).",
    "announcements.error.image_too_large": (
        "The image is {size} MB; the bot can upload at most {limit} MB in this server."
    ),
    "announcements.error.image_download": (
        "The attached image could not be downloaded from Discord. If this keeps happening, run /announce again with "
        "the image."
    ),
    # replies about the form itself (already published, in progress, expired...)
    "announcements.notice.title": "Announcement form",
    "announcements.error.form_unknown": "This announcement form is no longer valid. Run /announce again.",
    "announcements.error.not_owner": "This announcement form was opened by another staff member.",
    "announcements.error.busy": "This announcement is already being published.",
    "announcements.error.interrupted": (
        "The bot restarted while this announcement was being posted. Check {channel} before running /announce again."
    ),
    "announcements.error.expired": (
        "This announcement form expired ({hours} hours after it was opened). Run /announce again."
    ),
    # presets (/announce preset:<id>): the private preview, Post and Cancel
    "announcements.preset.preview_title": "Preview: {name}",
    "announcements.preset.preview_intro": (
        "The announcement above is what will be posted (its button works once it is posted; the mention is listed "
        "below). Check it, then press **Post announcement**."
    ),
    "announcements.preset.channel": "**Channel:** {channel}",
    "announcements.preset.mention": "**Mention:** {role}",
    "announcements.preset.mention_none": "**Mention:** nobody",
    "announcements.preset.image": "**Image:** attached under the text",
    "announcements.preset.coupon": "**Coupon:** `{code}` · {terms}",
    "announcements.preset.problem_title": "Preview: {name} (cannot be posted yet)",
    "announcements.preset.problem_hint": "Fix the problem above, then run /announce preset:{preset} again.",
    "announcements.preset.post_rerun_hint": (
        "Nothing was posted. Fix the problem above, then run /announce preset:{preset} again."
    ),
    "announcements.preset.post_retry_hint": (
        "Nothing was posted. Fix the problem, then press **Post announcement** again."
    ),
    "announcements.preset.post": "Post announcement",
    "announcements.preset.cancel": "Cancel",
    "announcements.preset.cancelled_title": "Announcement cancelled",
    "announcements.preset.cancelled": "Nothing was posted.",
    "announcements.preset.done_coupon": "Claim button: coupon `{code}` · {terms}",
    "announcements.preset.notice_title": "Announcement preview",
    "announcements.preset.already": "This announcement was already posted: {url}",
    "announcements.preset.unknown": (
        "This preview is no longer valid (it was cancelled or has expired). Run /announce again."
    ),
    "announcements.preset.not_owner": "This preview was opened by another staff member.",
    "announcements.preset.expired": "This preview expired ({hours} hours after it was opened). Run /announce again.",
    "announcements.preset.unknown_preset": (
        "There is no preset '{preset}' (announcements.presets). Pick one from the list /announce suggests."
    ),
    "announcements.preset.removed": (
        "The preset '{preset}' is no longer in the settings (announcements.presets). Nothing was posted."
    ),
    "announcements.preset.text_missing": (
        "The preset's text `{key}` is not set in content.toml. Set it with /content set, then try again."
    ),
    "announcements.preset.link_missing": (
        "The text links the channel `{channel}`, which does not exist yet. Run /setup first."
    ),
    "announcements.preset.coupon_missing": (
        "The coupon `{code}` does not exist, or it has ended or expired, so its claim button would not work. Create "
        "it with this command:\n{command}"
    ),
    "announcements.preset.coupon_expired": (
        "The coupon `{code}` has expired, so its claim button would not work. End it with `/discount end "
        "code:{code}`, then create it again with this command:\n{command}"
    ),
    "announcements.preset.coupon_changed": (
        "The coupon `{code}` was ended and created again since this preview, so its terms may differ from what the "
        "preview showed."
    ),
    "announcements.preset.ended_while_posting": (
        "The coupon `{code}` ended while the announcement was being posted, so its button is being switched to the "
        "ended label."
    ),
    "announcements.preset.coupon_command": (
        "`/discount create code:{code}` with the type and terms this announcement describes"
    ),
    "announcements.preset.coupon_used_up": (
        "The coupon `{code}` has been used as many times as it allows ({uses} orders). End it with "
        "`/discount end code:{code}`, create it again with more uses, then run /announce again."
    ),
    "announcements.preset.coupons_off": (
        "Discount codes are switched off (checkout.discount_codes = false), so the claim button would only say the "
        "offer has ended. Switch them on and run /reload, then run /announce again."
    ),
    "announcements.preset.checkout_off": (
        "The checkout feature is not ready, so the coupon cannot be claimed or used. Check /status, then run "
        "/announce again."
    ),
    "announcements.preset.already_announced": (
        "This offer (coupon `{code}`) was already announced (Open announcement below). To post it again, delete that "
        "announcement in Discord first."
    ),
}
