"""Component and form handlers.

Customer routes (``co``, any member): every handler re-loads the session/order by id, checks it belongs to the
configured guild and to the clicking member (staff can see order channels but cannot act on a customer's
workflow), checks its state, then performs and persists the change and edits the single workflow message. The claim
button of a coupon (``dc``, any member, e.g. under an announcement) answers privately (``discount_codes``).

Discord keeps a select menu showing the option that was picked until its message is edited, and picking the same
option again sends nothing. So after a pick in the product or cart menu (the quantity form opens) and after a
refused pick or quantity, the workflow message is re-rendered (best effort), and the same product can be picked
again.

Staff routes (``cs``, access ``staff`` incl. ``checkout.staff_roles``): status changes through the state machine
(a button acts only on the status it was rendered for; reject opens a form for the reason the customer receives),
refund forms, giving back applied store credit, claim/unclaim, internal notes, correction requests, tracking details,
the private full record ("details"), and the order list (pages, status filter, record menu). Every handler re-checks
the staff role server-side and reloads the order by id. Replies to staff are private operator text.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import MissingContent
from shopbot.core.countries import area_name
from shopbot.features import core_keys as core
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import (
    discount_codes,
    discount_views,
    flow,
    gift_cards,
    payment_methods,
    repo,
    service,
    staff,
    staff_keys,
    staff_views,
    views,
)
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import gift_card
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, edit_kwargs
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

_WHOLE = re.compile(r"^\d{1,7}$")
_AMOUNT = re.compile(r"^\d{1,13}$")


def staff_role_keys(app: App) -> tuple[str, ...]:
    """Roles that count as staff for orders besides permissions.staff_roles."""
    return app.settings.checkout.staff_roles


# -- helpers ------------------------------------------------------------------------------------------------------


async def _session(ctx: Ctx) -> m.Session:
    """The clicking member's own ACTIVE session named by the first custom_id argument."""
    session = await service.get_session(ctx.app, ctx.arg_int(0))
    if session is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if session.user_id != ctx.user_id:
        raise HandlerError(core.ERROR_PERMISSION)
    if not session.active:
        raise HandlerError(k.ERROR_SESSION_CLOSED)
    return session


async def _render(ctx: Ctx, session_id: int) -> None:
    await ctx.update(await service.workflow_spec(ctx.app, session_id))


async def _reset_workflow(ctx: Ctx, session_id: int) -> None:
    """Best effort, after the interaction was answered (e.g. with the quantity form): re-render the workflow
    message the pick came from, so its menus no longer show the picked option."""
    message = ctx.interaction.message
    if message is None:
        return
    try:
        spec = await service.workflow_spec(ctx.app, session_id)
        await message.edit(**edit_kwargs(spec, message.attachments))
    except (discord.HTTPException, RenderError, MissingContent, HandlerError) as exc:
        log_event(log, logging.INFO, "checkout_menu_reset_failed", session_id=session_id, error=type(exc).__name__)


async def _refuse_and_reset(ctx: Ctx, session_id: int, exc: HandlerError) -> None:
    """A refused pick or quantity: re-render the workflow message (resetting its menus) as the answer, then send
    the configured error privately."""
    if ctx.interaction.message is not None and not ctx.responded:
        try:
            await _render(ctx, session_id)
        except (discord.HTTPException, RenderError, MissingContent, HandlerError) as err:
            log_event(log, logging.INFO, "checkout_menu_reset_failed", session_id=session_id, error=type(err).__name__)
    await ctx.fail(exc.key, **exc.values)


def _selected_int(ctx: Ctx) -> int:
    if len(ctx.values) != 1 or not ctx.values[0].isdigit():
        raise HandlerError(core.ERROR_INVALID_INPUT)
    return int(ctx.values[0])


async def _open_quantity(ctx: Ctx, session: m.Session, product_id: int) -> None:
    app = ctx.app
    product = await app.db.run(catalog_repo.get_product, app.env.guild_id, product_id)
    if product is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    current = (await app.db.run(repo.cart_quantities, session.id)).get(product_id)
    # A product already in the cart can always be opened (e.g. to remove it with 0 after it became unavailable).
    if current is None and not product.listed:
        raise HandlerError(k.ERROR_PRODUCT_UNAVAILABLE, name=views.escape(product.name))
    available = product.available
    if current is None and available is not None and available <= 0:
        raise HandlerError(k.ERROR_OUT_OF_STOCK, name=views.escape(product.name), available=0)
    await ctx.send_modal(views.quantity_modal(app.ui, session.id, product_id, current))


