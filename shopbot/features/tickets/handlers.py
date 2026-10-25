"""Button, selection, and form handlers of the ticket system (route prefix ``tk``).

Routes are registered with ``access="any"`` because ticket staff may hold a category-specific staff
role that the router does not know about; every handler re-loads the ticket by id, verifies it belongs
to this guild and is a support ticket, checks the actor (owner and/or staff of that ticket's category)
and the ticket state, and only then performs the action.

The select-style panel keeps showing the option a member picked (Discord does not reset a select), so
after every pick its menu is re-rendered on the panel message; otherwise the same category could not be
picked again.
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import TicketCategory
from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import repo, service, views
from shopbot.features.tickets.models import KIND_SUPPORT, Ticket
from shopbot.features.tickets.runtime import runtime
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, to_view
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


# -- shared checks ------------------------------------------------------------------------------------------


async def _load(ctx: Ctx) -> Ticket:
    ticket = await service.get_ticket(ctx.app, ctx.arg_int(0))
    if ticket is None or ticket.guild_id != ctx.app.env.guild_id or not ticket.is_support:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return ticket


def _is_staff(ctx: Ctx, ticket: Ticket) -> bool:
    return service.is_ticket_staff(ctx.app, ctx.member, ctx.user_id, ticket)


def _require_staff(ctx: Ctx, ticket: Ticket) -> None:
    if not _is_staff(ctx, ticket):
        log_event(log, logging.INFO, "tickets_forbidden", ticket_id=ticket.id, user_id=ctx.user_id)
        raise HandlerError(core.ERROR_PERMISSION)


def _require_status(ticket: Ticket, status: str) -> None:
    if ticket.status != status:
        raise HandlerError(k.NOT_OPEN)


def _can_close(ctx: Ctx, ticket: Ticket) -> bool:
    if ticket.user_id == ctx.user_id and ctx.app.settings.tickets.allow_user_close:
        return True
    return _is_staff(ctx, ticket)


def _check_cooldown(ctx: Ctx) -> None:
    seconds = ctx.app.settings.tickets.creation_cooldown_seconds
    if seconds <= 0:
        return
    remaining = runtime(ctx.app).cooldowns.remaining(ctx.user_id, seconds)
    if remaining > 0:
        raise HandlerError(core.ERROR_COOLDOWN, retry_after=math.ceil(remaining))


async def _check_limit(ctx: Ctx, category: TicketCategory) -> None:
    """Early answer before a form is shown; the limits are enforced again atomically on creation."""
    app = ctx.app
    limit = service.open_limit(app, category.id)
    count = await app.db.run(repo.count_active, app.env.guild_id, ctx.user_id, KIND_SUPPORT, category.id)
    if count >= limit:
        raise HandlerError(k.LIMIT_REACHED, limit=limit)
    daily = k.daily_creation_cap(app.settings)
    if daily > 0:
        since = app.clock.now() - repo.DAY_SECONDS
        created = await app.db.run(repo.count_created_since, app.env.guild_id, ctx.user_id, KIND_SUPPORT, since)
        if created >= daily:
            raise HandlerError(k.LIMIT_REACHED, limit=daily)


def _category(ctx: Ctx, category_id: str | None) -> TicketCategory:
    category = ctx.app.settings.tickets.category(category_id or "")
    if category is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return category


def _member(ctx: Ctx) -> discord.Member:
    member = ctx.member
    if member is None or member.guild.id != ctx.app.env.guild_id:
        raise HandlerError(core.ERROR_PERMISSION)
    return member


# -- opening ------------------------------------------------------------------------------------------------


async def _open_checks(ctx: Ctx, category_id: str | None) -> TicketCategory:
    category = _category(ctx, category_id)
    _member(ctx)
    _check_cooldown(ctx)
    await _check_limit(ctx, category)
    return category


async def _start_open(ctx: Ctx, category_id: str | None) -> None:
    category = await _open_checks(ctx, category_id)
    if category.form:
        await ctx.send_modal(views.form_modal(ctx.app.ui, category))
        return
    await _create(ctx, category, {})


def _panel_view(ctx: Ctx) -> discord.ui.View | None:
    """The select-style panel's components, freshly rendered (no option selected); None if not buildable."""
    try:
        spec = views.panel(ctx.app.ui, ctx.app.settings.tickets, style="select")
    except MissingContent:
        return None
    return to_view(MessageSpec(rows=spec.rows))


async def _reset_panel(ctx: Ctx) -> None:
    """Best effort: re-render the panel's menu on the message the pick came from."""
    message = ctx.interaction.message
    view = _panel_view(ctx)
    if message is None or view is None:
        return
    try:
        await message.edit(view=view)
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "tickets_panel_reset_failed", status=exc.status)


