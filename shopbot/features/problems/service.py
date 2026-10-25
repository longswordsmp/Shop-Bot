"""Report a Problem logic: the private choice step, creating a report with its private ticket, staff decisions,
notifications, and the durable follow-ups.

Creation (``submit``)
    ``repo.reserve`` checks the limits and the chosen order and inserts the report, its numbered ticket row
    ('creating') and the durable finishing task in ONE transaction. Every message is then built and checked before
    any channel exists; the ticket service creates the private channel (customer + authorized staff only, in
    ``problems.discord_category``); the report panel and the answers are posted; ``repo.activate`` records the panel
    and opens the ticket atomically. Then staff are alerted (deduplicated), the creation is audited and the order gets
    a history note. A failure before activation removes the channel and the report, so the customer can simply try
    again; an interrupted creation is completed or cleaned up by the finishing task (``tasks.finish_task``).

Decision (``decide``)
    ``repo.decide`` changes ``open -> resolved | rejected`` exactly once and, in the same transaction, adds the
    order's history note and schedules the follow-up task. The follow-ups (report panel, customer notification, the
    order's staff message, audit) run right away and again from the task if the process stopped; each is idempotent.
    The customer is told through the report panel and, when direct messages are on, by direct message; the panel is
    the fallback of the direct message (when the panel cannot be updated, the notification is posted in the ticket).

The report's ticket is an ordinary support ticket of category ``problem-report``: closing, reopening, transcripts,
inactivity and deletion use the ticket system unchanged (its staff are the ticket staff plus ``problems.staff_roles``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import ProblemType
from shopbot.core.ratelimit import Cooldowns
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import repo as checkout_repo
from shopbot.features.checkout import views as checkout_views
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import repo, views
from shopbot.features.problems.models import Report
from shopbot.features.server_keys import channel_mentions
from shopbot.features.tickets import content_keys as tk
from shopbot.features.tickets import repo as tickets_repo
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import CLOSED, CREATING, DELETED, FAILED, OPEN, Ticket
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, check_message, edit_spec, send_spec
from shopbot.ui.routing import HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

AUDIT_TARGET = repo.AUDIT_TARGET
CHECKOUT = "checkout"


def alert_key(report_id: int) -> str:
    return f"problems.alert:{report_id}"


def notify_dedupe(report_id: int) -> str:
    return f"problems.decision:{report_id}"


# -- per-process runtime (anti-spam and per-report locks; not business state) ---------------------------------------


@dataclass
class ProblemsRuntime:
    cooldowns: Cooldowns
    locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    def lock(self, report_id: int) -> asyncio.Lock:
        """Serializes the creation/decision follow-ups of one report (in-process run and its durable task)."""
        found = self.locks.get(report_id)
        if found is None:
            found = asyncio.Lock()
            self.locks[report_id] = found
        return found


_RUNTIMES: weakref.WeakKeyDictionary[App, ProblemsRuntime] = weakref.WeakKeyDictionary()


def runtime(app: App) -> ProblemsRuntime:
    state = _RUNTIMES.get(app)
    if state is None:
        clock = app.clock
        state = ProblemsRuntime(cooldowns=Cooldowns(clock=lambda: float(clock.now())))
        _RUNTIMES[app] = state
    return state


# -- small helpers ------------------------------------------------------------------------------------------------


def staff_keys(app: App) -> tuple[str, ...]:
    """Role keys (besides permissions.staff_roles and admins) that are staff of problem reports."""
    return tuple(dict.fromkeys(app.settings.tickets.staff_roles + app.settings.problems.staff_roles))


def extra_ticket_staff_keys(app: App) -> tuple[str, ...]:
    """What the ticket system adds to tickets.staff_roles for report tickets (see register_external_category)."""
    return app.settings.problems.staff_roles


def is_staff(app: App, member: discord.Member | None, user_id: int) -> bool:
    return app.perms.is_staff(member, user_id, staff_keys(app))


def ping_role_ids(app: App) -> tuple[int, ...]:
    keys = (*app.settings.permissions.staff_roles, *staff_keys(app))
    return tuple(sorted(app.resources.role_ids(keys)))


def new_nonce() -> str:
    return uuid.uuid4().hex[:16]


def number_text(app: App, ticket_number: int) -> str:
    return tickets_service.format_number(app, ticket_number)


def cooldown_remaining(app: App, user_id: int) -> float:
    return runtime(app).cooldowns.remaining(user_id, app.settings.tickets.creation_cooldown_seconds)


async def _attempt(step: str, run: Callable[[], Awaitable[object]], **context: object) -> bool:
    """Run one isolated side effect: an error is logged and never stops the other steps."""
    try:
        await run()
    except Exception as exc:
        log_event(log, logging.ERROR, "problems_step_failed", step=step, error=type(exc).__name__, **context)
        log.exception("problems step %s failed", step)
        return False
    return True


def order_info(app: App, order: checkout_models.Order) -> views.OrderInfo:
    """The order as shown in the workflow (raises ``MissingContent`` when its text is not configured)."""
    write = checkout_views.order_money(app.ui, app.settings, order.currency)
    status = app.ui.text(checkout_keys.status_label_key(order.status))
    if status is None:
        raise MissingContent(checkout_keys.status_label_key(order.status))
    return views.OrderInfo(order_id=order.id, number=str(order.number), total=write(order.total_minor), status=status)


async def _order_of(app: App, report: Report) -> checkout_models.Order | None:
    return await app.db.run(checkout_repo.get_order, report.order_id) if report.order_id is not None else None


# -- the private choice step ----------------------------------------------------------------------------------------


async def limit_reply(app: App, user_id: int, limit: int | None = None) -> MessageSpec | None:
    """The 'limit reached' reply when the customer already has the maximum of undecided reports with an open ticket
    (with a link to the newest), else None. This early answer is repeated atomically when the report is created."""
    maximum = app.settings.problems.max_open_per_user
    if limit is None:
        count = await app.db.run(repo.count_open, app.env.guild_id, user_id)
        if count < maximum:
            return None
    channel_id = await app.db.run(repo.newest_open_channel, app.env.guild_id, user_id)
    return views.limit_reply(app.ui, limit=limit or maximum, channel_id=channel_id)


async def load_orders(app: App, user_id: int) -> list[views.OrderInfo]:
    """The customer's eligible orders, newest first, as many as fit in the order menu."""
    p = app.settings.problems
    room = views.MENU_OPTIONS - (1 if p.allow_without_order else 0)
    orders = await app.db.run(repo.eligible_orders, app.env.guild_id, user_id, p.eligible_statuses, room)
    return [order_info(app, order) for order in orders]


