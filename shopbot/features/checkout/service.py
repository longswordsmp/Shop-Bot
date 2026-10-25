"""Checkout logic: starting a session, the workflow, submission, the order state machine, refunds.

Public API (also used by future payment webhooks)
-------------------------------------------------
- ``change_status(app, order_id, to, *, actor_id, note=None) -> Outcome``: the ONLY way an order changes
  status (staff buttons, ``/order status``/``cancel``, the customer's cancel, expiry tasks, webhooks). A second
  concurrent request finds the state changed (``Outcome.applied`` False) and changes nothing.
- ``refund(app, order_id, *, amount_minor, to_credit, reason, actor_id) -> RefundOutcome``.
- ``order_view(app, order_id)``, ``get_order(app, order_id)``, ``get_order_by_number(app, number)``.

Every database write is one ``app.db.transaction`` unit (see ``repo``). Discord side effects (editing the workflow
message, the staff controls, notifications, closing the order channel) happen after the commit, are best effort
and are logged when they fail; the database stays the source of truth and ``tasks.on_ready`` repairs what a crash
left undone.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import uuid
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.ratelimit import Cooldowns
from shopbot.features import core_keys as core
from shopbot.features.catalog import service as catalog_service
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, repo, views
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import PaymentInstructions, PaymentProvider, RefundResult, get_provider
from shopbot.features.checkout.totals import compute_totals
from shopbot.features.credits import service as credits_service
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import KIND_ORDER
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, check_message, edit_spec, send_spec
from shopbot.ui.routing import HandlerError
from shopbot.ui.spec import EmbedSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_SESSION_EXPIRE = "checkout.session_expire"
TASK_PAYMENT_REMINDER = "checkout.payment_reminder"
TASK_PAYMENT_EXPIRE = "checkout.payment_expire"
TICKET_CATEGORY = "checkout"
CREDITS_FEATURE = "credits"
REVIEWS_FEATURE = "reviews"
# Anti-spam: minimum seconds between two order channels created by the same member (not a business rule).
START_COOLDOWN_SECONDS = 10
# Refund reasons are also stored as the store-credit note of a refund to credit.
REFUND_REASON_MAX = views.REFUND_REASON_MAX


def session_task_key(session_id: int) -> str:
    return f"checkout.session:{session_id}"


def reminder_task_key(order_id: int) -> str:
    return f"checkout.payment_reminder:{order_id}"


def expiry_task_key(order_id: int) -> str:
    return f"checkout.payment_expire:{order_id}"


def notify_key(order_id: int, kind: str) -> str:
    return f"checkout.notify:{order_id}:{kind}"


def staff_details_key(order_id: int, index: int) -> str:
    return f"checkout.staff_details:{order_id}:{index}"


# -- per-process runtime (anti-spam and per-order locks; not business state) ----------------------------------


@dataclass
class CheckoutRuntime:
    cooldowns: Cooldowns
    refund_locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    def refund_lock(self, order_id: int) -> asyncio.Lock:
        lock = self.refund_locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self.refund_locks[order_id] = lock
        return lock


_RUNTIMES: weakref.WeakKeyDictionary[App, CheckoutRuntime] = weakref.WeakKeyDictionary()


def runtime(app: App) -> CheckoutRuntime:
    state = _RUNTIMES.get(app)
    if state is None:
        clock = app.clock
        state = CheckoutRuntime(cooldowns=Cooldowns(clock=lambda: float(clock.now())))
        _RUNTIMES[app] = state
    return state


# -- small helpers ------------------------------------------------------------------------------------------------


def credits_enabled(app: App) -> bool:
    """Whether the review step may offer the customer's store credit."""
    return credits_service.available_at_checkout(app)


def credit_refunds_enabled(app: App) -> bool:
    return app.ready(CREDITS_FEATURE)


def provider_for(order: m.Order) -> PaymentProvider | None:
    return get_provider(order.payment_provider)


def _content_missing(exc: MissingContent, where: str) -> None:
    log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where=where)