async def _ack_by_resetting_panel(ctx: Ctx) -> bool:
    """Acknowledge a pick by re-rendering the panel's menu (the answer follows as a private followup).
    False when that is not possible; the caller acknowledges differently then."""
    view = _panel_view(ctx)
    if view is None or ctx.interaction.message is None or ctx.responded:
        return False
    try:
        await ctx.interaction.response.edit_message(view=view)
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "tickets_panel_reset_failed", status=exc.status)
        return False
    return True


async def _create(ctx: Ctx, category: TicketCategory, answers: dict[str, str], *, from_select: bool = False) -> None:
    app = ctx.app
    member = _member(ctx)
    if not (from_select and await _ack_by_resetting_panel(ctx)):
        await ctx.defer_reply(ephemeral=True)
    try:
        created = await service.open_support_ticket(app, member=member, category=category, answers=answers)
    except service.TicketLimitReached as exc:
        raise HandlerError(k.LIMIT_REACHED, limit=exc.limit) from None
    except service.TicketError:
        raise HandlerError(core.ERROR_GENERIC) from None
    runtime(app).cooldowns.mark(member.id)
    await ctx.respond(
        views.created_reply(app.ui, app.settings.tickets, channel_id=created.channel.id, number=created.ticket.number)
    )


async def open_button(ctx: Ctx) -> None:
    await _start_open(ctx, ctx.args[0] if ctx.args else None)


async def pick_select(ctx: Ctx) -> None:
    """A category picked in the select-style panel; the panel's menu is reset afterwards in every case."""
    try:
        category = await _open_checks(ctx, ctx.values[0] if ctx.values else None)
    except HandlerError as exc:
        await ctx.fail(exc.key, **exc.values)
        await _reset_panel(ctx)
        return
    if category.form:
        await ctx.send_modal(views.form_modal(ctx.app.ui, category))
        await _reset_panel(ctx)
        return
    await _create(ctx, category, {}, from_select=True)


async def form_submit(ctx: Ctx) -> None:
    category = _category(ctx, ctx.args[0] if ctx.args else None)
    if not category.form:
        raise HandlerError(core.ERROR_NOT_FOUND)
    _member(ctx)
    _check_cooldown(ctx)
    answers: dict[str, str] = {}
    for field in category.form:
        raw = ctx.fields.get(field.id, "")
        value = raw.strip()
        if not value:
            if field.required:
                raise HandlerError(core.ERROR_INVALID_INPUT)
            continue
        if len(raw) > field.max_length or len(value) < field.min_length:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        answers[field.id] = value
    await _check_limit(ctx, category)
    await _create(ctx, category, answers)


# -- closing ------------------------------------------------------------------------------------------------


async def _close(ctx: Ctx, ticket: Ticket) -> None:
    closed = await service.close_ticket(ctx.app, ticket.id, actor_id=ctx.user_id)
    if closed is None:
        raise HandlerError(k.NOT_OPEN)


async def close_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_status(ticket, "open")
    if not _can_close(ctx, ticket):
        raise HandlerError(core.ERROR_PERMISSION)
    if ctx.app.settings.tickets.close_confirmation:
        await ctx.respond(views.close_confirm(ctx.app.ui, ticket.id))
        return
    await ctx.defer_update()
    await _close(ctx, ticket)