async def _customer_order(ctx: Ctx) -> m.Order:
    order = await service.get_order(ctx.app, ctx.arg_int(0))
    if order is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if order.user_id != ctx.user_id:
        raise HandlerError(core.ERROR_PERMISSION)
    return order


async def _show_order(ctx: Ctx, order_id: int) -> None:
    """Show the order on the workflow message; what did not fit follows as separate messages (once)."""
    messages = await service.customer_order_messages(ctx.app, order_id)
    await ctx.update(messages.main)
    try:
        await service.post_order_extras(ctx.app, order_id, messages)
    except Exception as exc:  # the order view was shown; the follow-up task and later refreshes post the rest
        log_event(log, logging.ERROR, "checkout_order_extras_failed", order_id=order_id, error=type(exc).__name__)


# -- customer: start --------------------------------------------------------------------------------------------


async def on_start(ctx: Ctx) -> None:
    member = ctx.member
    if member is None or member.bot:
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_reply(ephemeral=True)
    spec = await service.start(ctx.app, member)
    await ctx.respond(spec, ephemeral=True)


async def on_buy(ctx: Ctx) -> None:
    """A catalog product's buy button (``sb:co:buy:<product_id>``, in the listing or private browsing): open or
    continue the member's order channel with the product in the cart. The custom_id carries only the product id;
    the product, the member's session and every limit are re-checked here."""
    member = ctx.member
    if member is None or member.bot:
        raise HandlerError(core.ERROR_PERMISSION)
    product_id = ctx.arg_int(0)
    await ctx.defer_reply(ephemeral=True)
    spec = await service.buy(ctx.app, member, product_id)
    await ctx.respond(spec, ephemeral=True)


# -- customer: workflow -----------------------------------------------------------------------------------------


async def on_pick(ctx: Ctx) -> None:
    session = await _session(ctx)
    try:
        await _open_quantity(ctx, session, _selected_int(ctx))
    except HandlerError as exc:
        await _refuse_and_reset(ctx, session.id, exc)
        return
    await _reset_workflow(ctx, session.id)


async def on_edit(ctx: Ctx) -> None:
    session = await _session(ctx)
    try:
        product_id = _selected_int(ctx)
        if product_id not in await ctx.app.db.run(repo.cart_quantities, session.id):
            raise HandlerError(core.ERROR_NOT_FOUND)
        await _open_quantity(ctx, session, product_id)
    except HandlerError as exc:
        await _refuse_and_reset(ctx, session.id, exc)
        return
    await _reset_workflow(ctx, session.id)


async def on_quantity(ctx: Ctx) -> None:
    session = await _session(ctx)
    try:
        product_id = ctx.arg_int(1)
        raw = ctx.fields.get(views.QUANTITY_INPUT, "").strip()
        if not _WHOLE.match(raw):
            raise HandlerError(core.ERROR_INVALID_INPUT)
        await service.set_quantity(ctx.app, session, product_id, int(raw))
    except HandlerError as exc:
        await _refuse_and_reset(ctx, session.id, exc)
        return
    await _render(ctx, session.id)


async def on_page(ctx: Ctx) -> None:
    session = await _session(ctx)
    await service.set_page(ctx.app, session, ctx.arg_int(1))
    await _render(ctx, session.id)


async def on_go(ctx: Ctx) -> None:
    session = await _session(ctx)
    target = ctx.args[1] if len(ctx.args) > 1 else ""
    if target not in m.STEPS:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.go(ctx.app, session, target)
    await _render(ctx, session.id)


async def on_fulfillment(ctx: Ctx) -> None:
    session = await _session(ctx)
    if len(ctx.values) != 1:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.choose_fulfillment(ctx.app, session, ctx.values[0])
    await _render(ctx, session.id)


async def on_extra(ctx: Ctx) -> None:
    session = await _session(ctx)
    if len(ctx.args) < 2:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.toggle_extra(ctx.app, session, ctx.args[1])
    await _render(ctx, session.id)


async def on_address_group(ctx: Ctx) -> None:
    session = await _session(ctx)
    if len(ctx.values) != 1:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.choose_address_group(ctx.app, session, ctx.values[0])
    await _render(ctx, session.id)


async def on_address_country(ctx: Ctx) -> None:
    session = await _session(ctx)
    if len(ctx.values) != 1:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.choose_address_country(ctx.app, session, ctx.values[0])
    await _render(ctx, session.id)


async def on_address_open(ctx: Ctx) -> None:
    session = await _session(ctx)
    country = session.details.get(flow.ADDRESS_COUNTRY)
    if not ctx.app.settings.checkout.ask_address or not country or not ctx.app.settings.shipping.ships_to(country):
        raise HandlerError(k.ERROR_COUNTRY_FIRST)
    await ctx.send_modal(views.address_modal(ctx.app.ui, ctx.app.settings, session))


