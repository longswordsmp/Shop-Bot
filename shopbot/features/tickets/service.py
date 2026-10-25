"""Ticket service: the reusable private-channel API and the support-ticket flows.

PUBLIC API (stable; the checkout feature reuses it for private ORDER channels, ``kind='order'``)
-----------------------------------------------------------------------------------------------
Errors: ``TicketError`` > ``TicketLimitReached(limit)``, ``TicketCreateFailed(reason)``,
``TicketStateError(status)``, ``TranscriptUnavailable``.

Reads
    ``await get_ticket(app, ticket_id) -> Ticket | None``
    ``await get_ticket_by_channel(app, channel_id) -> Ticket | None``
    ``await participants(app, ticket_id) -> list[int]``
    ``await ticket_channel(app, ticket) -> discord.TextChannel | None``
    ``format_number(app, number) -> str`` (zero-padded per ``tickets.number_padding``)

Private channels (any kind)
    ``await create_private_channel(app, *, member, kind, category_id, discord_category_key,
    channel_prefix_key, extra_staff_role_keys, topic, limit, form_answers=None) -> CreatedChannel``
        One transaction checks ``limit`` (active = 'creating'/'open' tickets of this user+kind+category)
        and inserts a numbered 'creating' row; then the private channel is created (``@everyone`` denied;
        owner, staff roles = permissions.staff_roles + permissions.admin_roles + ``extra_staff_role_keys``,
        and the bot allowed). No message is sent. Raises ``TicketLimitReached`` / ``TicketCreateFailed``
        (the row is then 'failed' and audited) or ``MissingContent`` (prefix not configured).
    ``await activate_ticket(app, ticket_id, *, control_message_id) -> Ticket``  'creating' -> 'open'.
    ``await abort_ticket(app, ticket_id, *, reason, channel=None)``  deletes the channel, 'creating' -> 'failed'.
    ``await staff_role_ids(app, extra_keys) -> list[discord.Role]`` (resolved Role objects).

Lifecycle (any kind; support-ticket UI messages are only posted for ``kind='support'``)
    ``await close_ticket(app, ticket_id, *, actor_id, reason=None, transcript=None,
    delete_after_minutes=None, lock=None, closed_category_key=None) -> Ticket | None``
        Conditional open -> closed (None if it was not open). Defaults per kind: support uses the
        ``tickets.*`` settings; order uses ``checkout.transcript_on_close`` (with the tickets transcript
        channel), ``checkout.delete_channel_after_close_minutes``, lock=True, no category move.
    ``await reopen_ticket(app, ticket_id, *, actor_id) -> Ticket | None``
    ``await schedule_channel_deletion(app, ticket_id, minutes) -> None`` (task ``tickets.delete``)
    ``await delete_ticket(app, ticket_id, *, actor_id) -> bool`` (closed only; transcript first)
    ``await generate_transcript(app, ticket_id, *, force=False, requested_by=None) -> bool``
    ``await add_participant(app, ticket, member, *, actor_id) -> bool``
    ``await remove_participant(app, ticket, member, *, actor_id) -> bool``
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import TicketCategory
from shopbot.core.channels import CREATE_PRIVATE_REQUIRED, OWNER_ALLOW, private_overwrites, render_channel_name
from shopbot.core.permissions import missing_permissions
from shopbot.db.repos import notifications as notif_repo
from shopbot.db.repos import state
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import repo, transcript, views
from shopbot.features.tickets.models import (
    CLOSED,
    DELETED,
    KIND_ORDER,
    KIND_SUPPORT,
    OPEN,
    TRANSCRIPT_DONE,
    TRANSCRIPT_FAILED,
    TRANSCRIPT_PENDING,
    TRANSCRIPT_SKIPPED,
    Ticket,
)
from shopbot.features.tickets.runtime import runtime
from shopbot.features.tickets.views import Rows
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, check_message, send_kwargs, send_spec, to_view
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_TRANSCRIPT = "tickets.transcript"
TASK_DELETE = "tickets.delete"
TASK_AUTO_CLOSE = "tickets.auto_close"
TASK_AUTO_CLOSE_WARN = "tickets.auto_close_warn"


def dedupe_transcript(ticket_id: int) -> str:
    return f"tickets.transcript:{ticket_id}"


def dedupe_delete(ticket_id: int) -> str:
    return f"tickets.delete:{ticket_id}"


def dedupe_auto_close(ticket_id: int) -> str:
    return f"tickets.autoclose:{ticket_id}"


def dedupe_auto_warn(ticket_id: int) -> str:
    return f"tickets.autowarn:{ticket_id}"


def closed_message_key(ticket_id: int) -> str:
    return f"tickets.closed_message:{ticket_id}"


# -- errors -----------------------------------------------------------------------------------------------


class TicketError(Exception):
    """Base class of ticket service errors."""


class TicketLimitReached(TicketError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"limit {limit} reached")
        self.limit = limit


class TicketCreateFailed(TicketError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TicketStateError(TicketError):
    """The ticket is not in a state that allows the requested action."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class TranscriptUnavailable(TicketError):
    """A required transcript could not be produced (deletion is refused so it is never lost silently)."""