def make_choice(
    app: App, nonce: str, orders: list[views.OrderInfo], order_sel: str, type_sel: str
) -> views.Choice | None:
    """The choice step for these orders, keeping the given selections only while they are still valid.

    The order menu is shown when there is more than one option (orders plus 'not about an order'); with exactly
    one option it is chosen automatically. The issue-type menu is shown when more than one type exists. None when
    there is nothing a report could refer to (no eligible order and problems.allow_without_order = false).
    """
    p = app.settings.problems
    options = [str(order.order_id) for order in orders] + ([views.NO_ORDER] if p.allow_without_order else [])
    if not options:
        return None
    show_orders = len(options) > 1
    order_value = order_sel if order_sel in options else views.NOT_CHOSEN
    if not show_orders:
        order_value = options[0]
    type_ids = [t.id for t in p.types]
    show_types = len(type_ids) > 1
    type_value = type_sel if type_sel in type_ids else views.NOT_CHOSEN
    if not show_types and type_ids:
        type_value = type_ids[0]
    return views.Choice(
        nonce=nonce,
        orders=tuple(orders) if show_orders else (),
        allow_none=p.allow_without_order and show_orders,
        show_orders=show_orders,
        show_types=show_types,
        order_sel=order_value,
        type_sel=type_value,
    )


