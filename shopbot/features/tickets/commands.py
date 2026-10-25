"""``/ticket`` staff commands. They act on the support ticket of the channel they are run in (``list``
works anywhere). ``close`` also works in an order ticket (``kind='order'``): the checkout feature's registered
closer ends an unfinished order first and refuses a submitted open order (see
``service.register_staff_closer``). Hidden from non-staff by default permissions; authorized by ``require_staff``
(any ticket or order staff role) and again per ticket (staff of that ticket's category, or order staff). Replies
are private operator text with a banner: acknowledgements ``general-response``, refusals and failures ``error``,
transcripts ``transcripts``, ``info``/``list`` ``staff-dashboard``."""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands
from discord.utils import escape_markdown

from shopbot.commands import app_of, operator_fields, reply, reply_operator, require_staff
from shopbot.config.banner_ids import BannerId
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import repo, service, views
from shopbot.features.tickets.models import CLOSED, KIND_ORDER, KIND_SUPPORT, OPEN, Ticket
from shopbot.logging_setup import log_event
from shopbot.ui.spec import EmbedField, MessageSpec
from shopbot.ui.style import chunk_lines

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

StatusChoice = Literal["creating", "open", "closed", "deleted", "failed"]
LIST_LIMIT = 25
LIST_SCAN = 500
# The feature that owns order tickets (kind 'order'; its closer is registered with service.register_staff_closer).
ORDER_FEATURE = "checkout"
CLOSE_DESCRIPTION = "Close this ticket (also an order ticket whose order was not submitted)."
CLOSE_REASON_DESCRIPTION = "Optional reason (shown on a support ticket's closed message if configured; audited)"
# Banners of the replies.
ACK: BannerId = "general-response"  # done, or nothing to do (already added, already claimed, ...)
REFUSED: BannerId = "error"  # the command could not do what was asked
OVERVIEW: BannerId = "staff-dashboard"  # /ticket info and /ticket list


def command_staff_keys(app: App) -> tuple[str, ...]:
    """Every role key that is staff for at least one ticket category or for order tickets (``checkout.staff_roles``,
    for ``/ticket close`` in an order ticket); per-ticket checks follow."""
    t = app.settings.tickets
    keys = list(t.staff_roles)
    for category in t.categories:
        keys.extend(category.staff_roles)
    keys.extend(service.external_staff_keys(app))
    keys.extend(app.settings.checkout.staff_roles)
    return tuple(dict.fromkeys(keys))


def _member(interaction: discord.Interaction[Any]) -> discord.Member | None:
    return interaction.user if isinstance(interaction.user, discord.Member) else None


def _when(timestamp: int | None) -> str:
    return f"<t:{timestamp}:f>" if timestamp else "-"


async def _reply(
    interaction: discord.Interaction[Any],
    banner: BannerId,
    title: str,
    description: str | None = None,
    *,
    tone: str = "info",
    fields: Sequence[EmbedField] = (),
) -> None:
    """``reply_operator`` with the banner of the reply's kind."""
    banner_file = app_of(interaction).ui.banner(banner)
    await reply_operator(interaction, title, description, tone=tone, fields=fields, banner=banner_file)


async def _refuse(interaction: discord.Interaction[Any], text: str, *, tone: str = "warning") -> None:
    await _reply(interaction, REFUSED, text, tone=tone)


async def _ticket_here(
    interaction: discord.Interaction[Any], kinds: tuple[str, ...] = (KIND_SUPPORT,)
) -> tuple[App, Ticket] | None:
    """The ticket of the channel the command runs in, when it is one of ``kinds`` and the caller is its staff.

    Support tickets need the ticket feature to be ready. Order tickets belong to checkout: they are available while
    checkout is switched on, even when the support-ticket texts are not configured (closing one needs none)."""
    app = app_of(interaction)
    channel_id = interaction.channel_id
    ticket = await service.get_ticket_by_channel(app, channel_id) if channel_id else None
    if ticket is not None and ticket.kind == KIND_ORDER and KIND_ORDER in kinds:
        if not app.settings.features.checkout:
            await _refuse(interaction, app.op("command.feature_disabled", feature=ORDER_FEATURE))
            return None
    elif not app.ready(k.FEATURE):
        await _refuse(interaction, app.op("command.feature_disabled", feature=k.FEATURE))
        return None
    if ticket is None or ticket.kind not in kinds or ticket.guild_id != app.env.guild_id:
        await _refuse(interaction, app.op("tickets.cmd.not_ticket"))
        return None
    if not service.is_ticket_staff(app, _member(interaction), interaction.user.id, ticket):
        await _refuse(interaction, app.op("tickets.cmd.not_staff_here"), tone="danger")
        return None
    return app, ticket