async def close_confirmed(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_status(ticket, "open")
    if not _can_close(ctx, ticket):
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_update()
    await _close(ctx, ticket)
    await _resolve_prompt(ctx, lambda: views.close_confirm(ctx.app.ui, ticket.id))


async def close_cancelled(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    await _resolve_prompt(ctx, lambda: views.close_confirm(ctx.app.ui, ticket.id))


async def _resolve_prompt(ctx: Ctx, build: Callable[[], MessageSpec]) -> None:
    """Remove the buttons from an answered private prompt: same text, no buttons. If that is not possible
    (text no longer configured, or the prompt disappeared with its channel) the click is acknowledged."""
    try:
        await ctx.update(views.resolved_prompt(build()))
    except (MissingContent, discord.HTTPException, RenderError) as exc:
        log_event(log, logging.DEBUG, "tickets_prompt_update_failed", error=type(exc).__name__)
        with contextlib.suppress(discord.HTTPException):
            await ctx.ack()


# -- reopen / claim -------------------------------------------------------------------------------------------


async def reopen_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    t = ctx.app.settings.tickets
    if not t.allow_reopen:
        raise HandlerError(core.ERROR_UNAVAILABLE)
    staff = _is_staff(ctx, ticket)
    owner_allowed = t.user_can_reopen and ticket.user_id == ctx.user_id
    if not owner_allowed and not staff:
        raise HandlerError(core.ERROR_PERMISSION)
    _require_status(ticket, "closed")
    # The owner may not exceed the category's open-ticket limit by reopening; staff reopen without limit.
    limit = None if staff else service.open_limit(ctx.app, ticket.category_id)
    await ctx.defer_update()
    try:
        reopened = await service.reopen_ticket(ctx.app, ticket.id, actor_id=ctx.user_id, limit=limit)
    except service.TicketLimitReached as exc:
        raise HandlerError(k.LIMIT_REACHED, limit=exc.limit) from None
    if reopened is None:
        raise HandlerError(k.NOT_OPEN)


async def claim_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_staff(ctx, ticket)
    _require_status(ticket, "open")
    if ticket.assigned_staff_id is not None:
        raise HandlerError(k.NOT_OPEN)
    await ctx.defer_update()
    if await service.claim_ticket(ctx.app, ticket.id, staff_id=ctx.user_id) is None:
        raise HandlerError(k.NOT_OPEN)


async def unclaim_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_staff(ctx, ticket)
    _require_status(ticket, "open")
    assignee = ticket.assigned_staff_id
    if assignee is None:
        raise HandlerError(k.NOT_OPEN)
    if assignee != ctx.user_id and not ctx.is_admin():
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_update()
    if await service.unclaim_ticket(ctx.app, ticket.id, actor_id=ctx.user_id, assignee_id=assignee) is None:
        raise HandlerError(k.NOT_OPEN)


# -- delete / transcript ----------------------------------------------------------------------------------------


async def delete_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_staff(ctx, ticket)
    _require_status(ticket, "closed")
    await ctx.respond(views.delete_confirm(ctx.app.ui, ticket.id))


async def delete_confirmed(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_staff(ctx, ticket)
    _require_status(ticket, "closed")
    await ctx.defer_update()
    try:
        deleted = await service.delete_ticket(ctx.app, ticket.id, actor_id=ctx.user_id)
    except service.TicketStateError:
        raise HandlerError(k.NOT_OPEN) from None
    except service.TranscriptUnavailable:
        raise HandlerError(core.ERROR_GENERIC) from None
    except discord.HTTPException as exc:
        log_event(log, logging.ERROR, "tickets_delete_failed", ticket_id=ticket.id, status=exc.status)
        raise HandlerError(core.ERROR_GENERIC) from None
    if not deleted:
        raise HandlerError(k.NOT_OPEN)
    await _resolve_prompt(ctx, lambda: views.delete_confirm(ctx.app.ui, ticket.id))


async def delete_cancelled(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    await _resolve_prompt(ctx, lambda: views.delete_confirm(ctx.app.ui, ticket.id))


async def transcript_button(ctx: Ctx) -> None:
    ticket = await _load(ctx)
    _require_staff(ctx, ticket)
    app = ctx.app
    t = app.settings.tickets
    if not t.transcript_channel:
        raise HandlerError(core.ERROR_UNAVAILABLE)
    await ctx.defer_reply(ephemeral=True)
    if not await service.generate_transcript(app, ticket.id, force=True, requested_by=ctx.user_id):
        raise HandlerError(core.ERROR_GENERIC)
    # Staff-only control (checked above), private reply: operator wording is allowed here.
    channel_id = app.resources.channel_id(t.transcript_channel) or 0
    text = app.op(
        "tickets.cmd.transcript_posted",
        number=service.format_number(app, ticket.number),
        channel=views.channel_mention(channel_id),
    )
    await ctx.respond(MessageSpec(embeds=(app.ui.operator_embed(text, tone="success"),)))


def register(app: App) -> None:
    r = app.router
    feature = k.FEATURE
    r.component(k.ROUTE, "open", feature=feature)(open_button)
    r.component(k.ROUTE, "pick", feature=feature)(pick_select)
    r.modal(k.ROUTE, "form", feature=feature)(form_submit)
    r.component(k.ROUTE, "close", feature=feature)(close_button)
    r.component(k.ROUTE, "closeok", feature=feature)(close_confirmed)
    r.component(k.ROUTE, "closeno", feature=feature)(close_cancelled)
    r.component(k.ROUTE, "reopen", feature=feature)(reopen_button)
    r.component(k.ROUTE, "claim", feature=feature)(claim_button)
    r.component(k.ROUTE, "unclaim", feature=feature)(unclaim_button)
    r.component(k.ROUTE, "delete", feature=feature)(delete_button)
    r.component(k.ROUTE, "delok", feature=feature)(delete_confirmed)
    r.component(k.ROUTE, "delno", feature=feature)(delete_cancelled)
    r.component(k.ROUTE, "transcript", feature=feature)(transcript_button)