async def choice_for(app: App, user_id: int, nonce: str, order_sel: str, type_sel: str) -> views.Choice | None:
    return make_choice(app, nonce, await load_orders(app, user_id), order_sel, type_sel)


def choose_spec(app: App, choice: views.Choice) -> MessageSpec:
    return views.choose_message(app.ui, app.settings.problems, choice, links=channel_mentions(app))


def no_orders_spec(app: App) -> MessageSpec:
    return views.no_orders_reply(app.ui, links=channel_mentions(app))


# -- creating a report --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    panel: MessageSpec
    details: list[MessageSpec]
    topic: str | None
    number: str
    order: views.OrderInfo | None
    note: str | None


def _panel_data(
    app: App,
    report: Report,
    ticket: Ticket,
    *,
    user_name: str,
    order: views.OrderInfo | None,
) -> views.PanelData:
    return views.PanelData(
        report_id=report.id,
        ticket_id=ticket.id,
        number=number_text(app, ticket.number),
        user_id=report.user_id,
        user_name=user_name,
        type_id=report.issue_type,
        order=order,
        status=report.status,
        reason=report.reason,
        decided_by=report.decided_by,
        ticket_open=ticket.status in (CREATING, OPEN),
    )


async def _prepare(app: App, report: Report, ticket: Ticket, *, user_name: str, problem_type: ProblemType) -> _Prepared:
    """Everything the new ticket shows, built and checked before the channel exists (raises ``MissingContent`` /
    ``RenderError``)."""
    p = app.settings.problems
    order = await _order_of(app, report)
    info = order_info(app, order) if order is not None else None
    ping = ping_role_ids(app) if p.ping_staff and not k.alerts_enabled(app.settings) else ()
    data = _panel_data(app, report, ticket, user_name=user_name, order=info)
    panel = views.ticket_panel(app.ui, p, data, links=channel_mentions(app), ping_role_ids=ping)
    details = views.details_messages(app.ui, problem_type, report.answers)
    for spec in (panel, *details):
        check_message(spec)
    number = data.number
    note = app.op("problems.order_note.reported", number=number, type=problem_type.id) if info is not None else None
    return _Prepared(
        panel=panel,
        details=details,
        topic=views.channel_topic(app.ui, number=number, user_name=user_name),
        number=number,
        order=info,
        note=note,
    )


async def _discard(app: App, report_id: int) -> None:
    await _attempt("discard", lambda: app.db.transaction(repo.discard, report_id, app.clock.now()), report_id=report_id)


async def _abort(
    app: App, report: Report, ticket: Ticket, *, reason: str, channel: discord.abc.GuildChannel | None = None
) -> None:
    """Undo a creation that cannot complete: the ticket is failed (its channel deleted) and the report removed."""
    await _attempt(
        "abort_ticket",
        lambda: tickets_service.abort_ticket(app, ticket.id, reason=f"problems:{reason}", channel=channel),
        report_id=report.id,
    )
    await _discard(app, report.id)


async def existing_reply(app: App, reservation: repo.Reservation) -> MessageSpec:
    """The same flow was submitted again: point to the report's ticket (never a second report)."""
    ticket = reservation.ticket
    if ticket.status == CREATING:
        raise HandlerError(core.ERROR_BUSY)  # the first submission is still creating the ticket
    if ticket.channel_id is None or ticket.status not in (OPEN, CLOSED):
        raise HandlerError(core.ERROR_NOT_FOUND)  # that report's ticket was deleted: start a new report
    return views.created_reply(app.ui, channel_id=ticket.channel_id, number=number_text(app, ticket.number))