async def on_address_form(ctx: Ctx) -> None:
    session = await _session(ctx)
    saved = await service.save_address(ctx.app, session, ctx.fields)
    await _render(ctx, session.id)
    if saved.excluded_area is not None:
        country = saved.session.details.get(flow.ADDRESS_COUNTRY, "")
        await ctx.fail(
            k.ERROR_AREA_EXCLUDED,
            area=area_name(country, saved.excluded_area),
            country=ctx.app.settings.shipping.country_name(country),
        )


async def on_details(ctx: Ctx) -> None:
    session = await _session(ctx)
    try:
        modal = views.details_modal(ctx.app.ui, ctx.app.settings, session, ctx.arg_int(1))
    except IndexError:
        raise HandlerError(core.ERROR_INVALID_INPUT) from None
    await ctx.send_modal(modal)


async def on_details_form(ctx: Ctx) -> None:
    session = await _session(ctx)
    await service.save_details(ctx.app, session, ctx.arg_int(1), ctx.fields)
    await _render(ctx, session.id)


async def on_credits(ctx: Ctx) -> None:
    session = await _session(ctx)
    await service.set_apply_credits(ctx.app, session, ctx.arg_int(1) == 1)
    await _render(ctx, session.id)


async def on_discount_open(ctx: Ctx) -> None:
    """Add discount code: the private code form (refused while the limit on guessing codes is reached)."""
    session = await _session(ctx)
    if not ctx.app.settings.checkout.discount_codes:
        raise HandlerError(core.ERROR_UNAVAILABLE)
    if session.step != m.REVIEW:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    if discount_codes.tries_wait(ctx.app, session.user_id) > 0:
        raise HandlerError(k.DISCOUNT_TOO_MANY_TRIES)
    await ctx.send_modal(views.discount_modal(ctx.app.ui, session.id))


async def on_discount_form(ctx: Ctx) -> None:
    """The code form was sent: apply the code (the review shows it) or answer privately why it cannot be used."""
    session = await _session(ctx)
    await discount_codes.apply_typed(ctx.app, session, ctx.fields.get(views.DISCOUNT_INPUT, ""))
    await _render(ctx, session.id)


async def on_discount_use(ctx: Ctx) -> None:
    """Use coupon: apply a coupon the customer claimed."""
    session = await _session(ctx)
    await discount_codes.apply_coupon(ctx.app, session, ctx.arg_int(1))
    await _render(ctx, session.id)


async def on_discount_remove(ctx: Ctx) -> None:
    session = await _session(ctx)
    await discount_codes.remove(ctx.app, session)
    await _render(ctx, session.id)


async def on_coupon_claim(ctx: Ctx) -> None:
    """A claim button (``sb:dc:claim:<code_id>``, e.g. under an announcement): claim the coupon, answer privately."""
    member = ctx.member
    if member is None or member.bot:
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_reply(ephemeral=True)
    await ctx.respond(await discount_codes.claim(ctx.app, member.id, ctx.arg_int(0)), ephemeral=True)


def _shown_amount(ctx: Ctx, index: int) -> int | None:
    """An amount the clicked control was rendered with; None when the control predates it (or is malformed)."""
    raw = ctx.args[index] if len(ctx.args) > index else ""
    return int(raw) if _AMOUNT.match(raw) else None


async def _submission_session(ctx: Ctx) -> m.Session | None:
    """The clicking member's session for a submit button/form. A session that was already submitted shows its order
    again (a repeated click creates nothing) and returns None."""
    session = await service.get_session(ctx.app, ctx.arg_int(0))
    if session is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if session.user_id != ctx.user_id:
        raise HandlerError(core.ERROR_PERMISSION)
    if session.status == m.SUBMITTED and session.order_id is not None:
        await _show_order(ctx, session.order_id)
        return None
    if not session.active:
        raise HandlerError(k.ERROR_SESSION_CLOSED)
    return session


async def _show_price_changed(ctx: Ctx, session_id: int, total_minor: int) -> None:
    notice = views.price_changed_notice(ctx.app.ui, total_minor)
    await ctx.update(await service.workflow_spec(ctx.app, session_id, notice=notice))


