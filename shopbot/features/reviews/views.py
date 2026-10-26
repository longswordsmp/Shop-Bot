"""Pure builders for every reviews message and form. No Discord calls: the runtime, the offline preview and the
limits checker all render through these functions.

Customer-facing wording comes only from content keys (missing required text raises ``MissingContent``; empty
optional text is omitted). Customer data (the comment, the reviewer's name) is escaped with
``discord.utils.escape_markdown`` and never pings: embeds do not mention, and only the explicit
``mention_user_ids`` of a spec are allowed to ping. The staff approval message is a business message (content keys
``reviews.approval.*``); staff command output, the staff reject form and staff replies use operator text (``op``).

A published review never identifies the member: no name, mention or avatar, only the rating, the text they wrote,
their photo and (for a review of a delivered order) the verification marker with the order number. The photo is
attached as ``review-<review_id>.<ext>``, never under the uploaded file's name.

Banners (closing the card at its bottom): the request, the published review and the #reviews intro panel show
``reviews``; a card with a photo shows the photo at its bottom and the banner above it (as an announcement with an
image). The request message is later replaced by thanks (``successful-confirmation``), thanks while approval is pending
(``pending-review``), declined (``general-response``) or expired (``cancellation``); every state sets its own banner,
so an edit swaps it. The approval message follows its state (``APPROVAL_BANNERS``). Customer notices: approved
``successful-confirmation``, rejected ``rejected-or-unsuccessful-submission``. The private stars menu of the Write a
review button shows ``reviews``. Replies to a review form (/review or the button): sent (``successful-confirmation``),
waiting for our team (``pending-review``), refusals that tell the member what to do instead (``general-response``) and
refused photos or descriptions (``rejected-or-unsuccessful-submission``). Staff replies choose theirs
(``operator_reply``/``operator_list``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import ReviewSettings
from shopbot.features.reviews import content_keys as k
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import ATTACHMENT_SCHEME, EMBED_FIELD_VALUE
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    EmbedSpec,
    FileSpec,
    FileUploadSpec,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui, chunk_lines

ROUTE = "rv"
COMMENT_INPUT_ID = "comment"
REASON_INPUT_ID = "reason"
DESCRIPTION_INPUT_ID = "description"
PHOTO_INPUT_ID = "photo"
STAR = "★"
NO_STAR = "☆"

OpText = Callable[..., str]


# -- custom ids --------------------------------------------------------------------------------------


def rate_id(review_id: int) -> str:
    return cid(ROUTE, "rate", review_id)


def decline_id(review_id: int) -> str:
    return cid(ROUTE, "decline", review_id)


def comment_id(review_id: int) -> str:
    return cid(ROUTE, "comment", review_id)


def approve_id(review_id: int) -> str:
    """Staff Approve button on the approval message."""
    return cid(ROUTE, "approve", review_id)


def reject_id(review_id: int) -> str:
    """Staff Reject button on the approval message (opens the reject form)."""
    return cid(ROUTE, "reject", review_id)


def reject_form_id(review_id: int) -> str:
    """The reject form (optional reason)."""
    return cid(ROUTE, "rejected", review_id)


def write_id(token: str) -> str:
    """The review form (its token names the open form: rating and photo)."""
    return cid(ROUTE, "write", token)


def start_id() -> str:
    """The Write a review button of the #reviews intro panel."""
    return cid(ROUTE, "start")


def pick_id() -> str:
    """The private stars menu the Write a review button answers with."""
    return cid(ROUTE, "pick")


# -- helpers -----------------------------------------------------------------------------------------