async def submit(
    app: App,
    member: discord.Member,
    *,
    nonce: str,
    order_id: int | None,
    problem_type: ProblemType,
    answers: dict[str, str],
) -> MessageSpec:
    """Create the report and its private ticket. Returns the private reply (confirmation or limit reached); raises
    ``HandlerError`` (or ``MissingContent``) when nothing could be created."""
    if member.guild.id != app.env.guild_id:
        raise HandlerError(core.ERROR_PERMISSION)
    p = app.settings.problems
    earlier = await app.db.run(repo.by_nonce, app.env.guild_id, member.id, nonce)
    if earlier is None:
        # Anti-spam between two reports (a repeated submission of the same flow is answered below instead).
        remaining = cooldown_remaining(app, member.id)
        if remaining > 0:
            raise HandlerError(core.ERROR_COOLDOWN, retry_after=math.ceil(remaining))
    try:
        reservation = await app.db.transaction(
            repo.reserve,
            guild_id=app.env.guild_id,
            user_id=member.id,
            nonce=nonce,
            order_id=order_id,
            issue_type=problem_type.id,
            answers=answers,
            eligible_statuses=p.eligible_statuses,
            max_open=p.max_open_per_user,
            daily_limit=tk.daily_creation_cap(app.settings),
            now=app.clock.now(),
            task_attempts=app.scheduler.max_attempts,
        )
    except repo.LimitReached as exc:
        reply = await limit_reply(app, member.id, exc.limit)
        assert reply is not None
        return reply
    except tickets_repo.LimitReached as exc:
        raise HandlerError(tk.LIMIT_REACHED, limit=exc.limit) from None
    except repo.NotEligible:
        raise HandlerError(core.ERROR_INVALID_INPUT) from None
    if not reservation.created:
        return await existing_reply(app, reservation)
    async with runtime(app).lock(reservation.report.id):
        reply = await _create(app, member, reservation.report, reservation.ticket, problem_type)
    runtime(app).cooldowns.mark(member.id)
    return reply


async def _create(
    app: App, member: discord.Member, report: Report, ticket: Ticket, problem_type: ProblemType
) -> MessageSpec:
    p = app.settings.problems
    try:
        prepared = await _prepare(app, report, ticket, user_name=member.display_name, problem_type=problem_type)
    except MissingContent as exc:
        await _abort(app, report, ticket, reason=f"missing_content:{exc.key}")
        raise
    except RenderError as exc:
        await _abort(app, report, ticket, reason="limits")
        raise HandlerError(core.ERROR_GENERIC) from exc
    try:
        channel = await tickets_service.create_ticket_channel(
            app,
            ticket,
            member=member,
            discord_category_key=p.discord_category,
            channel_prefix_key=k.CHANNEL_PREFIX,
            extra_staff_role_keys=staff_keys(app),
            topic=prepared.topic,
        )
    except tickets_service.TicketCreateFailed as exc:  # the ticket was failed (and audited) by the ticket service
        log_event(log, logging.ERROR, "problems_channel_create_failed", report_id=report.id, reason=exc.reason)
        await _discard(app, report.id)
        raise HandlerError(core.ERROR_GENERIC) from None
    except MissingContent:
        await _discard(app, report.id)
        raise
    try:
        message = await send_spec(channel, prepared.panel)
        for spec in prepared.details:
            await send_spec(channel, spec)
    except Exception as exc:  # incl. network errors that are not HTTPException: never leave a half-made ticket
        log_event(log, logging.ERROR, "problems_panel_send_failed", report_id=report.id, error=type(exc).__name__)
        await _abort(app, report, ticket, reason=f"panel_failed:{type(exc).__name__}", channel=channel)
        raise HandlerError(core.ERROR_GENERIC) from None
    try:
        report, opened = await app.db.transaction(
            repo.activate, report.id, message.id, order_note=prepared.note, now=app.clock.now()
        )
    except repo.Superseded:
        adopted = await _adopt_after_recovery(app, report, ticket, message.id, prepared.note)
        if adopted is None:
            await _abort(app, report, ticket, reason="superseded", channel=channel)
            raise HandlerError(core.ERROR_GENERIC) from None
        report, opened = adopted
    await run_creation_followups(app, report, opened, channel_id=channel.id, order=prepared.order)
    return views.created_reply(app.ui, channel_id=channel.id, number=prepared.number)