async def on_submit(ctx: Ctx) -> None:
    app = ctx.app
    session = await _submission_session(ctx)
    if session is None:
        return
    expected = ctx.arg_int(1)
    # The gross amount the review showed; a button rendered before it was recorded counts as a price change.
    expected_gross = _shown_amount(ctx, 2)
    if flow.submission_inputs(app.settings.checkout):
        # A submission form is configured: it opens first (unless the totals changed since the review was shown).
        data = await service.workflow_data(app, session.id)
        if data is None:
            raise HandlerError(core.ERROR_NOT_FOUND)
        if data.session.step != m.REVIEW:
            raise HandlerError(core.ERROR_INVALID_INPUT)
        if data.totals.total_minor != expected or data.totals.gross_minor != expected_gross:
            await _show_price_changed(ctx, session.id, data.totals.total_minor)
            return
        await ctx.send_modal(views.submission_modal(app.ui, app.settings, data.session, expected, expected_gross))
        return
    await _submit(ctx, session, expected, expected_gross)


async def on_submission_form(ctx: Ctx) -> None:
    """The submission form was sent: store the answers (checked server-side), then submit with the totals the
    customer was shown. A changed total shows the notice instead (the answers are kept and filled in again)."""
    session = await _submission_session(ctx)
    if session is None:
        return
    expected = ctx.arg_int(1)
    expected_gross = _shown_amount(ctx, 2)
    session = await service.save_submission(ctx.app, session, ctx.fields)
    await _submit(ctx, session, expected, expected_gross)


async def _submit(ctx: Ctx, session: m.Session, expected: int, expected_gross: int | None) -> None:
    app = ctx.app
    await ctx.defer_update()
    try:
        order = await service.submit(app, session, expected, expected_gross)
    except repo.PriceChanged as exc:
        await _show_price_changed(ctx, session.id, exc.totals.total_minor)
        return
    except repo.CheckoutError as exc:
        raise service.customer_error(app, exc) from None
    await _show_order(ctx, order.id)


async def on_cancel(ctx: Ctx) -> None:
    session = await _session(ctx)
    await ctx.update(views.cancel_confirm(ctx.app.ui, session.id))


async def on_cancel_no(ctx: Ctx) -> None:
    session = await _session(ctx)
    await _render(ctx, session.id)


async def on_cancel_yes(ctx: Ctx) -> None:
    session = await _session(ctx)
    # Built first: nothing changes if text is missing.
    spec = views.session_ended(
        ctx.app.ui, m.SESSION_CANCELLED, reference=await service.ticket_reference(ctx.app, session)
    )
    ended = await service.cancel_session(ctx.app, session)
    if ended is None:
        raise HandlerError(k.ERROR_SESSION_CLOSED)
    try:
        await ctx.update(spec)
    finally:
        # The order channel is closed even when answering the click failed (e.g. the interaction expired).
        await service.after_session_cancelled(ctx.app, ended, actor_id=ctx.user_id)


# -- customer: submitted order ------------------------------------------------------------------------------------


async def on_order_cancel(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    if order.status != m.PENDING:
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)
    await ctx.update(views.order_cancel_confirm(ctx.app.ui, order))


async def on_order_cancel_no(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    await _show_order(ctx, order.id)


async def on_order_cancel_yes(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    if order.status != m.PENDING:
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)
    await ctx.defer_update()
    # Only an unpaid order: if staff confirmed the payment meanwhile, the conditional update changes nothing.
    outcome = await service.change_status(
        ctx.app, order.id, m.CANCELLED, actor_id=ctx.user_id, allowed_from=(m.PENDING,)
    )
    await _show_order(ctx, order.id)
    if not outcome.applied:
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)


async def on_payment_form(ctx: Ctx) -> None:
    """Submit gift card: the private form of an order that waits for payment."""
    order = await _customer_order(ctx)
    await ctx.send_modal(await gift_cards.form(ctx.app, order))


async def on_payment_method(ctx: Ctx) -> None:
    """A payment-method button (order card or pop-up): a method that is down opens the private pop-up with the
    methods that work right now; an active one does what its kind says (``payment_methods``)."""
    app = ctx.app
    order = await _customer_order(ctx)
    method = app.settings.payments.method(ctx.args[1]) if len(ctx.args) > 1 else None
    if method is None:
        raise HandlerError(payment_methods.ERROR_UNAVAILABLE)
    if order.status != m.PENDING:
        raise HandlerError(payment_methods.ERROR_STATE)
    if not method.active:
        spec = payment_methods.down_message(
            app.ui, app.settings, order.id, method, payment_form_id=views.payment_form_id(order.id)
        )
        await ctx.respond(spec, ephemeral=True)
        return
    if method.kind == "gift_card":
        await ctx.send_modal(await gift_cards.form(app, order))
        return
    if method.kind == "staff":
        await ctx.defer_reply(ephemeral=True)
        first = await service.request_payment_method(app, order, method)
        key = payment_methods.STAFF_REPLY if first else payment_methods.STAFF_ALREADY
        await ctx.respond(payment_methods.reply(app.ui, key, method), ephemeral=True)
        return
    # A payment page (kind "link") is a link button, which sends nothing: an older button of the method.
    await ctx.respond(payment_methods.link_message(app.ui, method), ephemeral=True)