@dataclass(frozen=True)
class CreatedChannel:
    ticket: Ticket
    channel: discord.TextChannel


# -- reads & small helpers --------------------------------------------------------------------------------


async def get_ticket(app: App, ticket_id: int) -> Ticket | None:
    return await app.db.run(repo.get, ticket_id)


async def get_ticket_by_channel(app: App, channel_id: int) -> Ticket | None:
    return await app.db.run(repo.get_by_channel, channel_id)


async def participants(app: App, ticket_id: int) -> list[int]:
    return await app.db.run(repo.participants, ticket_id)


def format_number(app: App, number: int) -> str:
    return views.number_text(app.settings.tickets, number)


def support_staff_keys(app: App, category_id: str) -> tuple[str, ...]:
    """Role keys (besides permissions.staff_roles/admins) that are staff for a support category."""
    t = app.settings.tickets
    category = t.category(category_id)
    extra = category.staff_roles if category is not None else ()
    return tuple(dict.fromkeys(t.staff_roles + extra))


def staff_keys_for(app: App, ticket: Ticket) -> tuple[str, ...]:
    if ticket.kind == KIND_ORDER:
        return app.settings.checkout.staff_roles
    return support_staff_keys(app, ticket.category_id)


def is_ticket_staff(app: App, member: discord.Member | None, user_id: int, ticket: Ticket) -> bool:
    return app.perms.is_staff(member, user_id, staff_keys_for(app, ticket))


async def staff_role_ids(app: App, extra_keys: tuple[str, ...] | list[str]) -> list[discord.Role]:
    """Resolved staff roles with access to private channels: permissions.staff_roles + admin_roles +
    ``extra_keys`` (unknown or deleted roles are skipped and logged)."""
    guild = app.guild
    if guild is None:
        return []
    p = app.settings.permissions
    keys = list(dict.fromkeys((*p.staff_roles, *p.admin_roles, *extra_keys)))
    roles: list[discord.Role] = []
    for key in keys:
        role = app.resources.role(guild, key)
        if role is None:
            log_event(log, logging.WARNING, "tickets_staff_role_missing", role_key=key)
            continue
        roles.append(role)
    return roles


def _ping_role_ids(app: App, category: TicketCategory) -> tuple[int, ...]:
    keys = (*app.settings.permissions.staff_roles, *support_staff_keys(app, category.id))
    return tuple(sorted(app.resources.role_ids(keys)))


async def ticket_channel(app: App, ticket: Ticket) -> discord.TextChannel | None:
    if ticket.channel_id is None or app.bot is None:
        return None
    channel = app.bot.get_channel(ticket.channel_id)
    if channel is None:
        try:
            channel = await app.bot.fetch_channel(ticket.channel_id)
        except discord.NotFound:
            return None
        except discord.HTTPException as exc:
            log_event(log, logging.WARNING, "tickets_channel_fetch_failed", ticket_id=ticket.id, status=exc.status)
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def _member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_member_fetch_failed", user_id=user_id, status=exc.status)
        return None


def _bot_user_id(app: App) -> int:
    user = app.bot.user if app.bot is not None else None
    return user.id if user is not None else 0


_USER_SLUG = re.compile(r"[^a-z0-9_-]+")


def channel_user_slug(member: discord.abc.User) -> str:
    """Username for {user} in channel names: lower-case letters, digits, '-' and '_' only."""
    slug = _USER_SLUG.sub("-", member.name.lower()).strip("-")[:32]
    return slug or str(member.id)


# -- creation ---------------------------------------------------------------------------------------------


async def reserve_ticket(
    app: App, *, user_id: int, kind: str, category_id: str, limit: int, form_answers: dict[str, str] | None = None
) -> Ticket:
    """Limit check + numbered 'creating' row, atomically. Raises ``TicketLimitReached``."""
    ticket = await app.db.transaction(
        repo.reserve,
        guild_id=app.env.guild_id,
        kind=kind,
        category_id=category_id,
        user_id=user_id,
        form_answers=dict(form_answers or {}),
        limit=limit,
        now=app.clock.now(),
    )
    if ticket is None:
        raise TicketLimitReached(limit)
    return ticket


async def abort_ticket(
    app: App, ticket_id: int, *, reason: str, channel: discord.abc.GuildChannel | None = None
) -> None:
    """Undo a creation that cannot complete: delete the channel (best effort) and mark the row 'failed'."""
    if channel is not None:
        rt = runtime(app)
        rt.deleting.add(ticket_id)
        try:
            await channel.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            log_event(log, logging.ERROR, "tickets_abort_channel_delete_failed", ticket_id=ticket_id, status=exc.status)
        finally:
            rt.deleting.discard(ticket_id)
    failed = await app.db.transaction(repo.mark_failed, ticket_id, app.clock.now())
    if failed is not None:
        log_event(log, logging.ERROR, "tickets_create_failed", ticket_id=ticket_id, reason=reason)
        await app.audit.record(
            "tickets.create_failed",
            actor_id=failed.user_id,
            target_type="ticket",
            target_id=ticket_id,
            details={"number": failed.number, "kind": failed.kind, "category": failed.category_id, "reason": reason},
        )


