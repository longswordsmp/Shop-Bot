"""The ``/review`` slash command group (staff).

Visibility: ``default_permissions(manage_messages=True)`` hides the group from members without Manage Messages (a
convenience only). Every invocation is authorized by ``require_staff`` (``permissions.staff_roles``, the checkout
staff roles, or an admin). Replies are operator text and always ephemeral.

- ``list [status]``: the 25 most recently changed reviews.
- ``remove <order_number>``: deletes the published review message and closes the review (status declined); a
  review request that was not sent yet is cancelled.
- ``resend <order_number>``: sends the review request again while it is unanswered.
- ``approve <order_number>`` / ``reject <order_number> [reason]``: the staff decision on a review waiting for
  approval (``reviews.require_approval``), the same as the Approve / Reject buttons of its approval message (for
  example when that message was deleted). Exactly once: a second decision changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_staff
from shopbot.features.reviews import service, views
from shopbot.features.reviews.content_keys import REVIEWS

if TYPE_CHECKING:
    from shopbot.context import App

StatusChoice = Literal["prompted", "rated", "pending_approval", "published", "rejected", "declined"]
OrderNumber = app_commands.Range[int, 1, 2_147_483_647]
Reason = app_commands.Range[str, 1, 500]


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


async def review_remove(interaction: discord.Interaction[Any], order_number: int) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    result = await service.remove_review(app, order_number, actor_id=interaction.user.id)
    await _reply_result(interaction, app, app.op("reviews.cmd.remove_title"), result)


async def review_resend(interaction: discord.Interaction[Any], order_number: int) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    result = await service.resend_prompt(app, order_number, actor_id=interaction.user.id)
    await _reply_result(interaction, app, app.op("reviews.cmd.resend_title"), result)


async def review_decide(
    interaction: discord.Interaction[Any], order_number: int, *, approve: bool, reason: str | None = None
) -> None:
    app = await _ready(interaction)
    if app is None:
        return
    await _defer(interaction)
    cleaned = reason.strip() if reason else None
    result = await service.decide_by_order(
        app, order_number, approve=approve, actor_id=interaction.user.id, reason=cleaned or None
    )
    await _reply_result(interaction, app, app.op("reviews.decision.title"), result)


def build_review_group() -> app_commands.Group:
    """A fresh ``/review`` group (one per application instance)."""
    group = app_commands.Group(name="review", description="Customer review tools for staff.")
    app_commands.default_permissions(manage_messages=True)(group)
    app_commands.guild_only(group)

    @group.command(name="list", description="List recent reviews (max 25).")
    @app_commands.describe(status="Only reviews with this status")
    @require_staff(staff_role_keys)
    async def list_(interaction: discord.Interaction[Any], status: StatusChoice | None = None) -> None:
        await review_list(interaction, status)

    @group.command(name="remove", description="Delete a published review and close it.")
    @app_commands.describe(order_number="Number of the reviewed order")
    @require_staff(staff_role_keys)
    async def remove(interaction: discord.Interaction[Any], order_number: OrderNumber) -> None:
        await review_remove(interaction, order_number)

    @group.command(name="resend", description="Send an unanswered review request again.")
    @app_commands.describe(order_number="Number of the order")
    @require_staff(staff_role_keys)
    async def resend(interaction: discord.Interaction[Any], order_number: OrderNumber) -> None:
        await review_resend(interaction, order_number)

    @group.command(name="approve", description="Approve a review that waits for approval and publish it.")
    @app_commands.describe(order_number="Number of the reviewed order")
    @require_staff(staff_role_keys)
    async def approve(interaction: discord.Interaction[Any], order_number: OrderNumber) -> None:
        await review_decide(interaction, order_number, approve=True)

    @group.command(name="reject", description="Reject a review that waits for approval (it is not published).")
    @app_commands.describe(order_number="Number of the reviewed order", reason="Optional reason (max 500 characters)")
    @require_staff(staff_role_keys)
    async def reject(
        interaction: discord.Interaction[Any], order_number: OrderNumber, reason: Reason | None = None
    ) -> None:
        await review_decide(interaction, order_number, approve=False, reason=reason)

    return group
