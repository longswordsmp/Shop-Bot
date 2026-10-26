"""The ``/reviews`` slash command group (staff) and the ``/review`` command (every member).

``/reviews`` - visibility: ``default_permissions(manage_messages=True)`` hides the group from members without Manage
Messages (a convenience only). Every invocation is authorized by ``require_staff`` (``permissions.staff_roles``, the
checkout staff roles, or an admin). Replies are operator text and always ephemeral.

- ``list [status]``: the 25 most recently changed reviews.
- ``remove [order_number] [review]``: deletes the published review message and closes the review (status declined);
  a review request that was not sent yet is cancelled.
- ``resend <order_number>``: sends the review request again while it is unanswered.
- ``approve [order_number] [review]`` / ``reject [order_number] [review] [reason]``: the staff decision on a review
  waiting for approval, the same as the Approve / Reject buttons of its approval message (for example when that
  message was deleted). Exactly once: a second decision changes nothing.

``remove``, ``approve`` and ``reject`` name the review by its order's number or by its review number (shown in
``/reviews list`` and on the approval message); a review written without an order has only the review number.

``/review`` - every member of the server (no default permissions; guild only): ``stars`` (required; the choices are
the rating labels ``reviews.rating.<n>.label`` with a row of stars, highest first) and ``photo`` (an optional picture;
the callback's ``image`` parameter, shown as "photo" because every customer text calls it a photo).
It opens the private form for the description, or answers privately why a review cannot be written now. Its
descriptions and choices are customer-facing text: the command is registered only when ``features.reviews`` is on and
they are configured and valid; Discord receives them when the bot starts (``/reload`` does not re-register commands:
a restart applies a change).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, reply_content_error, require_staff
from shopbot.config.content import MissingContent, validate_value
from shopbot.features import core_keys as core
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import service, views
from shopbot.features.reviews.content_keys import REVIEWS
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, to_modal

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

StatusChoice = Literal["prompted", "rated", "pending_approval", "published", "rejected", "declined"]
OrderNumber = app_commands.Range[int, 1, 2_147_483_647]
ReviewNumber = app_commands.Range[int, 1, 2_147_483_647]
Reason = app_commands.Range[str, 1, 500]
# A choice's name is plain text: an icon at the start of a rating label is left out.
_LEADING_ICON = re.compile(r"^\{icon_[a-z0-9_]+\}\s*")
CHOICE_NAME_MAX = 100
# The name Discord shows for the optional picture (customer texts tell members to "add the photo option").
PHOTO_OPTION = "photo"


def staff_role_keys(app: App) -> tuple[str, ...]:
    """Roles that count as staff for reviews besides permissions.staff_roles: the checkout staff."""
    return app.settings.checkout.staff_roles


async def _ready(interaction: discord.Interaction[Any]) -> App | None:
    app = app_of(interaction)
    if app.ready(REVIEWS):
        return app
    title = app.op("command.feature_disabled", feature=REVIEWS)
    await reply(interaction, views.operator_reply(app.ui, title, [], tone="warning", banner_id="error"), ephemeral=True)
    return None


async def _reply_result(
    interaction: discord.Interaction[Any], app: App, title: str, result: service.StaffResult
) -> None:
    """The operator reply of a staff command, with the banner that fits its outcome."""
    spec = views.operator_reply(app.ui, title, result.lines, tone=result.tone, banner_id=result.banner)
    await reply(interaction, spec, ephemeral=True)


async def _defer(interaction: discord.Interaction[Any]) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def review_list(interaction: discord.Interaction[Any], status: StatusChoice | None = None) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    await reply(interaction, await service.list_view(app, status), ephemeral=True)


async def review_remove(
    interaction: discord.Interaction[Any], order_number: int | None = None, review: int | None = None
) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    target = service.Target(order_number=order_number, review_number=review)
    result = await service.remove_review(app, target, actor_id=interaction.user.id)
    await _reply_result(interaction, app, app.op("reviews.cmd.remove_title"), result)


async def review_resend(interaction: discord.Interaction[Any], order_number: int) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    result = await service.resend_prompt(app, order_number, actor_id=interaction.user.id)
    await _reply_result(interaction, app, app.op("reviews.cmd.resend_title"), result)


async def review_decide(
    interaction: discord.Interaction[Any],
    order_number: int | None = None,
    review: int | None = None,
    *,
    approve: bool,
    reason: str | None = None,
) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    cleaned = reason.strip() if reason else None
    target = service.Target(order_number=order_number, review_number=review)
    result = await service.decide_by_target(
        app, target, approve=approve, actor_id=interaction.user.id, reason=cleaned or None
    )
    await _reply_result(interaction, app, app.op("reviews.decision.title"), result)


def build_reviews_group() -> app_commands.Group:
    """A fresh ``/reviews`` staff group (one per application instance)."""
    group = app_commands.Group(name="reviews", description="Customer review tools for staff.")
    app_commands.default_permissions(manage_messages=True)(group)
    app_commands.guild_only(group)

    @group.command(name="list", description="List recent reviews (max 25).")
    @app_commands.describe(status="Only reviews with this status")
    @require_staff(staff_role_keys)
    async def list_(interaction: discord.Interaction[Any], status: StatusChoice | None = None) -> None:
        await review_list(interaction, status)

    @group.command(name="remove", description="Delete a published review and close it.")
    @app_commands.describe(
        order_number="Number of the reviewed order", review="Review number (for a review without an order)"
    )
    @require_staff(staff_role_keys)
    async def remove(
        interaction: discord.Interaction[Any],
        order_number: OrderNumber | None = None,
        review: ReviewNumber | None = None,
    ) -> None:
        await review_remove(interaction, order_number, review)

    @group.command(name="resend", description="Send an unanswered review request again.")
    @app_commands.describe(order_number="Number of the order")
    @require_staff(staff_role_keys)
    async def resend(interaction: discord.Interaction[Any], order_number: OrderNumber) -> None:
        await review_resend(interaction, order_number)

    @group.command(name="approve", description="Approve a review that waits for approval and publish it.")
    @app_commands.describe(
        order_number="Number of the reviewed order", review="Review number (for a review without an order)"
    )
    @require_staff(staff_role_keys)
    async def approve(
        interaction: discord.Interaction[Any],
        order_number: OrderNumber | None = None,
        review: ReviewNumber | None = None,
    ) -> None:
        await review_decide(interaction, order_number, review, approve=True)

    @group.command(name="reject", description="Reject a review that waits for approval (it is not published).")
    @app_commands.describe(
        order_number="Number of the reviewed order",
        review="Review number (for a review without an order)",
        reason="Optional reason (max 500 characters)",
    )
    @require_staff(staff_role_keys)
    async def reject(
        interaction: discord.Interaction[Any],
        order_number: OrderNumber | None = None,
        review: ReviewNumber | None = None,
        reason: Reason | None = None,
    ) -> None:
        await review_decide(interaction, order_number, review, approve=False, reason=reason)

    return group


# -- /review (every member) ------------------------------------------------------------------------------------------


async def write_review(
    interaction: discord.Interaction[Any], stars: int, image: discord.Attachment | None = None
) -> None:
    """/review: open the private description form, or answer privately why a review cannot be written now."""
    app = app_of(interaction)
    if interaction.guild_id != app.env.guild_id:
        await reply_content_error(interaction, core.ERROR_PERMISSION)
        return
    if not app.ready(REVIEWS):
        await reply_content_error(interaction, core.ERROR_UNAVAILABLE)
        return
    try:
        answer = await service.open_form(app, user_id=interaction.user.id, rating=stars, image=image)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="review_command")
        await reply_content_error(interaction, core.ERROR_UNAVAILABLE)
        return
    except RenderError as exc:
        error_id = uuid.uuid4().hex[:12]
        log_event(log, logging.ERROR, "reviews_form_render_failed", error_id=error_id, error=str(exc)[:200])
        await reply_content_error(interaction, core.ERROR_GENERIC, error_id=error_id)
        return
    if answer.modal is not None:
        await interaction.response.send_modal(to_modal(answer.modal))
    elif answer.reply is not None:
        await reply(interaction, answer.reply, ephemeral=True)
    else:
        await reply_content_error(interaction, core.ERROR_INVALID_INPUT)


def _plain_label(label: str) -> str:
    return _LEADING_ICON.sub("", label).strip()


def rating_choices(app: App) -> list[app_commands.Choice[int]] | None:
    """The ``stars`` choices, highest first: each rating's label with a row of stars (plain text: the label without a
    leading icon). None when a label is missing or the scale is not set."""
    scale = app.settings.reviews.rating_scale
    if not 1 <= scale <= k.MAX_RATING_SCALE:
        return None
    choices: list[app_commands.Choice[int]] = []
    for rating in range(scale, 0, -1):
        label = app.content.get(k.rating_key(rating, "label"))
        plain = _plain_label(label) if label else ""
        if not plain:
            return None
        row = views.stars(rating, scale)
        # A label fits a choice (both at most 100 characters); the stars follow only when they fit too.
        name = f"{plain} {row}" if len(plain) + 1 + len(row) <= CHOICE_NAME_MAX else plain
        choices.append(app_commands.Choice(name=name, value=rating))
    return choices


def _configured(app: App, key: str) -> str | None:
    """A configured text Discord accepts as a command or option description (None when it is missing or invalid)."""
    text = app.content.get(key)
    definition = app.content.registry.get(key)
    if text is None or definition is None or validate_value(definition, text):
        return None
    return text


def build_review_command(app: App) -> app_commands.Command[Any, Any, None] | None:
    """The ``/review`` command with its configured descriptions and choices, or None when they are not configured."""
    description = _configured(app, k.COMMAND_DESCRIPTION)
    stars_text = _configured(app, k.COMMAND_STARS)
    image_text = _configured(app, k.COMMAND_IMAGE)
    choices = rating_choices(app)
    if description is None or stars_text is None or image_text is None or choices is None:
        return None

    async def review(
        interaction: discord.Interaction[Any], stars: int, image: discord.Attachment | None = None
    ) -> None:
        await write_review(interaction, stars, image)

    app_commands.describe(stars=stars_text, image=image_text)(review)
    app_commands.choices(stars=choices)(review)
    app_commands.rename(image=PHOTO_OPTION)(review)
    command: app_commands.Command[Any, Any, None] = app_commands.Command(
        name=k.COMMAND_NAME, description=description, callback=review
    )
    app_commands.guild_only(command)
    return command


def register_commands(app: App) -> None:
    """Add ``/reviews`` (always) and ``/review`` (when reviews are on and its texts are configured) to the tree."""
    if app.bot is None:
        raise RuntimeError("register_commands() requires app.bot")
    app.bot.tree.add_command(build_reviews_group(), guild=app.guild_object)
    if not app.settings.features.reviews:
        return
    command = build_review_command(app)
    if command is None:
        # Never registered with invented wording: /status lists the missing text; a restart registers it.
        log_event(log, logging.WARNING, "reviews_command_not_registered", reason="text_not_configured")
        return
    app.bot.tree.add_command(command, guild=app.guild_object)