async def create_ticket_channel(
    app: App,
    ticket: Ticket,
    *,
    member: discord.Member,
    discord_category_key: str,
    channel_prefix_key: str,
    extra_staff_role_keys: tuple[str, ...],
    topic: str | None,
) -> discord.TextChannel:
    """Create the private channel of a reserved ticket and record its id. On failure the ticket is
    aborted ('failed', audited) and ``TicketCreateFailed`` (or ``MissingContent``) is raised."""
    guild = member.guild
    try:
        prefix = app.content.get(channel_prefix_key)
        if prefix is None:
            raise MissingContent(channel_prefix_key)
        category: discord.CategoryChannel | None = None
        if discord_category_key:
            category = app.resources.category(guild, discord_category_key)
            if category is None:
                log_event(log, logging.WARNING, "tickets_discord_category_missing", key=discord_category_key)
        bot_member = guild.me
        missing = missing_permissions(bot_member, category, CREATE_PRIVATE_REQUIRED)
        if missing:
            raise TicketCreateFailed("missing_permissions:" + ",".join(missing))
        roles = await staff_role_ids(app, extra_staff_role_keys)
        overwrites = private_overwrites(guild, owner_id=member.id, staff_roles=roles, bot_member=bot_member)
        t = app.settings.tickets
        name = render_channel_name(
            t.channel_name_pattern,
            prefix=prefix,
            number=ticket.number,
            padding=t.number_padding,
            user=channel_user_slug(member),
        )
        if topic is not None and len(topic) > 1024:
            log_event(log, logging.WARNING, "tickets_topic_too_long", ticket_id=ticket.id, length=len(topic))
            topic = None
        channel = await guild.create_text_channel(
            name,
            category=category,
            overwrites=overwrites,
            topic=topic if topic is not None else discord.utils.MISSING,
        )
    except discord.HTTPException as exc:
        await abort_ticket(app, ticket.id, reason=f"discord_{exc.status}_{exc.code}")
        raise TicketCreateFailed(f"discord_{exc.status}") from exc
    except TicketCreateFailed as exc:
        await abort_ticket(app, ticket.id, reason=exc.reason)
        raise
    except MissingContent as exc:
        await abort_ticket(app, ticket.id, reason=f"missing_content:{exc.key}")
        raise
    await app.db.transaction(repo.set_channel, ticket.id, channel.id, app.clock.now())
    return channel


async def create_private_channel(
    app: App,
    *,
    member: discord.Member,
    kind: str,
    category_id: str,
    discord_category_key: str,
    channel_prefix_key: str,
    extra_staff_role_keys: tuple[str, ...],
    topic: str | None,
    limit: int,
    form_answers: dict[str, str] | None = None,
) -> CreatedChannel:
    """Reusable creation (see module docstring). The ticket stays 'creating' until ``activate_ticket``."""
    if member.guild.id != app.env.guild_id:
        raise TicketCreateFailed("foreign_guild")
    ticket = await reserve_ticket(
        app, user_id=member.id, kind=kind, category_id=category_id, limit=limit, form_answers=form_answers
    )
    channel = await create_ticket_channel(
        app,
        ticket,
        member=member,
        discord_category_key=discord_category_key,
        channel_prefix_key=channel_prefix_key,
        extra_staff_role_keys=extra_staff_role_keys,
        topic=topic,
    )
    refreshed = await get_ticket(app, ticket.id)
    return CreatedChannel(ticket=refreshed or ticket, channel=channel)


async def activate_ticket(app: App, ticket_id: int, *, control_message_id: int | None) -> Ticket:
    opened = await app.db.transaction(repo.activate, ticket_id, control_message_id, app.clock.now())
    if opened is None:
        current = await get_ticket(app, ticket_id)
        raise TicketStateError(current.status if current is not None else "missing")
    return opened