async def on_payment_submit(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    await ctx.defer_reply(ephemeral=True)
    reply = await gift_cards.submit(
        ctx.app,
        order,
        code=ctx.fields.get(gift_card.CODE_INPUT, ""),
        pin=ctx.fields.get(gift_card.PIN_INPUT),
        amount_text=ctx.fields.get(gift_card.AMOUNT_INPUT, ""),
    )
    await ctx.respond(reply, ephemeral=True)


# -- staff ------------------------------------------------------------------------------------------------------------


async def _staff_reply(ctx: Ctx, reply: staff.OpReply) -> None:
    await ctx.respond(staff.reply_spec(ctx.app, reply), ephemeral=True)


async def _staff_order(ctx: Ctx) -> m.Order | None:
    order = await service.get_order(ctx.app, ctx.arg_int(0))
    if order is None:
        await _staff_reply(ctx, staff.OpReply(ctx.app.op("checkout.op.order_missing"), "warning"))
    return order


def _require_staff(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):  # also enforced by the route's access level
        raise HandlerError(core.ERROR_PERMISSION)


async def on_staff_action(ctx: Ctx) -> None:
    _require_staff(ctx)
    target = views.STAFF_ACTION_TARGETS.get(ctx.route.action)
    if target is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if target == m.REJECTED:
        await _open_reject_form(ctx)
        return
    if target == m.SHIPPED:
        await _open_ship_form(ctx)
        return
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    if target == m.PAID and not _may_confirm_payment(ctx, order):
        await _staff_reply(ctx, staff.OpReply(ctx.app.op("checkout.gift.no_permission"), "warning"))
        return
    rendered = ctx.args[1] if len(ctx.args) > 1 else ""
    if target == m.PAID and gift_cards.awaits_decision(order) and rendered == order.status:
        # Payment confirmed: every waiting card was redeemed; the order is paid only if they cover the total.
        await _staff_reply(
            ctx, await gift_cards.decide(ctx.app, order, actor_id=ctx.user_id, redeemed=True, reason=None)
        )
        return
    # ``rendered``: the status the button was rendered for; "" (never a status) for a button rendered before buttons
    # carried it, so such a stale control refreshes the controls instead of acting.
    reply = await staff.change_status(ctx.app, order, target, actor_id=ctx.user_id, rendered_status=rendered)
    await _staff_reply(ctx, reply)


def _may_confirm_payment(ctx: Ctx, order: m.Order) -> bool:
    """Gift card payments are confirmed only by the payment staff (they redeem the codes)."""
    if order.payment_provider != gift_card.PROVIDER_ID:
        return True
    return gift_cards.can_handle(ctx.app, ctx.member, ctx.user_id)


async def _payment_staff_order(ctx: Ctx) -> m.Order | None:
    """The order of a gift card control, for the payment staff only (others get a private refusal)."""
    _require_staff(ctx)
    if not gift_cards.can_handle(ctx.app, ctx.member, ctx.user_id):
        await _staff_reply(ctx, staff.OpReply(ctx.app.op("checkout.gift.no_permission"), "warning"))
        return None
    return await _staff_order(ctx)


async def on_gift_card_reveal(ctx: Ctx) -> None:
    await ctx.defer_reply(ephemeral=True)
    order = await _payment_staff_order(ctx)
    if order is None:
        return
    result = await gift_cards.reveal(ctx.app, order, actor_id=ctx.user_id)
    if isinstance(result, staff.OpReply):
        await _staff_reply(ctx, result)
    else:
        await ctx.respond(result, ephemeral=True)


def _card_arg(ctx: Ctx) -> int | None:
    """The card a control is for (None: every waiting card of the order)."""
    card_id = ctx.arg_int(1) if len(ctx.args) > 1 else 0
    return card_id or None


async def on_gift_card_problem(ctx: Ctx) -> None:
    """Card problem: ask for the reason the customer is shown (the order must still wait for a decision)."""
    order = await _payment_staff_order(ctx)
    if order is None:
        return
    if order.status != m.PAYMENT_SUBMITTED:
        await _staff_reply(
            ctx, staff.OpReply(ctx.app.op("checkout.gift.not_waiting", number=order.number, status=order.status))
        )
        return
    await ctx.send_modal(staff_views.gift_card_problem_modal(ctx.app.op, order, _card_arg(ctx) or 0))


async def on_gift_card_problem_form(ctx: Ctx) -> None:
    await ctx.defer_reply(ephemeral=True)
    order = await _payment_staff_order(ctx)
    if order is None:
        return
    reply = await gift_cards.decide(
        ctx.app,
        order,
        actor_id=ctx.user_id,
        redeemed=False,
        reason=_form_text(ctx, staff_views.REASON_INPUT),
        card_id=_card_arg(ctx),
    )
    await _staff_reply(ctx, reply)


async def on_gift_card_redeemed(ctx: Ctx) -> None:
    await ctx.defer_reply(ephemeral=True)
    order = await _payment_staff_order(ctx)
    if order is None:
        return
    reply = await gift_cards.decide(
        ctx.app, order, actor_id=ctx.user_id, redeemed=True, reason=None, card_id=_card_arg(ctx)
    )
    await _staff_reply(ctx, reply)


def _mode(ctx: Ctx) -> str:
    mode = ctx.args[1] if len(ctx.args) > 1 else ""
    if mode not in (views.MODE_MONEY, views.MODE_CREDIT):
        raise HandlerError(core.ERROR_NOT_FOUND)
    return mode


async def on_refund(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(core.ERROR_PERMISSION)
    app = ctx.app
    mode = _mode(ctx)
    order = await _staff_order(ctx)
    if order is None:
        return
    if not order.can_refund:
        await _staff_reply(
            ctx,
            staff.OpReply(
                app.op("checkout.op.refund_not_allowed", number=order.number, status=order.status), "warning"
            ),
        )
        return
    if mode == views.MODE_CREDIT and not service.credit_refunds_enabled(app):
        await _staff_reply(ctx, staff.OpReply(app.op("checkout.op.credits_unavailable"), "warning"))
        return
    if mode == views.MODE_CREDIT and order.currency != app.settings.checkout.currency:
        await _staff_reply(ctx, staff.OpReply(staff.currency_refused(app, order), "warning"))
        return
    modal = views.refund_modal(
        app.op,
        order,
        mode=mode,
        max_text=staff.plain_amount(app, order.refundable_minor, order.currency),
        example=staff.example_amount(app, order.currency),
    )
    await ctx.send_modal(modal)


async def on_refund_form(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(core.ERROR_PERMISSION)
    mode = _mode(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    reason = ctx.fields.get(views.REFUND_REASON_INPUT, "").strip() or None
    if reason is not None and len(reason) > views.REFUND_REASON_MAX:
        await _staff_reply(
            ctx, staff.OpReply(ctx.app.op("checkout.op.reason_too_long", max=views.REFUND_REASON_MAX), "warning")
        )
        return
    reply = await staff.refund(
        ctx.app,
        order,
        amount_text=ctx.fields.get(views.REFUND_AMOUNT_INPUT, ""),
        to_credit=mode == views.MODE_CREDIT,
        reason=reason,
        actor_id=ctx.user_id,
    )
    await _staff_reply(ctx, reply)


async def on_credit_return(ctx: Ctx) -> None:
    """Staff: confirm giving back the store credit applied to a paid order (opens a form with an optional reason)."""
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(core.ERROR_PERMISSION)
    app = ctx.app
    order = await _staff_order(ctx)
    if order is None:
        return
    if not order.can_return_credit:
        text = app.op("checkout.op.credit_return_not_allowed", number=order.number, status=order.status)
        await _staff_reply(ctx, staff.OpReply(text, "warning"))
        return
    shown = staff.amount(app, order.credit_returnable_minor, order.currency)
    await ctx.send_modal(views.credit_return_modal(app.op, order, amount_text=shown))


async def on_credit_return_form(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    reason = ctx.fields.get(views.REFUND_REASON_INPUT, "").strip() or None
    if reason is not None and len(reason) > views.REFUND_REASON_MAX:
        await _staff_reply(
            ctx, staff.OpReply(ctx.app.op("checkout.op.reason_too_long", max=views.REFUND_REASON_MAX), "warning")
        )
        return
    await _staff_reply(ctx, await staff.return_credit(ctx.app, order, reason=reason, actor_id=ctx.user_id))


# -- staff: the live order record's management buttons ------------------------------------------------------------


def _form_text(ctx: Ctx, input_id: str) -> str:
    return ctx.fields.get(input_id, "").strip()


async def _open_reject_form(ctx: Ctx) -> None:
    """The reject button: open the reason form while the order is still in the status the button was rendered for."""
    order = await _staff_order(ctx)
    if order is None:
        return
    rendered = ctx.args[1] if len(ctx.args) > 1 else ""
    if rendered != order.status or order.status not in m.TRANSITIONS[m.REJECTED]:
        await ctx.defer_reply(ephemeral=True)
        await _staff_reply(
            ctx, await staff.change_status(ctx.app, order, m.REJECTED, actor_id=ctx.user_id, rendered_status=rendered)
        )
        return
    await ctx.send_modal(staff_views.reject_modal(ctx.app.op, order, order.status))


async def _open_ship_form(ctx: Ctx) -> None:
    """Mark shipped: open the shipment form while the order is still in the status the button was rendered for."""
    order = await _staff_order(ctx)
    if order is None:
        return
    rendered = ctx.args[1] if len(ctx.args) > 1 else ""
    refused = await staff.ship_refused(ctx.app, order, rendered)
    if refused is not None:
        await ctx.defer_reply(ephemeral=True)
        await _staff_reply(ctx, refused)
        return
    await ctx.send_modal(staff_views.ship_modal(ctx.app.op, order, order.status))


async def on_ship_form(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    reply = await staff.ship(
        ctx.app,
        order,
        actor_id=ctx.user_id,
        carrier=ctx.fields.get(staff_views.CARRIER_INPUT, ""),
        tracking_number=ctx.fields.get(staff_views.TRACKING_NUMBER_INPUT, ""),
        tracking_url=ctx.fields.get(staff_views.TRACKING_URL_INPUT),
        rendered_status=ctx.args[1] if len(ctx.args) > 1 else "",
    )
    await _staff_reply(ctx, reply)


async def on_reject_form(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    rendered = ctx.args[1] if len(ctx.args) > 1 else ""
    reply = await staff.reject(
        ctx.app, order, actor_id=ctx.user_id, reason=_form_text(ctx, staff_views.REASON_INPUT), rendered_status=rendered
    )
    await _staff_reply(ctx, reply)


async def on_claim(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is not None:
        await _staff_reply(ctx, await staff.claim(ctx.app, order, actor_id=ctx.user_id))


async def on_unclaim(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is not None:
        reply = await staff.unclaim(ctx.app, order, actor_id=ctx.user_id, override=ctx.is_admin())
        await _staff_reply(ctx, reply)


async def on_note(ctx: Ctx) -> None:
    _require_staff(ctx)
    order = await _staff_order(ctx)
    if order is not None:
        await ctx.send_modal(staff_views.note_modal(ctx.app.op, order))


async def on_note_form(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is not None:
        text = _form_text(ctx, staff_views.NOTE_INPUT)
        await _staff_reply(ctx, await staff.note(ctx.app, order, actor_id=ctx.user_id, text=text))


async def on_correction(ctx: Ctx) -> None:
    _require_staff(ctx)
    order = await _staff_order(ctx)
    if order is None:
        return
    if order.status not in h.CORRECTION_STATUSES:
        text = ctx.app.op("checkout.op.correction_state", number=order.number, status=order.status)
        await _staff_reply(ctx, staff.OpReply(text, "warning"))
        return
    await ctx.send_modal(staff_views.correction_modal(ctx.app.op, order))


async def on_correction_form(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is not None:
        text = _form_text(ctx, staff_views.MESSAGE_INPUT)
        await _staff_reply(ctx, await staff.request_correction(ctx.app, order, actor_id=ctx.user_id, text=text))


async def on_tracking(ctx: Ctx) -> None:
    _require_staff(ctx)
    app = ctx.app
    order = await _staff_order(ctx)
    if order is None:
        return
    if not staff_keys.tracking_enabled(app.content):
        await _staff_reply(ctx, staff.OpReply(app.op("checkout.op.tracking_unavailable"), "warning"))
        return
    if order.status not in h.TRACKING_STATUSES:
        text = app.op("checkout.op.tracking_state", number=order.number, status=order.status)
        await _staff_reply(ctx, staff.OpReply(text, "warning"))
        return
    current = await app.db.run(h.tracking_text, order.id)
    await ctx.send_modal(staff_views.tracking_modal(app.op, order, current))


async def on_tracking_form(ctx: Ctx) -> None:
    _require_staff(ctx)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is not None:
        text = _form_text(ctx, staff_views.TRACKING_INPUT)
        await _staff_reply(ctx, await staff.set_tracking(ctx.app, order, actor_id=ctx.user_id, text=text))


async def _show_record(ctx: Ctx, order_id: int) -> None:
    """The full order record (operator text), privately to the staff member who asked."""
    await ctx.defer_reply(ephemeral=True)
    order = await service.get_order(ctx.app, order_id)
    if order is None:
        await _staff_reply(ctx, staff.OpReply(ctx.app.op("checkout.op.order_missing"), "warning"))
        return
    for spec in await staff.lookup_messages(ctx.app, order):
        await ctx.respond(spec, ephemeral=True)


async def on_record_details(ctx: Ctx) -> None:
    _require_staff(ctx)
    await _show_record(ctx, ctx.arg_int(0))


async def on_list_view(ctx: Ctx) -> None:
    _require_staff(ctx)
    if len(ctx.values) != 1 or not ctx.values[0].isdigit():
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await _show_record(ctx, int(ctx.values[0]))


async def on_list_filter(ctx: Ctx) -> None:
    _require_staff(ctx)
    if len(ctx.values) != 1:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    status = "" if ctx.values[0] == "all" else ctx.values[0]
    if status and status not in m.ORDER_STATUSES:
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await ctx.update(await staff.list_view(ctx.app, 0, status, ctx.arg_int(0)))


async def on_list_page(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):
        raise HandlerError(core.ERROR_PERMISSION)
    page = ctx.arg_int(0)
    status = ctx.args[1] if len(ctx.args) > 1 else "all"
    status = "" if status == "all" else status
    if status and status not in m.ORDER_STATUSES:
        raise HandlerError(core.ERROR_NOT_FOUND)
    user_id = ctx.arg_int(2) if len(ctx.args) > 2 else 0
    spec: MessageSpec = await staff.list_view(ctx.app, page, status, user_id)
    await ctx.update(spec)


def register(app: App) -> None:
    router = app.router
    feature = k.FEATURE
    route, staff_route = k.ROUTE, k.STAFF_ROUTE
    customer = {
        "start": on_start,
        "buy": on_buy,
        "pick": on_pick,
        "edit": on_edit,
        "page": on_page,
        "go": on_go,
        "ful": on_fulfillment,
        "extra": on_extra,
        "agroup": on_address_group,
        "acountry": on_address_country,
        "aopen": on_address_open,
        "details": on_details,
        "credits": on_credits,
        "dcode": on_discount_open,
        "dcuse": on_discount_use,
        "dcremove": on_discount_remove,
        "submit": on_submit,
        "cancel": on_cancel,
        "cancel_no": on_cancel_no,
        "cancel_yes": on_cancel_yes,
        "ocancel": on_order_cancel,
        "ocancel_no": on_order_cancel_no,
        "ocancel_yes": on_order_cancel_yes,
        "payform": on_payment_form,
        payment_methods.ACTION: on_payment_method,
    }
    for action, handler in customer.items():
        router.component(route, action, feature=feature)(handler)
    router.modal(route, "qty", feature=feature)(on_quantity)
    router.modal(route, "dform", feature=feature)(on_details_form)
    router.modal(route, "aform", feature=feature)(on_address_form)
    router.modal(route, "sform", feature=feature)(on_submission_form)
    router.modal(route, "paysubmit", feature=feature)(on_payment_submit)
    router.modal(route, "dcform", feature=feature)(on_discount_form)
    router.component(discount_views.CLAIM_ROUTE, discount_views.CLAIM_ACTION, feature=feature)(on_coupon_claim)
    for action in views.STAFF_ACTION_TARGETS:
        router.component(staff_route, action, feature=feature, access="staff", staff_roles=staff_role_keys)(
            on_staff_action
        )
    router.component(staff_route, "refund", feature=feature, access="staff", staff_roles=staff_role_keys)(on_refund)
    router.modal(staff_route, "refundform", feature=feature, access="staff", staff_roles=staff_role_keys)(
        on_refund_form
    )
    router.component(staff_route, "creturn", feature=feature, access="staff", staff_roles=staff_role_keys)(
        on_credit_return
    )
    router.modal(staff_route, "creturnform", feature=feature, access="staff", staff_roles=staff_role_keys)(
        on_credit_return_form
    )
    router.component(staff_route, "lpage", feature=feature, access="staff", staff_roles=staff_role_keys)(on_list_page)
    staff_components = {
        "claim": on_claim,
        "unclaim": on_unclaim,
        "note": on_note,
        "correct": on_correction,
        "track": on_tracking,
        "details": on_record_details,
        "lfilter": on_list_filter,
        "lview": on_list_view,
        "gcreveal": on_gift_card_reveal,
        "gcproblem": on_gift_card_problem,
        "gcok": on_gift_card_redeemed,
    }
    for action, handler in staff_components.items():
        router.component(staff_route, action, feature=feature, access="staff", staff_roles=staff_role_keys)(handler)
    staff_forms = {
        "noteform": on_note_form,
        "correctform": on_correction_form,
        "trackform": on_tracking_form,
        "rejectform": on_reject_form,
        "shipform": on_ship_form,
        "gcproblemform": on_gift_card_problem_form,
    }
    for action, handler in staff_forms.items():
        router.modal(staff_route, action, feature=feature, access="staff", staff_roles=staff_role_keys)(handler)
