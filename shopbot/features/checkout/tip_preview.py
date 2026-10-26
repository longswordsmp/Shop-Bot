"""Preview states of tips for our team (only while ``checkout.tips`` is on): the review step with a listed tip, and
with the customer's own amount next to a discount code and store credit (the summary adds up); the Other amount form
and its replies; orders placed with a tip (order card, staff record and full record). The review step without a tip is
the main ``workflow_review`` state. Built by the runtime view functions from synthetic sample data. Called by
``preview.preview``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_preview, staff, staff_preview, staff_views, tips, views
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.features.checkout.payments import get_provider
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.checkout.payments import PaymentInstructions

Add = Callable[[str, str, Callable[[], MessageSpec | ModalSpec | None]], None]


class DataFactory(Protocol):
    def __call__(
        self, session: m.Session, lines: tuple[m.CartLine, ...], *, code: d.Code | None = None, tip: int = 0
    ) -> m.WorkflowData: ...


class OrderFactory(Protocol):
    def __call__(
        self,
        status: str,
        lines: tuple[m.CartLine, ...],
        *,
        credits: int = 0,
        code: d.Code | None = None,
        tip: int = 0,
    ) -> m.OrderView: ...


def sample_custom_tip(amounts: tuple[int, ...], maximum: int) -> int:
    """An amount a customer could type that the menu does not list (shown as their own amount)."""
    return next(amount for amount in (750, 1_250, 333, maximum) if amount not in amounts and amount <= maximum)


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
    ui, settings = app.ui, app.settings
    c = settings.checkout
    listed = c.tips.amounts[min(1, len(c.tips.amounts) - 1)]  # the shipped $5.00
    custom = sample_custom_tip(c.tips.amounts, c.tips.max_minor)
    percent = discount_preview.sample_code(1, "SAMPLE10", d.PERCENT, 1000) if c.discount_codes else None
    credits = k.credits_possible(settings)

    def workflow(built: m.WorkflowData) -> Callable[[], MessageSpec]:
        return lambda: views.workflow_message(ui, settings, built)

    # -- the review step ------------------------------------------------------------------------------------------
    add(
        "workflow_review_tip",
        "Review step with a tip from the menu: 'Tip for our team' after the extras and fees, the menu shows it as "
        "selected, and the card's last line thanks the customer.",
        workflow(data(review, lines, tip=listed)),
    )
    add(
        "workflow_review_tip_custom_discount_credit",
        "Review step with the customer's own tip amount (Other amount), a percent discount code and store credit "
        "applied: the discount takes nothing off the tip, the store credit covers part of it, and the lines add up "
        "to the total.",
        workflow(
            data(replace(review, apply_credits=credits), lines, code=percent, tip=custom),
        ),
    )
    if c.tips.allow_custom:
        add(
            "tip_form",
            "The private Other amount form of the tip menu.",
            lambda: views.tip_modal(ui, settings, review.id),
        )
        for reason in (tips.INVALID, tips.TOO_HIGH):
            add(
                f"tip_error_{reason}",
                f"Private reply when the amount typed in the tip form cannot be used: {reason.replace('_', ' ')}.",
                partial(_tip_error, app, reason),
            )

    # -- orders placed with a tip -----------------------------------------------------------------------------------
    add(
        "order_tip_submitted",
        "Order card of an order placed with a tip, waiting for payment: the tip is part of the total to pay.",
        partial(views.order_message, ui, settings, make(m.PENDING, lines, tip=listed), instructions=instructions),
    )
    add(
        "order_tip_fulfilled",
        "Order card of a delivered order placed with the customer's own tip amount and a discount code.",
        partial(
            views.order_message,
            ui,
            settings,
            make(m.FULFILLED, lines, code=percent, tip=custom),
            instructions=None,
            links=links,
        ),
    )
    provider = get_provider(settings.payments.provider)
    paid = make(m.PAID, lines, code=percent, tip=custom)
    add(
        "staff_tip_record",
        "Live order record of a paid order placed with a tip and a discount code: the tip on its own line in the "
        "amounts, never among the extras.",
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
        "staff_tip_record_details",
        "Staff only: the full record (details button, /order view) of an order placed with a tip.",
        lambda: staff.lookup_specs(app, paid, None, [], staff_preview.sample_data(paid.order))[0],
    )


def _tip_error(app: App, reason: str) -> MessageSpec:
    key, values = views.tip_error(app.ui, app.settings, reason)
    return views.error_message(app.ui, key, **values)
