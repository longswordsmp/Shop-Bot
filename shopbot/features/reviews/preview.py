"""Preview: every reviews UI state, built by the runtime view functions from synthetic sample data.

Order numbers, member names, comments and reasons exist only in the preview and are obviously fake. A state whose text
is not configured yet is skipped (the content store records the missing keys for the checker). States that depend on
the rating options are built only when ``reviews.rating_scale`` is set. The staff approval states are always built (so
their limits are checked before ``reviews.require_approval`` is switched on).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, service, views
from shopbot.features.reviews.content_keys import MAX_RATING_SCALE, REVIEWS
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_REVIEW_ID = 1
SAMPLE_ORDER_NUMBER = 1
SAMPLE_MEMBER_ID = 100_000_000_000_000_001
SAMPLE_STAFF_ID = 100_000_000_000_000_002
SAMPLE_MEMBER_NAME = "[sample member]"
SAMPLE_COMMENT = "[sample review comment]"
SAMPLE_REASON = "[sample rejection reason]"
SAMPLE_REVIEW_LINK = "https://discord.com/channels/0/0/0"


def sample_long_comment(length: int) -> str:
    """A synthetic comment exactly ``length`` characters long (the longest the comment form accepts)."""
    unit = f"{SAMPLE_COMMENT} "
    return (unit * (length // len(unit) + 1))[:length]


def sample_long_reason() -> str:
    """A synthetic rejection reason as long as the reject form accepts."""
    unit = f"{SAMPLE_REASON} "
    return (unit * (repo.REASON_MAX // len(unit) + 1))[: repo.REASON_MAX]


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    settings = app.settings.reviews
    number = service.order_number_text(app, SAMPLE_ORDER_NUMBER)
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec | None]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        if spec is not None:
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
            "Review posted in the reviews channel (highest rating, with a short sample comment, and the verification "
            "marker when reviews.published.verified_field/verified_value are configured). With "
            "reviews.require_approval it is posted only after a staff member approved it.",
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

        def approval(state: str, *, comment: str | None = SAMPLE_COMMENT, reason: str | None = None) -> MessageSpec:
            return views.approval_view(
                ui,
                review_id=SAMPLE_REVIEW_ID,
                state=state,
                rating=scale,
                user_name=SAMPLE_MEMBER_NAME,
                user_id=SAMPLE_MEMBER_ID,
                order_number=number,
                comment=comment,
                staff_id=None if state == k.STATE_PENDING else SAMPLE_STAFF_ID,
                reason=reason,
            )

        staff_note = "Staff only, in reviews.approval_channel (when reviews.require_approval is on): "
        add(
            "approval_request",
            f"{staff_note}a submitted review waiting for a decision, with the Approve and Reject buttons.",
            lambda: approval(k.STATE_PENDING),
        )
        add(
            "approval_request_longest_comment",
            f"{staff_note}the approval request with the longest comment reviews.comment_max_length allows.",
            lambda: approval(k.STATE_PENDING, comment=sample_long_comment(settings.comment_max_length)),
        )
        add(
            "approval_request_without_comment",
            f"{staff_note}the approval request of a review submitted without a comment.",
            lambda: approval(k.STATE_PENDING, comment=None),
        )
        add(
            "approval_approved",
            f"{staff_note}the approval message after a staff member approved the review (buttons removed).",
            lambda: approval(k.STATE_APPROVED),
        )
        add(
            "approval_rejected",
            f"{staff_note}the approval message after a staff member rejected the review, with the longest reason.",
            lambda: approval(
                k.STATE_REJECTED,
                comment=sample_long_comment(settings.comment_max_length),
                reason=sample_long_reason(),
            ),
        )
        add(
            "approval_removed",
            f"{staff_note}the approval message after the review was removed with /review remove.",
            lambda: approval(k.STATE_REMOVED),
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
        "thanks_pending_approval",
        "Replaces the review request after the review was submitted while reviews.require_approval is on.",
        lambda: views.thanks_view(ui, order_number=number, pending_approval=True),
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
    add(
        "notice_approved",
        "Private notice to the customer once a staff-approved review is published (optional; direct message or "
        "the order channel).",
        lambda: views.approved_notice(ui, order_number=number, review_link=SAMPLE_REVIEW_LINK),
    )
    add(
        "notice_rejected",
        "Private notice to the customer when a staff member rejected the review, with the longest reason (optional; "
        "direct message or the order channel).",
        lambda: views.rejected_notice(ui, order_number=number, reason=sample_long_reason()),
    )
    add(
        "staff_reject_form",
        "Staff only: the reject form opened by the Reject button of an approval message (optional reason).",
        lambda: views.reject_modal(app.op, review_id=SAMPLE_REVIEW_ID, order_number=number, max_length=repo.REASON_MAX),
    )
    add(
        "staff_decision_reply",
        "Staff only: the private reply after approving a review.",
        lambda: views.operator_reply(
            ui,
            app.op("reviews.decision.title"),
            [app.op("reviews.decision.approved", order_number=number), app.op("reviews.decision.published")],
            tone="success",
            banner_id=service.StaffResult([], "success").banner,
        ),
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