async def open_support_ticket(
    app: App, *, member: discord.Member, category: TicketCategory, answers: dict[str, str]
) -> CreatedChannel:
    """Full support-ticket creation: limit + row, every message prepared before any channel exists,
    private channel, welcome + controls, 'open', staff alert, audit, inactivity tasks."""
    t = app.settings.tickets
    limit = category.max_open_per_user or t.max_open_per_user
    ticket = await reserve_ticket(
        app, user_id=member.id, kind=KIND_SUPPORT, category_id=category.id, limit=limit, form_answers=answers
    )
    try:
        ping = _ping_role_ids(app, category) if category.ping_staff and not t.staff_alert_channel else ()
        welcome = views.welcome_messages(
            app.ui,
            t,
            category,
            ticket_id=ticket.id,
            number=ticket.number,
            user_id=member.id,
            user_name=member.display_name,
            answers=answers,
            ping_role_ids=ping,
        )
        for spec in welcome:
            check_message(spec)
        topic = views.channel_topic(app.ui, t, category, number=ticket.number, user_name=member.display_name)
    except MissingContent as exc:
        await abort_ticket(app, ticket.id, reason=f"missing_content:{exc.key}")
        raise
    except RenderError as exc:
        await abort_ticket(app, ticket.id, reason="limits")
        raise TicketCreateFailed("limits") from exc
    channel = await create_ticket_channel(
        app,
        ticket,
        member=member,
        discord_category_key=category.discord_category,
        channel_prefix_key=k.category_key(category.id, "channel_prefix"),
        extra_staff_role_keys=support_staff_keys(app, category.id),
        topic=topic,
    )
    try:
        control = await send_spec(channel, welcome[0])
        for spec in welcome[1:]:
            await send_spec(channel, spec)
    except (discord.HTTPException, RenderError) as exc:
        await abort_ticket(app, ticket.id, reason=f"welcome_failed:{type(exc).__name__}", channel=channel)
        raise TicketCreateFailed("welcome_failed") from exc
    opened = await activate_ticket(app, ticket.id, control_message_id=control.id)
    await _send_staff_alert(app, opened, category, channel.id)
    await app.audit.record(
        "tickets.created",
        actor_id=member.id,
        target_type="ticket",
        target_id=opened.id,
        details={"number": opened.number, "category": category.id, "channel_id": channel.id},
    )
    await schedule_inactivity(app, opened)
    return CreatedChannel(ticket=opened, channel=channel)


async def _send_staff_alert(app: App, ticket: Ticket, category: TicketCategory, channel_id: int) -> None:
    key = app.settings.tickets.staff_alert_channel
    if not key:
        return
    alert_channel_id = app.resources.channel_id(key)
    if not alert_channel_id:
        log_event(log, logging.WARNING, "tickets_staff_alert_channel_missing", key=key)
        return
    try:
        spec = views.staff_alert(
            app.ui,
            app.settings.tickets,
            category,
            number=ticket.number,
            user_id=ticket.user_id,
            channel_id=channel_id,
            ping_role_ids=_ping_role_ids(app, category) if category.ping_staff else (),
        )
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.staff_alert")
        return
    await app.notifier.notify_channel(
        channel_id=alert_channel_id, spec=spec, kind="tickets.staff_alert", dedupe_key=f"tickets.alert:{ticket.id}"
    )


# -- inactivity ---------------------------------------------------------------------------------------------


async def schedule_inactivity(app: App, ticket: Ticket) -> None:
    """(Re)schedule the auto-close (and warning) tasks of an open support ticket from its last activity."""
    if not ticket.is_support or not ticket.is_open:
        return
    t = app.settings.tickets
    if not k.auto_close_enabled(app.settings):
        await cancel_inactivity(app, ticket.id)
        return
    payload = {"ticket_id": ticket.id}
    await app.scheduler.schedule(
        TASK_AUTO_CLOSE,
        run_at=ticket.last_activity_at + t.auto_close_inactive_minutes * 60,
        payload=payload,
        dedupe_key=dedupe_auto_close(ticket.id),
        replace=True,
    )
    if k.auto_close_warning_enabled(app.settings):
        await app.scheduler.schedule(
            TASK_AUTO_CLOSE_WARN,
            run_at=ticket.last_activity_at + (t.auto_close_inactive_minutes - t.auto_close_warning_minutes) * 60,
            payload=payload,
            dedupe_key=dedupe_auto_warn(ticket.id),
            replace=True,
        )
    else:
        await app.scheduler.cancel(dedupe_auto_warn(ticket.id))


async def cancel_inactivity(app: App, ticket_id: int) -> None:
    await app.scheduler.cancel(dedupe_auto_close(ticket_id))
    await app.scheduler.cancel(dedupe_auto_warn(ticket_id))


async def cancel_all_tasks(app: App, ticket_id: int) -> None:
    await cancel_inactivity(app, ticket_id)
    await app.scheduler.cancel(dedupe_delete(ticket_id))
    await app.scheduler.cancel(dedupe_transcript(ticket_id))


async def record_activity(app: App, channel_id: int, *, min_interval: int) -> Ticket | None:
    """Record activity in an open ticket channel (throttled) and push its inactivity tasks back."""
    ticket = await app.db.transaction(repo.touch_activity, channel_id, app.clock.now(), min_interval)
    if ticket is not None:
        await schedule_inactivity(app, ticket)
    return ticket


# -- Discord helpers for state changes ---------------------------------------------------------------------


async def _set_access(app: App, channel: discord.TextChannel, ticket: Ticket, *, can_send: bool) -> None:
    """Owner (and added participants) keep read access; ``can_send`` controls posting."""
    perms = dict(OWNER_ALLOW)
    if not can_send:
        perms.update(send_messages=False, attach_files=False)
    user_ids = [ticket.user_id, *await participants(app, ticket.id)]
    for user_id in dict.fromkeys(user_ids):
        member = await _member(channel.guild, user_id)
        if member is None:
            continue
        try:
            await channel.set_permissions(member, overwrite=discord.PermissionOverwrite(**perms))
        except discord.HTTPException as exc:
            log_event(log, logging.WARNING, "tickets_permission_update_failed", ticket_id=ticket.id, status=exc.status)