def customer_error(app: App, exc: repo.CheckoutError) -> HandlerError:
    """The configured customer message for a checkout rule violation."""
    if isinstance(exc, repo.SessionNotActive):
        return HandlerError(k.ERROR_SESSION_CLOSED)
    if isinstance(exc, (repo.SessionNotFound, repo.ProductMissing)):
        return HandlerError(core.ERROR_NOT_FOUND)
    if isinstance(exc, repo.ProductUnavailable):
        return HandlerError(k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape(exc.name))
    if isinstance(exc, repo.OutOfStock):
        return HandlerError(k.ERROR_OUT_OF_STOCK, name=views.escape(exc.name), available=exc.available)
    if isinstance(exc, repo.QuantityTooHigh):
        return HandlerError(k.ERROR_QUANTITY_INVALID, max=exc.maximum)
    if isinstance(exc, repo.CartFull):
        return HandlerError(k.ERROR_CART_FULL, max=exc.maximum)
    if isinstance(exc, repo.MinimumNotMet):
        return HandlerError(k.CART_MINIMUM_NOT_MET, minimum=views.money(app.ui, exc.minimum))
    return HandlerError(core.ERROR_INVALID_INPUT)


# -- reads ----------------------------------------------------------------------------------------------------------


async def get_session(app: App, session_id: int) -> m.Session | None:
    session = await app.db.run(repo.get_session, session_id)
    return session if session is not None and session.guild_id == app.env.guild_id else None


async def get_order(app: App, order_id: int) -> m.Order | None:
    order = await app.db.run(repo.get_order, order_id)
    return order if order is not None and order.guild_id == app.env.guild_id else None


async def get_order_by_number(app: App, number: int) -> m.Order | None:
    return await app.db.run(repo.get_order_by_number, app.env.guild_id, number)


async def order_view(app: App, order_id: int) -> m.OrderView | None:
    view = await app.db.run(repo.order_view, order_id)
    return view if view is not None and view.order.guild_id == app.env.guild_id else None


async def workflow_data(app: App, session_id: int) -> m.WorkflowData | None:
    return await app.db.run(repo.load_workflow, app.env.guild_id, session_id, app.settings, credits_enabled(app))


async def workflow_spec(app: App, session_id: int, *, notice: EmbedSpec | None = None) -> MessageSpec:
    data = await workflow_data(app, session_id)
    if data is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return views.workflow_message(app.ui, app.settings, data, notice=notice)


async def session_channel(app: App, session: m.Session) -> discord.TextChannel | None:
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is None or ticket.status not in ("creating", "open"):
        return None
    return await tickets_service.ticket_channel(app, ticket)


async def _channel_confirmed_gone(app: App, channel_id: int) -> bool:
    """True only when Discord says the channel does not exist (a transient error is not proof)."""
    if app.bot is None or app.bot.get_channel(channel_id) is not None:
        return False
    try:
        await app.bot.fetch_channel(channel_id)
    except discord.NotFound:
        return True
    except discord.HTTPException:
        return False
    return False


async def retire_vanished_session(app: App, session: m.Session) -> None:
    """The session's order channel is gone: expire the session and record the channel deletion (so the dead
    channel no longer counts toward the member's open-channel limit)."""
    await expire_session(app, session.id, reason="channel_missing", render=False, close=False)
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is None or ticket.channel_id is None or ticket.status not in ("creating", "open"):
        return
    if await _channel_confirmed_gone(app, ticket.channel_id):
        await tickets_service.channel_deleted(app, ticket.channel_id)


async def order_channel(app: App, order: m.Order) -> discord.TextChannel | None:
    if order.ticket_id is None:
        return None
    ticket = await tickets_service.get_ticket(app, order.ticket_id)
    return await tickets_service.ticket_channel(app, ticket) if ticket is not None else None


# -- starting a session ----------------------------------------------------------------------------------------------


async def schedule_session_expiry(app: App, session: m.Session) -> None:
    minutes = app.settings.checkout.session_expiry_minutes
    if minutes <= 0 or not session.active:
        return
    await app.scheduler.schedule(
        TASK_SESSION_EXPIRE,
        run_at=session.updated_at + minutes * 60,
        payload={"session_id": session.id},
        dedupe_key=session_task_key(session.id),
    )


