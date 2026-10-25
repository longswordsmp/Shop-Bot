"""Preview: every reviews UI state, built by the runtime view functions from synthetic sample data.

Order numbers, member names and comments exist only in the preview and are obviously fake. A state whose text is not
configured yet is skipped (the content store records the missing keys for the checker). States that depend on the
rating options are built only when ``reviews.rating_scale`` is set.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.reviews import service, views
from shopbot.features.reviews.content_keys import MAX_RATING_SCALE, REVIEWS
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_REVIEW_ID = 1
SAMPLE_ORDER_NUMBER = 1
SAMPLE_MEMBER_ID = 100_000_000_000_000_001
SAMPLE_MEMBER_NAME = "[sample member]"
SAMPLE_COMMENT = "[sample review comment]"


def sample_long_comment(length: int) -> str:
    """A synthetic comment exactly ``length`` characters long (the longest the comment form accepts)."""
    unit = f"{SAMPLE_COMMENT} "
    return (unit * (length // len(unit) + 1))[:length]


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    settings = app.settings.reviews
    number = service.order_number_text(app, SAMPLE_ORDER_NUMBER)
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=REVIEWS, name=name, note=note, spec=spec))

    scale = settings.rating_scale
    if 1 <= scale <= MAX_RATING_SCALE:
        add(
            "prompt",
            "Review request sent after an order is fulfilled (direct message or order channel): rating menu and "
            "decline button.",
            lambda: views.prompt_view(ui, settings, review_id=SAMPLE_REVIEW_ID, order_number=number),
        )
        add(
            "published_with_comment",
            "Review posted in the reviews channel (highest rating, with a short sample comment).",
            lambda: views.published_view(
                ui, rating=scale, user_name=SAMPLE_MEMBER_NAME, order_number=number, comment=SAMPLE_COMMENT
            ),
        )
        add(
            "published_longest_comment",
            "Review posted in the reviews channel with the longest comment reviews.comment_max_length allows.",
            lambda: views.published_view(
                ui,
                rating=scale,
                user_name=SAMPLE_MEMBER_NAME,
                order_number=number,
                comment=sample_long_comment(settings.comment_max_length),
            ),
        )
        add(
            "published_without_comment",
            "Review posted in the reviews channel without a comment (lowest rating).",
            lambda: views.published_view(ui, rating=1, user_name=SAMPLE_MEMBER_NAME, order_number=number, comment=None),
        )
    add(
        "comment_form",
        "Comment form opened after a rating is picked (only when reviews.allow_comment is on).",
        lambda: views.comment_modal(ui, settings, review_id=SAMPLE_REVIEW_ID),
    )
    add(
        "thanks",
        "Replaces the review request after the review was submitted.",
        lambda: views.thanks_view(ui, order_number=number),
    )
    add(
        "declined",
        "Replaces the review request after the customer declined.",
        lambda: views.declined_view(ui, order_number=number),
    )
    add(
        "already_reviewed",
        "Private reply when the customer rates or comments again after submitting.",
        lambda: views.already_reviewed_view(ui, order_number=number),
    )
    add(
        "expired",
        "Replaces the review request when the order can no longer be reviewed.",
        lambda: views.expired_view(ui, order_number=number),
    )
    member = f"<@{SAMPLE_MEMBER_ID}>"
    line = app.op(
        "reviews.list.line",
        order_number=number,
        status=app.op("reviews.status.published"),
        rating=scale or 1,
        member=member,
        message=app.op("reviews.list.none"),
    )
    add(
        "staff_list",
        "Staff only (/review list): a full page of sample reviews.",
        lambda: views.operator_list(
            ui, app.op("reviews.list.title", count=25), [line] * 25, app.op("reviews.list.empty")
        ),
    )
    add(
        "staff_list_empty",
        "Staff only (/review list) when there are no reviews.",
        lambda: views.operator_list(ui, app.op("reviews.list.title", count=0), [], app.op("reviews.list.empty")),
    )
    return states