async def _move(app: App, channel: discord.TextChannel, category_key: str, ticket_id: int) -> None:
    category = app.resources.category(channel.guild, category_key)
    if category is None:
        log_event(log, logging.WARNING, "tickets_move_category_missing", ticket_id=ticket_id, key=category_key)
        return
    if channel.category_id == category.id:
        return
    try:
        await channel.edit(category=category)
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_move_failed", ticket_id=ticket_id, status=exc.status)


async def _post(app: App, channel: discord.TextChannel, spec: MessageSpec, *, ticket_id: int) -> discord.Message | None:
    try:
        return await send_spec(channel, spec)
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "tickets_post_failed", ticket_id=ticket_id, error=type(exc).__name__)
        return None


async def _post_built(
    app: App, channel: discord.TextChannel, ticket: Ticket, build: Callable[[], MessageSpec], where: str
) -> None:
    """Post a status message built by ``build()``; missing content is logged and nothing is sent."""
    try:
        spec = build()
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where=where)
        return
    await _post(app, channel, spec, ticket_id=ticket.id)


async def _set_controls(app: App, channel: discord.TextChannel, ticket: Ticket, rows: Rows) -> None:
    """Replace the buttons of the welcome message (no rows removes them); its text is left unchanged."""
    if ticket.control_message_id is None:
        return
    message = channel.get_partial_message(ticket.control_message_id)
    try:
        await message.edit(view=to_view(MessageSpec(rows=rows)) if rows else None)
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_controls_update_failed", ticket_id=ticket.id, status=exc.status)


def _open_controls(app: App, ticket: Ticket) -> Rows | None:
    try:
        return views.control_rows(app.ui, ticket.id, assigned=ticket.assigned_staff_id is not None)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.controls")
        return None


# -- close / reopen ---------------------------------------------------------------------------------------


async def close_ticket(
    app: App,
    ticket_id: int,
    *,
    actor_id: int | None,
    reason: str | None = None,
    transcript: bool | None = None,
    delete_after_minutes: int | None = None,
    lock: bool | None = None,
    closed_category_key: str | None = None,
) -> Ticket | None:
    """Close an open ticket (``actor_id=None``: automatic). Returns None when it was not open."""
    current = await get_ticket(app, ticket_id)
    if current is None:
        return None
    t = app.settings.tickets
    support = current.is_support
    if transcript is None:
        transcript = bool(t.transcript_channel) and (support or app.settings.checkout.transcript_on_close)
    if delete_after_minutes is None:
        delete_after_minutes = (
            t.delete_after_close_minutes if support else app.settings.checkout.delete_channel_after_close_minutes
        )
    if lock is None:
        lock = t.lock_on_close if support else True
    if closed_category_key is None:
        closed_category_key = t.closed_category if support else ""
    now = app.clock.now()
    closed = await app.db.transaction(
        repo.close,
        ticket_id,
        closed_by=actor_id,
        reason=reason,
        transcript_status=TRANSCRIPT_PENDING if transcript else TRANSCRIPT_SKIPPED,
        now=now,
    )
    if closed is None:
        return None
    await cancel_inactivity(app, ticket_id)
    channel = await ticket_channel(app, closed)
    if channel is not None:
        if lock:
            await _set_access(app, channel, closed, can_send=False)
        if closed_category_key:
            await _move(app, channel, closed_category_key, ticket_id)
        if support:
            await _post_closed_ui(app, channel, closed)
    if transcript:
        await app.scheduler.schedule(
            TASK_TRANSCRIPT,
            run_at=now,
            payload={"ticket_id": ticket_id},
            dedupe_key=dedupe_transcript(ticket_id),
            replace=True,
        )
    if delete_after_minutes > 0:
        await schedule_channel_deletion(app, ticket_id, delete_after_minutes)
        if support and channel is not None:
            minutes = delete_after_minutes
            await _post_built(
                app, channel, closed, lambda: views.deletion_scheduled(app.ui, minutes), "tickets.deletion_scheduled"
            )
    await app.audit.record(
        "tickets.closed",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket_id,
        details={"number": closed.number, "kind": closed.kind, "reason": reason, "automatic": actor_id is None},
    )
    return closed


async def _post_closed_ui(app: App, channel: discord.TextChannel, ticket: Ticket) -> None:
    t = app.settings.tickets
    try:
        spec: MessageSpec | None = views.closed_message(
            app.ui,
            t,
            ticket_id=ticket.id,
            number=ticket.number,
            closer_id=ticket.closed_by,
            reason=ticket.close_reason,
            transcripts=bool(t.transcript_channel),
        )
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.closed")
        spec = None
    if spec is not None:
        message = await _post(app, channel, spec, ticket_id=ticket.id)
        if message is not None:
            await app.db.transaction(state.kv_set, closed_message_key(ticket.id), str(message.id), app.clock.now())
    await _set_controls(app, channel, ticket, ())


