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
from shopbot.features.checkout import flow, gift_card_preview, panel, staff, staff_preview, views
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
SAMPLE_TICKET_NUMBER = 42
# The stages a sample order in each status went through (its timeline).
SAMPLE_PATHS: dict[str, tuple[str, ...]] = {
    m.PENDING: (m.PENDING,),
    m.PAYMENT_SUBMITTED: (m.PENDING, m.PAYMENT_SUBMITTED),
    m.PAID: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID),
    m.PROCESSING: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING),
    m.SHIPPED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING, m.SHIPPED),
    m.FULFILLED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.PROCESSING, m.SHIPPED, m.FULFILLED),
    m.CANCELLED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.CANCELLED),
    m.REJECTED: (m.PENDING, m.PAYMENT_SUBMITTED, m.REJECTED),
    m.EXPIRED: (m.PENDING, m.EXPIRED),
    m.REFUNDED: (m.PENDING, m.PAYMENT_SUBMITTED, m.PAID, m.SHIPPED, m.FULFILLED, m.REFUNDED),
}


def sample_timeline(status: str, order_id: int = SAMPLE_ORDER_ID) -> tuple[m.OrderEvent, ...]:
    """Synthetic status changes leading to ``status`` (an hour apart)."""
    path = SAMPLE_PATHS.get(status, (status,))
    return tuple(
        m.OrderEvent(
            id=index + 1,
            order_id=order_id,
            from_status=path[index - 1] if index else None,
            to_status=stage,
            actor_id=None,
            note=None,
            created_at=SAMPLE_TIME + index * 3600,
        )
        for index, stage in enumerate(path)
    )


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
        ticket_number=SAMPLE_TICKET_NUMBER,
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
    inputs = flow.form_inputs(c, option.id if option is not None else None) + flow.submission_inputs(c)
    for index, item in enumerate(inputs, start=1):
        details[item.answer_key] = f"[sample answer {index}]"
    paid = status not in (*m.UNPAID_STATUSES, m.REJECTED, m.EXPIRED)
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
    return m.OrderView(
        order=order,
        items=items,
        fees=fees,
        channel_id=SAMPLE_CHANNEL_ID,
        ticket_number=SAMPLE_TICKET_NUMBER,
        timeline=sample_timeline(status),
    )


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
        "Private reply when the customer has the maximum number of open orders and none of their tickets is open.",
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
    sample_url = f"https://discord.com/channels/0/{SAMPLE_CHANNEL_ID}"
    add(
        "reply_order_exists",
        "Private reply when the customer has the maximum number of open orders: a link to the open order's ticket "
        "(with the optional open-ticket link button).",
        lambda: views.order_exists_reply(
            ui, SAMPLE_CHANNEL_ID, SAMPLE_ORDER_NUMBER, c.max_open_orders_per_user, sample_url
        ),
    )
    add(
        "reply_started_with_link",
        "Private reply after the order ticket was created, with the optional open-ticket link button.",
        lambda: views.started_reply(ui, SAMPLE_CHANNEL_ID, sample_url),
    )
    add(
        "ticket_intro",
        "First message of every order ticket: which ticket this is and who can see it (then the live panel).",
        lambda: views.ticket_intro(ui, settings, SAMPLE_TICKET_NUMBER, SAMPLE_USER_ID),
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
    if c.ask_address and settings.shipping.groups:
        group = settings.shipping.groups[-1]  # the last region (shipped: Europe, more than 25 countries: two menus)
        country = group.countries[0]
        address = {
            flow.ADDRESS_GROUP: group.id,
            flow.ADDRESS_COUNTRY: country,
            **{f.answer_key: f"[sample {f.id}]" for f in flow.ADDRESS_FIELDS},
        }
        add(
            "workflow_address_empty",
            "Workflow message: the shipping address step before a region is chosen (continue disabled).",
            workflow(_session(m.DETAILS, option=option_id), lines),
        )
        add(
            "workflow_address_entered",
            "Workflow message: the shipping address step with a region, country and address (the street is never "
            "shown in the channel).",
            workflow(_session(m.DETAILS, option=option_id, details=address), lines),
        )
        add(
            "address_form",
            "The private shipping address form (answers of an earlier attempt are filled in again).",
            partial(views.address_modal, ui, settings, _session(m.DETAILS, option=option_id, details=address)),
        )
        answers = {**answers, **address}
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
    reference = views.ticket_reference(settings, SAMPLE_TICKET_NUMBER)
    add(
        "workflow_cancelled",
        "Workflow message after the customer cancelled.",
        lambda: views.session_ended(ui, m.SESSION_CANCELLED, reference=reference),
    )
    add(
        "workflow_expired",
        "Workflow message after the unfinished order expired.",
        lambda: views.session_ended(ui, m.SESSION_EXPIRED, reference=reference),
    )
    add(
        "workflow_closed_by_staff",
        "Workflow message after staff closed the ticket of the unfinished order (/ticket close).",
        lambda: views.session_ended(ui, m.SESSION_CANCELLED, reference=reference, by_staff=True),
    )
    if option is not None:
        add(
            "workflow_fulfillment_largest",
            "Workflow message: fulfillment step after the largest possible cart (the lines give way to the subtotal "
            "when they do not fit next to the other data).",
            workflow(_session(m.FULFILLMENT, option=option_id), largest),
        )
    if inputs:
        add(
            "workflow_details_largest",
            "Workflow message: details step with answers after the largest possible cart.",
            workflow(_session(m.DETAILS, option=option_id, details=answers), largest),
        )
    submission = flow.submission_inputs(c)
    if submission:
        submitted_answers = {item.answer_key: f"[sample submission answer {i}]" for i, item in enumerate(submission, 1)}
        add(
            "submission_form",
            "Submission form (checkout.submission_form) that opens when the customer presses Submit.",
            lambda: views.submission_modal(ui, settings, review, 1000, 1000),
        )
        add(
            "submission_form_prefilled",
            "Submission form opened again after a refused attempt (the earlier answers are filled in).",
            lambda: views.submission_modal(
                ui, settings, replace(review, details={**review.details, **submitted_answers}), 1000, 1000
            ),
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
    instructions = provider.instructions(settings, pending.order, None) if provider is not None else None
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
    staff_preview.add_states(
        app,
        add,
        partial(_order, settings),
        lines=lines,
        largest=largest,
        staff_confirms=provider.requires_staff_confirmation if provider is not None else True,
        credit_refunds=settings.features.credits,
    )
    # The answers are posted in the staff channel only with operations.answers_in_orders_channel (otherwise staff see
    # them in the private record details, which the staff preview shows).
    posted = bool(inputs) and settings.operations.answers_in_orders_channel
    try:
        details_messages = views.staff_details_messages(ui, settings, pending.order) if posted else []
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
        "notify_submitted",
        "Customer notification: submission received (the order waits for payment).",
        lambda: views.status_notification(ui, settings, pending, k.NOTIFY_SUBMITTED),
    )
    warning_minutes = c.session_expiry_warning_minutes or 10
    add(
        "notify_session_expiry_warning",
        "Posted in the order ticket (mentioning the customer) before an unfinished order expires "
        "(checkout.session_expiry_warning_minutes).",
        lambda: replace(
            views.session_warning_notification(
                ui, minutes=warning_minutes, expires_at=SAMPLE_TIME + warning_minutes * 60
            ),
            mention_user_ids=(SAMPLE_USER_ID,),
        ),
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

    gift_card_preview.add_states(app, add, pending, _order(settings, m.PAYMENT_SUBMITTED, lines), instructions)

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
