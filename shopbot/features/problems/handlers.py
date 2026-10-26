"""Button, selection and form handlers of the Report a Problem workflow (route ``pr``; custom ids in ``views``).

Customer routes (``start``, ``order``, ``type``, ``go``, ``form``) work for any member in the server; every handler
rebuilds the choice step from the database (the customer's eligible orders, the configured types) and only accepts
selections that are still valid. Nothing about a running flow lives in memory, so every control keeps working after a
restart. The report itself is created atomically by ``service.submit`` (limits, order eligibility and the flow's nonce
are checked in the same transaction, so a double submission can never create two reports).

Staff routes (``resolve``, ``reject`` and their forms) are staff-only in the router (``permissions.staff_roles``,
admins, ``tickets.staff_roles`` and ``problems.staff_roles``) and re-check staff and the report's state themselves; the
decision is a conditional update, so a second or concurrent decision changes nothing. Their forms and replies use
operator text (staff only).

The choice step's menus never open a form directly: Discord keeps showing a picked option, so a closed form could not
be reopened by picking the same option again. Picking re-renders the step (with the choice marked) and its Continue
button opens the form, as often as needed.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import discord

from shopbot.config.banner_ids import BannerId
from shopbot.config.settings import ProblemType
from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import service, views
from shopbot.features.problems.models import Report
from shopbot.features.tickets import service as tickets_service
from shopbot.logging_setup import log_event
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


# -- shared checks ---------------------------------------------------------------------------------------------------


def _member(ctx: Ctx) -> discord.Member:
    member = ctx.member
    if member is None or member.guild.id != ctx.app.env.guild_id:
        raise HandlerError(core.ERROR_PERMISSION)
    return member


def _arg(ctx: Ctx, index: int) -> str:
    try:
        return ctx.args[index]
    except IndexError:
        raise HandlerError(core.ERROR_NOT_FOUND) from None


def _nonce(ctx: Ctx) -> str:
    nonce = _arg(ctx, 0)
    if not views.NONCE_RE.match(nonce):
        raise HandlerError(core.ERROR_NOT_FOUND)
    return nonce


def _check_cooldown(ctx: Ctx) -> None:
    remaining = service.cooldown_remaining(ctx.app, ctx.user_id)
    if remaining > 0:
        raise HandlerError(core.ERROR_COOLDOWN, retry_after=math.ceil(remaining))


def _picked(ctx: Ctx) -> str:
    if len(ctx.values) != 1:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return ctx.values[0]


def _private_step(ctx: Ctx) -> bool:
    """The interaction comes from the private choice step (an ephemeral message that may be replaced)."""
    message = ctx.interaction.message
    return message is not None and bool(message.flags.ephemeral)


async def _show(ctx: Ctx, spec: MessageSpec) -> None:
    """Replace the private step with ``spec``, or answer privately when the interaction is not from it."""
    if _private_step(ctx):
        await ctx.update(spec)
    else:
        await ctx.respond(spec)


# -- the customer flow -----------------------------------------------------------------------------------------------


async def start_button(ctx: Ctx) -> None:
    """The Report a Problem button: limit check, then the choice step, the form, or the report right away."""
    app = ctx.app
    member = _member(ctx)
    _check_cooldown(ctx)
    limit = await service.limit_reply(app, member.id)
    if limit is not None:
        await ctx.respond(limit)
        return
    choice = await service.choice_for(app, member.id, service.new_nonce(), views.NOT_CHOSEN, views.NOT_CHOSEN)
    if choice is None:
        await ctx.respond(service.no_orders_spec(app))
        return
    if choice.needs_input:
        await ctx.respond(service.choose_spec(app, choice))
        return
    await _proceed(ctx, choice)


async def _proceed(ctx: Ctx, choice: views.Choice) -> None:
    """The choice is complete: open the type's form, or create the report when the type has none."""
    problem_type = ctx.app.settings.problems.type(choice.type_sel)
    if problem_type is None:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    if problem_type.form:
        await ctx.send_modal(views.form_modal(ctx.app.ui, problem_type, nonce=choice.nonce, order_sel=choice.order_sel))
        return
    await _submit(ctx, choice.nonce, choice.order_id, problem_type, {})