async def schedule_channel_deletion(app: App, ticket_id: int, minutes: int) -> None:
    await app.scheduler.schedule(
        TASK_DELETE,
        run_at=app.clock.now() + max(0, minutes) * 60,
        payload={"ticket_id": ticket_id},
        dedupe_key=dedupe_delete(ticket_id),
        replace=True,
    )


async def reopen_ticket(app: App, ticket_id: int, *, actor_id: int) -> Ticket | None:
    """closed -> open: restore access, move back, cancel deletion, restore controls. None if not closed."""
    reopened = await app.db.transaction(repo.reopen, ticket_id, app.clock.now())
    if reopened is None:
        return None
    await app.scheduler.cancel(dedupe_delete(ticket_id))
    await app.scheduler.cancel(dedupe_transcript(ticket_id))
    channel = await ticket_channel(app, reopened)
    if channel is not None:
        await _set_access(app, channel, reopened, can_send=True)
        if reopened.is_support:
            category = app.settings.tickets.category(reopened.category_id)
            if category is not None and category.discord_category:
                await _move(app, channel, category.discord_category, ticket_id)
            await _disable_closed_message(app, channel, ticket_id)
            await _post_built(app, channel, reopened, lambda: views.reopened(app.ui, actor_id), "tickets.reopened")
            rows = _open_controls(app, reopened)
            if rows is not None:
                await _set_controls(app, channel, reopened, rows)
    await schedule_inactivity(app, reopened)
    await app.audit.record(
        "tickets.reopened",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket_id,
        details={"number": reopened.number},
    )
    return reopened


async def _disable_closed_message(app: App, channel: discord.TextChannel, ticket_id: int) -> None:
    raw = await app.db.run(state.kv_get, closed_message_key(ticket_id))
    if not raw:
        return
    try:
        await channel.get_partial_message(int(raw)).edit(view=None)
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "tickets_closed_message_update_failed", ticket_id=ticket_id, status=exc.status)


# -- claim ----------------------------------------------------------------------------------------------------


async def claim_ticket(app: App, ticket_id: int, *, staff_id: int) -> Ticket | None:
    claimed = await app.db.transaction(repo.claim, ticket_id, staff_id, app.clock.now())
    if claimed is None:
        return None
    if claimed.is_support:
        await _after_assignment(app, claimed, lambda: views.claimed(app.ui, staff_id))
    await app.audit.record(
        "tickets.claimed",
        actor_id=staff_id,
        target_type="ticket",
        target_id=ticket_id,
        details={"number": claimed.number},
    )
    return claimed


async def unclaim_ticket(app: App, ticket_id: int, *, actor_id: int, assignee_id: int) -> Ticket | None:
    unclaimed = await app.db.transaction(repo.unclaim, ticket_id, assignee_id, app.clock.now())
    if unclaimed is None:
        return None
    if unclaimed.is_support:
        await _after_assignment(app, unclaimed, lambda: views.unclaimed(app.ui))
    await app.audit.record(
        "tickets.unclaimed",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket_id,
        details={"number": unclaimed.number, "previous_assignee": assignee_id},
    )
    return unclaimed


async def _after_assignment(app: App, ticket: Ticket, build: Callable[[], MessageSpec]) -> None:
    channel = await ticket_channel(app, ticket)
    if channel is None:
        return
    await _post_built(app, channel, ticket, build, "tickets.assignment")
    rows = _open_controls(app, ticket)
    if rows is not None:
        await _set_controls(app, channel, ticket, rows)


# -- participants ----------------------------------------------------------------------------------------------


async def add_participant(app: App, ticket: Ticket, member: discord.Member, *, actor_id: int) -> bool:
    """Give ``member`` access. Returns False if they were already added. Raises ``discord.HTTPException``
    when Discord rejects the permission change (nothing is recorded then)."""
    if member.id in await participants(app, ticket.id):
        return False
    channel = await ticket_channel(app, ticket)
    if channel is None:
        raise TicketStateError("channel_missing")
    perms = dict(OWNER_ALLOW)
    if ticket.status == CLOSED and app.settings.tickets.lock_on_close:
        perms.update(send_messages=False, attach_files=False)
    await channel.set_permissions(member, overwrite=discord.PermissionOverwrite(**perms))
    added = await app.db.transaction(repo.add_participant, ticket.id, member.id, actor_id, app.clock.now())
    if not added:
        return False
    if ticket.is_support:
        await _post_built(app, channel, ticket, lambda: views.user_added(app.ui, member.id), "tickets.user_added")
    await app.audit.record(
        "tickets.participant_added",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket.id,
        details={"number": ticket.number, "user_id": member.id},
    )
    return True


async def remove_participant(app: App, ticket: Ticket, member: discord.Member, *, actor_id: int) -> bool:
    """Revoke ``member``'s access. Returns False if they were not an added participant."""
    if member.id not in await participants(app, ticket.id):
        return False
    channel = await ticket_channel(app, ticket)
    if channel is None:
        raise TicketStateError("channel_missing")
    await channel.set_permissions(member, overwrite=None)
    removed = await app.db.transaction(repo.remove_participant, ticket.id, member.id)
    if not removed:
        return False
    if ticket.is_support:
        await _post_built(app, channel, ticket, lambda: views.user_removed(app.ui, member.id), "tickets.user_removed")
    await app.audit.record(
        "tickets.participant_removed",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket.id,
        details={"number": ticket.number, "user_id": member.id},
    )
    return True