def _preflight(app: App, page: m.ProductPage) -> None:
    """Build what a new session shows before anything is created (missing text raises ``MissingContent``)."""
    session = m.Session(
        id=0,
        guild_id=app.env.guild_id,
        user_id=0,
        ticket_id=0,
        status=m.ACTIVE,
        step=m.BROWSE,
        page=0,
        fulfillment_option=None,
        details={},
        apply_credits=False,
        message_id=None,
        order_id=None,
        version=1,
        created_at=0,
        updated_at=0,
        expires_at=None,
    )
    data = m.WorkflowData(
        session=session,
        lines=(),
        browse=page,
        totals=compute_totals([]),
        credits_enabled=False,
        credits_available_minor=0,
    )
    check_message(views.workflow_message(app.ui, app.settings, data))
    views.started_reply(app.ui, 0)


async def start(app: App, member: discord.Member) -> MessageSpec:
    """The start button: reuse the member's unfinished session, refuse over the open-order limit, or create the
    private order channel with its workflow message. Returns the private reply."""
    guild_id = app.env.guild_id
    c = app.settings.checkout
    existing = await app.db.run(repo.active_session_for_user, guild_id, member.id)
    if existing is not None:
        channel = await session_channel(app, existing)
        if channel is not None:
            return views.session_exists_reply(app.ui, channel.id)
        await retire_vanished_session(app, existing)
    open_orders = await app.db.run(repo.count_open_orders, guild_id, member.id)
    if open_orders >= c.max_open_orders_per_user:
        return views.limit_reply(app.ui, c.max_open_orders_per_user)
    rt = runtime(app)
    remaining = rt.cooldowns.remaining(("start", member.id), START_COOLDOWN_SECONDS)
    if remaining > 0:
        raise HandlerError(core.ERROR_COOLDOWN, retry_after=math.ceil(remaining))
    page = await app.db.run(repo.product_page, guild_id, 0, c.products_per_page)
    _preflight(app, page)
    try:
        created = await tickets_service.create_private_channel(
            app,
            member=member,
            kind=KIND_ORDER,
            category_id=TICKET_CATEGORY,
            discord_category_key=c.order_category,
            channel_prefix_key=k.CHANNEL_PREFIX,
            extra_staff_role_keys=c.staff_roles,
            topic=views.channel_topic(app.ui, member.display_name),
            limit=c.max_open_orders_per_user,
        )
    except tickets_service.TicketLimitReached as exc:
        return views.limit_reply(app.ui, exc.limit)
    except tickets_service.TicketCreateFailed as exc:
        log_event(log, logging.ERROR, "checkout_channel_create_failed", user_id=member.id, reason=exc.reason)
        raise HandlerError(core.ERROR_GENERIC) from None
    rt.cooldowns.mark(("start", member.id))
    now = app.clock.now()
    session = await app.db.transaction(
        repo.create_session,
        guild_id=guild_id,
        user_id=member.id,
        ticket_id=created.ticket.id,
        now=now,
        expiry_seconds=c.session_expiry_minutes * 60,
    )
    try:
        spec = await workflow_spec(app, session.id)
        message = await send_spec(created.channel, spec)
        await app.db.transaction(repo.set_session_message, session.id, message.id, app.clock.now())
        await tickets_service.activate_ticket(app, created.ticket.id, control_message_id=message.id)
    except (MissingContent, RenderError, discord.HTTPException, tickets_service.TicketStateError) as exc:
        await app.db.transaction(repo.end_session, session.id, m.SESSION_CANCELLED, app.clock.now())
        await tickets_service.abort_ticket(
            app, created.ticket.id, reason=f"workflow_failed:{type(exc).__name__}", channel=created.channel
        )
        if isinstance(exc, MissingContent):
            raise
        raise HandlerError(core.ERROR_GENERIC) from None
    await schedule_session_expiry(app, session)
    await app.audit.record(
        "checkout.session_started",
        actor_id=member.id,
        target_type="checkout_session",
        target_id=session.id,
        details={"ticket_id": created.ticket.id, "channel_id": created.channel.id},
    )
    return views.started_reply(app.ui, created.channel.id)


