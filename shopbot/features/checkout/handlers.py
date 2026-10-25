"""Component and form handlers.

Customer routes (``co``, any member): every handler re-loads the session/order by id, checks it belongs to the
configured guild and to the clicking member (staff can see order channels but cannot act on a customer's
workflow), checks its state, then performs and persists the change and edits the single workflow message.

Staff routes (``cs``, access ``staff`` incl. ``checkout.staff_roles``): status changes through the state machine,
refund forms and the order-list pages. Replies to staff are private operator text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from shopbot.features import core_keys as core
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, staff, views
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

_WHOLE = re.compile(r"^\d{1,7}$")


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


# -- customer: start --------------------------------------------------------------------------------------------


async def on_start(ctx: Ctx) -> None:
    member = ctx.member
    if member is None or member.bot:
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.defer_reply(ephemeral=True)
    spec = await service.start(ctx.app, member)
    await ctx.respond(spec, ephemeral=True)


# -- customer: workflow -----------------------------------------------------------------------------------------


async def on_pick(ctx: Ctx) -> None:
    session = await _session(ctx)
    await _open_quantity(ctx, session, _selected_int(ctx))


async def on_edit(ctx: Ctx) -> None:
    session = await _session(ctx)
    product_id = _selected_int(ctx)
    if product_id not in await ctx.app.db.run(repo.cart_quantities, session.id):
        raise HandlerError(core.ERROR_NOT_FOUND)
    await _open_quantity(ctx, session, product_id)


async def on_quantity(ctx: Ctx) -> None:
    session = await _session(ctx)
    product_id = ctx.arg_int(1)
    raw = ctx.fields.get(views.QUANTITY_INPUT, "").strip()
    if not _WHOLE.match(raw):
        raise HandlerError(core.ERROR_INVALID_INPUT)
    await service.set_quantity(ctx.app, session, product_id, int(raw))
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


async def on_submit(ctx: Ctx) -> None:
    app = ctx.app
    session = await service.get_session(app, ctx.arg_int(0))
    expected = ctx.arg_int(1)
    if session is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    if session.user_id != ctx.user_id:
        raise HandlerError(core.ERROR_PERMISSION)
    if session.status == m.SUBMITTED and session.order_id is not None:
        # A repeated click on an already submitted order: show the order again, create nothing.
        await ctx.update(await service.customer_order_spec(app, session.order_id))
        return
    if not session.active:
        raise HandlerError(k.ERROR_SESSION_CLOSED)
    await ctx.defer_update()
    try:
        order = await service.submit(app, session, expected)
    except repo.PriceChanged as exc:
        notice = views.price_changed_notice(app.ui, exc.totals.total_minor)
        await ctx.update(await service.workflow_spec(app, session.id, notice=notice))
        return
    except repo.CheckoutError as exc:
        raise service.customer_error(app, exc) from None
    await ctx.update(await service.customer_order_spec(app, order.id))


async def on_cancel(ctx: Ctx) -> None:
    session = await _session(ctx)
    await ctx.update(views.cancel_confirm(ctx.app.ui, session.id))


async def on_cancel_no(ctx: Ctx) -> None:
    session = await _session(ctx)
    await _render(ctx, session.id)


async def on_cancel_yes(ctx: Ctx) -> None:
    session = await _session(ctx)
    spec = views.session_ended(ctx.app.ui, m.SESSION_CANCELLED)  # built first: nothing changes if text is missing
    ended = await service.cancel_session(ctx.app, session)
    if ended is None:
        raise HandlerError(k.ERROR_SESSION_CLOSED)
    await ctx.update(spec)
    await service.after_session_cancelled(ctx.app, ended, actor_id=ctx.user_id)


# -- customer: submitted order ------------------------------------------------------------------------------------


async def on_order_cancel(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    if order.status != m.PENDING:
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)
    await ctx.update(views.order_cancel_confirm(ctx.app.ui, order))


async def on_order_cancel_no(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    await ctx.update(await service.customer_order_spec(ctx.app, order.id))


async def on_order_cancel_yes(ctx: Ctx) -> None:
    order = await _customer_order(ctx)
    if order.status != m.PENDING:
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)
    await ctx.defer_update()
    outcome = await service.change_status(ctx.app, order.id, m.CANCELLED, actor_id=ctx.user_id)
    if not outcome.applied:
        await ctx.update(await service.customer_order_spec(ctx.app, order.id))
        raise HandlerError(k.ERROR_ORDER_NOT_CANCELLABLE)
    await ctx.update(await service.customer_order_spec(ctx.app, order.id))


# -- staff ------------------------------------------------------------------------------------------------------------


async def _staff_reply(ctx: Ctx, reply: staff.OpReply) -> None:
    await ctx.respond(staff.reply_spec(ctx.app, reply), ephemeral=True)


async def _staff_order(ctx: Ctx) -> m.Order | None:
    order = await service.get_order(ctx.app, ctx.arg_int(0))
    if order is None:
        await _staff_reply(ctx, staff.OpReply(ctx.app.op("checkout.op.order_missing"), "warning"))
    return order


async def on_staff_action(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):  # also enforced by the route's access level
        raise HandlerError(core.ERROR_PERMISSION)
    target = views.STAFF_ACTION_TARGETS.get(ctx.route.action)
    if target is None:
        raise HandlerError(core.ERROR_NOT_FOUND)
    await ctx.defer_reply(ephemeral=True)
    order = await _staff_order(ctx)
    if order is None:
        return
    reply = await staff.change_status(ctx.app, order, target, actor_id=ctx.user_id)
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
        "pick": on_pick,
        "edit": on_edit,
        "page": on_page,
        "go": on_go,
        "ful": on_fulfillment,
        "details": on_details,
        "credits": on_credits,
        "submit": on_submit,
        "cancel": on_cancel,
        "cancel_no": on_cancel_no,
        "cancel_yes": on_cancel_yes,
        "ocancel": on_order_cancel,
        "ocancel_no": on_order_cancel_no,
        "ocancel_yes": on_order_cancel_yes,
    }
    for action, handler in customer.items():
        router.component(route, action, feature=feature)(handler)
    router.modal(route, "qty", feature=feature)(on_quantity)
    router.modal(route, "dform", feature=feature)(on_details_form)
    for action in views.STAFF_ACTION_TARGETS:
        router.component(staff_route, action, feature=feature, access="staff", staff_roles=staff_role_keys)(
            on_staff_action
        )
    router.component(staff_route, "refund", feature=feature, access="staff", staff_roles=staff_role_keys)(on_refund)
    router.modal(staff_route, "refundform", feature=feature, access="staff", staff_roles=staff_role_keys)(
        on_refund_form
    )
    router.component(staff_route, "lpage", feature=feature, access="staff", staff_roles=staff_role_keys)(on_list_page)
