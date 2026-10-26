"""Preview states of discount codes (only while ``checkout.discount_codes`` is on): the review step with a percent
code, a buy one get one free code, a code that no longer applies and the customer's claimed coupons; the code form
and every reply when a code cannot be used; orders placed with a code (order card, notification, staff record and
full record); the claim replies; and ``/discount`` (list, create). Built by the runtime view functions from
synthetic sample data. Called by ``preview.preview``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_views, staff, staff_preview, staff_views, views
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.features.checkout.discount_repo import CodeStats
from shopbot.features.checkout.payments import get_provider
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.checkout.payments import PaymentInstructions

SAMPLE_TIME = staff_preview.SAMPLE_TIME
SAMPLE_CATALOG_CHANNEL_ID = 100_000_000_000_000_005
Add = Callable[[str, str, Callable[[], MessageSpec | ModalSpec | None]], None]


class DataFactory(Protocol):
    def __call__(
        self,
        session: m.Session,
        lines: tuple[m.CartLine, ...],
        *,
        code: d.Code | None = None,
        reason: str | None = None,
        offer: m.DiscountOffer | None = None,
    ) -> m.WorkflowData: ...


class OrderFactory(Protocol):
    def __call__(self, status: str, lines: tuple[m.CartLine, ...], *, code: d.Code | None = None) -> m.OrderView: ...


def sample_code(
    code_id: int,
    code: str,
    kind: str,
    value: int,
    *,
    minimum: int = 0,
    max_uses: int | None = None,
    once: bool = False,
    expires_at: int | None = None,
    ended_at: int | None = None,
    products: frozenset[int] = frozenset(),
) -> d.Code:
    return d.Code(
        id=code_id,
        guild_id=0,
        code=code,
        kind=kind,
        value=value,
        min_subtotal_minor=minimum,
        max_uses=max_uses,
        once_per_customer=once,
        expires_at=expires_at,
        created_by=staff_preview.SAMPLE_STAFF_ID,
        created_at=SAMPLE_TIME,
        ended_at=ended_at,
        ended_by=staff_preview.SAMPLE_STAFF_ID if ended_at else None,
        product_ids=products,
    )


def add_states(
    app: App,
    add: Add,
    data: DataFactory,
    make: OrderFactory,
    *,
    review: m.Session,
    lines: tuple[m.CartLine, ...],
    instructions: PaymentInstructions | None,
    links: Mapping[str, str],
) -> None:
    ui, settings, op = app.ui, app.settings, app.op
    first = lines[0].product
    percent = sample_code(1, "SAMPLE10", d.PERCENT, 1000, once=True, expires_at=SAMPLE_TIME + 14 * 86_400)
    bogo = sample_code(
        2,
        "SAMPLEBOGO",
        d.BOGO,
        0,
        once=True,
        max_uses=500,
        expires_at=SAMPLE_TIME + 30 * 86_400,
        products=frozenset({first.id, lines[1].product.id}),
    )
    amount = sample_code(3, "SAMPLE5OFF", d.AMOUNT, 500, minimum=2_500)
    subtotal = sum(line.line_total_minor for line in lines)
    too_high = sample_code(4, "SAMPLEBIG", d.PERCENT, 2000, minimum=subtotal + 1_000)
    money = partial(views.money, ui)

    def workflow(built: m.WorkflowData) -> Callable[[], MessageSpec]:
        return lambda: views.workflow_message(ui, settings, built)

    # -- the review step ------------------------------------------------------------------------------------------
    add(
        "workflow_review_discount_percent",
        "Review step with a percent discount code applied: shown under the subtotal (percent fees are charged on the "
        "items after it); Remove discount replaces the discount buttons.",
        workflow(data(review, lines, code=percent)),
    )
    add(
        "workflow_review_discount_bogo",
        "Review step with a buy one, get one free code: the cheaper item of every pair is free.",
        workflow(data(review, lines, code=bogo)),
    )
    add(
        "workflow_review_discount_not_applying",
        "Review step after the cart changed below the applied code's minimum: 'not applied' in the discount's place, "
        "the total without the discount, the reason under the total; Remove discount stays.",
        workflow(data(review, lines, code=too_high, reason=d.MINIMUM)),
    )
    if k.credits_possible(settings):
        add(
            "workflow_review_discount_credits_applied",
            "Review step with a percent code and store credit applied: subtotal, discount, delivery, fees, store "
            "credit and the total add up; Remove store credit and Remove discount share one row.",
            workflow(data(replace(review, apply_credits=True), lines, code=percent)),
        )
    # The longest coupon a preset can offer (14 characters, ``settings.ANNOUNCEMENT_COUPON_MAX``): its button label
    # stays within 25 characters.
    longest = "SAMPLESPRING26"
    coupons = m.DiscountOffer(codes=True, coupons=((bogo.id, bogo.code), (percent.id + 10, longest)))
    add(
        "workflow_review_discount_buttons",
        "Review step of a customer who claimed two coupons (newest first; the second has the longest code a preset "
        "can offer) while the shop has codes: one Use coupon button each and Add discount code, in the store credit's "
        "row.",
        workflow(data(review, lines, offer=coupons)),
    )
    add(
        "discount_form",
        "The private discount code form (Add discount code).",
        lambda: views.discount_modal(ui, review.id),
    )

    def refused(reason: str) -> MessageSpec:
        shown = bogo if reason == d.NEEDS_TWO_ITEMS else percent  # only buy one, get one free needs two items
        values: dict[str, str | int] = {"code": views.escape(shown.code)}
        if reason == d.MINIMUM:
            values["minimum"] = money(too_high.min_subtotal_minor)
        return views.error_message(ui, k.discount_error_key(reason), **values)

    for reason in d.REASONS:
        add(
            f"discount_error_{reason}",
            f"Private reply when a discount code cannot be used: {reason.replace('_', ' ')}.",
            partial(refused, reason),
        )
    add(
        "discount_error_too_many_tries",
        "Private reply after 5 codes that could not be used within 10 minutes (protection against guessing codes).",
        partial(views.error_message, ui, k.DISCOUNT_TOO_MANY_TRIES),
    )

    # -- orders placed with a code ----------------------------------------------------------------------------------
    pending = make(m.PENDING, lines, code=bogo)
    add(
        "order_discount_submitted",
        "Order card of an order placed with a buy one, get one free code, waiting for payment (the total to pay is "
        "after the discount).",
        partial(views.order_message, ui, settings, pending, instructions=instructions),
    )
    for status in (m.PAID, m.SHIPPED):
        add(
            f"order_discount_{status}",
            f"Order card of an order placed with a percent code, once it is '{status}'.",
            partial(
                views.order_message,
                ui,
                settings,
                make(status, lines, code=percent),
                instructions=None,
                shipment=staff_preview.SAMPLE_SHIPMENT if status == m.SHIPPED else None,
                links=links,
            ),
        )
    add(
        "notify_submitted_discount",
        "Customer notification of an order placed with a code: notifications name the total (after the discount); the "
        "order card shows the breakdown.",
        lambda: views.status_notification(ui, settings, pending, k.NOTIFY_SUBMITTED),
    )
    provider = get_provider(settings.payments.provider)
    paid = make(m.PAID, lines, code=amount)
    add(
        "staff_discount_record",
        "Live order record of a paid order placed with a discount code: the discount line under the subtotal.",
        partial(
            staff_views.staff_message,
            ui,
            settings,
            paid,
            staff_preview.sample_data(paid.order),
            staff_confirms_payment=provider.requires_staff_confirmation if provider is not None else True,
            credit_refunds=settings.features.credits,
            tracking=False,
        ),
    )
    add(
        "staff_discount_record_details",
        "Staff only: the full record (details button, /order view) of an order placed with a discount code.",
        lambda: staff.lookup_specs(app, paid, None, [], staff_preview.sample_data(paid.order))[0],
    )

    # -- claimed coupons ----------------------------------------------------------------------------------------------
    names = [line.product.name for line in lines]
    links = {**links, **{name: f"<#{SAMPLE_CATALOG_CHANNEL_ID}>" for name in k.catalog_placeholder(settings)}}
    add(
        "discount_claim_reply",
        "Private reply after a member claimed a coupon (an announcement's claim button): the code, its terms and how "
        "to use it.",
        lambda: discount_views.claim_reply(ui, settings, bogo, names, links=links),
    )
    add(
        "discount_claim_reply_simple",
        "Private reply after claiming a percent coupon for every item, without an end date.",
        lambda: discount_views.claim_reply(ui, settings, sample_code(5, "SAMPLE15", d.PERCENT, 1500), [], links=links),
    )
    add(
        "discount_offer_ended",
        "Private reply to a claim button whose offer has ended (the code ended, expired, was used up or deleted by "
        "/reset, or discount codes are switched off).",
        lambda: discount_views.offer_ended_reply(ui),
    )

    # -- staff: /discount ------------------------------------------------------------------------------------------
    now = SAMPLE_TIME + 86_400
    usable = [
        CodeStats(bogo, uses=37, claims=112, product_names=tuple(names)),
        CodeStats(percent, uses=4, claims=0, product_names=()),
        CodeStats(amount, uses=0, claims=0, product_names=()),
    ]
    ended = [
        CodeStats(
            sample_code(6, "SAMPLEOLD", d.PERCENT, 2500, ended_at=SAMPLE_TIME), uses=12, claims=3, product_names=()
        )
    ]
    add(
        "staff_discount_list",
        "Staff only (/discount list): the active codes with their terms, uses and claims, then the latest ended ones.",
        lambda: discount_views.list_messages(ui, op, money, usable, ended, now=now)[0],
    )
    add(
        "staff_discount_created",
        "Staff only (/discount create): the new code and its terms in plain words.",
        lambda: discount_views.created_reply(ui, op, money, bogo, names, now=now),
    )