async def _wrong_state(interaction: discord.Interaction[Any], app: App, ticket: Ticket) -> None:
    await _refuse(
        interaction,
        app.op("tickets.cmd.wrong_state", number=service.format_number(app, ticket.number), status=ticket.status),
    )


async def _defer(interaction: discord.Interaction[Any]) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def ticket_add(interaction: discord.Interaction[Any], member: discord.Member) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    number = service.format_number(app, ticket.number)
    if member.bot:
        await _refuse(interaction, app.op("tickets.cmd.bot_member"))
        return
    if member.id == ticket.user_id:
        await _reply(interaction, ACK, app.op("tickets.cmd.owner", user=member.mention, number=number))
        return
    if ticket.status not in (OPEN, CLOSED):
        await _wrong_state(interaction, app, ticket)
        return
    await _defer(interaction)
    try:
        added = await service.add_participant(app, ticket, member, actor_id=interaction.user.id)
    except service.TicketStateError:
        await _wrong_state(interaction, app, ticket)
        return
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_add_failed", ticket_id=ticket.id, status=exc.status)
        await _refuse(interaction, app.op("tickets.cmd.discord_error", status=exc.status), tone="danger")
        return
    key = "tickets.cmd.added" if added else "tickets.cmd.already_added"
    await _reply(interaction, ACK, app.op(key, user=member.mention, number=number), tone="success")


async def ticket_remove(interaction: discord.Interaction[Any], member: discord.Member) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    number = service.format_number(app, ticket.number)
    if member.id == ticket.user_id:
        await _reply(interaction, ACK, app.op("tickets.cmd.owner", user=member.mention, number=number))
        return
    if ticket.status not in (OPEN, CLOSED):
        await _wrong_state(interaction, app, ticket)
        return
    await _defer(interaction)
    try:
        removed = await service.remove_participant(app, ticket, member, actor_id=interaction.user.id)
    except service.TicketStateError:
        await _wrong_state(interaction, app, ticket)
        return
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_remove_failed", ticket_id=ticket.id, status=exc.status)
        await _refuse(interaction, app.op("tickets.cmd.discord_error", status=exc.status), tone="danger")
        return
    key = "tickets.cmd.removed" if removed else "tickets.cmd.not_participant"
    await _reply(interaction, ACK, app.op(key, user=member.mention, number=number), tone="success")


async def ticket_close(interaction: discord.Interaction[Any], reason: str | None = None) -> None:
    found = await _ticket_here(interaction, (KIND_SUPPORT, KIND_ORDER))
    if found is None:
        return
    app, ticket = found
    if ticket.status != OPEN:
        await _wrong_state(interaction, app, ticket)
        return
    if ticket.kind != KIND_SUPPORT:
        await _close_other_kind(interaction, app, ticket, reason or None)
        return
    await _defer(interaction)
    closed = await service.close_ticket(app, ticket.id, actor_id=interaction.user.id, reason=reason or None)
    if closed is None:
        current = await service.get_ticket(app, ticket.id)
        await _wrong_state(interaction, app, current or ticket)
        return
    await _reply(
        interaction, ACK, app.op("tickets.cmd.closed", number=service.format_number(app, ticket.number)), tone="success"
    )


async def _close_other_kind(
    interaction: discord.Interaction[Any], app: App, ticket: Ticket, reason: str | None
) -> None:
    """``/ticket close`` in a ticket of another kind (an order ticket): its feature's registered closer decides (it
    re-reads the ticket and its records; e.g. a submitted open order is refused). Any failure is answered with
    operator text; nothing internal is shown."""
    closer = service.staff_closer(app, ticket.kind)
    if closer is None:
        await _refuse(interaction, app.op("tickets.cmd.not_ticket"))
        return
    await _defer(interaction)
    try:
        result = await closer(app, ticket, interaction.user.id, reason)
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "tickets_close_failed", ticket_id=ticket.id, status=exc.status)
        await _refuse(interaction, app.op("tickets.cmd.discord_error", status=exc.status), tone="danger")
        return
    # The closer's answer about the order ticket (closed, or why not) is a response, whatever its tone.
    await _reply(interaction, ACK, result.text, result.detail, tone=result.tone)


