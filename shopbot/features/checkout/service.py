"""Checkout logic: starting a session, the workflow, submission, the order state machine, refunds.

Public API (also used by future payment webhooks)
-------------------------------------------------
- ``change_status(app, order_id, to, *, actor_id, note=None, allowed_from=None) -> Outcome``: the ONLY way an order
  changes status (staff buttons, ``/order status``/``cancel``, the customer's cancel, expiry tasks, webhooks). A second
  concurrent request finds the state changed (``Outcome.applied`` False) and changes nothing. ``allowed_from``
  narrows the states the change may start from (the customer cancels only an unpaid order; a staff button acts only
  on the status it was rendered for).
- ``refund(app, order_id, *, amount_minor, to_credit, reason, actor_id) -> RefundOutcome``: a money refund (through
  the payment provider when it supports refunds, else recorded for staff to return; or as store credit).
- ``return_credit(app, order_id, *, reason, actor_id) -> RefundOutcome``: gives the store credit the customer applied
  to a paid order back, all at once.
- ``order_view(app, order_id)``, ``get_order(app, order_id)``, ``get_order_by_number(app, number)``.
- ``add_note(app, order_id, *, actor_id, note)``: an internal staff note (staff activity, shown on the live order
  record; never to customers). ``record_activity``/``run_activity_followup``: the staff activity of the live record
  (claims, notes, correction requests, tracking details; see ``handling_repo``).

Events (``shopbot.core.events``): ``ORDER_SUBMITTED`` and ``ORDER_STATUS_CHANGED`` are published from the durable
follow-ups (``run_submit_followups``, ``run_transition_followups``), so they are published again when a follow-up is
retried; the operations feature schedules its deduplicated alert and customer-role tasks from them.

Refunds of a paid order
-----------------------
Cancelling a paid (or processing) order returns NOTHING automatically - neither money nor the store credit the
customer applied: staff decide with the refund controls (money refund, refund to store credit, and the
return-credit control, which gives the applied credit back through ``credits_repo.return_for_order`` with an
``order_adjustments`` row of kind ``credit_return``, in one transaction). The order becomes ``refunded`` once money
refunds cover its total AND the applied credit was given back; neither can exceed what was paid or applied, and a
repeated request changes nothing (an order marked ``refunded`` by money alone before this rule can still get its
applied credit back). An unpaid order that ends (rejected/cancelled/expired) gets its credit back
automatically, in the transaction that ends it. Refunds to store credit need the order's currency to still be the
shop currency (the credit ledger has no currency of its own).

Durability
----------
Every database write is one ``app.db.transaction`` unit (see ``repo``). Discord side effects (editing the workflow
message and the staff controls, notifications, closing the order channel, the review request) happen after the
commit; every step is isolated (an error is logged and the other steps still run) and safe to repeat. The commit
itself schedules a deduplicated follow-up task (``checkout.after_submit``, ``checkout.after_transition``,
``checkout.ticket_check``; see ``repo``) that performs the same steps again unless the process completed them right
away, so neither a crash nor an unexpected error after the commit loses them. ``tasks.on_ready`` additionally
closes order channels that can no longer be used and re-posts missing staff controls.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import PaymentMethod
from shopbot.core.events import ORDER_STATUS_CHANGED, ORDER_SUBMITTED, PAYMENT_METHOD_REQUESTED
from shopbot.core.ratelimit import Cooldowns
from shopbot.db.repos import notifications as notif_repo
from shopbot.features import core_keys as core
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog import service as catalog_service
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, gift_card_store, payment_methods, repo, staff_keys, staff_views, views
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import (
    PaymentInstructions,
    PaymentProvider,
    RefundResult,
    get_provider,
    gift_card,
)
from shopbot.features.checkout.totals import compute_totals
from shopbot.features.credits import service as credits_service
from shopbot.features.server_keys import channel_mentions
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import CREATING, KIND_ORDER, OPEN, Ticket
from shopbot.logging_setup import log_event
from shopbot.ui import custom_ids
from shopbot.ui.discord_render import RenderError, check_message, edit_spec, send_spec
from shopbot.ui.routing import HandlerError
from shopbot.ui.spec import EmbedSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_SESSION_EXPIRE = repo.TASK_SESSION_EXPIRE
TASK_PAYMENT_REMINDER = repo.TASK_PAYMENT_REMINDER
TASK_PAYMENT_EXPIRE = repo.TASK_PAYMENT_EXPIRE
TASK_AFTER_SUBMIT = repo.TASK_AFTER_SUBMIT
TASK_AFTER_TRANSITION = repo.TASK_AFTER_TRANSITION
TASK_TICKET_CHECK = repo.TASK_TICKET_CHECK
TASK_SESSION_WARNING = repo.TASK_SESSION_WARNING
TICKET_CATEGORY = "checkout"
CREDITS_FEATURE = "credits"
REVIEWS_FEATURE = "reviews"
# Anti-spam: minimum seconds between two order channels created by the same member (not a business rule).
START_COOLDOWN_SECONDS = 10
# Refund reasons are also stored as the store-credit note of a refund to credit.
REFUND_REASON_MAX = views.REFUND_REASON_MAX
# How many recent staff-channel messages are searched for an order's controls before posting them again.
STAFF_SCAN_LIMIT = 50

session_task_key = repo.session_task_key
reminder_task_key = repo.reminder_task_key
expiry_task_key = repo.expiry_task_key


def notify_key(order_id: int, kind: str) -> str:
    return f"checkout.notify:{order_id}:{kind}"


def staff_details_key(order_id: int, index: int, channel: str = "") -> str:
    """``channel``: set when the controls are posted again in a new channel (its parts are posted there again too)."""
    return f"checkout.staff_details:{order_id}:{index}{channel}"


def staff_items_key(order_id: int, index: int, channel: str = "") -> str:
    return f"checkout.staff_items:{order_id}:{index}{channel}"


def order_extra_key(order_id: int, part: str, index: int) -> str:
    return f"checkout.order_{part}:{order_id}:{index}"


def ticket_intro_key(ticket_id: int) -> str:
    return f"checkout.ticket_intro:{ticket_id}"


def session_warning_notice_key(session_id: int, idle_since: int) -> str:
    """One expiry warning per idle period of a session (any activity starts a new period)."""
    return f"checkout.session_warning:{session_id}:{idle_since}"


def ticket_url(app: App, channel_id: int) -> str:
    """A https:// link to a ticket channel (for link buttons)."""
    return f"https://discord.com/channels/{app.env.guild_id}/{channel_id}"


class FollowupIncomplete(Exception):
    """A follow-up step could not be completed (the follow-up task is retried)."""


# -- per-process runtime (anti-spam and per-order locks; not business state) ----------------------------------


@dataclass
class CheckoutRuntime:
    cooldowns: Cooldowns
    refund_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    followup_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    start_locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    def start_lock(self, user_id: int) -> asyncio.Lock:
        """Serializes one member's start and buy clicks (different buttons are not caught by the router's duplicate
        guard), so concurrent clicks open at most one order channel and every product lands in it."""
        lock = self.start_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self.start_locks[user_id] = lock
        return lock

    def refund_lock(self, order_id: int) -> asyncio.Lock:
        lock = self.refund_locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self.refund_locks[order_id] = lock
        return lock

    def followup_lock(self, order_id: int) -> asyncio.Lock:
        """Serializes the follow-up steps of one order (the in-process run and its durable task)."""
        lock = self.followup_locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self.followup_locks[order_id] = lock
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


def credit_refunds_possible(app: App, order: m.Order) -> bool:
    """Refunds of ``order`` can go to store credit: the credits feature is ready and the order was paid in the
    current shop currency (the credit ledger is kept in the shop currency)."""
    return credit_refunds_enabled(app) and order.currency == app.settings.checkout.currency


def provider_for(order: m.Order) -> PaymentProvider | None:
    return get_provider(order.payment_provider)


def _content_missing(exc: MissingContent, where: str) -> None:
    log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where=where)