# -- workflow changes (the customer's own session) ------------------------------------------------------------------


async def _update(app: App, session: m.Session, **changes: object) -> m.Session:
    try:
        return await app.db.transaction(
            repo.update_session,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            now=app.clock.now(),
            expiry_seconds=app.settings.checkout.session_expiry_minutes * 60,
            **changes,  # type: ignore[arg-type]
        )
    except repo.CheckoutError as exc:
        raise customer_error(app, exc) from None


async def set_page(app: App, session: m.Session, page: int) -> m.Session:
    return await _update(app, session, page=page, step=m.BROWSE)


async def set_quantity(app: App, session: m.Session, product_id: int, quantity: int) -> m.Session:
    try:
        return await app.db.transaction(
            repo.set_quantity,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            product_id=product_id,
            quantity=quantity,
            settings=app.settings.checkout,
            now=app.clock.now(),
        )
    except repo.CheckoutError as exc:
        raise customer_error(app, exc) from None


async def go(app: App, session: m.Session, target: str) -> m.Session:
    """Move to ``target`` if every earlier step is complete (forged or stale jumps are refused)."""
    c = app.settings.checkout
    data = await workflow_data(app, session.id)
    if data is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    current = data.session
    steps = flow.steps_for(c, current.fulfillment_option)
    if target not in steps:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    index = steps.index(target)
    if index > steps.index(m.CART):
        if not data.lines:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        if not views.minimum_met(app.settings, data):
            raise HandlerError(k.CART_MINIMUM_NOT_MET, minimum=views.money(app.ui, c.min_order_total_minor))
    if (
        m.FULFILLMENT in steps
        and index > steps.index(m.FULFILLMENT)
        and c.fulfillment(current.fulfillment_option) is None
    ):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    inputs = flow.form_inputs(c, current.fulfillment_option)
    if target == m.REVIEW and m.DETAILS in steps and not flow.details_complete(inputs, current.details):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _update(app, current, step=target)


async def choose_fulfillment(app: App, session: m.Session, option_id: str) -> m.Session:
    if app.settings.checkout.fulfillment(option_id) is None:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _update(app, session, fulfillment_option=option_id, step=m.FULFILLMENT)


async def save_details(app: App, session: m.Session, part: int, fields: Mapping[str, str]) -> m.Session:
    """Validate and store one part of the details form (answers of other parts are kept)."""
    c = app.settings.checkout
    parts = flow.form_parts(flow.form_inputs(c, session.fulfillment_option))
    if not 0 <= part < len(parts):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    updates: dict[str, str | None] = {}
    for item in parts[part]:
        text = fields.get(item.answer_key, "").strip()
        if not text:
            if item.field.required:
                raise HandlerError(core.ERROR_INVALID_INPUT)
            updates[item.answer_key] = None
            continue
        if not item.field.min_length <= len(text) <= item.field.max_length:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        updates[item.answer_key] = text
    try:
        return await app.db.transaction(
            repo.merge_details,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            updates=updates,
            now=app.clock.now(),
            expiry_seconds=c.session_expiry_minutes * 60,
        )
    except repo.CheckoutError as exc:
        raise customer_error(app, exc) from None


async def set_apply_credits(app: App, session: m.Session, apply: bool) -> m.Session:
    if apply and not credits_enabled(app):
        raise HandlerError(core.ERROR_UNAVAILABLE)
    return await _update(app, session, apply_credits=apply)


# -- ending a session ---------------------------------------------------------------------------------------------


async def _edit_session_message(app: App, session: m.Session, spec: MessageSpec) -> None:
    if session.message_id is None:
        return
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    channel = await tickets_service.ticket_channel(app, ticket) if ticket is not None else None
    if channel is None:
        return
    try:
        await edit_spec(channel.get_partial_message(session.message_id), spec)
    except (discord.HTTPException, RenderError) as exc:
        log_event(
            log, logging.WARNING, "checkout_workflow_edit_failed", session_id=session.id, error=type(exc).__name__
        )


