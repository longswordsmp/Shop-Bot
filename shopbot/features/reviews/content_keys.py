"""Customer-facing content keys of the reviews feature and its settings checks.

Rating options are generated per configured ``reviews.rating_scale`` (``reviews.rating.<n>.*`` for n = 1..scale);
comment-form keys are required only when ``reviews.allow_comment`` is on, the staff approval message keys
(``reviews.approval.*``) only when a review can wait for approval (``reviews.require_approval``, or
``reviews.allow_unverified``: unverified reviews always do; they are always declared, so the preview and the content
checklist show them). Optional keys are omitted from the UI when empty; the per-rating ``display`` text falls back to
the rating's option label.

Published reviews never name the member: their keys offer only the rating (``{stars}``, ``{rating}``, ``{scale}``) and,
for the verification marker of a delivered order, ``{order_number}``. The title and the marker text refuse any other
placeholder (e.g. ``{user_name}``) in the content check; the other published texts take no placeholders, so a
``{user_name}`` typed there is shown as written and never filled in. Either way no name reaches the reviews channel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import ReviewSettings, Settings
from shopbot.features.server_keys import private_channel_problems, reference_problems

if TYPE_CHECKING:
    from shopbot.config.content import ContentStore

REVIEWS = "reviews"

MAX_RATING_SCALE = 10

# -- rating request -----------------------------------------------------------------------------------
PROMPT_TITLE = "reviews.prompt.title"
PROMPT_DESCRIPTION = "reviews.prompt.description"
PROMPT_SELECT_PLACEHOLDER = "reviews.prompt.select_placeholder"
BUTTON_DECLINE = "reviews.button.decline"

# -- comment form -------------------------------------------------------------------------------------
COMMENT_TITLE = "reviews.comment.title"
COMMENT_LABEL = "reviews.comment.label"
COMMENT_DESCRIPTION = "reviews.comment.description"
COMMENT_PLACEHOLDER = "reviews.comment.placeholder"

# -- /review and the Write a review button (the stars menu, the form and its replies) -----------------------
COMMAND_NAME = "review"
COMMAND_DESCRIPTION = "reviews.command.description"
COMMAND_STARS = "reviews.command.stars"
COMMAND_IMAGE = "reviews.command.image"
WRITE_TITLE = "reviews.write.title"
WRITE_LABEL = "reviews.write.label"
WRITE_DESCRIPTION = "reviews.write.description"
WRITE_PLACEHOLDER = "reviews.write.placeholder"
WRITE_PHOTO_LABEL = "reviews.write.photo_label"
WRITE_PHOTO_DESCRIPTION = "reviews.write.photo_description"
PICK_DESCRIPTION = "reviews.pick.description"
PICK_PLACEHOLDER = "reviews.pick.placeholder"
WRITTEN = "reviews.written.description"
WRITTEN_PENDING = "reviews.written_pending.description"
COOLDOWN = "reviews.cooldown.description"
LIMIT = "reviews.limit.description"
NO_ORDER = "reviews.no_order.description"
IMAGE_PROBLEM = "reviews.image_problem.description"
IMAGE_PROBLEM_TEXT_FIELD = "reviews.image_problem.text_field"
TOO_LONG = "reviews.too_long.description"
FORM_EXPIRED = "reviews.form_expired.description"

# -- the #reviews intro panel (posted by /setup at the top of the reviews channel) ------------------------------
INTRO_TITLE = "reviews.intro.title"
INTRO_DESCRIPTION = "reviews.intro.description"
INTRO_BUTTON = "reviews.intro.button"
INTRO_CHECKED_ALL = "reviews.intro.checked_all"
INTRO_CHECKED_UNVERIFIED = "reviews.intro.checked_unverified"
INTRO_CHECKED_PHOTOS = "reviews.intro.checked_photos"
INTRO_CHECKED_PHOTOS_UNVERIFIED = "reviews.intro.checked_photos_unverified"

# -- published review ---------------------------------------------------------------------------------
PUBLISHED_TITLE = "reviews.published.title"
PUBLISHED_RATING_FIELD = "reviews.published.rating_field"
PUBLISHED_VERIFIED_FIELD = "reviews.published.verified_field"
PUBLISHED_VERIFIED_VALUE = "reviews.published.verified_value"

# -- replies to the customer --------------------------------------------------------------------------
THANKS = "reviews.thanks.description"
THANKS_PENDING = "reviews.thanks_pending.description"
DECLINED = "reviews.declined.description"
ALREADY_REVIEWED = "reviews.already_reviewed.description"
EXPIRED = "reviews.expired.description"

# -- staff approval message (reviews.require_approval) ---------------------------------------------------
APPROVAL_TITLE = "reviews.approval.title"
APPROVAL_RATING_FIELD = "reviews.approval.rating_field"
APPROVAL_ORDER_FIELD = "reviews.approval.order_field"
APPROVAL_NO_ORDER = "reviews.approval.no_order"
APPROVAL_CUSTOMER_FIELD = "reviews.approval.customer_field"
APPROVAL_STATUS_FIELD = "reviews.approval.status_field"
APPROVAL_REASON_FIELD = "reviews.approval.reason_field"
APPROVAL_BUTTON_APPROVE = "reviews.approval.button.approve"
APPROVAL_BUTTON_REJECT = "reviews.approval.button.reject"

# States of the approval message (values of reviews.approval.status_field).
STATE_PENDING = "pending"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_REMOVED = "removed"
APPROVAL_STATES = (STATE_PENDING, STATE_APPROVED, STATE_REJECTED, STATE_REMOVED)

# -- private notices to the customer about the staff decision ---------------------------------------------
NOTICE_DECISIONS = (STATE_APPROVED, STATE_REJECTED)
NOTICE_REJECTED_REASON_FIELD = "reviews.notice.rejected.reason_field"

ORDER_PLACEHOLDERS = ("order_number",)
# Published reviews never name the member (see the module docstring).
PUBLISHED_PLACEHOLDERS = ("stars", "rating", "scale")
# The staff-only approval message may name the member.
APPROVAL_PLACEHOLDERS = ("stars", "rating", "scale", "user_name", "review_number")
STAFF_PLACEHOLDERS = ("staff_mention",)
APPROVED_NOTICE_PLACEHOLDERS = ("review_link",)

_ORDER_DOC = "{order_number} is the number of the reviewed order."
_RATING_DOC = (
    "{stars} is the rating as a row of stars (e.g. ★★★★☆), {rating} the chosen rating as a number and {scale} the "
    "highest rating (reviews.rating_scale)."
)


def approval_status_key(state: str) -> str:
    """Status text of the approval message, e.g. ``reviews.approval.status.pending``."""
    return f"reviews.approval.status.{state}"


def notice_key(decision: str, name: str) -> str:
    """Decision notice key, e.g. ``reviews.notice.approved.description``."""
    return f"reviews.notice.{decision}.{name}"


def rating_key(rating: int, name: str) -> str:
    """Per-rating key, e.g. ``reviews.rating.5.label``."""
    return f"reviews.rating.{rating}.{name}"


def _rating_keys(rating: int, scale: int) -> list[ContentKey]:
    group = "Reviews: rating options"
    position = f"rating {rating} of {scale}"
    label = rating_key(rating, "label")
    return [
        ck(
            label,
            Kind.OPTION_LABEL,
            REVIEWS,
            group,
            f"Option label for {position} in the rating selection menu of the review request (options are listed "
            "from the highest rating to the lowest). Also shown on the published review when "
            f"{rating_key(rating, 'display')} is empty.",
        ),
        ck(
            rating_key(rating, "description"),
            Kind.OPTION_DESCRIPTION,
            REVIEWS,
            group,
            f"Optional small text under the option for {position} in the rating selection menu.",
            required=False,
        ),
        ck(
            rating_key(rating, "emoji"),
            Kind.EMOJI,
            REVIEWS,
            group,
            f"Optional emoji shown next to the option for {position} in the rating selection menu.",
            required=False,
        ),
        ck(
            rating_key(rating, "display"),
            Kind.FIELD_VALUE,
            REVIEWS,
            group,
            f"Optional text shown as the rating on a published review with {position} (for example a row of "
            f"stars). If empty, the option label ({label}) is shown instead.",
            fallback=label,
        ),
    ]


def content_keys(settings: Settings) -> list[ContentKey]:
    rv = settings.reviews
    g_prompt = "Reviews: rating request (sent to the customer after an order is fulfilled)"
    g_comment = "Reviews: comment form (opens after the customer picks a rating)"
    g_published = "Reviews: published review (posted in the reviews channel)"
    g_replies = "Reviews: replies to the customer"
    keys = [
        ck(
            PROMPT_TITLE,
            Kind.TITLE,
            REVIEWS,
            g_prompt,
            "Optional title of the review request sent to the customer (by direct message or in the order channel, "
            f"see reviews.prompt_via) after their order was fulfilled. {_ORDER_DOC}",
            required=False,
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            PROMPT_DESCRIPTION,
            Kind.DESCRIPTION,
            REVIEWS,
            g_prompt,
            f"Text of the review request asking the customer to rate their order. {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            PROMPT_SELECT_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            REVIEWS,
            g_prompt,
            "Placeholder of the rating selection menu on the review request (shown before a rating is picked).",
        ),
        ck(
            BUTTON_DECLINE,
            Kind.BUTTON_LABEL,
            REVIEWS,
            g_prompt,
            "Label of the button on the review request that lets the customer decline to leave a review.",
        ),
    ]
    scale = rv.rating_scale if 1 <= rv.rating_scale <= MAX_RATING_SCALE else 0
    for rating in range(1, scale + 1):
        keys.extend(_rating_keys(rating, scale))
    keys += [
        ck(
            COMMENT_TITLE,
            Kind.MODAL_TITLE,
            REVIEWS,
            g_comment,
            "Title of the form that asks for a written comment after the customer picked a rating. Required when "
            "reviews.allow_comment is on.",
            required=rv.allow_comment,
        ),
        ck(
            COMMENT_LABEL,
            Kind.INPUT_LABEL,
            REVIEWS,
            g_comment,
            "Label of the comment box in the comment form. Required when reviews.allow_comment is on.",
            required=rv.allow_comment,
        ),
        ck(
            COMMENT_DESCRIPTION,
            Kind.INPUT_DESCRIPTION,
            REVIEWS,
            g_comment,
            "Optional help text under the label of the comment box.",
            required=False,
        ),
        ck(
            COMMENT_PLACEHOLDER,
            Kind.INPUT_PLACEHOLDER,
            REVIEWS,
            g_comment,
            "Optional greyed-out example text inside the empty comment box.",
            required=False,
        ),
        ck(
            PUBLISHED_TITLE,
            Kind.TITLE,
            REVIEWS,
            g_published,
            f"Title of a review posted in the reviews channel. {_RATING_DOC} The customer's comment or description "
            "(if any) is shown below the title exactly as written, and the photo sent with /review below it. "
            "Published reviews never show the member's name, so no placeholder names them.",
            placeholders=PUBLISHED_PLACEHOLDERS,
        ),
        ck(
            PUBLISHED_RATING_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            g_published,
            "Optional heading of the rating on a published review; the rating itself is shown with "
            "reviews.rating.<n>.display (or the rating's option label). If empty, no rating field is shown (for "
            "example when the title shows the stars).",
            required=False,
        ),
        ck(
            PUBLISHED_VERIFIED_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            g_published,
            "Optional heading of the verification marker shown on a published review of a delivered order (a "
            "verified purchase). An unverified review written with /review without an order shows no marker. Set it "
            f"together with {PUBLISHED_VERIFIED_VALUE}, or leave both empty to show no marker.",
            required=False,
        ),
        ck(
            PUBLISHED_VERIFIED_VALUE,
            Kind.FIELD_VALUE,
            REVIEWS,
            g_published,
            f"Optional verification marker text shown under {PUBLISHED_VERIFIED_FIELD} on a published review of a "
            f"delivered order (for example that the reviewer bought the order). Set it together with "
            f"{PUBLISHED_VERIFIED_FIELD}. {_ORDER_DOC}",
            required=False,
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            THANKS,
            Kind.DESCRIPTION,
            REVIEWS,
            g_replies,
            "Replaces the review request after the customer submitted their review (the rating menu and buttons "
            f"are removed). {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            THANKS_PENDING,
            Kind.DESCRIPTION,
            REVIEWS,
            g_replies,
            "Optional text that replaces the review request after the customer submitted their review while "
            "reviews.require_approval is on (the review is published only after a staff member approves it). If "
            f"empty, {THANKS} is shown instead. {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
            fallback=THANKS,
        ),
        ck(
            DECLINED,
            Kind.DESCRIPTION,
            REVIEWS,
            g_replies,
            f"Replaces the review request after the customer chose not to leave a review. {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            ALREADY_REVIEWED,
            Kind.DESCRIPTION,
            REVIEWS,
            g_replies,
            "Shown privately when the customer tries to rate or comment on an order they already reviewed. "
            f"{_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
        ck(
            EXPIRED,
            Kind.DESCRIPTION,
            REVIEWS,
            g_replies,
            "Replaces the review request when the order can no longer be reviewed (for example it was refunded, or "
            f"staff closed the review). {_ORDER_DOC}",
            placeholders=ORDER_PLACEHOLDERS,
        ),
    ]
    keys += _command_keys(rv)
    keys += _intro_keys(rv)
    keys += _approval_keys(approval_needed(rv))
    keys += _notice_keys()
    return keys


def approval_needed(rv: ReviewSettings) -> bool:
    """Whether a review can wait for a staff decision: every review with reviews.require_approval, unverified reviews
    (reviews.allow_unverified) always, and reviews sent with a photo (reviews.photo_requires_approval)."""
    return rv.require_approval or rv.allow_unverified or rv.photo_requires_approval


def intro_checked_key(rv: ReviewSettings) -> str | None:
    """The intro panel's last line about our team's check, chosen from the settings so it is always true (None: no
    review waits for a check)."""
    if rv.require_approval:
        return INTRO_CHECKED_ALL
    if rv.allow_unverified and rv.photo_requires_approval:
        return INTRO_CHECKED_PHOTOS_UNVERIFIED
    if rv.allow_unverified:
        return INTRO_CHECKED_UNVERIFIED
    if rv.photo_requires_approval:
        return INTRO_CHECKED_PHOTOS
    return None


def _command_keys(rv: ReviewSettings) -> list[ContentKey]:
    group = "Reviews: /review and the Write a review button (the stars menu, the form and the private replies)"
    registered = (
        " Discord receives it when the bot starts (a change needs a restart: /reload does not update the command)."
    )
    unverified = rv.allow_unverified
    return [
        ck(
            COMMAND_DESCRIPTION,
            Kind.COMMAND_DESCRIPTION,
            REVIEWS,
            group,
            "Description of the /review command shown in Discord's command list (every member can use it). The command "
            f"is registered only when this text, the two option texts below and the rating labels are set.{registered}",
        ),
        ck(
            COMMAND_STARS,
            Kind.COMMAND_DESCRIPTION,
            REVIEWS,
            group,
            "Description of the required 'stars' option of /review (its choices are the rating labels "
            f"reviews.rating.<n>.label with a row of stars, highest first).{registered}",
        ),
        ck(
            COMMAND_IMAGE,
            Kind.COMMAND_DESCRIPTION,
            REVIEWS,
            group,
            f"Description of the optional 'photo' option of /review (a PNG, JPG, GIF or WebP picture).{registered}",
        ),
        ck(
            PICK_DESCRIPTION,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private message shown when a member presses Write a review on the intro panel of the reviews channel "
            "(it works where members cannot type, unlike /review): the stars menu below it opens the review form.",
        ),
        ck(
            PICK_PLACEHOLDER,
            Kind.SELECT_PLACEHOLDER,
            REVIEWS,
            group,
            "Placeholder of the stars menu shown after Write a review (its options are the rating labels "
            "reviews.rating.<n>.*, highest first).",
        ),
        ck(
            WRITE_TITLE,
            Kind.MODAL_TITLE,
            REVIEWS,
            group,
            "Title of the private review form (opened by /review, or by the stars menu after Write a review).",
        ),
        ck(WRITE_LABEL, Kind.INPUT_LABEL, REVIEWS, group, "Label of the description box in the review form."),
        ck(
            WRITE_DESCRIPTION,
            Kind.INPUT_DESCRIPTION,
            REVIEWS,
            group,
            "Optional help text under the label of the description box (at most reviews.comment_max_length "
            "characters can be typed).",
            required=False,
        ),
        ck(
            WRITE_PLACEHOLDER,
            Kind.INPUT_PLACEHOLDER,
            REVIEWS,
            group,
            "Optional greyed-out example text inside the empty description box.",
            required=False,
        ),
        ck(
            WRITE_PHOTO_LABEL,
            Kind.INPUT_LABEL,
            REVIEWS,
            group,
            "Label of the optional photo upload in the review form (shown unless the photo was already added with "
            "the photo option of /review).",
        ),
        ck(
            WRITE_PHOTO_DESCRIPTION,
            Kind.INPUT_DESCRIPTION,
            REVIEWS,
            group,
            "Optional help text under the label of the photo upload (a PNG, JPG, GIF or WebP picture; Discord lets "
            "members pick any file, and the bot refuses other files when the form is sent).",
            required=False,
        ),
        ck(
            WRITTEN,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply after a review form was sent and the review is published in the reviews channel right away "
            "(a review of a delivered order while reviews.require_approval is off).",
        ),
        ck(
            WRITTEN_PENDING,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply after a review form was sent and the review waits for our team's check before it is "
            "published (every unverified review, and every review while reviews.require_approval is on).",
        ),
        ck(
            COOLDOWN,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply when a member without a delivered order to review already wrote an unverified review in "
            "the last reviews.unverified_cooldown_days days. {date} is the day they can write another one (shown in "
            "the member's own time zone). Required when reviews.allow_unverified is on and the cooldown is not 0.",
            required=unverified and rv.unverified_cooldown_days > 0,
            placeholders=("date",),
        ),
        ck(
            LIMIT,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply when a member without a delivered order to review already wrote their one unverified "
            "review (reviews.unverified_cooldown_days = 0). Required when reviews.allow_unverified is on and the "
            "cooldown is 0.",
            required=unverified and rv.unverified_cooldown_days == 0,
        ),
        ck(
            NO_ORDER,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply to /review or Write a review from a member who has no delivered order to review while "
            "reviews.allow_unverified is off. Required when reviews.allow_unverified is off.",
            required=not unverified,
        ),
        ck(
            IMAGE_PROBLEM,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply when the photo of a review cannot be used (not a PNG, JPG, GIF or WebP picture, too "
            "large, or Discord did not deliver it). {limit} is the largest size in MB (e.g. 9.5). A photo added with "
            "/review is refused before the form opens; one uploaded in the form only when the form is sent.",
            placeholders=("limit",),
        ),
        ck(
            IMAGE_PROBLEM_TEXT_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            f"Optional heading over the member's own description, shown with {IMAGE_PROBLEM} when the photo is "
            "refused after the form was sent (a photo uploaded in the form, or one added with /review that could not "
            "be downloaded), so the text they wrote is not lost. If empty, the description is not shown.",
            required=False,
        ),
        ck(
            TOO_LONG,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply when the description cannot be shown within Discord's limits (it can only happen with a "
            "large reviews.comment_max_length). {max} is reviews.comment_max_length.",
            placeholders=("max",),
        ),
        ck(
            FORM_EXPIRED,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Private reply when a review form is sent after it expired (6 hours after it opened) or was already sent.",
        ),
    ]


def _intro_keys(rv: ReviewSettings) -> list[ContentKey]:
    group = "Reviews: the intro panel (posted by /setup at the top of the reviews channel)"
    checked = intro_checked_key(rv)
    return [
        ck(INTRO_TITLE, Kind.TITLE, REVIEWS, group, "Title of the intro panel at the top of the reviews channel."),
        ck(
            INTRO_DESCRIPTION,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Text of the intro panel: what the reviews channel is and how to leave a review with the Write a review "
            "button below it or /review (stars, a description and an optional photo; the member's name is never "
            "shown). The line about our team's check follows it (reviews.intro.checked_*), chosen from the settings "
            "so it is always true.",
        ),
        ck(
            INTRO_BUTTON,
            Kind.BUTTON_LABEL,
            REVIEWS,
            group,
            "Label of the button on the intro panel that starts a review: it answers privately with the stars menu "
            "(reviews.pick.*), then opens the review form. It works in the read-only reviews channel, where members "
            "cannot type /review.",
        ),
        ck(
            INTRO_CHECKED_ALL,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Last line of the intro panel while reviews.require_approval is on (our team checks every review before "
            "it appears). Required then.",
            required=checked == INTRO_CHECKED_ALL,
        ),
        ck(
            INTRO_CHECKED_PHOTOS_UNVERIFIED,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Last line of the intro panel while reviews.require_approval is off and both reviews.allow_unverified and "
            "reviews.photo_requires_approval are on (a review of a delivered order without a photo appears right "
            "away; reviews with a photo and reviews without a delivered order once our team checked them). Required "
            "then.",
            required=checked == INTRO_CHECKED_PHOTOS_UNVERIFIED,
        ),
        ck(
            INTRO_CHECKED_UNVERIFIED,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Last line of the intro panel while reviews.require_approval and reviews.photo_requires_approval are off "
            "and reviews.allow_unverified is on (reviews of a delivered order appear right away, other reviews once "
            "our team checked them). Required then.",
            required=checked == INTRO_CHECKED_UNVERIFIED,
        ),
        ck(
            INTRO_CHECKED_PHOTOS,
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Last line of the intro panel while reviews.photo_requires_approval is the only check that applies "
            "(reviews with a photo appear once our team checked them, the others right away). Required then.",
            required=checked == INTRO_CHECKED_PHOTOS,
        ),
    ]


def _approval_keys(required: bool) -> list[ContentKey]:
    group = "Reviews: approval request (staff only, posted in reviews.approval_channel)"
    when = (
        " Required when a review can wait for approval (reviews.require_approval, reviews.allow_unverified or "
        "reviews.photo_requires_approval is on)."
    )
    keys = [
        ck(
            APPROVAL_TITLE,
            Kind.TITLE,
            REVIEWS,
            group,
            "Title of the message that asks staff to approve or reject a submitted review (posted in the staff "
            "channel reviews.approval_channel; the customer's comment is shown below it exactly as written, and the "
            f"photo sent with /review below it). {_RATING_DOC} {{user_name}} is the reviewer's display name (staff "
            "only; published reviews never show it), {review_number} the review's number (for /reviews approve and "
            f"reject).{when}",
            required=required,
            placeholders=APPROVAL_PLACEHOLDERS,
        ),
        ck(
            APPROVAL_RATING_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            "Heading of the rating on the approval message (the rating is shown with reviews.rating.<n>.display or "
            f"the rating's option label).{when}",
            required=required,
        ),
        ck(
            APPROVAL_ORDER_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            f"Heading of the reviewed order's number on the approval message (or of {APPROVAL_NO_ORDER}).{when}",
            required=required,
        ),
        ck(
            APPROVAL_NO_ORDER,
            Kind.FIELD_VALUE,
            REVIEWS,
            group,
            "Shown under the order heading of the approval message of an unverified review (written with /review "
            f"by a member without a delivered order to review; it is published without a verification marker).{when}",
            required=required,
        ),
        ck(
            APPROVAL_CUSTOMER_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            f"Heading of the reviewer (a mention of the customer) on the approval message.{when}",
            required=required,
        ),
        ck(
            APPROVAL_STATUS_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            "Heading of the approval status on the approval message (its value is one of "
            f"reviews.approval.status.*).{when}",
            required=required,
        ),
    ]
    status_docs = {
        STATE_PENDING: ("the review waits for a staff decision (the Approve and Reject buttons are shown)", ()),
        STATE_APPROVED: (
            "a staff member approved the review (it is then published in the reviews channel). {staff_mention} "
            "mentions the staff member who approved it",
            STAFF_PLACEHOLDERS,
        ),
        STATE_REJECTED: (
            "a staff member rejected the review (it is not published). {staff_mention} mentions the staff member who "
            "rejected it",
            STAFF_PLACEHOLDERS,
        ),
        STATE_REMOVED: ("the review was removed with /reviews remove (it is not or no longer published)", ()),
    }
    for state in APPROVAL_STATES:
        doc, placeholders = status_docs[state]
        keys.append(
            ck(
                approval_status_key(state),
                Kind.FIELD_VALUE,
                REVIEWS,
                group,
                f"Approval status shown on the approval message when {doc}.{when}",
                required=required,
                placeholders=placeholders,
            )
        )
    keys += [
        ck(
            APPROVAL_REASON_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            "Optional heading of the rejection reason on the approval message (the reason is shown exactly as the "
            "staff member wrote it in the reject form). If empty, the reason is not shown there.",
            required=False,
        ),
        ck(
            APPROVAL_BUTTON_APPROVE,
            Kind.BUTTON_LABEL,
            REVIEWS,
            group,
            "Label of the Approve button on the approval message (staff only; publishes the review in the reviews "
            f"channel).{when}",
            required=required,
        ),
        ck(
            APPROVAL_BUTTON_REJECT,
            Kind.BUTTON_LABEL,
            REVIEWS,
            group,
            "Label of the Reject button on the approval message (staff only; opens a form for an optional reason; "
            f"the review is not published).{when}",
            required=required,
        ),
    ]
    return keys


def _notice_keys() -> list[ContentKey]:
    group = "Reviews: decision notices (private, to the customer, after a staff decision)"
    delivery = (
        "Sent by direct message when features.dm_notifications is on, otherwise (or when direct messages are "
        "closed) in the order channel with a mention (notifications.fallback_to_channel; a review without an order "
        "has no order channel)."
    )
    return [
        ck(
            notice_key(STATE_APPROVED, "title"),
            Kind.TITLE,
            REVIEWS,
            group,
            f"Optional title of the private message telling the customer their review was approved and published. "
            f"Needs {notice_key(STATE_APPROVED, 'description')}.",
            required=False,
        ),
        ck(
            notice_key(STATE_APPROVED, "description"),
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            "Optional text of the private message telling the customer their review was approved; it is sent once "
            f"the review is published in the reviews channel. {delivery} If empty, no message is sent. "
            "{review_link} is a link to the published review.",
            required=False,
            placeholders=APPROVED_NOTICE_PLACEHOLDERS,
        ),
        ck(
            notice_key(STATE_REJECTED, "title"),
            Kind.TITLE,
            REVIEWS,
            group,
            "Optional title of the private message telling the customer their review was not approved. Needs "
            f"{notice_key(STATE_REJECTED, 'description')}.",
            required=False,
        ),
        ck(
            notice_key(STATE_REJECTED, "description"),
            Kind.DESCRIPTION,
            REVIEWS,
            group,
            f"Optional text of the private message telling the customer their review was not approved (it is not "
            f"published). {delivery} If empty, no message is sent.",
            required=False,
        ),
        ck(
            NOTICE_REJECTED_REASON_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            group,
            "Optional heading under which the reason the staff member gave in the reject form is shown in that "
            "message (exactly as written). If empty, or when no reason was given, the reason is not shown to the "
            "customer.",
            required=False,
        ),
    ]


def content_problems(settings: Settings, content: ContentStore) -> list[str]:
    """Optional texts that only work in pairs: the verification marker (heading and text) and the decision notices
    (a title needs its text)."""
    del settings
    problems: list[str] = []
    field, value = content.has(PUBLISHED_VERIFIED_FIELD), content.has(PUBLISHED_VERIFIED_VALUE)
    if field != value:
        missing, present = (
            (PUBLISHED_VERIFIED_VALUE, PUBLISHED_VERIFIED_FIELD)
            if field
            else (PUBLISHED_VERIFIED_FIELD, PUBLISHED_VERIFIED_VALUE)
        )
        problems.append(f"{missing} is not configured (needed because {present} is configured)")
    for decision in NOTICE_DECISIONS:
        title, description = notice_key(decision, "title"), notice_key(decision, "description")
        if content.has(title) and not content.has(description):
            problems.append(f"{description} is not configured (needed because {title} is configured)")
    return problems


def settings_problems(settings: Settings) -> list[str]:
    rv = settings.reviews
    problems: list[str] = []
    if rv.prompt_delay_minutes < 0:
        problems.append(
            "reviews.prompt_delay_minutes is not set (business decision: minutes after an order is fulfilled "
            "before the review request is sent; 0 = immediately)"
        )
    if not 1 <= rv.rating_scale <= MAX_RATING_SCALE:
        problems.append(
            f"reviews.rating_scale is not set (business decision: number of rating options, 1-{MAX_RATING_SCALE})"
        )
    if not rv.channel:
        problems.append("reviews.channel: required (the channel where reviews are published)")
    problems.extend(reference_problems(settings, channels=(("reviews.channel", rv.channel),)))
    if rv.prompt_via == "dm" and not settings.features.dm_notifications:
        problems.append(
            "reviews.prompt_via = 'dm' requires features.dm_notifications = true (otherwise review requests are "
            "never delivered); use 'dm_then_channel' or 'channel' instead"
        )
    if rv.comment_required and not rv.allow_comment:
        problems.append("reviews.comment_required = true requires reviews.allow_comment = true")
    if approval_needed(rv):
        if not rv.approval_channel:
            problems.append(
                "reviews.approval_channel: required when reviews.require_approval, reviews.allow_unverified or "
                "reviews.photo_requires_approval = true (the staff channel where submitted reviews wait for approval)"
            )
        approval = (("reviews.approval_channel", rv.approval_channel),)
        problems.extend(reference_problems(settings, channels=approval))
        # The approval message shows comments that are not public yet and staff-only buttons.
        problems.extend(private_channel_problems(settings, approval))
    return problems