async def _attempt(step: str, run: Callable[[], Awaitable[object]], **context: object) -> bool:
    """Run one side effect after a commit. Any error is logged and reported as False; the caller goes on with the
    other steps (a committed change is never undone, and the follow-up task repeats what failed)."""
    try:
        await run()
    except Exception as exc:
        log_event(log, logging.ERROR, "checkout_step_failed", step=step, error=type(exc).__name__, **context)
        log.warning("checkout step %s failed", step, exc_info=True)
        return False
    return True


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


async def request_payment_method(app: App, order: m.Order, method: PaymentMethod) -> bool:
    """The customer chose a payment method our team handles (kind "staff"): post it once in the order ticket, alert
    the staff (``PAYMENT_METHOD_REQUESTED``) and audit it. False when the customer already asked for this method."""
    key = payment_methods.request_key(order.id, method.id)
    if await app.db.run(notif_repo.already_sent, key):
        return False
    view = await order_view(app, order.id)
    if view is not None and view.channel_id:
        await app.notifier.notify_channel(
            channel_id=view.channel_id,
            spec=payment_methods.staff_method_post(app.ui, method, order.user_id),
            kind=payment_methods.NOTIFY_KIND,
            dedupe_key=key,
            user_id=order.user_id,
        )
    await app.audit.record(
        payment_methods.AUDIT_ACTION,
        actor_id=order.user_id,
        target_type="order",
        target_id=order.id,
        details={"number": order.number, "method": method.id},
    )
    await app.events.publish(PAYMENT_METHOD_REQUESTED, order_id=order.id, method=method.id)
    return True


async def order_view(app: App, order_id: int) -> m.OrderView | None:
    view = await app.db.run(repo.order_view, order_id)
    return view if view is not None and view.order.guild_id == app.env.guild_id else None


async def workflow_data(app: App, session_id: int) -> m.WorkflowData | None:
    return await app.db.run(repo.load_workflow, app.env.guild_id, session_id, app.settings, credits_enabled(app))


async def workflow_spec(app: App, session_id: int, *, notice: str | None = None) -> MessageSpec:
    data = await workflow_data(app, session_id)
    if data is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return views.workflow_message(app.ui, app.settings, data, notice=notice)


async def ticket_reference(app: App, session: m.Session) -> str | None:
    """The reference of the session's order ticket as the live panel shows it."""
    number = await app.db.run(repo.ticket_number, session.ticket_id)
    return views.ticket_reference(app.settings, number) if number is not None else None


async def session_channel(app: App, session: m.Session) -> discord.TextChannel | None:
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is None or ticket.status not in (CREATING, OPEN):
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


async def retire_vanished_session(app: App, session: m.Session) -> bool:
    """The session's order channel could not be found. Expire the session only when that is certain - its ticket is
    no longer active, or Discord answered that the channel does not exist (the deletion is then recorded, so the dead
    channel no longer counts toward the member's limit). A transient lookup failure changes nothing (False)."""
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    channel_id = ticket.channel_id if ticket is not None and ticket.status in (CREATING, OPEN) else None
    if channel_id is not None and not await _channel_confirmed_gone(app, channel_id):
        log_event(log, logging.WARNING, "checkout_session_channel_unknown", session_id=session.id)
        return False
    await expire_session(app, session.id, reason="channel_missing", render=False, close=False)
    if channel_id is not None:
        await tickets_service.channel_deleted(app, channel_id)
    return True


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


async def schedule_session_warning(app: App, session: m.Session) -> None:
    """Make sure an active session has its expiry-warning task (e.g. the warning was switched on later). The task
    re-arms itself for every idle period until the session ends (see ``tasks.session_warning_task``)."""
    c = app.settings.checkout
    if not k.session_warning_enabled(app.settings) or not session.active:
        return
    await app.scheduler.schedule(
        TASK_SESSION_WARNING,
        run_at=session.updated_at + (c.session_expiry_minutes - c.session_expiry_warning_minutes) * 60,
        payload={"session_id": session.id},
        dedupe_key=repo.session_warning_key(session.id),
        replace=True,
    )


async def warn_session_expiry(app: App, session: m.Session) -> str:
    """Post the expiry warning of ``session``'s current idle period in its order ticket, mentioning the customer
    (at most once per idle period). Returns the delivery ('channel', 'duplicate', 'none')."""
    c = app.settings.checkout
    channel = await session_channel(app, session)
    if channel is None:
        return "none"
    expires_at = session.updated_at + c.session_expiry_minutes * 60
    try:
        spec = views.session_warning_notification(
            app.ui, minutes=c.session_expiry_warning_minutes, expires_at=expires_at
        )
    except MissingContent as exc:
        _content_missing(exc, "checkout.session_warning")
        return "none"
    return await app.notifier.notify_user(
        user_id=session.user_id,
        spec=spec,
        kind=f"checkout.{k.NOTIFY_SESSION_WARNING}",
        dedupe_key=session_warning_notice_key(session.id, session.updated_at),
        fallback_channel_id=channel.id,
        prefer_channel=True,
    )


async def post_ticket_intro(
    app: App, *, ticket_id: int, channel: discord.TextChannel, number: int, user_id: int
) -> bool:
    """The order ticket's introduction (which ticket this is, who can see it), posted at most once per ticket and
    recorded like a notification. True when it is posted now or was posted before; False when it could not be
    built or Discord refused it (logged). It is sent to the channel object itself: a channel created a moment ago
    may not be in the gateway cache yet."""
    key = ticket_intro_key(ticket_id)
    if await app.db.run(notif_repo.already_sent, key):
        return True
    try:
        spec = views.ticket_intro(app.ui, app.settings, number, user_id)
    except MissingContent as exc:
        _content_missing(exc, "checkout.ticket_intro")
        return False
    try:
        await send_spec(channel, spec)
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "checkout_ticket_intro_failed", ticket_id=ticket_id, error=type(exc).__name__)
        return False
    await app.db.transaction(
        notif_repo.record,
        dedupe_key=key,
        user_id=user_id,
        channel_id=channel.id,
        kind="checkout.ticket_intro",
        delivery="channel",
        status="sent",
        error=None,
        now=app.clock.now(),
    )
    return True


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
        ticket_number=0,
    )
    check_message(views.ticket_intro(app.ui, app.settings, 0, 0))
    check_message(views.workflow_message(app.ui, app.settings, data))
    views.started_reply(app.ui, 0)


async def _limit_reply(app: App, user_id: int, limit: int) -> MessageSpec:
    """The member is at the open-order limit: a private link to the ticket of their newest open order whose ticket is
    still open (no second ticket is created), or the limit text when no such ticket can be linked."""
    for order in await app.db.run(repo.open_orders_for_user, app.env.guild_id, user_id):
        if order.ticket_id is None:
            continue
        ticket = await tickets_service.get_ticket(app, order.ticket_id)
        if ticket is None or ticket.status != OPEN:
            continue
        channel = await tickets_service.ticket_channel(app, ticket)
        if channel is not None:
            return views.order_exists_reply(app.ui, channel.id, order.number, limit, ticket_url(app, channel.id))
    return views.limit_reply(app.ui, limit)


async def finish_activation(app: App, session_id: int) -> None:
    """The session's workflow message exists but its ticket is still 'creating': open it."""
    session = await app.db.run(repo.get_session, session_id)
    if session is None or session.message_id is None:
        return
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is not None and ticket.status == CREATING:
        try:
            await tickets_service.activate_ticket(app, ticket.id, control_message_id=session.message_id)
        except tickets_service.TicketStateError:
            return


async def _ensure_panel(app: App, session: m.Session, channel: discord.TextChannel) -> bool:
    """The live panel of an active session exists: re-posted when it was never sent or was deleted. False when it is
    missing and could not be sent."""
    if session.message_id is not None:
        try:
            await channel.fetch_message(session.message_id)
            return True
        except discord.NotFound:
            log_event(log, logging.INFO, "checkout_workflow_missing", session_id=session.id)
        except discord.HTTPException:
            return True  # cannot tell whether it exists: never risk a second panel
    return await resend_workflow(app, session, channel)


