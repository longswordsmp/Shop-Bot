"""Pure builders for every reviews message and form. No Discord calls: the runtime, the offline preview and the
limits checker all render through these functions.

Customer-facing wording comes only from content keys (missing required text raises ``MissingContent``; empty
optional text is omitted). Customer data (the comment, the reviewer's name) is escaped with
``discord.utils.escape_markdown`` and never pings: embeds do not mention, and only the explicit
``mention_user_ids`` of a spec are allowed to ping. Staff command output uses operator text (``op``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from discord.utils import escape_markdown

from shopbot.config.content import MissingContent
from shopbot.config.settings import ReviewSettings
from shopbot.features.reviews import content_keys as k
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedField,
    EmbedSpec,
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

OpText = Callable[..., str]


# -- custom ids --------------------------------------------------------------------------------------


def rate_id(review_id: int) -> str:
    return cid(ROUTE, "rate", review_id)


def decline_id(review_id: int) -> str:
    return cid(ROUTE, "decline", review_id)


def comment_id(review_id: int) -> str:
    return cid(ROUTE, "comment", review_id)


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


# -- rating request ------------------------------------------------------------------------------------


def rating_options(ui: Ui, scale: int) -> tuple[SelectOptionSpec, ...]:
    """One option per rating, highest first. Every label is required; description and emoji are optional."""
    return tuple(
        SelectOptionSpec(
            label=_text(ui, k.rating_key(rating, "label")),
            value=str(rating),
            description=ui.content.get(k.rating_key(rating, "description")),
            emoji=ui.content.get(k.rating_key(rating, "emoji")),
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
    return MessageSpec(embeds=(embed,), rows=rows_of(select, decline))


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


def published_view(ui: Ui, *, rating: int, user_name: str, order_number: str, comment: str | None) -> MessageSpec:
    """The review posted in the reviews channel. ``user_name`` and ``comment`` are raw customer data (escaped here);
    an empty comment is omitted."""
    title = _text(ui, k.PUBLISHED_TITLE, rating=rating, user_name=escape(user_name), order_number=order_number)
    field = EmbedField(name=_text(ui, k.PUBLISHED_RATING_FIELD), value=rating_display(ui, rating))
    description = escape(comment) if comment else None
    embed = ui.embed(title=title, description=description, fields=(field,), tone="primary", timestamp=True)
    return MessageSpec(embeds=(embed,))


# -- replies to the customer -----------------------------------------------------------------------------


def thanks_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Replaces the request after a submitted review (no components)."""
    return MessageSpec(embeds=(_description_embed(ui, k.THANKS, tone="success", order_number=order_number),))


def declined_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Replaces the request after the customer declined (no components)."""
    return MessageSpec(embeds=(_description_embed(ui, k.DECLINED, tone="neutral", order_number=order_number),))


def already_reviewed_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Private reply to a second rating/comment on a finished review."""
    return MessageSpec(embeds=(_description_embed(ui, k.ALREADY_REVIEWED, tone="info", order_number=order_number),))


def expired_view(ui: Ui, *, order_number: str) -> MessageSpec:
    """Replaces the request when the order can no longer be reviewed (no components)."""
    return MessageSpec(embeds=(_description_embed(ui, k.EXPIRED, tone="warning", order_number=order_number),))


# -- staff (operator text) ---------------------------------------------------------------------------------


def operator_reply(ui: Ui, title: str, lines: Sequence[str], *, tone: str = "info") -> MessageSpec:
    return MessageSpec(embeds=(ui.operator_embed(title, "\n".join(lines) or None, tone=tone),))


def operator_list(ui: Ui, title: str, lines: Sequence[str], empty: str) -> MessageSpec:
    """Staff list output split into embeds that fit Discord's limits (never cut)."""
    if not lines:
        return operator_reply(ui, title, [empty])
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
    return MessageSpec(embeds=tuple(kept))