async def ticket_reopen(interaction: discord.Interaction[Any]) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    if not app.settings.tickets.allow_reopen:
        await _refuse(interaction, app.op("tickets.cmd.reopen_disabled"))
        return
    if ticket.status != CLOSED:
        await _wrong_state(interaction, app, ticket)
        return
    await _defer(interaction)
    reopened = await service.reopen_ticket(app, ticket.id, actor_id=interaction.user.id)
    if reopened is None:
        current = await service.get_ticket(app, ticket.id)
        await _wrong_state(interaction, app, current or ticket)
        return
    await _reply(
        interaction,
        ACK,
        app.op("tickets.cmd.reopened", number=service.format_number(app, ticket.number)),
        tone="success",
    )


async def ticket_claim(interaction: discord.Interaction[Any]) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    number = service.format_number(app, ticket.number)
    if ticket.status != OPEN:
        await _wrong_state(interaction, app, ticket)
        return
    if ticket.assigned_staff_id is not None:
        staff = views.user_mention(ticket.assigned_staff_id)
        await _reply(interaction, ACK, app.op("tickets.cmd.already_claimed", number=number, staff=staff))
        return
    await _defer(interaction)
    claimed = await service.claim_ticket(app, ticket.id, staff_id=interaction.user.id)
    if claimed is None:
        current = await service.get_ticket(app, ticket.id) or ticket
        staff = views.user_mention(current.assigned_staff_id) if current.assigned_staff_id else "-"
        await _reply(interaction, ACK, app.op("tickets.cmd.already_claimed", number=number, staff=staff))
        return
    await _reply(interaction, ACK, app.op("tickets.cmd.claimed", number=number), tone="success")


async def ticket_transcript(interaction: discord.Interaction[Any]) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    number = service.format_number(app, ticket.number)
    await _defer(interaction)
    t = app.settings.tickets
    if t.transcript_channel:
        if await service.generate_transcript(app, ticket.id, force=True, requested_by=interaction.user.id):
            channel_id = app.resources.channel_id(t.transcript_channel) or 0
            text = app.op("tickets.cmd.transcript_posted", number=number, channel=views.channel_mention(channel_id))
            await _reply(interaction, views.TRANSCRIPT_BANNER, text, tone="success")
        else:
            await _refuse(interaction, app.op("tickets.cmd.transcript_failed", number=number), tone="danger")
        return
    rendered = await service.render_transcript(app, ticket)
    if rendered is None:
        await _refuse(interaction, app.op("tickets.cmd.transcript_failed", number=number), tone="danger")
        return
    data, filename = rendered
    await reply_operator(
        interaction,
        app.op("tickets.cmd.transcript_attached", number=number),
        files=[discord.File(io.BytesIO(data), filename=filename)],  # uploaded after the banner
        banner=service.room_for_file(app.ui.banner(views.TRANSCRIPT_BANNER), len(data)),
    )


async def ticket_info(interaction: discord.Interaction[Any]) -> None:
    found = await _ticket_here(interaction)
    if found is None:
        return
    app, ticket = found
    added = await service.participants(app, ticket.id)
    pairs = [
        (app.op("tickets.info.status"), ticket.status),
        (app.op("tickets.info.category"), ticket.category_id),
        (app.op("tickets.info.owner"), views.user_mention(ticket.user_id)),
        (
            app.op("tickets.info.assigned"),
            views.user_mention(ticket.assigned_staff_id) if ticket.assigned_staff_id else "-",
        ),
        (app.op("tickets.info.channel"), views.channel_mention(ticket.channel_id) if ticket.channel_id else "-"),
        (app.op("tickets.info.created"), _when(ticket.created_at)),
        (app.op("tickets.info.last_activity"), _when(ticket.last_activity_at)),
        (app.op("tickets.info.transcript"), ticket.transcript_status),
        (app.op("tickets.info.participants"), " ".join(views.user_mention(u) for u in added) or "-"),
    ]
    if ticket.closed_at is not None:
        closer = views.user_mention(ticket.closed_by) if ticket.closed_by else app.op("tickets.info.automatic")
        pairs += [
            (app.op("tickets.info.closed"), _when(ticket.closed_at)),
            (app.op("tickets.info.closed_by"), closer),
            (app.op("tickets.info.close_reason"), escape_markdown(ticket.close_reason) if ticket.close_reason else "-"),
        ]
    await _reply(
        interaction,
        OVERVIEW,
        app.op("tickets.info.title", number=service.format_number(app, ticket.number)),
        fields=operator_fields(pairs, inline=True),
    )