async def resend_workflow(app: App, session: m.Session, channel: discord.TextChannel) -> bool:
    """An active session without its workflow message (the process stopped, or sending failed, after the session
    was recorded): send it now, record it and open the ticket. False when it could not be sent."""
    ticket = await tickets_service.get_ticket(app, session.ticket_id)
    if ticket is not None:
        # The introduction comes first; it is posted only if it never was (best effort: the panel matters most).
        await _attempt(
            "ticket_intro",
            lambda: post_ticket_intro(
                app, ticket_id=ticket.id, channel=channel, number=ticket.number, user_id=session.user_id
            ),
            session_id=session.id,
        )
    try:
        message = await send_spec(channel, await workflow_spec(app, session.id))
    except Exception as exc:
        log_event(
            log, logging.WARNING, "checkout_workflow_resend_failed", session_id=session.id, error=type(exc).__name__
        )
        return False
    await app.db.transaction(repo.set_session_message, session.id, message.id, app.clock.now())
    await finish_activation(app, session.id)
    return True


async def start(app: App, member: discord.Member) -> MessageSpec:
    """The start button: reuse the member's unfinished session, refuse over the open-order limit, or create the
    private order channel with its workflow message. Returns the private reply."""
    async with runtime(app).start_lock(member.id):
        return await _start(app, member, None)


async def buy(app: App, member: discord.Member, product_id: int) -> MessageSpec:
    """A catalog buy button (``sb:co:buy:<product_id>``): like the start button, but the product is put in the cart
    (one unit; every cart rule applies) and the workflow shows the cart. An existing unfinished order channel is
    continued (its live workflow message is updated) instead of opening a second one. Returns the private reply
    with a link to the channel. Nothing is created for a product that cannot be bought."""
    async with runtime(app).start_lock(member.id):
        return await _start(app, member, product_id)


def _buyable(product: catalog_repo.Product | None) -> catalog_repo.Product:
    """The product a buy button names, if it can be bought right now (else the configured customer error)."""
    if product is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if not product.listed:
        raise HandlerError(k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape(product.name))
    if product.available is not None and product.available <= 0:
        raise HandlerError(k.ERROR_OUT_OF_STOCK, name=views.escape(product.name), available=0)
    return product


async def _add_bought(app: App, session: m.Session, product_id: int) -> m.Session:
    try:
        updated, _ = await app.db.transaction(
            repo.add_to_cart,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            product_id=product_id,
            settings=app.settings.checkout,
            now=app.clock.now(),
        )
    except repo.CheckoutError as exc:
        raise customer_error(app, exc) from None
    return updated


async def _start(app: App, member: discord.Member, product_id: int | None) -> MessageSpec:
    guild_id = app.env.guild_id
    c = app.settings.checkout
    product = None
    if product_id is not None:
        product = _buyable(await app.db.run(catalog_repo.get_product, guild_id, product_id))
    existing = await app.db.run(repo.active_session_for_user, guild_id, member.id)
    if existing is not None:
        channel = await session_channel(app, existing)
        if channel is not None:
            if not await _ensure_panel(app, existing, channel):
                raise HandlerError(core.ERROR_GENERIC)
            if product is None:
                return views.session_exists_reply(app.ui, channel.id, ticket_url(app, channel.id))
            reply = views.buy_added_reply(app.ui, product.name, channel.id)  # built first: text may be missing
            updated = await _add_bought(app, existing, product.id)

            async def show_cart() -> None:
                await _edit_session_message(app, updated, await workflow_spec(app, updated.id))

            await _attempt("render_workflow", show_cart, session_id=updated.id)
            return reply
        if not await retire_vanished_session(app, existing):
            raise HandlerError(core.ERROR_GENERIC)  # the channel may still exist: keep the session
    open_orders = await app.db.run(repo.count_open_orders, guild_id, member.id)
    if open_orders >= c.max_open_orders_per_user:
        return await _limit_reply(app, member.id, c.max_open_orders_per_user)
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
        return await _limit_reply(app, member.id, exc.limit)
    except tickets_service.TicketCreateFailed as exc:
        log_event(log, logging.ERROR, "checkout_channel_create_failed", user_id=member.id, reason=exc.reason)
        raise HandlerError(core.ERROR_GENERIC) from None
    rt.cooldowns.mark(("start", member.id))
    attempts = app.scheduler.max_attempts
    try:
        session = await app.db.transaction(
            repo.create_session,
            guild_id=guild_id,
            user_id=member.id,
            ticket_id=created.ticket.id,
            now=app.clock.now(),
            expiry_seconds=c.session_expiry_minutes * 60,
            task_attempts=attempts,
            warning_seconds=c.session_expiry_warning_minutes * 60 if k.session_warning_enabled(app.settings) else 0,
        )
    except Exception as exc:
        log_event(log, logging.ERROR, "checkout_session_create_failed", user_id=member.id, error=type(exc).__name__)
        reason = f"session_failed:{type(exc).__name__}"
        await _attempt(
            "abort_ticket",
            lambda: tickets_service.abort_ticket(app, created.ticket.id, reason=reason, channel=created.channel),
        )
        raise HandlerError(core.ERROR_GENERIC) from None
    notice: EmbedSpec | None = None
    if product is not None:
        try:
            session = await _add_bought(app, session, product.id)
        except HandlerError as exc:  # e.g. sold out a moment ago: the channel opens with an empty cart
            log_event(log, logging.INFO, "checkout_buy_not_added", session_id=session.id, reason=exc.key)
            notice = app.ui.error_embed(exc.key, **exc.values)
    try:
        # First the ticket's introduction (which ticket this is, who can see it), then the live panel.
        introduced = await post_ticket_intro(
            app,
            ticket_id=created.ticket.id,
            channel=created.channel,
            number=created.ticket.number,
            user_id=member.id,
        )
        if not introduced:
            raise RuntimeError("the ticket introduction could not be posted")
        spec = await workflow_spec(app, session.id)
        message = await send_spec(created.channel, spec)
        await app.db.transaction(repo.set_session_message, session.id, message.id, app.clock.now())
        await tickets_service.activate_ticket(app, created.ticket.id, control_message_id=message.id)
    except Exception as exc:  # incl. network errors that are not HTTPException: never leave a half-made channel
        log_event(log, logging.ERROR, "checkout_workflow_send_failed", session_id=session.id, error=type(exc).__name__)
        reason = f"workflow_failed:{type(exc).__name__}"
        await _attempt(
            "end_session",
            lambda: app.db.transaction(
                repo.end_session, session.id, m.SESSION_CANCELLED, app.clock.now(), task_attempts=attempts
            ),
        )
        await _attempt(
            "abort_ticket",
            lambda: tickets_service.abort_ticket(app, created.ticket.id, reason=reason, channel=created.channel),
        )
        if isinstance(exc, MissingContent):
            raise
        raise HandlerError(core.ERROR_GENERIC) from None
    await _attempt(
        "audit",
        lambda: app.audit.record(
            "checkout.session_started",
            actor_id=member.id,
            target_type="checkout_session",
            target_id=session.id,
            details={"ticket_id": created.ticket.id, "channel_id": created.channel.id, "product_id": product_id},
        ),
    )
    if product is not None:
        return views.buy_started_reply(app.ui, created.channel.id, notice)
    return views.started_reply(app.ui, created.channel.id, ticket_url(app, created.channel.id))


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
    details_done = flow.details_step_complete(c, app.settings.shipping, current.fulfillment_option, current.details)
    if m.DETAILS in steps and index > steps.index(m.DETAILS) and not details_done:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _update(app, current, step=target)


async def choose_fulfillment(app: App, session: m.Session, option_id: str) -> m.Session:
    if app.settings.checkout.fulfillment(option_id) is None:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _update(app, session, fulfillment_option=option_id, step=m.FULFILLMENT)


async def choose_address_group(app: App, session: m.Session, group_id: str) -> m.Session:
    """The region menu: show that region's countries (the chosen country stays until another one is picked)."""
    if not app.settings.checkout.ask_address or all(g.id != group_id for g in app.settings.shipping.groups):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _merge_answers(app, session, {flow.ADDRESS_GROUP: group_id})


async def choose_address_country(app: App, session: m.Session, country: str) -> m.Session:
    if not app.settings.checkout.ask_address or not app.settings.shipping.ships_to(country):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _merge_answers(app, session, {flow.ADDRESS_COUNTRY: country})


