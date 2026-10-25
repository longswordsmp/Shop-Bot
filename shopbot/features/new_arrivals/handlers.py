"""Component handler (route ``na``).

``sb:na:toggle`` (the panel's button): gives the clicking member the notification role when they do not have it,
otherwise removes it, and answers privately with the "now subscribed" or "now unsubscribed" confirmation.

- The custom id carries no state: the decision is made from the member's roles in the click itself, and the role is
  resolved from the current settings, so the button keeps working after restarts and configuration reloads.
- Adding a role a member already has is a no-op in Discord, and the router drops a second click of the same member
  while the first is still being processed, so double clicks never assign the role twice.
- The confirmation is built before anything changes: if its text is missing, nothing changes and the router answers
  with the "unavailable" text.
- A missing role, a role the bot may not assign (@everyone, integration-managed, or above the bot's own role), or a
  Discord refusal is logged, audited
  (``new_arrivals.toggle_failed``) and answered with the generic error text and its reference (``error_id``).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, NoReturn

import discord

from shopbot.features import core_keys as ck
from shopbot.features.new_arrivals import service, views
from shopbot.features.new_arrivals.content_keys import NEW_ARRIVALS
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import check_message
from shopbot.ui.routing import Ctx, HandlerError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


async def _fail(ctx: Ctx, reason: str, *, role_id: int | None, **fields: object) -> NoReturn:
    """Log + audit a failed toggle, then answer with the generic error text and its reference."""
    error_id = uuid.uuid4().hex[:12]
    log_event(
        log,
        logging.ERROR,
        "new_arrivals_toggle_failed",
        reason=reason,
        user_id=ctx.user_id,
        role_id=role_id,
        error_id=error_id,
        **fields,
    )
    await ctx.app.audit.record(
        "new_arrivals.toggle_failed",
        actor_id=ctx.user_id,
        target_type="role",
        target_id=role_id if role_id is not None else ctx.app.settings.new_arrivals.role,
        details={"reason": reason, "error_id": error_id, **fields},
    )
    raise HandlerError(ck.ERROR_GENERIC, error_id=error_id)


async def on_toggle(ctx: Ctx) -> None:
    app = ctx.app
    guild, member = ctx.guild, ctx.member
    if guild is None or member is None:
        raise HandlerError(ck.ERROR_NOT_FOUND)
    # Acknowledge first (privately): the role change and the audit entry are Discord calls that must not push the
    # answer past Discord's 3-second limit. Every answer below, including errors, follows privately.
    await ctx.defer_reply(ephemeral=True)
    role = service.notification_role(app, guild)
    if role is None:
        await _fail(ctx, "role_missing", role_id=None)
    subscribe = not service.has_role(member, role)
    spec = views.confirmation(app.ui, subscribed=subscribe, values=service.text_values(app))
    check_message(spec)
    if not service.can_assign(guild, role):
        await _fail(ctx, "role_not_manageable", role_id=role.id)
    reason = app.op("new_arrivals.role_reason")
    try:
        if subscribe:
            await member.add_roles(role, reason=reason)
        else:
            await member.remove_roles(role, reason=reason)
    except discord.HTTPException as exc:
        cause = "forbidden" if isinstance(exc, discord.Forbidden) else "discord_error"
        if isinstance(exc, discord.NotFound):
            cause = "role_missing"
        await _fail(ctx, cause, role_id=role.id, status=exc.status, code=exc.code)
    log_event(
        log,
        logging.INFO,
        "new_arrivals_subscribed" if subscribe else "new_arrivals_unsubscribed",
        user_id=ctx.user_id,
        role_id=role.id,
    )
    try:
        await ctx.respond(spec, ephemeral=True)
    finally:
        await app.audit.record(
            "new_arrivals.subscribed" if subscribe else "new_arrivals.unsubscribed",
            actor_id=ctx.user_id,
            target_type="role",
            target_id=role.id,
        )


def register_handlers(app: App) -> None:
    app.router.component(views.ROUTE, views.TOGGLE_ACTION, feature=NEW_ARRIVALS)(on_toggle)