async def _adopt_after_recovery(
    app: App, report: Report, ticket: Ticket, message_id: int, note: str | None
) -> tuple[Report, Ticket] | None:
    """The ticket left 'creating' while this creation was slow: the ticket system's recovery either failed it (then
    nothing is kept) or opened it with the report panel as its control message (then the report is kept)."""
    current = await tickets_service.get_ticket(app, ticket.id)
    if current is None or current.status not in (OPEN, CLOSED):
        return None
    adopted = await app.db.transaction(repo.adopt_panel, report.id, message_id, order_note=note, now=app.clock.now())
    if adopted is None:
        return None
    refreshed = await tickets_service.get_ticket(app, ticket.id)
    return adopted, refreshed or current


async def run_creation_followups(
    app: App, report: Report, ticket: Ticket, *, channel_id: int, order: views.OrderInfo | None
) -> None:
    """After the report panel exists: staff alert (once), audit (once), inactivity timers, the order's staff message,
    and the finishing task is no longer needed. Each step is isolated and safe to repeat."""
    number = number_text(app, ticket.number)
    await _attempt(
        "staff_alert",
        lambda: _send_staff_alert(app, report, number=number, channel_id=channel_id, order=order),
        report_id=report.id,
    )
    if not await app.db.run(repo.audited, "problems.reported", report.id):
        await _attempt(
            "audit",
            lambda: app.audit.record(
                "problems.reported",
                actor_id=report.user_id,
                target_type=AUDIT_TARGET,
                target_id=report.id,
                details={
                    "number": ticket.number,
                    "type": report.issue_type,
                    "order_number": order.number if order is not None else None,
                    "ticket_id": ticket.id,
                    "channel_id": channel_id,
                },
            ),
            report_id=report.id,
        )
    if ticket.is_open:
        await _attempt("inactivity", lambda: tickets_service.schedule_inactivity(app, ticket), report_id=report.id)
    if report.order_id is not None:
        await _refresh_order(app, report.order_id)
    await _attempt(
        "finish_task",
        lambda: app.db.transaction(repo.cancel_task, repo.finish_key(report.id), app.clock.now()),
        report_id=report.id,
    )


async def _send_staff_alert(
    app: App, report: Report, *, number: str, channel_id: int, order: views.OrderInfo | None
) -> None:
    key = app.settings.tickets.staff_alert_channel
    if not key:
        return
    alert_channel_id = app.resources.channel_id(key)
    if not alert_channel_id:
        log_event(log, logging.WARNING, "problems_staff_alert_channel_missing", key=key)
        return
    p = app.settings.problems
    try:
        spec = views.staff_alert(
            app.ui,
            p,
            number=number,
            type_id=report.issue_type,
            user_id=report.user_id,
            channel_id=channel_id,
            order=order,
            ping_role_ids=ping_role_ids(app) if p.ping_staff else (),
        )
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="problems.staff_alert")
        return
    await app.notifier.notify_channel(
        channel_id=alert_channel_id, spec=spec, kind="problems.staff_alert", dedupe_key=alert_key(report.id)
    )


async def _refresh_order(app: App, order_id: int) -> None:
    """Best effort: the order's staff message reflects the order's updated record (checkout keeps it live)."""
    if not app.ready(CHECKOUT):
        return
    from shopbot.features.checkout import service as checkout_service

    await _attempt(
        "order_staff_message", lambda: checkout_service.refresh_staff_message(app, order_id), order_id=order_id
    )


# -- the report panel -----------------------------------------------------------------------------------------------


async def get_report(app: App, report_id: int) -> Report | None:
    return await app.db.run(repo.get, report_id)


async def _user_name(app: App, user_id: int) -> str:
    guild = app.guild
    member = guild.get_member(user_id) if guild is not None else None
    if member is None and guild is not None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    return member.display_name if member is not None else str(user_id)


async def panel_spec(app: App, report: Report, ticket: Ticket) -> MessageSpec:
    """The report panel for the report's current state (raises ``MissingContent``)."""
    order = await _order_of(app, report)
    data = _panel_data(
        app,
        report,
        ticket,
        user_name=await _user_name(app, report.user_id),
        order=order_info(app, order) if order is not None else None,
    )
    return views.ticket_panel(app.ui, app.settings.problems, data, links=channel_mentions(app))