@dataclass(frozen=True)
class AddressSaved:
    session: m.Session
    # The excluded area the address is in (e.g. "AK"): the answers are kept so the customer can correct them.
    excluded_area: str | None = None


async def save_address(app: App, session: m.Session, fields: Mapping[str, str]) -> AddressSaved:
    """Validate the address form server-side (lengths, required fields) and store it, even when it is in an area we
    do not ship to: the form then opens with the answers again so they can be corrected."""
    shipping = app.settings.shipping
    country = session.details.get(flow.ADDRESS_COUNTRY)
    if not app.settings.checkout.ask_address or country is None or not shipping.ships_to(country):
        raise HandlerError(k.ERROR_COUNTRY_FIRST)
    updates: dict[str, str | None] = {}
    for item in flow.ADDRESS_FIELDS:
        text = " ".join(fields.get(item.answer_key, "").split())  # one line: no stray line breaks or spaces
        if not text:
            if flow.address_field_required(item, shipping, country):
                raise HandlerError(core.ERROR_INVALID_INPUT)
            updates[item.answer_key] = None
            continue
        if not item.min_length <= len(text) <= item.max_length:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        updates[item.answer_key] = text
    saved = await _merge_answers(app, session, updates)
    area = flow.excluded_area(shipping, country, updates.get(f"{flow.ADDRESS_PREFIX}region") or "")
    return AddressSaved(saved, area)


async def toggle_extra(app: App, session: m.Session, extra_id: str) -> m.Session:
    """Add the extra to the order, or remove it if it was added (the delivery step's toggle buttons)."""
    if app.settings.checkout.find_extra(extra_id) is None:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    key = flow.extra_key(extra_id)
    added = session.details.get(key) == "1"
    return await _merge_answers(app, session, {key: None if added else "1"})


def _validated_answers(inputs: Sequence[flow.FormInput], fields: Mapping[str, str]) -> dict[str, str | None]:
    """The submitted answers of ``inputs`` checked server-side (required, length); None removes an empty optional
    answer. Raises ``HandlerError`` (invalid input) for anything the form itself should have refused."""
    updates: dict[str, str | None] = {}
    for item in inputs:
        text = fields.get(item.answer_key, "").strip()
        if not text:
            if item.field.required:
                raise HandlerError(core.ERROR_INVALID_INPUT)
            updates[item.answer_key] = None
            continue
        if not item.field.min_length <= len(text) <= item.field.max_length:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        updates[item.answer_key] = text
    return updates


async def save_details(app: App, session: m.Session, part: int, fields: Mapping[str, str]) -> m.Session:
    """Validate and store one part of the details form (answers of other parts are kept)."""
    c = app.settings.checkout
    parts = flow.form_parts(flow.form_inputs(c, session.fulfillment_option))
    if not 0 <= part < len(parts):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _merge_answers(app, session, _validated_answers(parts[part], fields))


async def save_submission(app: App, session: m.Session, fields: Mapping[str, str]) -> m.Session:
    """Validate and store the answers to ``checkout.submission_form`` in the session (they become part of the order
    when it is submitted, and are filled in again if the submission has to be repeated)."""
    inputs = flow.submission_inputs(app.settings.checkout)
    if not inputs:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return await _merge_answers(app, session, _validated_answers(inputs, fields))


async def _merge_answers(app: App, session: m.Session, updates: Mapping[str, str | None]) -> m.Session:
    c = app.settings.checkout
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
    except discord.NotFound:
        # The live panel was deleted: an unfinished order must keep its controls, so post it again.
        current = await app.db.run(repo.get_session, session.id)
        if current is not None and current.status == m.ACTIVE and isinstance(channel, discord.TextChannel):
            await resend_workflow(app, current, channel)
    except (discord.HTTPException, RenderError) as exc:
        log_event(
            log, logging.WARNING, "checkout_workflow_edit_failed", session_id=session.id, error=type(exc).__name__
        )


async def cancel_session(app: App, session: m.Session) -> m.Session | None:
    """The customer confirmed cancelling their unfinished order. None when it was no longer active. The
    cancellation also schedules the durable channel check (``checkout.ticket_check``)."""
    return await app.db.transaction(
        repo.end_session,
        session.id,
        m.SESSION_CANCELLED,
        app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
    )


async def _close_session_channel(app: App, session: m.Session, *, actor_id: int | None, reason: str) -> bool:
    """Close the order channel of an ended session; the durable channel check is no longer needed then."""
    closed = await _attempt(
        "close_channel",
        lambda: tickets_service.close_ticket(app, session.ticket_id, actor_id=actor_id, reason=reason),
        session_id=session.id,
    )
    if closed:
        await _attempt("cancel_check", lambda: app.scheduler.cancel(repo.ticket_check_key(session.ticket_id)))
    return closed


async def after_session_cancelled(app: App, session: m.Session, *, actor_id: int) -> None:
    """Close the cancelled session's order channel and audit it (each step runs even if another failed)."""
    await _close_session_channel(app, session, actor_id=actor_id, reason="cancelled")
    await _attempt(
        "audit",
        lambda: app.audit.record(
            "checkout.session_cancelled",
            actor_id=actor_id,
            target_type="checkout_session",
            target_id=session.id,
            details={"ticket_id": session.ticket_id},
        ),
    )


async def expire_session(app: App, session_id: int, *, reason: str, render: bool = True, close: bool = True) -> bool:
    """active -> expired (idle too long, or its channel disappeared). Returns False if it was not active. After the
    commit the channel is closed and the expiry audited even when re-rendering the workflow message fails."""
    ended = await app.db.transaction(
        repo.end_session, session_id, m.SESSION_EXPIRED, app.clock.now(), task_attempts=app.scheduler.max_attempts
    )
    if ended is None:
        return False

    async def show_expired() -> None:
        try:
            spec = views.session_ended(app.ui, m.SESSION_EXPIRED, reference=await ticket_reference(app, ended))
        except MissingContent as exc:
            _content_missing(exc, "checkout.session_expired")
            return
        await _edit_session_message(app, ended, spec)

    if render:
        await _attempt("render_expired", show_expired, session_id=session_id)
    if close:
        await _close_session_channel(app, ended, actor_id=None, reason="expired")
    await _attempt(
        "audit",
        lambda: app.audit.record(
            "checkout.session_expired",
            actor_id=None,
            target_type="checkout_session",
            target_id=session_id,
            details={"ticket_id": ended.ticket_id, "reason": reason},
        ),
    )
    return True


# -- staff closing an order ticket (/ticket close) -------------------------------------------------------------------


def _record_location(app: App, order: m.Order) -> str:
    """Where staff find an order's live record: a link to its message, else the staff channel, else '-'."""
    if order.staff_channel_id is not None and order.staff_message_id is not None:
        return f"https://discord.com/channels/{app.env.guild_id}/{order.staff_channel_id}/{order.staff_message_id}"
    channel_id = app.resources.channel_id(app.settings.checkout.staff_channel)
    return views.channel_mention(channel_id) if channel_id else "-"


def _order_open_reply(app: App, number: str, order: m.Order) -> tickets_service.StaffCloseReply:
    text = app.op("checkout.op.ticket_order_open", number=number, order=order.number, status=order.status)
    hint = app.op("checkout.op.ticket_order_open_hint", record=_record_location(app, order))
    return tickets_service.StaffCloseReply(text, "warning", hint)


async def _after_staff_ended(app: App, ended: m.Session, *, actor_id: int, reason: str | None) -> None:
    """A session staff ended by closing its ticket: audit it, then show the ended panel (best effort; missing text
    only skips the panel update)."""
    await _attempt(
        "audit",
        lambda: app.audit.record(
            "checkout.session_closed_by_staff",
            actor_id=actor_id,
            target_type="checkout_session",
            target_id=ended.id,
            details={"ticket_id": ended.ticket_id, "reason": reason},
        ),
        session_id=ended.id,
    )

    async def show_closed() -> None:
        try:
            reference = await ticket_reference(app, ended)
            spec = views.session_ended(app.ui, m.SESSION_CANCELLED, reference=reference, by_staff=True)
        except MissingContent as exc:
            _content_missing(exc, "checkout.staff_closed")
            return
        await _edit_session_message(app, ended, spec)

    await _attempt("render_staff_closed", show_closed, session_id=ended.id)


