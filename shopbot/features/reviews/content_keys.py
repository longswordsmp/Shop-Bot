"""Customer-facing content keys of the reviews feature and its settings checks.

Rating options are generated per configured ``reviews.rating_scale`` (``reviews.rating.<n>.*`` for n = 1..scale);
comment-form keys are required only when ``reviews.allow_comment`` is on. Optional keys are omitted from the UI
when empty; the per-rating ``display`` text falls back to the rating's option label.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.server_keys import reference_problems

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

# -- published review ---------------------------------------------------------------------------------
PUBLISHED_TITLE = "reviews.published.title"
PUBLISHED_RATING_FIELD = "reviews.published.rating_field"

# -- replies to the customer --------------------------------------------------------------------------
THANKS = "reviews.thanks.description"
DECLINED = "reviews.declined.description"
ALREADY_REVIEWED = "reviews.already_reviewed.description"
EXPIRED = "reviews.expired.description"

ORDER_PLACEHOLDERS = ("order_number",)
PUBLISHED_PLACEHOLDERS = ("rating", "user_name", "order_number")

_ORDER_DOC = "{order_number} is the number of the reviewed order."


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
            "Title of a review posted in the reviews channel. {rating} is the chosen rating as a number (1 to "
            "reviews.rating_scale), {user_name} the reviewer's display name, {order_number} the number of the "
            "reviewed order. The customer's comment (if any) is shown below the title exactly as written.",
            placeholders=PUBLISHED_PLACEHOLDERS,
        ),
        ck(
            PUBLISHED_RATING_FIELD,
            Kind.FIELD_NAME,
            REVIEWS,
            g_published,
            "Heading of the rating on a published review; the rating itself is shown with reviews.rating.<n>.display "
            "(or the rating's option label).",
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
    return keys


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
    return problems
