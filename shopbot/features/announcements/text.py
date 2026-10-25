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
}