async def staff_close_ticket(
    app: App, ticket: Ticket, actor_id: int, reason: str | None
) -> tickets_service.StaffCloseReply:
    """``/ticket close`` in an order ticket (registered with the ticket system, which already checked that
    ``actor_id`` is order staff). Everything is re-read here:

    - a submitted order of the ticket that is still open (waiting for payment, paid, in progress) is refused: staff
      end it with the order's controls in the staff channel (its ticket closes when the order is final);
    - an unfinished order (active session; never submitted, so no stock or credit is held) is cancelled through the
      normal durable path (``repo.end_session`` schedules the ``checkout.ticket_check`` that closes the channel even
      if this process stops right after), audited, and its live panel shows the staff-closed text;
    - then the ticket is closed like an automatic closure (lock, transcript and deletion per settings, audit), with
      the reason the automatic closure would use (the session's or order's final status) so the transcript shows the
      final status. The staff member's optional ``reason`` is recorded in the audit log
      (``checkout.session_closed_by_staff``, or ``checkout.ticket_closed_by_staff`` when no session had to end).

    Conditional writes make repeated or concurrent closures (two staff, the customer cancelling or submitting at the
    same moment) change things at most once."""
    number = tickets_service.format_number(app, ticket.number)
    current = await tickets_service.get_ticket(app, ticket.id)
    if current is None or current.kind != KIND_ORDER or current.status != OPEN:
        status = current.status if current is not None else "-"
        return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_not_open", number=number, status=status))
    order = await app.db.run(repo.open_order_for_ticket, ticket.id)
    if order is not None:
        return _order_open_reply(app, number, order)
    session = await app.db.run(repo.active_session_for_ticket, ticket.id)
    ended: m.Session | None = None
    if session is not None:
        ended = await app.db.transaction(
            repo.end_session,
            session.id,
            m.SESSION_CANCELLED,
            app.clock.now(),
            task_attempts=app.scheduler.max_attempts,
        )
        if ended is not None:
            log_event(log, logging.INFO, "checkout_session_closed_by_staff", session_id=ended.id, ticket_id=ticket.id)
            await _after_staff_ended(app, ended, actor_id=actor_id, reason=reason)
    close_reason = await app.db.run(repo.ticket_close_reason, ticket.id)
    if close_reason is None:
        # The customer submitted the order at the same moment (or the ticket is otherwise in use again).
        order = await app.db.run(repo.open_order_for_ticket, ticket.id)
        if order is not None:
            return _order_open_reply(app, number, order)
        return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_busy", number=number), "warning")
    try:
        closed = await tickets_service.close_ticket(app, ticket.id, actor_id=actor_id, reason=close_reason)
    except Exception as exc:  # the durable channel check closes it later; never a crash in the command
        log_event(log, logging.ERROR, "checkout_staff_close_failed", ticket_id=ticket.id, error=type(exc).__name__)
        log.warning("closing order ticket %s failed", ticket.id, exc_info=True)
        await _attempt(
            "schedule_check",
            lambda: app.scheduler.schedule(
                TASK_TICKET_CHECK,
                run_at=app.clock.now() + repo.FOLLOWUP_DELAY_SECONDS,
                payload={"ticket_id": ticket.id},
                dedupe_key=repo.ticket_check_key(ticket.id),
                replace=True,
            ),
            ticket_id=ticket.id,
        )
        return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_close_pending", number=number), "warning")
    if closed is not None:
        await _attempt("cancel_check", lambda: app.scheduler.cancel(repo.ticket_check_key(ticket.id)))
        if ended is None:  # (an ended session was audited with the reason above)
            await _attempt(
                "audit",
                lambda: app.audit.record(
                    "checkout.ticket_closed_by_staff",
                    actor_id=actor_id,
                    target_type="ticket",
                    target_id=ticket.id,
                    details={"number": ticket.number, "close_reason": close_reason, "reason": reason},
                ),
                ticket_id=ticket.id,
            )
    if ended is not None:
        return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_session_closed", number=number), "success")
    if closed is None:
        latest = await tickets_service.get_ticket(app, ticket.id)
        status = latest.status if latest is not None else "-"
        return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_not_open", number=number, status=status))
    return tickets_service.StaffCloseReply(app.op("checkout.op.ticket_closed", number=number), "success")


# -- submission ----------------------------------------------------------------------------------------------------


async def submit(
    app: App, session: m.Session, expected_total_minor: int, expected_gross_minor: int | None = None
) -> m.Order:
    """Create the order from the session (idempotent). ``expected_*``: the totals the customer was shown (a missing
    gross amount counts as changed). Raises ``repo.CheckoutError`` (incl. ``PriceChanged``)."""
    result = await app.db.transaction(
        repo.submit_order,
        guild_id=app.env.guild_id,
        session_id=session.id,
        user_id=session.user_id,
        expected_total_minor=expected_total_minor,
        expected_gross_minor=expected_gross_minor,
        settings=app.settings,
        credits_enabled=credits_enabled(app),
        provider_id=app.settings.payments.provider,
        now=app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
    )
    if result.created:
        await _after_submit(app, result.order)
    return await get_order(app, result.order.id) or result.order


async def _after_submit(app: App, order: m.Order) -> None:
    """Right after the commit (which already scheduled the payment tasks and the ``checkout.after_submit``
    follow-up): the audit entry, then the Discord steps; the follow-up is cancelled when they all completed."""
    log_event(log, logging.INFO, "checkout_order_submitted", order_id=order.id, number=order.number)
    await _attempt(
        "audit",
        lambda: app.audit.record(
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
        ),
        order_id=order.id,
    )
    if await run_submit_followups(app, order.id, adopt=False):
        await _attempt("cancel_followup", lambda: app.scheduler.cancel(repo.after_submit_key(order.id)))


async def run_submit_followups(app: App, order_id: int, *, adopt: bool) -> bool:
    """The Discord side of a new order, safe to repeat: the catalog listing refresh, the payment provider's start
    (only while the payment has no provider reference yet), the staff controls (posted once; ``adopt`` first looks
    for controls that were posted but never recorded), the customer's form answers for staff (deduplicated; only
    with ``operations.answers_in_orders_channel``) and the customer's 'submission received' notification (once).
    Returns True when every step completed."""
    async with runtime(app).followup_lock(order_id):
        order = await get_order(app, order_id)
        if order is None:
            return True
        ok = await _attempt("listing_refresh", lambda: catalog_service.schedule_listing_refresh(app), order_id=order_id)
        ok &= await _attempt("payment_start", lambda: _start_payment(app, order_id), order_id=order_id)
        ok &= await _attempt(
            "staff_message", lambda: _ensure_staff_message(app, order_id, adopt=adopt), order_id=order_id
        )
        ok &= await _attempt("staff_details", lambda: post_staff_details(app, order_id), order_id=order_id)
        ok &= await _attempt("notification", lambda: _notify_submitted(app, order_id), order_id=order_id)
        # Operations: the new-order staff alert (its handler schedules a deduplicated task).
        await app.events.publish(ORDER_SUBMITTED, order_id=order_id)
        return ok


async def _notify_submitted(app: App, order_id: int) -> None:
    """'Submission received' (once per order; only while the order still waits for payment)."""
    view = await order_view(app, order_id)
    if view is None or view.order.status != m.PENDING:
        return
    kind = k.NOTIFY_SUBMITTED
    await notify_customer(
        app,
        view,
        kind,
        lambda: views.status_notification(app.ui, app.settings, view, kind, links=channel_mentions(app)),
    )


async def _start_payment(app: App, order_id: int) -> None:
    order = await get_order(app, order_id)
    provider = provider_for(order) if order is not None else None
    if order is None or provider is None or order.status != m.PENDING:
        return
    payment = await app.db.run(repo.payment_for, order.id)
    if payment is not None and payment.external_ref:
        return  # already started
    started = await provider.start(app, order)
    if started.external_ref:
        await app.db.transaction(repo.set_payment_external_ref, order.id, started.external_ref, app.clock.now())