async def cancel_session(app: App, session: m.Session) -> m.Session | None:
    """The customer confirmed cancelling their unfinished order. None when it was no longer active."""
    ended = await app.db.transaction(repo.end_session, session.id, m.SESSION_CANCELLED, app.clock.now())
    return ended


async def after_session_cancelled(app: App, session: m.Session, *, actor_id: int) -> None:
    await app.scheduler.cancel(session_task_key(session.id))
    await tickets_service.close_ticket(app, session.ticket_id, actor_id=actor_id, reason="cancelled")
    await app.audit.record(
        "checkout.session_cancelled",
        actor_id=actor_id,
        target_type="checkout_session",
        target_id=session.id,
        details={"ticket_id": session.ticket_id},
    )


async def expire_session(app: App, session_id: int, *, reason: str, render: bool = True, close: bool = True) -> bool:
    """active -> expired (idle too long, or its channel disappeared). Returns False if it was not active."""
    ended = await app.db.transaction(repo.end_session, session_id, m.SESSION_EXPIRED, app.clock.now())
    if ended is None:
        return False
    await app.scheduler.cancel(session_task_key(session_id))
    if render:
        try:
            await _edit_session_message(app, ended, views.session_ended(app.ui, m.SESSION_EXPIRED))
        except MissingContent as exc:
            _content_missing(exc, "checkout.session_expired")
    if close:
        await tickets_service.close_ticket(app, ended.ticket_id, actor_id=None, reason="expired")
    await app.audit.record(
        "checkout.session_expired",
        actor_id=None,
        target_type="checkout_session",
        target_id=session_id,
        details={"ticket_id": ended.ticket_id, "reason": reason},
    )
    return True


# -- submission ----------------------------------------------------------------------------------------------------


async def submit(app: App, session: m.Session, expected_total_minor: int) -> m.Order:
    """Create the order from the session (idempotent). Raises ``repo.CheckoutError`` (incl. ``PriceChanged``)."""
    result = await app.db.transaction(
        repo.submit_order,
        guild_id=app.env.guild_id,
        session_id=session.id,
        user_id=session.user_id,
        expected_total_minor=expected_total_minor,
        settings=app.settings,
        credits_enabled=credits_enabled(app),
        provider_id=app.settings.payments.provider,
        now=app.clock.now(),
    )
    if result.created:
        await _after_submit(app, result.order)
    return await get_order(app, result.order.id) or result.order


async def _after_submit(app: App, order: m.Order) -> None:
    log_event(log, logging.INFO, "checkout_order_submitted", order_id=order.id, number=order.number)
    if order.session_id is not None:
        await app.scheduler.cancel(session_task_key(order.session_id))
    await catalog_service.schedule_listing_refresh(app)
    provider = provider_for(order)
    if provider is not None:
        try:
            started = await provider.start(app, order)
        except Exception as exc:  # the order exists; staff can still handle it manually
            log_event(log, logging.ERROR, "checkout_payment_start_failed", order_id=order.id, error=type(exc).__name__)
        else:
            if started.external_ref:
                await app.db.transaction(repo.set_payment_external_ref, order.id, started.external_ref, app.clock.now())
    await post_staff_message(app, order.id)
    await post_staff_details(app, order.id)
    await schedule_payment_tasks(app, order)
    await app.audit.record(
        "checkout.order_submitted",
        actor_id=order.user_id,
        target_type="order",
        target_id=order.id,
        details={
            "number": order.number,
            "total_minor": order.total_minor,
            "credits_applied_minor": order.credits_applied_minor,
            "currency": order.currency,
            "provider": order.payment_provider,
        },
    )


