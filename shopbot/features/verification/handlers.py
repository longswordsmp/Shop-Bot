"""The 'I agree, enter the server' button (route ``verify``, custom id ``sb:verify:agree``).

Flow for one click (the router has already checked the server, the rate limit, duplicate in-flight clicks and that
the feature is ready):

1. Every reply is built first, so missing text never leaves a half-done verification (the router then answers with
   the 'unavailable' text).
2. The Member role is resolved from ``permissions.member_role``. A member who already has it gets the private
   "already verified" reply and nothing changes. The role list in the click payload can be stale (a second click
   sent before the first one was processed), so a member without the role is re-read from Discord before granting.
3. The bot grants the role, which immediately reveals the ``members_*`` channels, records the acceptance, audits
   ``verification.accepted``, answers privately and, when configured, confirms by direct message (at most once per
   acceptance).

When the role cannot be granted (it does not exist, it is not below the bot's highest role, the bot lacks Manage
Roles, or Discord refuses), the failure is logged and audited with a reference, and the member gets the configured
generic error with that reference. Nothing about the click lives in memory, so the button keeps working after a
restart. Staff and the Owner see the channels through their own roles; clicking works for them all the same.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, NoReturn

import discord

from shopbot.core.permissions import can_manage_role
from shopbot.features import core_keys as ck
from shopbot.features.server_keys import channel_mentions
from shopbot.features.verification import repo, service, views
from shopbot.features.verification.content_keys import VERIFICATION
from shopbot.logging_setup import log_event
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

NOTIFICATION_KIND = "verification.confirmed"


def has_role(member: discord.Member, role: discord.Role) -> bool:
    return any(r.id == role.id for r in member.roles)


async def _fail(ctx: Ctx, reason: str, **details: Any) -> NoReturn:
    """Log and audit a failure to grant the role, then answer with the generic error and its reference."""
    error_id = uuid.uuid4().hex[:12]
    log_event(
        log,
        logging.WARNING,
        "verification_failed",
        reason=reason,
        user_id=ctx.user_id,
        error_id=error_id,
        **details,
    )
    try:
        await ctx.app.audit.record(
            "verification.failed",
            actor_id=ctx.user_id,
            target_type="member",
            target_id=ctx.user_id,
            details={"reason": reason, "error_id": error_id, **details},
        )
    except Exception:  # auditing must never hide the answer to the member
        log.exception("verification failure audit failed (%s)", error_id)
    raise HandlerError(ck.ERROR_GENERIC, error_id=error_id)


async def _current_member(ctx: Ctx, guild: discord.Guild) -> discord.Member:
    """The clicking member as Discord has them now (the payload's role list may predate an earlier click)."""
    try:
        return await guild.fetch_member(ctx.user_id)
    except discord.NotFound:
        await _fail(ctx, "member_not_found")
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "verification_member_fetch_failed", user_id=ctx.user_id, status=exc.status)
        payload_member = ctx.member
        if payload_member is None:
            await _fail(ctx, "member_unavailable", status=exc.status)
        return payload_member


async def _grant(ctx: Ctx, member: discord.Member, role: discord.Role) -> None:
    try:
        await member.add_roles(role, reason=ctx.app.op("verification.audit_reason"))
    except discord.Forbidden as exc:
        await _fail(ctx, "forbidden", role_id=role.id, code=exc.code)
    except discord.NotFound as exc:
        await _fail(ctx, "not_found", role_id=role.id, code=exc.code)
    except discord.HTTPException as exc:
        await _fail(ctx, "http_error", role_id=role.id, status=exc.status, code=exc.code)


async def _record(app: App, guild_id: int, user_id: int) -> int | None:
    """Store the acceptance; returns how many times this member has been granted the role (None if not stored)."""
    try:
        acceptance = await app.db.transaction(repo.record_acceptance, guild_id, user_id, app.clock.now())
    except Exception:  # the role is the source of truth; history must not turn a success into an error
        log.exception("recording the verification of %s failed", user_id)
        return None
    return acceptance.acceptances


async def _after_acceptance(
    ctx: Ctx, role: discord.Role, acceptances: int | None, dm: MessageSpec | None, guild_id: int
) -> None:
    """Audit the acceptance and send the optional direct message. Never raises (the member was answered)."""
    app = ctx.app
    try:
        await app.audit.record(
            "verification.accepted",
            actor_id=ctx.user_id,
            target_type="member",
            target_id=ctx.user_id,
            details={"role_id": role.id, "acceptances": acceptances},
        )
    except Exception:
        log.exception("verification audit failed for %s", ctx.user_id)
    if dm is None:
        return
    dedupe_key = f"verification.dm:{guild_id}:{ctx.user_id}:{acceptances}" if acceptances is not None else None
    try:
        delivery = await app.notifier.notify_user(
            user_id=ctx.user_id, spec=dm, kind=NOTIFICATION_KIND, dedupe_key=dedupe_key
        )
    except Exception:
        log.exception("verification DM failed for %s", ctx.user_id)
        delivery = "none"
    if delivery == "none":
        try:
            await app.audit.record(
                "verification.dm_failed",
                actor_id=ctx.user_id,
                target_type="member",
                target_id=ctx.user_id,
                details={"acceptances": acceptances},
            )
        except Exception:
            log.exception("verification DM failure audit failed for %s", ctx.user_id)


async def on_agree(ctx: Ctx) -> None:
    app = ctx.app
    guild = ctx.guild
    if guild is None:  # the router only lets this route run inside the server
        raise HandlerError(ck.ERROR_UNAVAILABLE)
    values = views.reply_values(channel_mentions(app), user_name=ctx.interaction.user.display_name, user_id=ctx.user_id)
    confirmation = views.confirmation_view(app.ui, values)
    already = views.already_verified_view(app.ui, values)
    settings = app.settings
    send_dm = settings.verification.dm_confirmation and settings.features.dm_notifications
    dm = views.dm_view(app.ui, values) if send_dm else None
    await ctx.defer_reply(ephemeral=True)

    role = service.member_role(app, guild)
    if role is None:
        await _fail(ctx, "role_missing", role_key=settings.permissions.member_role)
    member = ctx.member
    if member is not None and has_role(member, role):
        await ctx.respond(already, ephemeral=True)
        return
    member = await _current_member(ctx, guild)
    if has_role(member, role):
        await ctx.respond(already, ephemeral=True)
        return
    me = guild.me
    if me is None or not can_manage_role(me, role):
        await _fail(ctx, "role_not_assignable", role_id=role.id)
    await _grant(ctx, member, role)
    log_event(log, logging.INFO, "verification_accepted", user_id=ctx.user_id, role_id=role.id)
    acceptances = await _record(app, guild.id, ctx.user_id)
    try:
        await ctx.respond(confirmation, ephemeral=True)
    finally:
        await _after_acceptance(ctx, role, acceptances, dm, guild.id)


def register_handlers(app: App) -> None:
    app.router.component(views.ROUTE, views.AGREE, feature=VERIFICATION)(on_agree)