async def _ensure_staff_message(app: App, order_id: int, *, adopt: bool) -> None:
    order = await get_order(app, order_id)
    if order is None or order.staff_message_id is not None:
        return
    if not await post_staff_message(app, order_id, adopt=adopt):
        raise FollowupIncomplete("staff controls not posted")


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
    instructions = provider.instructions(app.settings, order, payment.external_ref if payment is not None else None)
    # The provider's text is only registered while it is the configured provider.
    if instructions.description_key not in app.content.registry:
        return None
    progress = await provider.progress(app, order)
    if progress:
        instructions = replace(instructions, fields=progress)
    if provider.id == gift_card.PROVIDER_ID:
        counted = gift_card.counted_minor(await app.db.run(gift_card_store.cards, order.id))
        if counted:  # part of the total is paid: the instructions ask for the rest
            due = views.order_money(app.ui, app.settings, order.currency)(max(0, order.total_minor - counted))
            instructions = replace(instructions, amount_due=due)
    return instructions


async def _order_messages(app: App, view: m.OrderView) -> views.OrderMessages:
    """The order's workflow message: with the payment instructions while it waits for payment, the shipment (carrier,
    tracking number, link) once it shipped, and the place-order channel linked."""
    shipment = None
    if view.order.status == m.SHIPPED:
        shipment = (await app.db.run(h.get_handling, view.order.id)).shipment
    return views.order_messages(
        app.ui,
        app.settings,
        view,
        instructions=await payment_instructions(app, view.order),
        shipment=shipment,
        links=channel_mentions(app),
    )


async def customer_order_messages(app: App, order_id: int) -> views.OrderMessages:
    """The order's workflow message and what did not fit into it (see ``views.order_messages``)."""
    view = await order_view(app, order_id)
    if view is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    return await _order_messages(app, view)


async def customer_order_spec(app: App, order_id: int) -> MessageSpec:
    return (await customer_order_messages(app, order_id)).main


async def post_order_extras(app: App, order_id: int, messages: views.OrderMessages) -> None:
    """Post what did not fit into the order's workflow message (payment instructions, order lines) in the order
    channel, each part at most once."""
    if not messages.instructions and not messages.items:
        return
    view = await order_view(app, order_id)
    if view is None or view.channel_id is None:
        return
    for part, specs in (("instructions", messages.instructions), ("items", messages.items)):
        for index, spec in enumerate(specs):
            await app.notifier.notify_channel(
                channel_id=view.channel_id,
                spec=spec,
                kind=f"checkout.order_{part}",
                dedupe_key=order_extra_key(order_id, part, index),
                user_id=view.order.user_id,
            )


async def refresh_customer_message(app: App, order_id: int) -> bool:
    """Edit the order's workflow message to its current status view (best effort)."""
    view = await order_view(app, order_id)
    if view is None or view.order.customer_message_id is None:
        return False
    channel = await order_channel(app, view.order)
    if channel is None:
        return False
    try:
        messages = await _order_messages(app, view)
        await edit_spec(channel.get_partial_message(view.order.customer_message_id), messages.main)
    except MissingContent as exc:
        _content_missing(exc, "checkout.order_view")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "checkout_order_view_edit_failed", order_id=order_id, error=type(exc).__name__)
        return False
    await _attempt("order_extras", lambda: post_order_extras(app, order_id, messages), order_id=order_id)
    return True


# -- staff order controls ------------------------------------------------------------------------------------------


def staff_specs(app: App, view: m.OrderView, data: h.StaffData) -> list[MessageSpec]:
    """The live order record with its controls, then (large orders only) the order lines that did not fit into it."""
    provider = provider_for(view.order)
    return staff_views.staff_messages(
        app.ui,
        app.settings,
        view,
        data,
        staff_confirms_payment=provider.requires_staff_confirmation if provider is not None else True,
        credit_refunds=credit_refunds_possible(app, view.order),
        tracking=staff_keys.tracking_enabled(app.content),
    )


def staff_spec(app: App, view: m.OrderView, data: h.StaffData) -> MessageSpec:
    return staff_specs(app, view, data)[0]


async def staff_data(app: App, order_id: int) -> h.StaffData:
    """Claim, tracking details, status history and staff activity of an order (for the live record)."""
    return await app.db.run(h.staff_data, order_id)


def staff_channel(app: App) -> discord.TextChannel | None:
    guild = app.guild
    if guild is None:
        return None
    return app.resources.text_channel(guild, app.settings.checkout.staff_channel)


def component_custom_ids(message: object) -> list[str]:
    """custom_ids of a message's components (``discord.Message.components``: rows and their buttons/menus)."""
    top = getattr(message, "components", None)
    stack: list[object] = list(top) if isinstance(top, (list, tuple)) else []
    found: list[str] = []
    while stack:
        component = stack.pop()
        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str):
            found.append(custom_id)
        children = getattr(component, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(children)
        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            stack.append(accessory)
    return found


def controls_order_id(custom_id: str) -> int | None:
    """The order whose staff controls carry ``custom_id`` (the order id is the first argument), else None."""
    parsed = custom_ids.parse(custom_id)
    if parsed is None:
        return None
    route, action, args = parsed
    if route != k.STAFF_ROUTE or action not in views.ORDER_CONTROL_ACTIONS or not args or not args[0].isdigit():
        return None
    return int(args[0])


async def find_staff_message(app: App, channel: discord.TextChannel, order_id: int) -> discord.Message | None:
    """The bot's controls of ``order_id`` among the recent messages of the staff channel - posted, but never
    recorded (the response was lost or the process stopped) - recognized by the order id in their buttons."""
    me = app.bot.user if app.bot is not None else None
    if me is None:
        return None
    try:
        async for message in channel.history(limit=STAFF_SCAN_LIMIT):
            if message.author.id != me.id:
                continue
            if any(controls_order_id(custom_id) == order_id for custom_id in component_custom_ids(message)):
                return message
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "checkout_staff_scan_failed", order_id=order_id, status=exc.status)
    return None


async def _post_staff_extras(
    app: App, order_id: int, channel_id: int, specs: Sequence[MessageSpec], channel: str = ""
) -> None:
    for index, spec in enumerate(specs):
        await app.notifier.notify_channel(
            channel_id=channel_id,
            spec=spec,
            kind="checkout.staff_items",
            dedupe_key=staff_items_key(order_id, index, channel),
        )


async def post_staff_message(app: App, order_id: int, *, adopt: bool = False, new_channel: bool = False) -> bool:
    """Post the order controls in the staff channel and remember where they are (with ``adopt``, controls already
    posted for this order but never recorded are reused instead of posting a duplicate). The order lines of a large
    order and (with ``operations.answers_in_orders_channel``) the customer's form answers follow as separate
    messages (each at most once per channel: with ``new_channel`` they are posted again in the new one)."""
    view = await order_view(app, order_id)
    channel = staff_channel(app)
    if view is None or channel is None:
        log_event(log, logging.ERROR, "checkout_staff_channel_missing", order_id=order_id)
        return False
    try:
        specs = staff_specs(app, view, await staff_data(app, order_id))
    except MissingContent as exc:
        _content_missing(exc, "checkout.staff_message")
        return False
    message = await find_staff_message(app, channel, order_id) if adopt else None
    if message is not None:
        log_event(log, logging.WARNING, "checkout_staff_message_adopted", order_id=order_id, message_id=message.id)
        try:
            await edit_spec(message, specs[0])
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.WARNING, "checkout_staff_edit_failed", order_id=order_id, error=type(exc).__name__)
    else:
        try:
            message = await send_spec(channel, specs[0])
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.ERROR, "checkout_staff_post_failed", order_id=order_id, error=type(exc).__name__)
            return False
    await app.db.transaction(repo.set_staff_message, order_id, channel.id, message.id, app.clock.now())
    suffix = f":c{channel.id}" if new_channel else ""
    await _attempt(
        "staff_items", lambda: _post_staff_extras(app, order_id, channel.id, specs[1:], suffix), order_id=order_id
    )
    await _attempt("staff_details", lambda: post_staff_details(app, order_id, suffix), order_id=order_id)
    return True