async def schedule_payment_tasks(app: App, order: m.Order) -> None:
    c = app.settings.checkout
    if order.status != m.PENDING:
        return
    payload = {"order_id": order.id}
    if c.payment_reminder_minutes > 0:
        await app.scheduler.schedule(
            TASK_PAYMENT_REMINDER,
            run_at=order.created_at + c.payment_reminder_minutes * 60,
            payload=payload,
            dedupe_key=reminder_task_key(order.id),
        )
    if c.payment_expiry_minutes > 0:
        await app.scheduler.schedule(
            TASK_PAYMENT_EXPIRE,
            run_at=order.created_at + c.payment_expiry_minutes * 60,
            payload=payload,
            dedupe_key=expiry_task_key(order.id),
        )


async def payment_instructions(app: App, order: m.Order) -> PaymentInstructions | None:
    provider = provider_for(order)
    if provider is None or order.status != m.PENDING:
        return None
    payment = await app.db.run(repo.payment_for, order.id)
    return provider.instructions(order, payment.external_ref if payment is not None else None)


async def customer_order_spec(app: App, order_id: int) -> MessageSpec:
    view = await order_view(app, order_id)
    if view is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return views.order_message(app.ui, view, instructions=await payment_instructions(app, view.order))


async def refresh_customer_message(app: App, order_id: int) -> bool:
    """Edit the order's workflow message to its current status view (best effort)."""
    view = await order_view(app, order_id)
    if view is None or view.order.customer_message_id is None:
        return False
    channel = await order_channel(app, view.order)
    if channel is None:
        return False
    try:
        spec = views.order_message(app.ui, view, instructions=await payment_instructions(app, view.order))
        await edit_spec(channel.get_partial_message(view.order.customer_message_id), spec)
    except MissingContent as exc:
        _content_missing(exc, "checkout.order_view")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "checkout_order_view_edit_failed", order_id=order_id, error=type(exc).__name__)
        return False
    return True


# -- staff order controls ------------------------------------------------------------------------------------------


def staff_spec(app: App, view: m.OrderView) -> MessageSpec:
    provider = provider_for(view.order)
    return views.staff_message(
        app.ui,
        view,
        staff_confirms_payment=provider.requires_staff_confirmation if provider is not None else True,
        credit_refunds=credit_refunds_enabled(app),
    )


def staff_channel(app: App) -> discord.TextChannel | None:
    guild = app.guild
    if guild is None:
        return None
    return app.resources.text_channel(guild, app.settings.checkout.staff_channel)


async def post_staff_message(app: App, order_id: int) -> bool:
    """Post the order controls in the staff channel and remember where they are."""
    view = await order_view(app, order_id)
    channel = staff_channel(app)
    if view is None or channel is None:
        log_event(log, logging.ERROR, "checkout_staff_channel_missing", order_id=order_id)
        return False
    try:
        message = await send_spec(channel, staff_spec(app, view))
    except MissingContent as exc:
        _content_missing(exc, "checkout.staff_message")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.ERROR, "checkout_staff_post_failed", order_id=order_id, error=type(exc).__name__)
        return False
    await app.db.transaction(repo.set_staff_message, order_id, channel.id, message.id, app.clock.now())
    return True


async def post_staff_details(app: App, order_id: int) -> None:
    """Post the customer's form answers under the staff controls (once per message part)."""
    order = await get_order(app, order_id)
    channel = staff_channel(app)
    if order is None or channel is None or not order.details:
        return
    try:
        messages = views.staff_details_messages(app.ui, app.settings, order)
    except MissingContent as exc:
        _content_missing(exc, "checkout.staff_details")
        return
    for index, spec in enumerate(messages):
        await app.notifier.notify_channel(
            channel_id=channel.id,
            spec=spec,
            kind="checkout.staff_details",
            dedupe_key=staff_details_key(order_id, index),
        )