async def _submit(
    ctx: Ctx, nonce: str, order_id: int | None, problem_type: ProblemType, answers: dict[str, str]
) -> None:
    member = _member(ctx)
    private = _private_step(ctx)
    if private:
        await ctx.defer_update()
    else:
        await ctx.defer_reply(ephemeral=True)
    reply = await service.submit(
        ctx.app, member, nonce=nonce, order_id=order_id, problem_type=problem_type, answers=answers
    )
    if private:
        try:
            await ctx.update(reply)
            return
        except discord.HTTPException as exc:  # e.g. the customer dismissed the private step meanwhile
            log_event(log, logging.INFO, "problems_step_update_failed", status=exc.status)
    await ctx.respond(reply)


async def _rerender(ctx: Ctx, order_sel: str, type_sel: str) -> views.Choice | None:
    """Re-render the private step with these selections (invalid ones are dropped). None when the customer has
    nothing left to report about (the step then shows the 'no orders' reply)."""
    app = ctx.app
    choice = await service.choice_for(app, ctx.user_id, _nonce(ctx), order_sel, type_sel)
    if choice is None:
        await ctx.update(service.no_orders_spec(app))
        return None
    await ctx.update(service.choose_spec(app, choice))
    return choice


async def order_pick(ctx: Ctx) -> None:
    _member(ctx)
    picked = _picked(ctx)
    choice = await _rerender(ctx, picked, _arg(ctx, 1))
    if choice is not None and choice.show_orders and choice.order_sel != picked:
        await ctx.fail(core.ERROR_INVALID_INPUT)  # no longer one of the customer's eligible orders


async def type_pick(ctx: Ctx) -> None:
    _member(ctx)
    picked = _picked(ctx)
    choice = await _rerender(ctx, _arg(ctx, 1), picked)
    if choice is not None and choice.show_types and choice.type_sel != picked:
        await ctx.fail(core.ERROR_INVALID_INPUT)  # the issue type is no longer configured


async def continue_button(ctx: Ctx) -> None:
    app = ctx.app
    member = _member(ctx)
    order_sel, type_sel = _arg(ctx, 1), _arg(ctx, 2)
    choice = await service.choice_for(app, member.id, _nonce(ctx), order_sel, type_sel)
    if choice is None:
        await _show(ctx, service.no_orders_spec(app))
        return
    if not choice.complete or choice.order_sel != order_sel or choice.type_sel != type_sel:
        await _show(ctx, service.choose_spec(app, choice))
        await ctx.fail(core.ERROR_INVALID_INPUT)
        return
    _check_cooldown(ctx)
    limit = await service.limit_reply(app, member.id)
    if limit is not None:
        await _show(ctx, limit)
        return
    await _proceed(ctx, choice)


def _order_arg(ctx: Ctx, value: str) -> int | None:
    if value == views.NO_ORDER:
        if not ctx.app.settings.problems.allow_without_order:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        return None
    if not value.isdigit():
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return int(value)


def _answers(ctx: Ctx, problem_type: ProblemType) -> dict[str, str]:
    answers: dict[str, str] = {}
    for field in problem_type.form:
        raw = ctx.fields.get(field.id, "")
        value = raw.strip()
        if not value:
            if field.required:
                raise HandlerError(core.ERROR_INVALID_INPUT)
            continue
        if len(raw) > field.max_length or len(value) < field.min_length:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        answers[field.id] = value
    return answers


async def form_submit(ctx: Ctx) -> None:
    nonce = _nonce(ctx)
    problem_type = ctx.app.settings.problems.type(_arg(ctx, 2))
    if problem_type is None or not problem_type.form:
        raise HandlerError(core.ERROR_NOT_FOUND)
    _member(ctx)
    order_id = _order_arg(ctx, _arg(ctx, 1))
    answers = _answers(ctx, problem_type)
    await _submit(ctx, nonce, order_id, problem_type, answers)


# -- staff decisions -------------------------------------------------------------------------------------------------