async def ticket_list(
    interaction: discord.Interaction[Any], status: StatusChoice | None = None, member: discord.Member | None = None
) -> None:
    app = app_of(interaction)
    if not app.ready(k.FEATURE):
        await _refuse(interaction, app.op("command.feature_disabled", feature=k.FEATURE))
        return
    rows = await app.db.run(
        repo.list_tickets,
        app.env.guild_id,
        kind=KIND_SUPPORT,
        statuses=(status,) if status else (),
        user_id=member.id if member is not None else None,
        limit=LIST_SCAN,
    )
    # Category-scoped staff only see the tickets of the categories they are staff for.
    caller = _member(interaction)
    tickets = [t for t in rows if service.is_ticket_staff(app, caller, interaction.user.id, t)][:LIST_LIMIT]
    title = app.op("tickets.list.title", count=len(tickets))
    if not tickets:
        await _reply(interaction, OVERVIEW, title, app.op("tickets.list.empty"))
        return
    lines = [
        app.op(
            "tickets.list.line",
            number=service.format_number(app, t.number),
            status=t.status,
            category=t.category_id,
            owner=views.user_mention(t.user_id),
            channel=views.channel_mention(t.channel_id) if t.channel_id else "-",
        )
        for t in tickets
    ]
    blocks = chunk_lines(lines, 4000)
    embeds = [app.ui.operator_embed(title, blocks[0])]
    embeds += [app.ui.operator_embed(title, block) for block in blocks[1:]]
    await reply(interaction, MessageSpec(embeds=tuple(embeds[:10]), banner=app.ui.banner(OVERVIEW)), ephemeral=True)


def build_group() -> app_commands.Group:
    """A fresh ``/ticket`` group (one per application instance)."""
    group = app_commands.Group(name="ticket", description="Support ticket tools for staff.")
    app_commands.guild_only(group)
    app_commands.default_permissions(manage_messages=True)(group)

    @group.command(name="add", description="Give a member access to this ticket.")
    @app_commands.describe(member="Member to add")
    @require_staff(command_staff_keys)
    async def add(interaction: discord.Interaction[Any], member: discord.Member) -> None:
        await ticket_add(interaction, member)

    @group.command(name="remove", description="Remove a member that was added to this ticket.")
    @app_commands.describe(member="Member to remove")
    @require_staff(command_staff_keys)
    async def remove(interaction: discord.Interaction[Any], member: discord.Member) -> None:
        await ticket_remove(interaction, member)

    @group.command(name="close", description=CLOSE_DESCRIPTION)
    @app_commands.describe(reason=CLOSE_REASON_DESCRIPTION)
    @require_staff(command_staff_keys)
    async def close(
        interaction: discord.Interaction[Any], reason: app_commands.Range[str, 1, 500] | None = None
    ) -> None:
        await ticket_close(interaction, reason)

    @group.command(name="reopen", description="Reopen this closed ticket.")
    @require_staff(command_staff_keys)
    async def reopen(interaction: discord.Interaction[Any]) -> None:
        await ticket_reopen(interaction)

    @group.command(name="claim", description="Claim this ticket.")
    @require_staff(command_staff_keys)
    async def claim(interaction: discord.Interaction[Any]) -> None:
        await ticket_claim(interaction)

    @group.command(name="transcript", description="Generate and post a transcript of this ticket now.")
    @require_staff(command_staff_keys)
    async def transcript(interaction: discord.Interaction[Any]) -> None:
        await ticket_transcript(interaction)

    @group.command(name="info", description="Show the details of this ticket.")
    @require_staff(command_staff_keys)
    async def info(interaction: discord.Interaction[Any]) -> None:
        await ticket_info(interaction)

    @group.command(name="list", description="List recent support tickets (max 25).")
    @app_commands.describe(status="Only tickets with this status", member="Only tickets of this member")
    @require_staff(command_staff_keys)
    async def list_(
        interaction: discord.Interaction[Any], status: StatusChoice | None = None, member: discord.Member | None = None
    ) -> None:
        await ticket_list(interaction, status, member)

    return group


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.bot.tree.add_command(build_group(), guild=app.guild_object)