async def refresh_panel(app: App, report: Report, ticket: Ticket | None) -> bool:
    """Edit the report panel to the report's state. False when it could not be updated (logged)."""
    if ticket is None or report.panel_message_id is None:
        return False
    channel = await tickets_service.ticket_channel(app, ticket)
    if channel is None:
        log_event(log, logging.WARNING, "problems_panel_channel_missing", report_id=report.id)
        return False
    try:
        spec = await panel_spec(app, report, ticket)
        await edit_spec(channel.get_partial_message(report.panel_message_id), spec)
    except MissingContent as exc:
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="problems.panel")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "problems_panel_update_failed", report_id=report.id, error=type(exc).__name__)
        return False
    return True


# -- decisions ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    applied: bool
    report: Report
    number: str
    delivery: str = ""


async def decide(app: App, report_id: int, *, status: str, reason: str | None, actor_id: int) -> Decision:
    """Resolve or reject an open report, exactly once; then update the panel, notify the customer and audit."""
    if status not in k.DECISIONS:
        raise ValueError(status)
    report = await get_report(app, report_id)
    if report is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    ticket = await tickets_service.get_ticket(app, report.ticket_id)
    number = number_text(app, ticket.number) if ticket is not None else str(report.id)
    note = app.op(f"problems.order_note.{status}", number=number, reason=reason or app.op("problems.order_note.none"))
    decided = await app.db.transaction(
        repo.decide,
        report_id,
        status=status,
        reason=reason,
        actor_id=actor_id,
        order_note=note,
        now=app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
    )
    if decided is None:
        current = await get_report(app, report_id)
        return Decision(applied=False, report=current or report, number=number)
    followups = await run_decision_followups(app, decided.id)
    return Decision(applied=True, report=decided, number=number, delivery=followups.delivery)


@dataclass(frozen=True)
class Followups:
    # How the customer was told: dm, channel, panel, none, duplicate or failed ("" when there was nothing to do).
    delivery: str
    # False when a step failed unexpectedly (the durable follow-up task is kept and runs again).
    complete: bool = True


async def run_decision_followups(app: App, report_id: int) -> Followups:
    """Panel, customer notification (once), the order's staff message, audit (once); the follow-up task is then no
    longer needed."""
    async with runtime(app).lock(report_id):
        report = await get_report(app, report_id)
        if report is None or report.is_open:
            return Followups(delivery="")
        ticket = await tickets_service.get_ticket(app, report.ticket_id)
        complete = True
        panel_ok = False
        delivery = "failed"
        try:
            panel_ok = await refresh_panel(app, report, ticket)
            delivery = await _notify_decision(app, report, ticket, panel_ok=panel_ok)
        except Exception as exc:  # e.g. a network error: the durable follow-up task tries again
            complete = False
            log_event(
                log, logging.ERROR, "problems_decision_followup_failed", report_id=report.id, error=type(exc).__name__
            )
        if report.order_id is not None:
            await _refresh_order(app, report.order_id)
        action = f"problems.{report.status}"
        if not await app.db.run(repo.audited, action, report.id):
            await _attempt(
                "audit",
                lambda: app.audit.record(
                    action,
                    actor_id=report.decided_by,
                    target_type=AUDIT_TARGET,
                    target_id=report.id,
                    details={
                        "number": ticket.number if ticket is not None else None,
                        "type": report.issue_type,
                        "order_id": report.order_id,
                        "with_reason": bool(report.reason),
                        "panel_updated": panel_ok,
                        "notification": delivery,
                    },
                ),
                report_id=report.id,
            )
        if complete:
            await _attempt(
                "decision_task",
                lambda: app.db.transaction(repo.cancel_task, repo.decision_key(report.id), app.clock.now()),
                report_id=report.id,
            )
        return Followups(delivery=delivery, complete=complete)