async def _load_report(ctx: Ctx) -> Report:
    report = await service.get_report(ctx.app, ctx.arg_int(0))
    if report is None or report.guild_id != ctx.app.env.guild_id or not report.finished:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if not service.is_staff(ctx.app, ctx.member, ctx.user_id):
        raise HandlerError(core.ERROR_PERMISSION)
    return report


async def _number(ctx: Ctx, report: Report) -> str:
    ticket = await tickets_service.get_ticket(ctx.app, report.ticket_id)
    return service.number_text(ctx.app, ticket.number) if ticket is not None else str(report.id)


async def _staff_reply(
    ctx: Ctx, title: str, text: str | None = None, *, tone: str, banner: BannerId = "general-response"
) -> None:
    await ctx.respond(views.operator_reply(ctx.app.ui, title, text, tone=tone, banner=banner))


async def _already_decided(ctx: Ctx, report: Report, number: str) -> None:
    app = ctx.app
    status = app.op(f"problems.op.status.{report.status}") if report.status in k.DECISIONS else report.status
    title = app.op("problems.op.already_decided_title", number=number, status=status)
    await _staff_reply(ctx, title, app.op("problems.op.already_decided"), tone="warning")


async def resolve_button(ctx: Ctx) -> None:
    report = await _load_report(ctx)
    number = await _number(ctx, report)
    if not report.is_open:
        await _already_decided(ctx, report, number)
        return
    await ctx.send_modal(views.resolve_modal(ctx.app.op, report_id=report.id, number=number))


async def reject_button(ctx: Ctx) -> None:
    report = await _load_report(ctx)
    number = await _number(ctx, report)
    if not report.is_open:
        await _already_decided(ctx, report, number)
        return
    required = ctx.app.settings.problems.require_reason_on_reject
    await ctx.send_modal(views.reject_modal(ctx.app.op, report_id=report.id, number=number, required=required))


async def _decide(ctx: Ctx, status: str, *, reason_required: bool) -> None:
    app = ctx.app
    report = await _load_report(ctx)
    reason = ctx.fields.get(views.REASON_INPUT, "").strip()
    if reason_required and not reason:
        title = app.op("problems.op.reason_required_title")
        await _staff_reply(ctx, title, app.op("problems.op.reason_required"), tone="warning", banner="error")
        return
    if len(reason) > k.REASON_MAX:
        text = app.op("problems.op.reason_too_long", max=k.REASON_MAX, length=len(reason))
        await _staff_reply(ctx, app.op("problems.op.reason_too_long_title"), text, tone="warning", banner="error")
        return
    await ctx.defer_reply(ephemeral=True)
    outcome = await service.decide(app, report.id, status=status, reason=reason or None, actor_id=ctx.user_id)
    if not outcome.applied:
        await _already_decided(ctx, outcome.report, outcome.number)
        return
    title = app.op("problems.op.decided_title", number=outcome.number, status=app.op(f"problems.op.status.{status}"))
    text = app.op("problems.op.decided", delivery=app.op(f"problems.op.delivery.{outcome.delivery or 'none'}"))
    await _staff_reply(ctx, title, text, tone="success")


async def resolve_submit(ctx: Ctx) -> None:
    await _decide(ctx, k.RESOLVED, reason_required=False)


async def reject_submit(ctx: Ctx) -> None:
    await _decide(ctx, k.REJECTED, reason_required=ctx.app.settings.problems.require_reason_on_reject)


def register(app: App) -> None:
    r = app.router
    feature = k.FEATURE
    route = views.ROUTE
    r.component(route, "start", feature=feature)(start_button)
    r.component(route, "order", feature=feature)(order_pick)
    r.component(route, "type", feature=feature)(type_pick)
    r.component(route, "go", feature=feature)(continue_button)
    r.modal(route, "form", feature=feature)(form_submit)
    staff = service.staff_keys
    r.component(route, "resolve", feature=feature, access="staff", staff_roles=staff)(resolve_button)
    r.component(route, "reject", feature=feature, access="staff", staff_roles=staff)(reject_button)
    r.modal(route, "resolved", feature=feature, access="staff", staff_roles=staff)(resolve_submit)
    r.modal(route, "rejected", feature=feature, access="staff", staff_roles=staff)(reject_submit)
