"""Preview: every reviews UI state, built by the runtime view functions from synthetic sample data.

Order numbers, member names, comments, reasons and the sample photo exist only in the preview and are obviously fake.
A state whose text is not configured yet is skipped (the content store records the missing keys for the checker).
States that depend on the rating options are built only when ``reviews.rating_scale`` is set. The staff approval
states are always built (so their limits are checked before approval is needed).
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import images, repo, service, views
from shopbot.features.reviews.content_keys import MAX_RATING_SCALE, REVIEWS
from shopbot.ui.spec import FileSpec, MessageSpec, ModalSpec, PreviewState

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
SAMPLE_TOKEN = "0" * 24
SAMPLE_UNTIL = 1_767_225_600  # 2026-01-01 00:00 UTC


def sample_long_comment(length: int) -> str:
    """A synthetic comment exactly ``length`` characters long (the longest the comment form accepts)."""
    unit = f"{SAMPLE_COMMENT} "
    return (unit * (length // len(unit) + 1))[:length]


def sample_long_reason() -> str:
    """A synthetic rejection reason as long as the reject form accepts."""
    unit = f"{SAMPLE_REASON} "
    return (unit * (repo.REASON_MAX // len(unit) + 1))[: repo.REASON_MAX]


def sample_photo_png(width: int = 320, height: int = 200) -> bytes:
    """A synthetic sample photo: a plain blue-to-grey gradient PNG (generated, never a real picture)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    rows = b"".join(b"\x00" + bytes((90 + y // 4, 120 + y // 5, 200 - y // 4)) * width for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def sample_photo(review_id: int = SAMPLE_REVIEW_ID) -> FileSpec:
    """The sample photo attached as a review's photo is (``review-<id>.png``)."""
    return FileSpec.of(images.attachment_name(review_id, "image/png"), sample_photo_png(), "image/png")


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
            "start_menu",
            "Private answer to the Write a review button of the #reviews intro panel: the stars menu that opens the "
            "review form (members cannot type /review in the read-only reviews channel).",
            lambda: views.pick_view(ui, settings),
        )
        add(
            "published_with_comment",
            "Review posted in the reviews channel (highest rating, with a short sample comment, and the verification "
            "marker of a delivered order when reviews.published.verified_field/verified_value are configured). It "
            "never shows the member's name. With reviews.require_approval it is posted only after a staff member "
            "approved it.",
            lambda: views.published_view(ui, rating=scale, scale=scale, order_number=number, comment=SAMPLE_COMMENT),
        )
        add(
            "published_with_photo",
            "Review written with /review for a delivered order, with its photo (the photo closes the card; the banner "
            "shows above it).",
            lambda: views.published_view(
                ui,
                rating=scale - 1 or 1,
                scale=scale,
                order_number=number,
                comment=SAMPLE_COMMENT,
                image=sample_photo(),
            ),
        )
        add(
            "published_unverified",
            "Unverified review written with /review without a delivered order (after a staff member approved it): no "
            "verification marker, never the member's name.",
            lambda: views.published_view(ui, rating=scale, scale=scale, order_number=None, comment=SAMPLE_COMMENT),
        )
        add(
            "published_longest_comment",
            "Review posted in the reviews channel with the longest comment reviews.comment_max_length allows.",
            lambda: views.published_view(
                ui,
                rating=scale,
                scale=scale,
                order_number=number,
                comment=sample_long_comment(settings.comment_max_length),
            ),
        )
        add(
            "published_without_comment",
            "Review posted in the reviews channel without a comment (lowest rating).",
            lambda: views.published_view(ui, rating=1, scale=scale, order_number=number, comment=None),
        )

        def approval(
            state: str,
            *,
            comment: str | None = SAMPLE_COMMENT,
            reason: str | None = None,
            order_number: str | None = number,
            image: FileSpec | None = None,
        ) -> MessageSpec:
            return views.approval_view(
                ui,
                review_id=SAMPLE_REVIEW_ID,
                state=state,
                rating=scale,
                scale=scale,
                user_name=SAMPLE_MEMBER_NAME,
                user_id=SAMPLE_MEMBER_ID,
                order_number=order_number,
                comment=comment,
                staff_id=None if state == k.STATE_PENDING else SAMPLE_STAFF_ID,
                reason=reason,
                image=image,
            )

        staff_note = (
            "Staff only, in reviews.approval_channel (reviews.require_approval, every unverified review, and reviews "
            "with a photo while reviews.photo_requires_approval is on): "
        )
        add(
            "approval_request",
            f"{staff_note}a submitted review waiting for a decision, with the Approve and Reject buttons.",
            lambda: approval(k.STATE_PENDING),
        )
        add(
            "approval_request_with_photo",
            f"{staff_note}an unverified review written with /review, with its photo.",
            lambda: approval(k.STATE_PENDING, order_number=None, image=sample_photo()),
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
            f"{staff_note}the approval message after the review was removed with /reviews remove.",
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
        "write_form",
        "The private review form after stars were picked in the Write a review menu (or /review without its photo "
        "option): the description and an optional photo upload.",
        lambda: views.write_modal(ui, settings, token=SAMPLE_TOKEN, photo_upload=True),
    )
    add(
        "write_form_photo_added",
        "/review with its photo option: the private form for the description only (the photo is already added).",
        lambda: views.write_modal(ui, settings, token=SAMPLE_TOKEN),
    )
    add(
        "written",
        "/review: private reply when the review is published right away (a delivered order, reviews.require_approval "
        "off, and no photo while reviews.photo_requires_approval is on).",
        lambda: views.written_view(ui, pending=False),
    )
    add(
        "written_pending",
        "/review: private reply when the review waits for our team's check (every unverified review, a review with a "
        "photo while reviews.photo_requires_approval is on, and every review while reviews.require_approval is on).",
        lambda: views.written_view(ui, pending=True),
    )
    add(
        "refused_cooldown",
        "/review refused: no delivered order to review, and the member wrote an unverified review in the last "
        "reviews.unverified_cooldown_days days.",
        lambda: views.cooldown_view(ui, until=SAMPLE_UNTIL),
    )
    add(
        "refused_limit",
        "/review refused: no delivered order to review, and the member already wrote their one unverified review "
        "(reviews.unverified_cooldown_days = 0).",
        lambda: views.cooldown_view(ui, until=None),
    )
    add(
        "refused_no_order",
        "/review refused: no delivered order to review while reviews.allow_unverified is off.",
        lambda: views.no_order_view(ui),
    )
    add(
        "refused_image",
        "/review refused before the form opens: its photo is not a PNG, JPG, GIF or WebP picture, or too large.",
        lambda: views.image_problem_view(ui, limit_mb=images.megabytes(service.photo_limit(app))),
    )
    add(
        "refused_image_after_form",
        "A sent review form refused because the photo uploaded in it (or the /review photo, when it could not be "
        "downloaded) cannot be used: the member's description is shown back so it is not lost (the longest one).",
        lambda: views.image_problem_view(
            ui,
            limit_mb=images.megabytes(service.photo_limit(app)),
            text=sample_long_comment(settings.comment_max_length),
        ),
    )
    add(
        "refused_too_long",
        "/review refused: the description cannot be shown within Discord's limits.",
        lambda: views.too_long_view(ui, max_length=settings.comment_max_length),
    )
    add(
        "form_expired",
        "/review: private reply when the form is sent after it expired or a second time.",
        lambda: views.form_expired_view(ui),
    )
    checked = {
        k.INTRO_CHECKED_ALL: "the line about every review being checked (reviews.require_approval on)",
        k.INTRO_CHECKED_PHOTOS_UNVERIFIED: (
            "the line about reviews with a photo and reviews without a delivered order being checked "
            "(reviews.photo_requires_approval and reviews.allow_unverified on)"
        ),
        k.INTRO_CHECKED_UNVERIFIED: "the line about unverified reviews being checked (reviews.allow_unverified on)",
        k.INTRO_CHECKED_PHOTOS: "the line about photos being checked (reviews.photo_requires_approval on)",
        None: "no line about checks (no review waits for one)",
    }[k.intro_checked_key(settings)]
    add(
        "intro_panel",
        f"The intro panel /setup keeps at the top of the reviews channel, with {checked}, and the Write a review "
        "button (it works in the read-only channel, where members cannot type /review).",
        lambda: views.intro_panel(ui, settings),
    )
    add(
        "notice_approved",
        "Private notice to the customer once a staff-approved review is published (optional; direct message or "
        "the order channel).",
        lambda: views.approved_notice(ui, review_link=SAMPLE_REVIEW_LINK),
    )
    add(
        "notice_rejected",
        "Private notice to the customer when a staff member rejected the review, with the longest reason (optional; "
        "direct message or the order channel).",
        lambda: views.rejected_notice(ui, reason=sample_long_reason()),
    )
    add(
        "staff_reject_form",
        "Staff only: the reject form opened by the Reject button of an approval message (optional reason).",
        lambda: views.reject_modal(app.op, review_id=SAMPLE_REVIEW_ID, max_length=repo.REASON_MAX),
    )
    label = app.op("reviews.ref.order", review_number=SAMPLE_REVIEW_ID, order_number=number)
    add(
        "staff_decision_reply",
        "Staff only: the private reply after approving a review.",
        lambda: views.operator_reply(
            ui,
            app.op("reviews.decision.title"),
            [app.op("reviews.decision.approved", review=label), app.op("reviews.decision.published")],
            tone="success",
            banner_id=service.StaffResult([], "success").banner,
        ),
    )
    member = f"<@{SAMPLE_MEMBER_ID}>"
    line = app.op(
        "reviews.list.line",
        review_number=SAMPLE_REVIEW_ID,
        order=app.op("reviews.list.order", order_number=number),
        status=app.op("reviews.status.published"),
        rating=scale or 1,
        member=member,
        message=app.op("reviews.list.none"),
    )
    add(
        "staff_list",
        "Staff only (/reviews list): a full page of sample reviews.",
        lambda: views.operator_list(
            ui, app.op("reviews.list.title", count=25), [line] * 25, app.op("reviews.list.empty")
        ),
    )
    add(
        "staff_list_empty",
        "Staff only (/reviews list) when there are no reviews.",
        lambda: views.operator_list(ui, app.op("reviews.list.title", count=0), [], app.op("reviews.list.empty")),
    )
    return states