async def refresh_staff_message(app: App, order_id: int) -> bool:
    """Edit the staff controls to the order's current state; re-post them if they were deleted."""
    view = await order_view(app, order_id)
    if view is None:
        return False
    order = view.order
    if order.staff_message_id is None or order.staff_channel_id is None:
        return await post_staff_message(app, order_id)
    channel = app.bot.get_channel(order.staff_channel_id) if app.bot is not None else None
    if not isinstance(channel, discord.TextChannel):
        return await post_staff_message(app, order_id)
    try:
        await edit_spec(channel.get_partial_message(order.staff_message_id), staff_spec(app, view))
    except discord.NotFound:
        return await post_staff_message(app, order_id)
    except MissingContent as exc:
        _content_missing(exc, "checkout.staff_message")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "checkout_staff_edit_failed", order_id=order_id, error=type(exc).__name__)
        return False
    return True


# -- notifications ---------------------------------------------------------------------------------------------------


async def notify_customer(
    app: App, view: m.OrderView, kind: str, build: Callable[[], MessageSpec], *, dedupe_suffix: str | None = None
) -> str:
    """Send one customer notification (DM, falling back to the order channel), at most once per dedupe key."""
    try:
        spec = build()
    except MissingContent as exc:
        _content_missing(exc, f"checkout.notify.{kind}")
        return "none"
    dedupe = notify_key(view.order.id, kind if dedupe_suffix is None else f"{kind}:{dedupe_suffix}")
    try:
        return await app.notifier.notify_user(
            user_id=view.order.user_id,
            spec=spec,
            kind=f"checkout.{kind}",
            dedupe_key=dedupe,
            fallback_channel_id=view.channel_id,
        )
    except Exception as exc:  # a notification failure never undoes the committed change
        log_event(
            log, logging.ERROR, "checkout_notify_failed", order_id=view.order.id, kind=kind, error=type(exc).__name__
        )
        return "none"


# -- order state machine -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    applied: bool
    order: m.Order | None
    previous: str | None = None


async def change_status(app: App, order_id: int, to: str, *, actor_id: int | None, note: str | None = None) -> Outcome:
    """Move an order to ``to`` through the state machine (see ``models``) and apply every side effect."""
    allowed = m.TRANSITIONS.get(to)
    if allowed is None:
        raise ValueError(f"no transition leads to {to!r}")
    moved = await app.db.transaction(
        repo.transition,
        guild_id=app.env.guild_id,
        order_id=order_id,
        allowed_from=allowed,
        to=to,
        actor_id=actor_id,
        note=note,
        now=app.clock.now(),
    )
    if moved is None:
        return Outcome(applied=False, order=await get_order(app, order_id))
    await after_transition(app, moved, actor_id=actor_id)
    return Outcome(applied=True, order=moved.after, previous=moved.before.status)


async def after_transition(app: App, moved: repo.Transition, *, actor_id: int | None) -> None:
    before, after = moved.before, moved.after
    log_event(
        log,
        logging.INFO,
        "checkout_order_status",
        order_id=after.id,
        number=after.number,
        old=before.status,
        new=after.status,
    )
    if before.status == m.PENDING:
        await app.scheduler.cancel(reminder_task_key(after.id))
        await app.scheduler.cancel(expiry_task_key(after.id))
    if moved.released:
        await catalog_service.schedule_listing_refresh(app)
    await refresh_customer_message(app, after.id)
    view = await order_view(app, after.id)
    if view is not None and after.status in k.NOTIFIED_STATUSES:
        await notify_customer(app, view, after.status, lambda: views.status_notification(app.ui, view, after.status))
    if after.status in m.TERMINAL_STATUSES and after.ticket_id is not None:
        await tickets_service.close_ticket(app, after.ticket_id, actor_id=actor_id, reason=after.status)
    await refresh_staff_message(app, after.id)
    if after.status == m.FULFILLED:
        await _schedule_review(app, after.id)
    await app.audit.record(
        f"checkout.order_{after.status}",
        actor_id=actor_id,
        target_type="order",
        target_id=after.id,
        details={"number": after.number, "from": before.status, "to": after.status, "automatic": actor_id is None},
    )