def _text(ui: Ui, key: str, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _description_embed(ui: Ui, key: str, *, tone: str, order_number: str) -> EmbedSpec:
    return ui.embed(description=_text(ui, key, order_number=order_number), tone=tone)


def escape(text: str) -> str:
    return escape_markdown(text)


def attachment_url(filename: str) -> str:
    return f"{ATTACHMENT_SCHEME}{filename}"


def stars(rating: int, scale: int) -> str:
    """The rating as a row of stars, e.g. ★★★★☆ for 4 of 5."""
    return STAR * rating + NO_STAR * max(0, scale - rating)


def rating_values(rating: int, scale: int) -> dict[str, str | int]:
    """The rating placeholders of the published review and the approval message."""
    return {"stars": stars(rating, scale), "rating": rating, "scale": scale}


# -- rating request ------------------------------------------------------------------------------------


def rating_options(ui: Ui, scale: int) -> tuple[SelectOptionSpec, ...]:
    """One option per rating, highest first. Every label is required; description and emoji are optional."""
    return tuple(
        SelectOptionSpec(
            label=_text(ui, k.rating_key(rating, "label")),
            value=str(rating),
            description=ui.content.get(k.rating_key(rating, "description")),
            emoji=ui.content.render(k.rating_key(rating, "emoji")),
        )
        for rating in range(scale, 0, -1)
    )


def prompt_view(ui: Ui, settings: ReviewSettings, *, review_id: int, order_number: str) -> MessageSpec:
    """The review request: text, the rating selection menu and the decline button."""
    values: dict[str, str | int] = {"order_number": order_number}
    description = _text(ui, k.PROMPT_DESCRIPTION, **values)
    title = ui.text(k.PROMPT_TITLE, **values)
    embed = ui.embed(title=title, description=description, tone="primary")
    select = SelectSpec(
        custom_id=rate_id(review_id),
        options=rating_options(ui, settings.rating_scale),
        placeholder=_text(ui, k.PROMPT_SELECT_PLACEHOLDER),
    )
    decline = ButtonSpec(label=_text(ui, k.BUTTON_DECLINE), custom_id=decline_id(review_id), style="secondary")
    return MessageSpec(embeds=(embed,), rows=rows_of(select, decline), banner=ui.banner("reviews"))


def comment_modal(ui: Ui, settings: ReviewSettings, *, review_id: int) -> ModalSpec:
    """The comment form opened after a rating was picked (only when reviews.allow_comment is on)."""
    comment = TextInputSpec(
        custom_id=COMMENT_INPUT_ID,
        label=_text(ui, k.COMMENT_LABEL),
        style="paragraph",
        description=ui.content.get(k.COMMENT_DESCRIPTION),
        placeholder=ui.content.get(k.COMMENT_PLACEHOLDER),
        required=settings.comment_required,
        max_length=settings.comment_max_length,
    )
    return ModalSpec(custom_id=comment_id(review_id), title=_text(ui, k.COMMENT_TITLE), inputs=(comment,))


# -- published review ----------------------------------------------------------------------------------


def rating_display(ui: Ui, rating: int) -> str:
    """The rating as shown on a published review: its display text, or else its option label."""
    text = ui.content.get(k.rating_key(rating, "display"))
    if text is None:
        raise MissingContent(k.rating_key(rating, "label"))
    return text


def verified_field(ui: Ui, *, order_number: str) -> EmbedField | None:
    """The optional verification marker of a review of a delivered order: shown when both its heading and its text
    are configured (content_problems reports one without the other)."""
    name = ui.text(k.PUBLISHED_VERIFIED_FIELD)
    value = ui.text(k.PUBLISHED_VERIFIED_VALUE, order_number=order_number)
    if not name or not value:
        return None
    return EmbedField(name=name, value=value, inline=True)


def quoted(text: str) -> str:
    """Customer text as a quote block (escaped first: it is customer data)."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in escape(text).splitlines())


def published_view(
    ui: Ui,
    *,
    rating: int,
    scale: int,
    order_number: str | None,
    comment: str | None,
    image: FileSpec | None = None,
) -> MessageSpec:
    """The review posted in the reviews channel: title (the rating), the approved comment, the optional rating field,
    the verification marker of a review of a delivered order (``order_number``; None: an unverified review, no
    marker) and the photo. Nothing in it names the member. ``comment`` is raw customer data (escaped here); an empty
    comment is omitted."""
    title = _text(ui, k.PUBLISHED_TITLE, **rating_values(rating, scale))
    fields: list[EmbedField] = []
    heading = ui.text(k.PUBLISHED_RATING_FIELD)
    if heading:
        fields.append(EmbedField(name=heading, value=rating_display(ui, rating), inline=True))
    verified = verified_field(ui, order_number=order_number) if order_number is not None else None
    if verified is not None:
        fields.append(verified)
    description = quoted(comment) if comment else None
    embed = ui.embed(
        title=title,
        description=description,
        fields=fields,
        tone="primary",
        image_url=attachment_url(image.filename) if image is not None else None,
        timestamp=True,
    )
    return MessageSpec(embeds=(embed,), files=(image,) if image is not None else None, banner=ui.banner("reviews"))


# -- staff approval message (business message: content keys) ---------------------------------------------

_APPROVAL_TONES = {
    k.STATE_PENDING: "warning",
    k.STATE_APPROVED: "success",
    k.STATE_REJECTED: "danger",
    k.STATE_REMOVED: "neutral",
}
# The approval message is one message whose banner follows its state.
APPROVAL_BANNERS: dict[str, BannerId] = {
    k.STATE_PENDING: "pending-review",
    k.STATE_APPROVED: "successful-confirmation",
    k.STATE_REJECTED: "rejected-or-unsuccessful-submission",
    k.STATE_REMOVED: "cancellation",
}


def user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


def approval_view(
    ui: Ui,
    *,
    review_id: int,
    state: str,
    rating: int,
    scale: int,
    user_name: str,
    user_id: int,
    order_number: str | None,
    comment: str | None,
    staff_id: int | None = None,
    reason: str | None = None,
    image: FileSpec | None = None,
) -> MessageSpec:
    """The staff message in reviews.approval_channel for one submitted review: title, the customer's comment
    (escaped, omitted when empty), rating, order (``order_number``; None: an unverified review), customer and status
    fields, the photo, and while ``state`` is pending the Approve / Reject buttons. A rejection reason (staff text,
    shown as written) is shown when its heading is configured. Mentions in embeds never ping. The message always
    manages its attachments (``files``), so an edit keeps the photo while its bytes are stored and removes it once
    they were released."""
    if state not in k.APPROVAL_STATES:
        raise ValueError(state)
    title = _text(
        ui, k.APPROVAL_TITLE, user_name=escape(user_name), review_number=review_id, **rating_values(rating, scale)
    )
    staff = user_mention(staff_id) if staff_id else ""
    # "#12": next to the review number in the title, a bare order number could be taken for it.
    order = f"#{order_number}" if order_number is not None else _text(ui, k.APPROVAL_NO_ORDER)
    fields = [
        EmbedField(name=_text(ui, k.APPROVAL_RATING_FIELD), value=rating_display(ui, rating), inline=True),
        EmbedField(name=_text(ui, k.APPROVAL_ORDER_FIELD), value=order, inline=True),
        EmbedField(name=_text(ui, k.APPROVAL_CUSTOMER_FIELD), value=user_mention(user_id), inline=True),
        EmbedField(
            name=_text(ui, k.APPROVAL_STATUS_FIELD), value=_text(ui, k.approval_status_key(state), staff_mention=staff)
        ),
    ]
    if state == k.STATE_REJECTED and reason:
        reason_field = ui.field(k.APPROVAL_REASON_FIELD, reason)
        if reason_field is not None:
            fields.append(reason_field)
    rows: tuple[tuple[ButtonSpec, ...], ...] = ()
    if state == k.STATE_PENDING:
        approve = ButtonSpec(
            label=_text(ui, k.APPROVAL_BUTTON_APPROVE), custom_id=approve_id(review_id), style="success"
        )
        reject = ButtonSpec(label=_text(ui, k.APPROVAL_BUTTON_REJECT), custom_id=reject_id(review_id), style="danger")
        rows = ((approve, reject),)
    embed = ui.embed(
        title=title,
        description=escape(comment) if comment else None,
        fields=fields,
        tone=_APPROVAL_TONES[state],
        image_url=attachment_url(image.filename) if image is not None else None,
        timestamp=True,
    )
    files = (image,) if image is not None else ()
    return MessageSpec(embeds=(embed,), rows=rows, files=files, banner=ui.banner(APPROVAL_BANNERS[state]))


def reject_modal(op: OpText, *, review_id: int, max_length: int) -> ModalSpec:
    """Staff-only reject form with an optional reason (operator text)."""
    reason = TextInputSpec(
        custom_id=REASON_INPUT_ID,
        label=op("reviews.form.reject_reason"),
        style="paragraph",
        description=op("reviews.form.reject_reason_help"),
        required=False,
        max_length=max_length,
    )
    return ModalSpec(
        custom_id=reject_form_id(review_id),
        title=op("reviews.form.reject_title", review_number=review_id),
        inputs=(reason,),
    )


# -- private decision notices to the customer ----------------------------------------------------------------


def approved_notice(ui: Ui, *, review_link: str) -> MessageSpec | None:
    """'Your review was approved' (after publication). None when its text is not configured (optional)."""
    description = ui.text(k.notice_key(k.STATE_APPROVED, "description"), review_link=review_link)
    if description is None:
        return None
    title = ui.text(k.notice_key(k.STATE_APPROVED, "title"))
    embed = ui.embed(title=title, description=description, tone="success", timestamp=True)
    return MessageSpec(embeds=(embed,), banner=ui.banner("successful-confirmation"))


def rejected_notice(ui: Ui, *, reason: str | None) -> MessageSpec | None:
    """'Your review was not approved', with the staff reason when its heading is configured. None when its text is
    not configured (optional)."""
    description = ui.text(k.notice_key(k.STATE_REJECTED, "description"))
    if description is None:
        return None
    title = ui.text(k.notice_key(k.STATE_REJECTED, "title"))
    reason_field = ui.field(k.NOTICE_REJECTED_REASON_FIELD, reason) if reason else None
    fields = (reason_field,) if reason_field is not None else ()
    embed = ui.embed(title=title, description=description, fields=fields, tone="warning", timestamp=True)
    return MessageSpec(embeds=(embed,), banner=ui.banner("rejected-or-unsuccessful-submission"))


# -- replies to the customer -----------------------------------------------------------------------------


def thanks_view(ui: Ui, *, order_number: str, pending_approval: bool = False) -> MessageSpec:
    """Replaces the request after a submitted review (no components). While the review waits for staff approval
    the 'pending' text is shown (it falls back to the normal thank-you text)."""
    key = k.THANKS_PENDING if pending_approval else k.THANKS
    banner = ui.banner("pending-review" if pending_approval else "successful-confirmation")
    return MessageSpec(embeds=(_description_embed(ui, key, tone="success", order_number=order_number),), banner=banner)


def declined_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Replaces the request after the customer declined (no components)."""
    embed = _description_embed(ui, k.DECLINED, tone="neutral", order_number=order_number)
    return MessageSpec(embeds=(embed,), banner=ui.banner("general-response"))


def already_reviewed_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Private reply to a second rating/comment on a finished review."""
    embed = _description_embed(ui, k.ALREADY_REVIEWED, tone="info", order_number=order_number)
    return MessageSpec(embeds=(embed,), banner=ui.banner("general-response"))


def expired_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Replaces the request when the order can no longer be reviewed (no components)."""
    embed = _description_embed(ui, k.EXPIRED, tone="warning", order_number=order_number)
    return MessageSpec(embeds=(embed,), banner=ui.banner("cancellation"))


# -- writing a review (/review or the Write a review button): the stars menu, the form and its replies ------------


def pick_view(ui: Ui, settings: ReviewSettings) -> MessageSpec:
    """Private answer to the Write a review button: a short text and the stars menu that opens the review form."""
    embed = ui.embed(description=_text(ui, k.PICK_DESCRIPTION), tone="primary")
    select = SelectSpec(
        custom_id=pick_id(),
        options=rating_options(ui, settings.rating_scale),
        placeholder=_text(ui, k.PICK_PLACEHOLDER),
    )
    return MessageSpec(embeds=(embed,), rows=rows_of(select), banner=ui.banner("reviews"))


def write_modal(ui: Ui, settings: ReviewSettings, *, token: str, photo_upload: bool = False) -> ModalSpec:
    """The private review form: the review's description (required) and, with ``photo_upload`` (no photo was added
    with /review), an optional photo upload (one file)."""
    description = TextInputSpec(
        custom_id=DESCRIPTION_INPUT_ID,
        label=_text(ui, k.WRITE_LABEL),
        style="paragraph",
        description=ui.content.get(k.WRITE_DESCRIPTION),
        placeholder=ui.content.get(k.WRITE_PLACEHOLDER),
        required=True,
        max_length=settings.comment_max_length,
    )
    uploads: tuple[FileUploadSpec, ...] = ()
    if photo_upload:
        photo = FileUploadSpec(
            custom_id=PHOTO_INPUT_ID,
            label=_text(ui, k.WRITE_PHOTO_LABEL),
            description=ui.content.get(k.WRITE_PHOTO_DESCRIPTION),
            required=False,
        )
        uploads = (photo,)
    return ModalSpec(custom_id=write_id(token), title=_text(ui, k.WRITE_TITLE), inputs=(description,), uploads=uploads)


def _reply(ui: Ui, key: str, *, tone: str, banner: BannerId, **values: str | int) -> MessageSpec:
    embed = ui.embed(description=_text(ui, key, **values), tone=tone)
    return MessageSpec(embeds=(embed,), banner=ui.banner(banner))


def written_view(ui: Ui, *, pending: bool) -> MessageSpec:
    """Private reply after /review: published right away, or waiting for our team's check (``pending``)."""
    if pending:
        return _reply(ui, k.WRITTEN_PENDING, tone="success", banner="pending-review")
    return _reply(ui, k.WRITTEN, tone="success", banner="successful-confirmation")


def cooldown_view(ui: Ui, *, until: int | None) -> MessageSpec:
    """/review refused: the member's unverified review limit is reached (``until`` None: one ever)."""
    if until is None:
        return _reply(ui, k.LIMIT, tone="info", banner="general-response")
    return _reply(ui, k.COOLDOWN, tone="info", banner="general-response", date=f"<t:{until}:D>")


def no_order_view(ui: Ui) -> MessageSpec:
    """/review refused: no delivered order to review, and unverified reviews are off."""
    return _reply(ui, k.NO_ORDER, tone="info", banner="general-response")


def image_problem_view(ui: Ui, *, limit_mb: str, text: str | None = None) -> MessageSpec:
    """The photo cannot be used. ``text``: the description of a sent form (a photo uploaded in the form is checked
    only then), shown back under its optional heading so it is not lost; left out when it does not fit a field."""
    written = escape(text) if text else None
    field = ui.field(k.IMAGE_PROBLEM_TEXT_FIELD, written) if written and len(written) <= EMBED_FIELD_VALUE else None
    embed = ui.embed(
        description=_text(ui, k.IMAGE_PROBLEM, limit=limit_mb), fields=(field,) if field else (), tone="warning"
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("rejected-or-unsuccessful-submission"))


def too_long_view(ui: Ui, *, max_length: int) -> MessageSpec:
    """/review refused: the description cannot be shown within Discord's limits."""
    return _reply(ui, k.TOO_LONG, tone="warning", banner="rejected-or-unsuccessful-submission", max=max_length)


def form_expired_view(ui: Ui) -> MessageSpec:
    """The review form expired or was already sent."""
    return _reply(ui, k.FORM_EXPIRED, tone="warning", banner="general-response")


# -- the #reviews intro panel ------------------------------------------------------------------------------------


def intro_panel(ui: Ui, settings: ReviewSettings) -> MessageSpec:
    """The panel at the top of the reviews channel, with the Write a review button (members cannot type in the
    channel, so /review cannot be run there; the button works anywhere). Its last line about our team's check is
    chosen from the settings (``k.intro_checked_key``), so it is true whatever they are: every review is checked
    (require_approval), reviews with a photo and/or reviews without a delivered order are (photo_requires_approval,
    allow_unverified), or none (no line).

    No timestamp: the panel is compared with its published copy by content hash, so /setup edits it only when its
    text actually changed."""
    description = _text(ui, k.INTRO_DESCRIPTION)
    checked = k.intro_checked_key(settings)
    if checked is not None:
        description = f"{description}\n\n{_text(ui, checked)}"
    embed = ui.embed(title=_text(ui, k.INTRO_TITLE), description=description, tone="primary", panel=True)
    start = ButtonSpec(label=_text(ui, k.INTRO_BUTTON), custom_id=start_id(), style="primary")
    return MessageSpec(embeds=(embed,), rows=((start,),), banner=ui.banner("reviews"))


# -- staff (operator text) ---------------------------------------------------------------------------------


def operator_reply(ui: Ui, title: str, lines: Sequence[str], *, tone: str = "info", banner_id: BannerId) -> MessageSpec:
    """A staff reply (operator text) with the banner that fits its outcome (``service.StaffResult.banner``)."""
    return MessageSpec(
        embeds=(ui.operator_embed(title, "\n".join(lines) or None, tone=tone),), banner=ui.banner(banner_id)
    )


def operator_list(ui: Ui, title: str, lines: Sequence[str], empty: str) -> MessageSpec:
    """Staff list output split into embeds that fit Discord's limits (never cut), with the ``staff-dashboard``
    banner (the banner is an attachment, not one of the 10 embeds)."""
    if not lines:
        return operator_reply(ui, title, [empty], banner_id="staff-dashboard")
    blocks = chunk_lines(lines, 4000)
    embeds = [ui.operator_embed(title, block) for block in blocks]
    # 10 embeds per message and 6000 characters across them: keep the first blocks that fit.
    kept: list[EmbedSpec] = []
    total = 0
    for embed in embeds[:10]:
        if kept and total + embed.text_length() > 6000:
            break
        kept.append(embed)
        total += embed.text_length()
    return MessageSpec(embeds=tuple(kept), banner=ui.banner("staff-dashboard"))