async def repost_lost_staff_messages(app: App) -> int:
    """Post the controls of every open order whose controls were in a channel that no longer exists (the orders
    channel was deleted, e.g. by /reset): /setup calls it after creating the new channel. Orders whose channel still
    exists are left alone; a single deleted message is posted again by the order's next change or a restart.
    Returns how many were posted."""
    if app.bot is None:
        return 0
    posted = 0
    for order in await app.db.run(repo.orders_with_status, app.env.guild_id, m.OPEN_STATUSES):
        if order.staff_channel_id is None or app.bot.get_channel(order.staff_channel_id) is not None:
            continue  # never posted yet (its submission task posts it), or still in place
        if await post_staff_message(app, order.id, new_channel=True):
            posted += 1
    if posted:
        log_event(log, logging.INFO, "checkout_staff_messages_reposted", count=posted)
    return posted


def answers_in_staff_channel(app: App) -> bool:
    """Whether the customer's form answers are posted as messages in the staff channel
    (``operations.answers_in_orders_channel``). Off: staff see them only privately (the record's Details button,
    ``/order view``), after the staff check of that interaction."""
    return app.settings.operations.answers_in_orders_channel


async def post_staff_details(app: App, order_id: int, channel_suffix: str = "") -> None:
    """Post the customer's form answers under the staff controls (once per message part), only when
    ``operations.answers_in_orders_channel`` is on; otherwise nothing is posted."""
    if not answers_in_staff_channel(app):
        return
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
            dedupe_key=staff_details_key(order_id, index, channel_suffix),
        )


async def refresh_staff_message(app: App, order_id: int) -> bool:
    """Edit the staff controls to the order's current state; re-post them if they were deleted."""
    view = await order_view(app, order_id)
    if view is None:
        return False
    order = view.order
    if order.staff_message_id is None or order.staff_channel_id is None:
        return await post_staff_message(app, order_id, adopt=True)
    channel = app.bot.get_channel(order.staff_channel_id) if app.bot is not None else None
    if not isinstance(channel, discord.TextChannel):
        return await post_staff_message(app, order_id)
    try:
        specs = staff_specs(app, view, await staff_data(app, order_id))
        await edit_spec(channel.get_partial_message(order.staff_message_id), specs[0])
    except discord.NotFound:
        return await post_staff_message(app, order_id)
    except MissingContent as exc:
        _content_missing(exc, "checkout.staff_message")
        return False
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "checkout_staff_edit_failed", order_id=order_id, error=type(exc).__name__)
        return False
    await _attempt("staff_items", lambda: _post_staff_extras(app, order_id, channel.id, specs[1:]), order_id=order_id)
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


async def change_status(
    app: App,
    order_id: int,
    to: str,
    *,
    actor_id: int | None,
    note: str | None = None,
    allowed_from: Sequence[str] | None = None,
) -> Outcome:
    """Move an order to ``to`` through the state machine (see ``models``) and apply every side effect.
    ``allowed_from`` (optional) narrows the statuses the change may start from; it never widens them."""
    allowed = m.TRANSITIONS.get(to)
    if allowed is None:
        raise ValueError(f"no transition leads to {to!r}")
    if allowed_from is not None:
        allowed = tuple(status for status in allowed if status in allowed_from)
    moved = await app.db.transaction(
        repo.transition,
        guild_id=app.env.guild_id,
        order_id=order_id,
        allowed_from=allowed,
        to=to,
        actor_id=actor_id,
        note=note,
        now=app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
    )
    if moved is None:
        return Outcome(applied=False, order=await get_order(app, order_id))
    await after_transition(app, moved, actor_id=actor_id)
    return Outcome(applied=True, order=moved.after, previous=moved.before.status)


async def after_transition(app: App, moved: repo.Transition, *, actor_id: int | None) -> None:
    """Right after a committed status change: the audit entry, then the follow-up steps; the durable follow-up
    (``checkout.after_transition``, scheduled by the commit) is cancelled when every step completed."""
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
    await _attempt(
        "audit",
        lambda: app.audit.record(
            f"checkout.order_{after.status}",
            actor_id=actor_id,
            target_type="order",
            target_id=after.id,
            details={"number": after.number, "from": before.status, "to": after.status, "automatic": actor_id is None},
        ),
        order_id=after.id,
    )
    done = await run_transition_followups(app, after.id, after.status, actor_id=actor_id, released=moved.released)
    if done:
        key = repo.after_transition_key(after.id, after.status)
        await _attempt("cancel_followup", lambda: app.scheduler.cancel(key), order_id=after.id)


async def run_transition_followups(
    app: App, order_id: int, status: str, *, actor_id: int | None, released: bool = False
) -> bool:
    """Everything that follows a change to ``status``, each step isolated and safe to repeat: the catalog listing
    (when held stock was released), the customer's order view, the status notification (once; only while the order
    is still in ``status``), closing the order channel of a final order, the staff controls, and the review request
    of a fulfilled order (deduplicated by reviews). Returns True when every step completed."""
    async with runtime(app).followup_lock(order_id):
        ok = True
        if released:
            ok &= await _attempt("listing_refresh", lambda: catalog_service.schedule_listing_refresh(app))
        ok &= await _attempt("customer_message", lambda: refresh_customer_message(app, order_id), order_id=order_id)
        ok &= await _attempt("notification", lambda: _notify_status(app, order_id, status), order_id=order_id)
        ok &= await _attempt("close_channel", lambda: _close_final_channel(app, order_id, actor_id), order_id=order_id)
        ok &= await _attempt("staff_message", lambda: refresh_staff_message(app, order_id), order_id=order_id)
        if status == m.FULFILLED:
            ok &= await _attempt("review", lambda: _schedule_review(app, order_id), order_id=order_id)
        # Operations: staff alerts and the customer role (handlers schedule deduplicated tasks).
        await app.events.publish(ORDER_STATUS_CHANGED, order_id=order_id, status=status, actor_id=actor_id)
        return ok


# Statuses an order can reach more than once (gift cards submitted again after one was refused): their notification
# is sent again each time (deduplicated per order version instead of once per order).
_REPEATABLE_NOTIFICATIONS = (m.PAYMENT_SUBMITTED,)


async def _notify_status(app: App, order_id: int, status: str) -> None:
    view = await order_view(app, order_id)
    if view is None or view.order.status != status:
        return
    if status == m.PENDING:
        await _notify_payment_refused(app, view)
        return
    if status not in k.NOTIFIED_STATUSES:
        return
    # Staff-entered data the notification carries: the rejection reason, the tracking details of a completed order.
    extras = await app.db.run(h.notification_data, order_id, status)
    await notify_customer(
        app,
        view,
        status,
        lambda: replace(
            staff_views.with_fields(
                views.status_notification(app.ui, app.settings, view, status, links=channel_mentions(app)),
                staff_views.notification_fields(app.ui, status, extras),
            ),
            rows=staff_views.notification_rows(app.ui, status, extras),
        ),
        dedupe_suffix=f"v{view.order.version}" if status in _REPEATABLE_NOTIFICATIONS else None,
    )


async def _notify_payment_refused(app: App, view: m.OrderView) -> None:
    """The order waits for payment again because staff refused a gift card: the cards they refused with the reasons,
    what is still to pay (the order view in the ticket shows the Submit gift card button again)."""
    order = view.order
    since = next((e.created_at for e in reversed(view.timeline) if e.to_status == m.PAYMENT_SUBMITTED), None)
    if since is None or not gift_card.enabled(app.settings):
        return
    cards = await app.db.run(gift_card_store.cards, order.id)
    refused = tuple(c.id for c in cards if c.status == gift_card_store.REFUSED and (c.decided_at or 0) >= since)
    if not refused:
        return
    counted = sum(c.amount_minor for c in cards if c.status in gift_card_store.ACTIVE)
    remaining = views.order_money(app.ui, app.settings, order.currency)(max(0, order.total_minor - counted))
    await notify_customer(
        app,
        view,
        gift_card.NOTIFY_CARD_PROBLEM,
        lambda: views.payment_refused_notification(
            app.ui,
            app.settings,
            view,
            remaining=remaining,
            fields=gift_card.cards_fields(app.ui, app.settings, order, cards, only=refused),
        ),
        dedupe_suffix=f"v{order.version}",
    )