# -- transcripts ---------------------------------------------------------------------------------------------


async def render_transcript(app: App, ticket: Ticket) -> tuple[bytes, str] | None:
    """Build the transcript file (bytes, filename) from the channel history; None if unreadable."""
    channel = await ticket_channel(app, ticket)
    if channel is None:
        return None
    rt = runtime(app)
    if not app.settings.bot.message_content_intent and not rt.warned_no_content_intent:
        rt.warned_no_content_intent = True
        log_event(
            log,
            logging.WARNING,
            "tickets_transcript_without_content_intent",
            hint="bot.message_content_intent is false: transcripts contain no message text",
        )
    try:
        messages = await transcript.collect_messages(channel)
    except discord.HTTPException as exc:
        log_event(log, logging.ERROR, "tickets_transcript_history_failed", ticket_id=ticket.id, status=exc.status)
        return None
    added = await participants(app, ticket.id)
    names: dict[int, str] = {}
    for user_id in (ticket.user_id, *added):
        member = channel.guild.get_member(user_id)
        if member is not None:
            names[user_id] = member.display_name
    number = format_number(app, ticket.number)
    document = transcript.build_html(
        number=number,
        category_id=ticket.category_id,
        created_at=ticket.created_at,
        closed_at=ticket.closed_at,
        participants=transcript.participants_of(ticket.user_id, added, messages, names),
        messages=messages,
    )
    return document.encode("utf-8"), f"transcript-{number}.html"


async def generate_transcript(
    app: App, ticket_id: int, *, force: bool = False, requested_by: int | None = None
) -> bool:
    """Generate and post the transcript to the transcript channel.

    Without ``force`` a transcript is only produced when one is pending (or previously failed); the
    result is True when no transcript is outstanding afterwards. With ``force`` a fresh transcript is
    always posted (staff request). Serialized per ticket, so concurrent callers never double-post.
    """
    async with runtime(app).transcript_lock(ticket_id):
        ticket = await get_ticket(app, ticket_id)
        if ticket is None:
            return False
        outstanding = ticket.transcript_status in (TRANSCRIPT_PENDING, TRANSCRIPT_FAILED)
        if not force and not outstanding:
            return True
        t = app.settings.tickets
        now = app.clock.now()
        if not t.transcript_channel:
            if outstanding:
                # Transcripts were switched off by the operator after this ticket was closed.
                log_event(log, logging.WARNING, "tickets_transcript_disabled", ticket_id=ticket_id)
                await app.db.transaction(repo.set_transcript, ticket_id, status=TRANSCRIPT_SKIPPED, now=now)
            return not force
        guild = app.guild
        target = app.resources.text_channel(guild, t.transcript_channel) if guild is not None else None
        if target is None:
            log_event(log, logging.ERROR, "tickets_transcript_channel_missing", key=t.transcript_channel)
            return False
        if await ticket_channel(app, ticket) is None:
            log_event(log, logging.ERROR, "tickets_transcript_source_missing", ticket_id=ticket_id)
            if ticket.transcript_status == TRANSCRIPT_PENDING:
                await app.db.transaction(
                    repo.set_transcript, ticket_id, status=TRANSCRIPT_FAILED, now=now, only_if=(TRANSCRIPT_PENDING,)
                )
            return False
        rendered = await render_transcript(app, ticket)
        if rendered is None:
            return False
        data, filename = rendered
        if ticket.closed_at is not None:
            closer_id = ticket.closed_by or _bot_user_id(app)
        else:
            closer_id = requested_by or _bot_user_id(app)
        try:
            spec = views.transcript_post(app.ui, t, number=ticket.number, user_id=ticket.user_id, closer_id=closer_id)
            message = await target.send(file=discord.File(io.BytesIO(data), filename=filename), **send_kwargs(spec))
        except MissingContent as exc:
            log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.transcript")
            return False
        except (discord.HTTPException, RenderError) as exc:
            log_event(
                log, logging.ERROR, "tickets_transcript_post_failed", ticket_id=ticket_id, error=type(exc).__name__
            )
            return False
        await app.db.transaction(
            repo.set_transcript, ticket_id, status=TRANSCRIPT_DONE, message_id=message.id, now=app.clock.now()
        )
        await app.audit.record(
            "tickets.transcript_posted",
            actor_id=requested_by,
            target_type="ticket",
            target_id=ticket_id,
            details={"number": ticket.number, "message_id": message.id, "bytes": len(data)},
        )
        if t.transcript_dm_user and ticket.status in (CLOSED, DELETED):
            await _dm_transcript(app, ticket, data, filename)
        return True