async def _schedule_review(app: App, order_id: int) -> None:
    """Ask the reviews feature (built separately) to prompt for a review, when it is ready and supports it."""
    if not app.ready(REVIEWS_FEATURE):
        return
    try:
        module = importlib.import_module("shopbot.features.reviews.service")
    except ImportError:
        return
    schedule = getattr(module, "schedule_review_prompt", None)
    if schedule is None:
        return
    try:
        await schedule(app, order_id)
    except Exception as exc:
        log_event(log, logging.ERROR, "checkout_review_schedule_failed", order_id=order_id, error=type(exc).__name__)


# -- refunds ------------------------------------------------------------------------------------------------------


class RefundError(Exception):
    """reason: 'missing' | 'state' | 'amount' | 'reason' | 'credits_unavailable' | 'provider_failed'."""

    def __init__(self, reason: str, order: m.Order | None = None, error_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.order = order
        self.error_id = error_id


@dataclass(frozen=True)
class RefundOutcome:
    record: repo.RefundRecord
    result: RefundResult | None  # None for a refund to store credit


async def refund(
    app: App, order_id: int, *, amount_minor: int, to_credit: bool, reason: str | None, actor_id: int
) -> RefundOutcome:
    """Record a (partial) refund, through the payment provider when it supports refunds, or as store credit."""
    async with runtime(app).refund_lock(order_id):
        order = await get_order(app, order_id)
        if order is None:
            raise RefundError("missing")
        if not order.can_refund:
            raise RefundError("state", order)
        if amount_minor <= 0 or amount_minor > order.refundable_minor:
            raise RefundError("amount", order)
        if to_credit and not credit_refunds_enabled(app):
            raise RefundError("credits_unavailable", order)
        if reason is not None and len(reason) > REFUND_REASON_MAX:
            raise RefundError("reason", order)
        result: RefundResult | None = None
        method = "credits"
        if not to_credit:
            provider = provider_for(order)
            result = RefundResult(method="manual")
            if provider is not None and provider.supports_refunds:
                try:
                    result = await provider.refund(app, order, amount_minor)
                except Exception as exc:
                    error_id = uuid.uuid4().hex[:12]
                    log_event(
                        log,
                        logging.ERROR,
                        "checkout_refund_provider_failed",
                        order_id=order_id,
                        error_id=error_id,
                        error=type(exc).__name__,
                    )
                    raise RefundError("provider_failed", order, error_id) from None
            method = "provider" if result.returned_by_provider else "manual"
        try:
            record = await app.db.transaction(
                repo.record_refund,
                guild_id=app.env.guild_id,
                order_id=order_id,
                amount_minor=amount_minor,
                method=method,
                to_credit=to_credit,
                reason=reason or None,
                actor_id=actor_id,
                external_ref=result.external_ref if result is not None else None,
                now=app.clock.now(),
            )
        except repo.RefundNotAllowed as exc:
            raise RefundError(exc.reason, order) from None
    if record.transition is not None:
        # The full refund moved the order to 'refunded': its status notification tells the customer.
        await after_transition(app, record.transition, actor_id=actor_id)
    else:
        await refresh_staff_message(app, order_id)
        view = await order_view(app, order_id)
        if view is not None:
            await notify_customer(
                app,
                view,
                k.NOTIFY_REFUND,
                lambda: views.refund_notification(app.ui, view, amount_minor),
                dedupe_suffix=str(record.adjustment.id),
            )
    await app.audit.record(
        "checkout.order_refund",
        actor_id=actor_id,
        target_type="order",
        target_id=order_id,
        details={
            "number": record.order.number,
            "amount_minor": amount_minor,
            "method": method,
            "to_credit": to_credit,
            "refunded_minor": record.order.refunded_minor,
            "adjustment_id": record.adjustment.id,
        },
    )
    return RefundOutcome(record=record, result=result)


async def add_note(app: App, order_id: int, *, actor_id: int, note: str) -> m.Order | None:
    order = await app.db.transaction(
        repo.add_note, guild_id=app.env.guild_id, order_id=order_id, actor_id=actor_id, note=note, now=app.clock.now()
    )
    if order is not None:
        await app.audit.record(
            "checkout.order_note",
            actor_id=actor_id,
            target_type="order",
            target_id=order_id,
            details={"number": order.number},
        )
    return order
