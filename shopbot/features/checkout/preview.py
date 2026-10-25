"""Preview: every checkout UI state, built by the runtime view functions from synthetic sample data.

Sample products, answers, members and orders are obviously synthetic (``[sample product N]``, ``[sample answer N]``)
and exist only in the preview. States that depend on configuration (fulfillment options, forms, fees, credits, the
minimum order total) use the configured options. A state whose text is not configured yet is skipped (the content
store records the missing keys for the checker).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.config.settings import FulfillmentOption, Settings
from shopbot.features.catalog.repo import NAME_MAX, Product
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import flow, panel, staff, views
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import get_provider
from shopbot.features.checkout.totals import compute_totals
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_USER_ID = 100_000_000_000_000_001
SAMPLE_CHANNEL_ID = 100_000_000_000_000_003
SAMPLE_SESSION_ID = 1
SAMPLE_ORDER_ID = 1
SAMPLE_ORDER_NUMBER = 1001
SAMPLE_TIME = 1_700_000_000
SAMPLE_CREDITS_MINOR = 1_500


def _product(
    index: int, *, price_minor: int, stock: int | None, reserved: int = 0, limit: int | None = None
) -> Product:
    return Product(
        id=index,
        guild_id=0,
        name=f"[sample product {index}]",
        description="",
        price_minor=price_minor,
        image_url=None,
        stock_on_hand=stock,
        reserved=reserved,
        max_per_order=limit,
        active=True,
        archived=False,
        sort_order=index,
        version=1,
        created_at=0,
        updated_at=0,
    )


def _products(count: int) -> tuple[Product, ...]:
    return tuple(
        _product(
            i,
            price_minor=250 * i + 99,
            stock=None if i % 3 == 0 else 10 + i,
            reserved=i % 2,
            limit=5 if i == 1 else None,
        )
        for i in range(1, count + 1)
    )


def _session(
    step: str, *, option: str | None = None, details: dict[str, str] | None = None, credits: bool = False
) -> m.Session:
    return m.Session(
        id=SAMPLE_SESSION_ID,
        guild_id=0,
        user_id=SAMPLE_USER_ID,
        ticket_id=1,
        status=m.ACTIVE,
        step=step,
        page=0,
        fulfillment_option=option,
        details=dict(details or {}),
        apply_credits=credits,
        message_id=None,
        order_id=None,
        version=1,
        created_at=SAMPLE_TIME,
        updated_at=SAMPLE_TIME,
        expires_at=None,
    )


def _data(
    settings: Settings,
    session: m.Session,
    lines: tuple[m.CartLine, ...],
    *,
    page: m.ProductPage | None = None,
    credits_enabled: bool = False,
) -> m.WorkflowData:
    c = settings.checkout
    option = c.fulfillment(session.fulfillment_option)
    available = SAMPLE_CREDITS_MINOR if credits_enabled else 0
    totals = compute_totals(
        [(line.product.price_minor, line.quantity) for line in lines],
        fulfillment_fee_minor=option.fee_minor if option is not None else 0,
        fees=c.fees,
        credits_available_minor=available,
        apply_credits=session.apply_credits and credits_enabled,
    )
    return m.WorkflowData(
        session=session,
        lines=lines,
        browse=page,
        totals=totals,
        credits_enabled=credits_enabled,
        credits_available_minor=min(available, totals.gross_minor),
    )


def _sample_option(settings: Settings) -> FulfillmentOption | None:
    """The option the preview shows as chosen: one with a form (and a fee) when there is one."""
    options = sorted(settings.checkout.fulfillment_options, key=lambda o: (not o.form, o.fee_minor <= 0))
    return options[0] if options else None


def _largest_cart(settings: Settings) -> tuple[m.CartLine, ...]:
    """The largest cart a customer can build: ``max_cart_lines`` lines whose product names have the maximum length
    (made of characters that markdown escaping doubles) and large prices - the worst case for message limits."""
    lines: list[m.CartLine] = []
    for index in range(1, settings.checkout.max_cart_lines + 1):
        product = replace(
            _product(index, price_minor=99_999_999 + index, stock=None),
            name=f"[sample product {index}]".ljust(NAME_MAX, "_"),
        )
        lines.append(m.CartLine(product, 99))
    return tuple(lines)


def _order(
    settings: Settings,
    status: str,
    lines: tuple[m.CartLine, ...],
    *,
    refunded: int | None = None,
    credits: int = 0,
) -> m.OrderView:
    c = settings.checkout
    option = _sample_option(settings)
    totals = compute_totals(
        [(line.product.price_minor, line.quantity) for line in lines],
        fulfillment_fee_minor=option.fee_minor if option is not None else 0,
        fees=c.fees,
        credits_available_minor=credits,
        apply_credits=credits > 0,
    )
    details = {}
    for index, item in enumerate(flow.form_inputs(c, option.id if option is not None else None), start=1):
        details[item.answer_key] = f"[sample answer {index}]"
    paid = status not in (m.PENDING, m.REJECTED, m.EXPIRED)
    order = m.Order(
        id=SAMPLE_ORDER_ID,
        guild_id=0,
        number=SAMPLE_ORDER_NUMBER,
        user_id=SAMPLE_USER_ID,
        session_id=SAMPLE_SESSION_ID,
        ticket_id=1,
        status=status,
        currency=c.currency or "XXX",
        subtotal_minor=totals.subtotal_minor,
        fulfillment_fee_minor=totals.fulfillment_fee_minor,
        fees_minor=totals.fees_minor,
        credits_applied_minor=totals.credits_applied_minor,
        total_minor=totals.total_minor,
        refunded_minor=(totals.total_minor if status == m.REFUNDED else 0) if refunded is None else refunded,
        fulfillment_option=option.id if option is not None else None,
        details=details,
        payment_provider=settings.payments.provider,
        customer_message_id=None,
        staff_channel_id=None,
        staff_message_id=None,
        version=1,
        created_at=SAMPLE_TIME,
        updated_at=SAMPLE_TIME,
        paid_at=SAMPLE_TIME if paid else None,
        fulfilled_at=SAMPLE_TIME if status in (m.FULFILLED, m.REFUNDED) else None,
        closed_at=None,
    )
    items = tuple(
        m.OrderItem(
            product_id=line.product.id,
            name=line.product.name,
            unit_price_minor=line.product.price_minor,
            quantity=line.quantity,
            line_total_minor=line.line_total_minor,
        )
        for line in lines
    )
    fees = tuple(m.OrderFee(fee_id=f.fee_id, amount_minor=f.amount_minor) for f in totals.fees)
    return m.OrderView(order=order, items=items, fees=fees, channel_id=SAMPLE_CHANNEL_ID)


def _same(spec: MessageSpec) -> MessageSpec:
    return spec


def preview(app: App) -> list[PreviewState]:
    ui, settings, op = app.ui, app.settings, app.op
    c = settings.checkout
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec | None]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        if spec is not None:
            states.append(PreviewState(feature=k.FEATURE, name=name, note=note, spec=spec))

    products = _products(max(2, c.products_per_page + 2))
    first_page = m.ProductPage(products=products[: c.products_per_page], page=0, pages=2, total=len(products))
    second_page = m.ProductPage(products=products[c.products_per_page :], page=1, pages=2, total=len(products))
    single_page = m.ProductPage(products=products[:2], page=0, pages=1, total=2)
    empty_page = m.ProductPage(products=(), page=0, pages=1, total=0)
    lines = (m.CartLine(products[0], 2), m.CartLine(products[1], 1))
    many_lines = tuple(
        m.CartLine(p, (i % 5) + 1) for i, p in enumerate(products[: min(c.max_cart_lines, len(products))])
    )
    credits = k.credits_possible(settings)
    option = _sample_option(settings)
    option_id = option.id if option is not None else None

    # -- shop panel and private replies --------------------------------------------------------------------------
    add(
        "shop_panel",
        "Shop panel in checkout.panel_channel (start, browse and balance buttons).",
        lambda: panel.panel_spec(app),
    )
    add(
        "reply_started",
        "Private reply after the order channel was created.",
        lambda: views.started_reply(ui, SAMPLE_CHANNEL_ID),
    )
    add(
        "reply_session_exists",
        "Private reply when the customer already has an unfinished order channel.",
        lambda: views.session_exists_reply(ui, SAMPLE_CHANNEL_ID),
    )
    add(
        "reply_limit_reached",
        "Private reply when the customer has the maximum number of open orders.",
        lambda: views.limit_reply(ui, c.max_open_orders_per_user),
    )
    add(
        "reply_buy_added",
        "Private reply to a catalog buy button when the customer already has an unfinished order channel: the "
        "product is now in its cart (falls back to the 'session exists' text when not configured).",
        lambda: views.buy_added_reply(ui, products[0].name, SAMPLE_CHANNEL_ID),
    )
    add(
        "reply_buy_started_sold_out",
        "Private reply to a catalog buy button that opened a new order channel, when the product sold out a moment "
        "earlier and could not be put in the cart (the configured out-of-stock error follows the link).",
        lambda: views.buy_started_reply(
            ui,
            SAMPLE_CHANNEL_ID,
            ui.error_embed(k.ERROR_OUT_OF_STOCK, name=views.escape(products[0].name), available=0),
        ),
    )

    # -- workflow --------------------------------------------------------------------------------------------------
    def workflow(
        session: m.Session, cart: tuple[m.CartLine, ...], page: m.ProductPage | None = single_page
    ) -> Callable[[], MessageSpec]:
        return lambda: views.workflow_message(
            ui, settings, _data(settings, session, cart, page=page, credits_enabled=credits)
        )

    add(
        "workflow_browse_empty_cart",
        "Workflow message: choosing products, empty cart.",
        workflow(_session(m.BROWSE), (), first_page),
    )
    add(
        "workflow_browse_items_page_2",
        "Workflow message: choosing products on page 2 of 2, with items in the cart.",
        workflow(replace(_session(m.BROWSE), page=1), lines, second_page),
    )
    add(
        "workflow_browse_empty_catalog",
        "Workflow message: no product is offered right now.",
        workflow(_session(m.BROWSE), (), empty_page),
    )
    add("workflow_cart", "Workflow message: cart step with items.", workflow(_session(m.CART), lines))
    add(
        "workflow_cart_full",
        "Workflow message: cart step with the maximum number of lines.",
        workflow(_session(m.CART), many_lines),
    )
    add("workflow_cart_empty", "Workflow message: cart step with an empty cart.", workflow(_session(m.CART), ()))
    minimum = max(c.min_order_total_minor, sum(line.line_total_minor for line in lines) + 1)
    min_settings = replace(settings, checkout=replace(c, min_order_total_minor=minimum))
    add(
        "workflow_cart_minimum_not_met",
        "Workflow message: the subtotal is below checkout.min_order_total_minor (continue disabled).",
        lambda: views.workflow_message(
            ui, min_settings, _data(min_settings, _session(m.CART), lines, credits_enabled=credits)
        ),
    )
    if option is not None:
        add(
            "workflow_fulfillment_unchosen",
            "Workflow message: choosing a fulfillment option (nothing chosen yet).",
            workflow(_session(m.FULFILLMENT), lines),
        )
        add(
            "workflow_fulfillment_chosen",
            "Workflow message: a fulfillment option is chosen.",
            workflow(_session(m.FULFILLMENT, option=option_id), lines),
        )
    inputs = flow.form_inputs(c, option_id)
    answers = {item.answer_key: f"[sample answer {index}]" for index, item in enumerate(inputs, start=1)}
    if inputs:
        add(
            "workflow_details_empty",
            "Workflow message: order details not entered yet (continue disabled).",
            workflow(_session(m.DETAILS, option=option_id), lines),
        )
        add(
            "workflow_details_answered",
            "Workflow message: order details with the customer's answers.",
            workflow(_session(m.DETAILS, option=option_id, details=answers), lines),
        )
        for part in range(len(flow.form_parts(inputs))):
            add(
                f"details_form_{part + 1}",
                f"Order details form, part {part + 1}.",
                partial(
                    views.details_modal, ui, settings, _session(m.DETAILS, option=option_id, details=answers), part
                ),
            )
    review = _session(m.REVIEW, option=option_id, details=answers)
    add(
        "workflow_review",
        "Workflow message: review step with fees (and credit that could be applied).",
        workflow(review, lines),
    )
    if credits:
        add(
            "workflow_review_credits_applied",
            "Workflow message: review step with store credit applied.",
            workflow(replace(review, apply_credits=True), lines),
        )
    add(
        "workflow_review_price_changed",
        "Workflow message: the total changed before submission (nothing was submitted).",
        lambda: views.workflow_message(
            ui,
            settings,
            _data(settings, review, lines, credits_enabled=credits),
            notice=views.price_changed_notice(ui, sum(line.line_total_minor for line in lines)),
        ),
    )
    largest = _largest_cart(settings)
    add(
        "workflow_cart_largest",
        "Workflow message: cart step with the largest possible cart (checkout.max_cart_lines lines, longest product "
        "names, large prices). A limit violation here means a full cart cannot be shown with the configured text.",
        workflow(_session(m.CART), largest),
    )
    add(
        "workflow_review_largest",
        "Workflow message: review step with the largest possible cart (see workflow_cart_largest).",
        workflow(review, largest),
    )
    add(
        "workflow_cancel_confirm",
        "Workflow message: confirm cancelling the unfinished order.",
        lambda: views.cancel_confirm(ui, SAMPLE_SESSION_ID),
    )
    add(
        "workflow_cancelled",
        "Workflow message after the customer cancelled.",
        lambda: views.session_ended(ui, m.SESSION_CANCELLED),
    )
    add(
        "workflow_expired",
        "Workflow message after the unfinished order expired.",
        lambda: views.session_ended(ui, m.SESSION_EXPIRED),
    )
    add(
        "quantity_form_new",
        "Quantity form for a product not in the cart yet.",
        lambda: views.quantity_modal(ui, SAMPLE_SESSION_ID, products[0].id, None),
    )
    add(
        "quantity_form_edit",
        "Quantity form prefilled with the quantity in the cart.",
        lambda: views.quantity_modal(ui, SAMPLE_SESSION_ID, products[0].id, 2),
    )

    # -- submitted order, status views, staff controls, notifications -----------------------------------------------
    provider = get_provider(settings.payments.provider)
    pending = _order(settings, m.PENDING, lines)
    instructions = provider.instructions(pending.order, None) if provider is not None else None
    add(
        "order_submitted",
        "Workflow message after submission: waiting for payment, with the payment instructions.",
        lambda: views.order_message(ui, settings, pending, instructions=instructions),
    )
    try:
        largest_order = views.order_messages(
            ui, settings, _order(settings, m.PENDING, largest), instructions=instructions
        )
    except MissingContent:
        largest_order = None
    if largest_order is not None:
        add(
            "order_submitted_largest",
            "Workflow message after submitting the largest possible cart: when the lines do not fit next to the "
            "configured text, they follow as separate messages (the instructions and the cancel button stay).",
            partial(_same, largest_order.main),
        )
        extras = [*largest_order.instructions, *largest_order.items]
        for index, spec in enumerate(extras, start=1):
            add(
                f"order_submitted_largest_part_{index}",
                "Separate message after the largest order's workflow message (payment instructions or order lines).",
                partial(_same, spec),
            )
    add(
        "order_cancel_confirm",
        "Customer confirms cancelling an unpaid order.",
        lambda: views.order_cancel_confirm(ui, pending.order),
    )
    for status in m.STATUS_VIEWS:
        view = _order(settings, status, lines)
        add(
            f"order_{status}",
            f"Workflow message once the order is '{status}'.",
            partial(views.order_message, ui, settings, view, instructions=None),
        )
    staff_confirms = provider.requires_staff_confirmation if provider is not None else True
    credit_refunds = settings.features.credits
    for status in m.ORDER_STATUSES:
        view = _order(settings, status, lines)
        add(
            f"staff_{status}",
            f"Staff order controls for an order that is '{status}'.",
            partial(
                views.staff_message,
                ui,
                settings,
                view,
                staff_confirms_payment=staff_confirms,
                credit_refunds=credit_refunds,
            ),
        )
    partly_refunded = _order(settings, m.PAID, lines, refunded=1)
    add(
        "staff_paid_partially_refunded",
        "Staff order controls after a partial refund.",
        lambda: views.staff_message(
            ui, settings, partly_refunded, staff_confirms_payment=staff_confirms, credit_refunds=credit_refunds
        ),
    )
    with_credit = _order(settings, m.CANCELLED, lines, credits=SAMPLE_CREDITS_MINOR)
    add(
        "staff_cancelled_credit_applied",
        "Staff order controls of a paid order that was cancelled and had store credit applied (refund buttons and "
        "the button that gives the applied credit back).",
        lambda: views.staff_message(
            ui, settings, with_credit, staff_confirms_payment=staff_confirms, credit_refunds=credit_refunds
        ),
    )
    try:
        largest_staff = views.staff_messages(
            ui,
            settings,
            _order(settings, m.PENDING, largest),
            staff_confirms_payment=staff_confirms,
            credit_refunds=credit_refunds,
        )
    except MissingContent:
        largest_staff = []
    for index, spec in enumerate(largest_staff):
        add(
            "staff_pending_largest" if index == 0 else f"staff_pending_largest_part_{index}",
            "Staff order controls of the largest possible order (the lines follow as separate messages when they do "
            "not fit next to the configured text).",
            partial(_same, spec),
        )
    try:
        details_messages = views.staff_details_messages(ui, settings, pending.order) if inputs else []
    except MissingContent:
        details_messages = []
    for index, spec in enumerate(details_messages, start=1):
        add(f"staff_details_{index}", "Customer's form answers under the staff order controls.", partial(_same, spec))
    for status in k.NOTIFIED_STATUSES:
        view = _order(settings, status, lines)
        add(
            f"notify_{status}",
            f"Customer notification: the order is '{status}'.",
            partial(views.status_notification, ui, settings, view, status),
        )
    add(
        "notify_payment_reminder",
        "Customer notification: payment reminder (with the payment instructions).",
        lambda: views.reminder_notification(ui, settings, pending, instructions),
    )
    add(
        "notify_refund",
        "Customer notification: a refund was recorded (or applied store credit was given back).",
        lambda: views.refund_notification(ui, settings, _order(settings, m.PAID, lines), 500),
    )

    # -- errors (private) -------------------------------------------------------------------------------------------
    sample = views.escape(products[0].name)
    add(
        "error_session_closed",
        "Error: the workflow was already submitted, cancelled or expired.",
        lambda: views.error_message(ui, k.ERROR_SESSION_CLOSED),
    )
    add(
        "error_out_of_stock",
        "Error: not enough units available.",
        lambda: views.error_message(ui, k.ERROR_OUT_OF_STOCK, name=sample, available=3),
    )
    add(
        "error_quantity_invalid",
        "Error: quantity above the allowed maximum.",
        lambda: views.error_message(ui, k.ERROR_QUANTITY_INVALID, max=5),
    )
    add(
        "error_cart_full",
        "Error: the cart has the maximum number of lines.",
        lambda: views.error_message(ui, k.ERROR_CART_FULL, max=c.max_cart_lines),
    )
    add(
        "error_product_unavailable",
        "Error: a product is no longer offered.",
        lambda: views.error_message(ui, k.ERROR_PRODUCT_UNAVAILABLE, name=sample),
    )
    add(
        "error_order_not_cancellable",
        "Error: the order can no longer be cancelled by the customer.",
        lambda: views.error_message(ui, k.ERROR_ORDER_NOT_CANCELLABLE),
    )
    add(
        "error_minimum_not_met",
        "Error: continuing below the minimum order total.",
        lambda: views.error_message(ui, k.CART_MINIMUM_NOT_MET, minimum=views.money(ui, minimum)),
    )

    # -- staff only (operator text) ---------------------------------------------------------------------------------
    paid = _order(settings, m.PAID, lines).order
    currency = paid.currency
    add(
        "staff_refund_form",
        "Staff only: refund form.",
        lambda: views.refund_modal(
            op,
            paid,
            mode=views.MODE_MONEY,
            max_text=staff.plain_amount(app, paid.total_minor, currency),
            example=staff.example_amount(app, currency),
        ),
    )
    add(
        "staff_refund_credit_form",
        "Staff only: refund-to-store-credit form.",
        lambda: views.refund_modal(
            op,
            paid,
            mode=views.MODE_CREDIT,
            max_text=staff.plain_amount(app, paid.total_minor, currency),
            example=staff.example_amount(app, currency),
        ),
    )
    add(
        "staff_credit_return_form",
        "Staff only: confirm giving back the store credit applied to an order.",
        lambda: views.credit_return_modal(op, paid, amount_text=staff.amount(app, SAMPLE_CREDITS_MINOR, currency)),
    )
    sample_orders = [
        replace(_order(settings, s, lines).order, id=i, number=SAMPLE_ORDER_NUMBER + i)
        for i, s in enumerate(m.ORDER_STATUSES, start=1)
    ]
    add(
        "staff_order_list",
        "Staff only (/order list): one page of sample orders with paging buttons.",
        lambda: views.order_list_page(
            ui,
            op,
            partial(staff.amount, app),
            sample_orders,
            page=0,
            pages=3,
            total=24,
            status="",
            user_id=0,
        ),
    )
    return states