async def _dm_transcript(app: App, ticket: Ticket, data: bytes, filename: str) -> None:
    """Send the transcript file to the ticket owner by DM, at most once per ticket."""
    dedupe_key = f"tickets.transcript_dm:{ticket.id}"
    if await app.db.run(notif_repo.already_sent, dedupe_key) or app.bot is None:
        return
    try:
        spec = views.transcript_dm(app.ui, app.settings.tickets, number=ticket.number)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="tickets.transcript_dm")
        return
    status, error = "sent", None
    try:
        user = app.bot.get_user(ticket.user_id) or await app.bot.fetch_user(ticket.user_id)
        await user.send(file=discord.File(io.BytesIO(data), filename=filename), **send_kwargs(spec))
    except discord.Forbidden:
        status, error = "failed", "dm_forbidden"
        await app.db.transaction(state.set_dm_unavailable, ticket.user_id, app.clock.now())
    except (discord.HTTPException, RenderError) as exc:
        status, error = "failed", type(exc).__name__
    log_event(log, logging.INFO, "tickets_transcript_dm", ticket_id=ticket.id, status=status, error=error)
    await app.db.transaction(
        notif_repo.record,
        dedupe_key=dedupe_key,
        user_id=ticket.user_id,
        channel_id=None,
        kind="tickets.transcript_dm",
        delivery="dm" if status == "sent" else "none",
        status=status,
        error=error,
        now=app.clock.now(),
    )


# -- deletion -----------------------------------------------------------------------------------------------


async def delete_ticket(app: App, ticket_id: int, *, actor_id: int | None) -> bool:
    """Delete a closed ticket's channel. An outstanding transcript is generated first; if that fails,
    ``TranscriptUnavailable`` is raised and nothing is deleted. Raises ``TicketStateError`` when the
    ticket is not closed and ``discord.HTTPException`` when Discord refuses the deletion. Returns False
    if another path already deleted it."""
    ticket = await get_ticket(app, ticket_id)
    if ticket is None:
        raise TicketStateError("missing")
    if ticket.status != CLOSED:
        raise TicketStateError(ticket.status)
    outstanding = ticket.transcript_status in (TRANSCRIPT_PENDING, TRANSCRIPT_FAILED)
    if outstanding and not await generate_transcript(app, ticket_id):
        log_event(log, logging.ERROR, "tickets_delete_refused_transcript", ticket_id=ticket_id)
        raise TranscriptUnavailable(str(ticket_id))
    rt = runtime(app)
    rt.deleting.add(ticket_id)
    try:
        channel = await ticket_channel(app, ticket)
        if channel is not None:
            with contextlib.suppress(discord.NotFound):
                await channel.delete()
        result = await app.db.transaction(repo.mark_deleted, ticket_id, app.clock.now(), external=False)
    finally:
        rt.deleting.discard(ticket_id)
    if result is None:
        return False
    await cancel_all_tasks(app, ticket_id)
    await app.audit.record(
        "tickets.deleted",
        actor_id=actor_id,
        target_type="ticket",
        target_id=ticket_id,
        details={"number": ticket.number, "kind": ticket.kind, "automatic": actor_id is None},
    )
    return True


async def channel_deleted(app: App, channel_id: int) -> Ticket | None:
    """A ticket channel disappeared without us (deleted manually): record it. Returns the updated ticket."""
    ticket = await get_ticket_by_channel(app, channel_id)
    if ticket is None or ticket.id in runtime(app).deleting:
        return None
    now = app.clock.now()
    if ticket.status == "creating":
        failed = await app.db.transaction(repo.mark_failed, ticket.id, now)
        if failed is not None:
            await app.audit.record(
                "tickets.create_failed",
                actor_id=None,
                target_type="ticket",
                target_id=ticket.id,
                details={"number": ticket.number, "reason": "channel_deleted"},
            )
        return failed
    result = await app.db.transaction(repo.mark_deleted, ticket.id, now, external=True, reason="channel_deleted")
    if result is None:
        return None
    before, after = result
    await cancel_all_tasks(app, ticket.id)
    log_event(log, logging.WARNING, "tickets_channel_deleted_externally", ticket_id=ticket.id, was=before.status)
    await app.audit.record(
        "tickets.channel_deleted",
        actor_id=None,
        target_type="ticket",
        target_id=ticket.id,
        details={"number": ticket.number, "previous_status": before.status, "transcript": after.transcript_status},
    )
    return after


__all__ = [
    "KIND_ORDER",
    "KIND_SUPPORT",
    "OPEN",
    "CreatedChannel",
    "TicketCreateFailed",
    "TicketError",
    "TicketLimitReached",
    "TicketStateError",
    "TranscriptUnavailable",
    "abort_ticket",
    "activate_ticket",
    "add_participant",
    "claim_ticket",
    "close_ticket",
    "create_private_channel",
    "delete_ticket",
    "format_number",
    "generate_transcript",
    "get_ticket",
    "get_ticket_by_channel",
    "participants",
    "remove_participant",
    "reopen_ticket",
    "schedule_channel_deletion",
    "staff_role_ids",
    "ticket_channel",
    "unclaim_ticket",
]