async def _close_final_channel(app: App, order_id: int, actor_id: int | None) -> None:
    order = await get_order(app, order_id)
    if order is None or order.status not in m.TERMINAL_STATUSES or order.ticket_id is None:
        return
    ticket = await tickets_service.get_ticket(app, order.ticket_id)
    if ticket is None or ticket.status != OPEN:
        return
    await tickets_service.close_ticket(app, ticket.id, actor_id=actor_id, reason=order.status)


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
    """reason: 'missing' | 'state' | 'amount' | 'reason' | 'credits_unavailable' | 'currency' | 'provider_failed'."""

    def __init__(self, reason: str, order: m.Order | None = None, error_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.order = order
        self.error_id = error_id


@dataclass(frozen=True)
class RefundOutcome:
    record: repo.RefundRecord
    result: RefundResult | None  # None for a refund to store credit or a credit return


async def refund(
    app: App, order_id: int, *, amount_minor: int, to_credit: bool, reason: str | None, actor_id: int
) -> RefundOutcome:
    """Record a (partial) money refund, through the payment provider when it supports refunds, or as store credit."""
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
        if to_credit and order.currency != app.settings.checkout.currency:
            raise RefundError("currency", order)
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
                task_attempts=app.scheduler.max_attempts,
            )
        except repo.RefundNotAllowed as exc:
            raise RefundError(exc.reason, order) from None
    await _after_refund(app, record, amount_minor=amount_minor, actor_id=actor_id)
    await _attempt(
        "audit",
        lambda: app.audit.record(
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
        ),
        order_id=order_id,
    )
    return RefundOutcome(record=record, result=result)


async def return_credit(app: App, order_id: int, *, reason: str | None, actor_id: int) -> RefundOutcome:
    """Give the store credit the customer applied to a paid order back to their balance, all at once (see the module
    docstring). Raises ``RefundError`` ('missing', 'state' when there is nothing to give back, 'reason')."""
    async with runtime(app).refund_lock(order_id):
        order = await get_order(app, order_id)
        if order is None:
            raise RefundError("missing")
        if not order.can_return_credit:
            raise RefundError("state", order)
        if reason is not None and len(reason) > REFUND_REASON_MAX:
            raise RefundError("reason", order)
        try:
            record = await app.db.transaction(
                repo.return_order_credit,
                guild_id=app.env.guild_id,
                order_id=order_id,
                reason=reason or None,
                actor_id=actor_id,
                now=app.clock.now(),
                task_attempts=app.scheduler.max_attempts,
            )
        except repo.RefundNotAllowed as exc:
            raise RefundError(exc.reason, order) from None
    amount = record.adjustment.amount_minor
    await _after_refund(app, record, amount_minor=amount, actor_id=actor_id)
    await _attempt(
        "audit",
        lambda: app.audit.record(
            "checkout.order_credit_returned",
            actor_id=actor_id,
            target_type="order",
            target_id=order_id,
            details={"number": record.order.number, "amount_minor": amount, "adjustment_id": record.adjustment.id},
        ),
        order_id=order_id,
    )
    return RefundOutcome(record=record, result=None)


async def _after_refund(app: App, record: repo.RefundRecord, *, amount_minor: int, actor_id: int) -> None:
    order_id = record.order.id
    if record.transition is not None:
        # The order is now fully refunded: its 'refunded' status notification tells the customer.
        await after_transition(app, record.transition, actor_id=actor_id)
        return
    await _attempt("staff_message", lambda: refresh_staff_message(app, order_id), order_id=order_id)
    view = await order_view(app, order_id)
    if view is not None:
        await notify_customer(
            app,
            view,
            k.NOTIFY_REFUND,
            lambda: views.refund_notification(app.ui, app.settings, view, amount_minor, links=channel_mentions(app)),
            dedupe_suffix=str(record.adjustment.id),
        )


# -- staff activity (claims, notes, correction requests, tracking details) -------------------------------------------


async def record_activity(
    app: App,
    write: Callable[..., h.Change],
    order_id: int,
    *,
    actor_id: int,
    audit_action: str,
    **values: object,
) -> h.Change:
    """Run one ``handling_repo`` write (it raises ``HandlingError`` and changes nothing when it is not allowed), audit
    it, then run its follow-up right away (the durable follow-up task, scheduled by the write, is cancelled when every
    step completed)."""
    change = await app.db.transaction(
        write,
        guild_id=app.env.guild_id,
        order_id=order_id,
        actor_id=actor_id,
        now=app.clock.now(),
        task_attempts=app.scheduler.max_attempts,
        **values,
    )
    activity = change.activity
    details: dict[str, object] = {"number": change.order.number, "activity_id": activity.id}
    if activity.text is not None:
        details["length"] = len(activity.text)  # the text itself stays in the database (it may be private)
    await _attempt(
        "audit",
        lambda: app.audit.record(
            audit_action, actor_id=actor_id, target_type="order", target_id=order_id, details=details
        ),
        order_id=order_id,
    )
    if await run_activity_followup(app, order_id, activity.id):
        await _attempt("cancel_followup", lambda: app.scheduler.cancel(h.activity_followup_key(activity.id)))
    return change


async def run_activity_followup(app: App, order_id: int, activity_id: int) -> bool:
    """After a staff activity: the live record is brought up to date, and a correction request or tracking details
    of a completed order are sent to the customer (each at most once). Safe to repeat; True when every step
    completed."""

    async def refresh() -> None:
        if not await refresh_staff_message(app, order_id):
            raise FollowupIncomplete("live order record not updated")  # e.g. a Discord error: the task retries

    async with runtime(app).followup_lock(order_id):
        ok = await _attempt("staff_message", refresh, order_id=order_id)
        activity = await app.db.run(h.get_activity, activity_id)
        if activity is not None and activity.kind in (h.CORRECTION, h.TRACKING):
            ok &= await _attempt("notification", lambda: notify_activity(app, activity), order_id=order_id)
        return ok


async def notify_activity(app: App, activity: h.Activity) -> str:
    """The customer notification of a correction request (only while the order still waits for payment) or of
    tracking details entered after the order shipped. Returns the delivery ('skipped' when not applicable)."""
    view = await order_view(app, activity.order_id)
    if view is None or not activity.text:
        return "skipped"
    order = view.order
    text = activity.text
    if activity.kind == h.CORRECTION and order.status == m.PENDING:
        return await notify_customer(
            app,
            view,
            staff_keys.NOTIFY_CORRECTION,
            lambda: staff_views.correction_notification(app.ui, app.settings, view, text),
            dedupe_suffix=str(activity.id),
        )
    if activity.kind == h.TRACKING and await _tracking_already_sent(app, order, activity):
        if not staff_keys.tracking_enabled(app.content):
            return "skipped"
        return await notify_customer(
            app,
            view,
            staff_keys.NOTIFY_TRACKING,
            lambda: staff_views.tracking_notification(app.ui, app.settings, view, text),
            dedupe_suffix=str(activity.id),
        )
    return "skipped"


async def _tracking_already_sent(app: App, order: m.Order, activity: h.Activity) -> bool:
    """Tracking details entered after the customer was told the order shipped (or was delivered) are sent on their
    own; earlier ones are replaced by the shipment Mark shipped records, or go out with the delivered notification."""
    if order.status == m.FULFILLED:
        return order.fulfilled_at is not None and order.fulfilled_at <= activity.created_at
    if order.status == m.SHIPPED:
        shipped_at = (await app.db.run(h.get_handling, order.id)).shipped_at
        return shipped_at is not None and shipped_at <= activity.created_at
    return False


async def add_note(app: App, order_id: int, *, actor_id: int, note: str) -> m.Order | None:
    """An internal staff note (``/order note``, the note button; other features may call it too). None when the order
    does not exist or the note is empty/too long. Notes are never shown to customers."""
    try:
        change = await record_activity(
            app, h.add_note, order_id, actor_id=actor_id, audit_action="checkout.order_note", text=note
        )
    except h.HandlingError:
        return None
    return change.order