async def _notify_decision(app: App, report: Report, ticket: Ticket | None, *, panel_ok: bool) -> str:
    """Direct message when direct messages are on. The updated report panel is the fallback; only when the panel
    could not be updated is the notification posted in the ticket (``notifications.fallback_to_channel``)."""
    dm = app.settings.features.dm_notifications
    if panel_ok and not dm:
        return "panel"
    order = await _order_of(app, report)
    channel_id = ticket.channel_id if ticket is not None else None
    try:
        spec = views.decision_notification(
            app.ui,
            app.settings.problems,
            status=report.status,
            number=number_text(app, ticket.number) if ticket is not None else str(report.id),
            type_id=report.issue_type,
            order=order_info(app, order) if order is not None else None,
            reason=report.reason,
            channel_id=channel_id,
        )
        check_message(spec)
    except (MissingContent, RenderError) as exc:
        log_event(
            log, logging.ERROR, "problems_notification_unbuildable", report_id=report.id, error=type(exc).__name__
        )
        return "none" if panel_ok else "failed"
    delivery = await app.notifier.notify_user(
        user_id=report.user_id,
        spec=spec,
        kind=f"problems.{report.status}",
        dedupe_key=notify_dedupe(report.id),
        fallback_channel_id=None if panel_ok else channel_id,
        alert_on_failure=not panel_ok,  # the updated report panel already shows the decision
    )
    if delivery == "none" and not panel_ok:
        return "failed"
    return delivery


# -- recovery of an interrupted creation ------------------------------------------------------------------------------


async def finish_creation(app: App, report_id: int) -> str:
    """Complete or clean up a creation that was interrupted (process stopped). Returns what happened:
    ``done`` (nothing to do or completed), ``discarded`` (the ticket never opened), ``wait`` (the ticket system has
    not decided about the ticket yet)."""
    async with runtime(app).lock(report_id):
        report = await get_report(app, report_id)
        if report is None:
            return "done"
        ticket = await tickets_service.get_ticket(app, report.ticket_id)
        if ticket is None or ticket.status == FAILED or (ticket.status == DELETED and not report.finished):
            await _discard(app, report.id)
            return "discarded"
        if ticket.status == CREATING:
            return "wait"
        if report.panel_message_id is None:
            adopted = await _complete_unrecorded(app, report, ticket)
            if adopted is None:
                return "discarded"
            report = adopted
            ticket = await tickets_service.get_ticket(app, report.ticket_id) or ticket
        order = await _order_of(app, report)
        try:
            info = order_info(app, order) if order is not None else None
        except MissingContent:
            info = None
        await run_creation_followups(app, report, ticket, channel_id=ticket.channel_id or 0, order=info)
        return "done"


async def _complete_unrecorded(app: App, report: Report, ticket: Ticket) -> Report | None:
    """The ticket exists but the report panel was not recorded: adopt the panel the ticket system took as its control
    message, or post the panel now. None when the ticket's channel is gone (the report is removed)."""
    order = await _order_of(app, report)
    note = None
    if order is not None:
        note = app.op("problems.order_note.reported", number=number_text(app, ticket.number), type=report.issue_type)
    if ticket.control_message_id is not None:
        return await app.db.transaction(
            repo.adopt_panel, report.id, ticket.control_message_id, order_note=note, now=app.clock.now()
        )
    channel = await tickets_service.ticket_channel(app, ticket)
    if channel is None:
        await _discard(app, report.id)
        return None
    problem_type = app.settings.problems.type(report.issue_type)
    try:
        spec = await panel_spec(app, report, ticket)
        message = await send_spec(channel, spec)
        if problem_type is not None:
            for details in views.details_messages(app.ui, problem_type, report.answers):
                await send_spec(channel, details)
    except (MissingContent, RenderError, discord.HTTPException) as exc:
        log_event(log, logging.WARNING, "problems_recovery_panel_failed", report_id=report.id, error=type(exc).__name__)
        raise
    return await app.db.transaction(repo.adopt_panel, report.id, message.id, order_note=note, now=app.clock.now())
