"""Banners of the checkout's customer messages: the shop panel, the private replies, the ticket introduction, the
single live workflow message (its banner follows the step, then the order's status), the customer notifications and
the preview states. An edit that keeps the banner never uploads it again; continuation messages show none."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import panel, service, staff, staff_views, views
from shopbot.features.checkout import preview as checkout_preview
from shopbot.features.tickets import service as tickets_service
from shopbot.ui.spec import MessageSpec
from tests.checkout.banner_util import Watch, make_banner_checkout_env, shows
from tests.checkout.util import (
    FULL_SETTINGS,
    add_product,
    channel,
    go,
    order,
    place_order,
    press,
    reject,
    reload_session,
    session_of,
    set_qty,
    staff_message,
    start,
    submit,
    ticket_channel_of,
    ticket_intro_message,
    to_review,
    workflow_message,
)
from tests.fakes import FakeMessage, click, submit_modal
from tests.helpers import TestEnv


@pytest.fixture
async def bco(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Shipped checkout settings with banners switched on (a tiny picture for every banner id)."""
    tenv = await make_banner_checkout_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


@pytest.fixture
async def bcof(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """The full workflow (fulfillment options, a details form, fees) with banners switched on."""
    tenv = await make_banner_checkout_env(tmp_path, FULL_SETTINGS)
    try:
        yield tenv
    finally:
        await tenv.close()


def last_reply(interaction: Any) -> FakeMessage:
    assert interaction.last_sent is not None, "no reply"
    reply: FakeMessage = interaction.last_sent
    return reply


# -- shop panel and private replies ------------------------------------------------------------------------------------


async def test_the_shop_panel_shows_its_banner_and_keeps_it_through_text_edits(bco: TestEnv) -> None:
    await panel.publish(bco.app)
    (message,) = channel(bco, "shop").messages
    assert shows(bco, message, "purchase-panel")
    watch = Watch(bco, message)
    bco.app.content.replace(file_values={**bco.content, k.PANEL_TITLE: "[a new title]"})
    await panel.publish(bco.app)
    assert message.embeds[0].title == "[a new title]"
    assert watch.kept("purchase-panel")


async def test_start_reply_ticket_introduction_and_workflow_message(bco: TestEnv) -> None:
    customer = bco.member()
    await add_product(bco, "[p1]")
    interaction, _, ch, message = await start(bco, customer)
    assert shows(bco, last_reply(interaction), "ticket-created")
    # Two separate messages open the ticket, each with its own banner.
    assert shows(bco, ticket_intro_message(ch), "ticket-created")
    assert shows(bco, message, "selection-and-cart")
    # Start again: the unfinished order exists (a link to it).
    again = await click(bco.app, user=customer, custom_id=views.start_id())
    assert shows(bco, last_reply(again), "ticket-created")
    assert len(ch.messages) == 2


async def test_buy_buttons_reply_with_their_banners_and_keep_the_cart_banner(bco: TestEnv) -> None:
    customer = bco.member()
    p1 = await add_product(bco, "[p1]")
    p2 = await add_product(bco, "[p2]")
    opened = await click(bco.app, user=customer, custom_id=f"sb:co:buy:{p1.id}")
    assert shows(bco, last_reply(opened), "ticket-created")
    session = await session_of(bco, customer)
    ch = await ticket_channel_of(bco, session.ticket_id)
    message = workflow_message(ch)
    assert shows(bco, message, "selection-and-cart")  # opened at the cart step
    watch = Watch(bco, message)
    added = await click(bco.app, user=customer, custom_id=f"sb:co:buy:{p2.id}")
    assert shows(bco, last_reply(added), "selection-and-cart")
    assert watch.kept("selection-and-cart")  # the live message shows the new cart; the picture is not sent again
    # A product that could not be put in the cart after all: its notice is a second card of the same reply.
    notice = bco.app.ui.error_embed(k.ERROR_OUT_OF_STOCK, name="[p1]", available=0)
    assert notice is not None
    sold_out = views.buy_started_reply(bco.app.ui, ch.id, notice)
    assert sold_out.banner == bco.app.ui.banner("ticket-created") and len(sold_out.embeds) == 2


async def test_open_order_limit_replies(tmp_path: Path) -> None:
    tenv = await make_banner_checkout_env(tmp_path, {"checkout": {"max_open_orders_per_user": 1}})
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        await place_order(tenv, customer, [(p1.id, 1)])
        refused = await click(tenv.app, user=customer, custom_id=views.start_id())
        assert shows(tenv, last_reply(refused), "order-status")  # a link to the open order's ticket
        limit = views.limit_reply(tenv.app.ui, 1)  # no ticket can be linked
        assert limit.banner == tenv.app.ui.banner("rejected-or-unsuccessful-submission")
    finally:
        await tenv.close()


# -- the live workflow message -----------------------------------------------------------------------------------------


async def test_the_workflow_banner_follows_the_step_and_is_not_uploaded_again(bco: TestEnv) -> None:
    customer = bco.member()
    p1 = await add_product(bco, "[p1]", stock=5)
    _, session, _, message = await start(bco, customer)
    watch = Watch(bco, message)
    # Picking a product opens the quantity form; the menu reset keeps the banner.
    await press(bco, customer, message, views.pick_id(session.id, 0), values=[str(p1.id)])
    assert watch.kept("selection-and-cart")
    await set_qty(bco, customer, message, session.id, p1.id, 2)
    assert watch.kept("selection-and-cart")
    await set_qty(bco, customer, message, session.id, p1.id, 50)  # refused: re-rendered, then the error privately
    assert watch.kept("selection-and-cart")
    await go(bco, customer, message, session.id, m.CART)
    assert watch.kept("selection-and-cart")  # browse and cart share the banner
    await go(bco, customer, message, session.id, m.REVIEW)
    assert watch.swapped("review-and-summary")
    await press(bco, customer, message, views.cancel_id(session.id))
    assert watch.swapped("general-response")
    await press(bco, customer, message, views.cancel_no_id(session.id))
    assert watch.swapped("review-and-summary")
    await go(bco, customer, message, session.id, m.CART)
    assert watch.swapped("selection-and-cart")
    await press(bco, customer, message, views.cancel_id(session.id))
    assert watch.swapped("general-response")
    await press(bco, customer, message, views.cancel_yes_id(session.id))
    assert watch.swapped("cancellation")
    assert len(message.attachments) == 1


async def test_fulfillment_and_details_steps_show_details_and_options(bcof: TestEnv) -> None:
    tenv = bcof
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]")
    _, session, _, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, p1.id, 1)
    await go(tenv, customer, message, session.id, m.CART)
    watch = Watch(tenv, message)
    await go(tenv, customer, message, session.id, m.FULFILLMENT)
    assert watch.swapped("details-and-options")
    await press(tenv, customer, message, views.fulfillment_id(session.id), values=["delivery"])
    assert watch.kept("details-and-options")
    await go(tenv, customer, message, session.id, m.DETAILS)
    assert watch.kept("details-and-options")
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(session.id, 0),
        fields={"d.name": "[sample name]", "o.delivery.address": "[sample address]", "o.delivery.phone": ""},
        message=message,
    )
    assert watch.kept("details-and-options")
    await go(tenv, customer, message, session.id, m.REVIEW)
    assert watch.swapped("review-and-summary")
    await go(tenv, customer, message, session.id, m.DETAILS)
    assert watch.swapped("details-and-options")


async def test_a_changed_price_keeps_the_review_banner(bco: TestEnv) -> None:
    customer = bco.member()
    p1 = await add_product(bco, "[p1]", price=1000)
    _, session, _, message = await start(bco, customer)
    await to_review(bco, customer, message, session.id, [(p1.id, 1)])
    watch = Watch(bco, message)
    await submit(bco, customer, message, session.id, total=999)  # the total the customer saw is outdated
    assert (await reload_session(bco, session.id)).status == m.ACTIVE
    assert len(message.embeds) == 2 and watch.kept("review-and-summary")  # with the price-changed notice


async def test_an_expired_or_staff_closed_unfinished_order_shows_the_cancellation_banner(bco: TestEnv) -> None:
    customer = bco.member()
    _, session, _, message = await start(bco, customer)
    watch = Watch(bco, message)
    assert await service.expire_session(bco.app, session.id, reason="idle")
    assert watch.swapped("cancellation")

    other = bco.member(name="other")
    staffer = bco.member(name="staffer", staff=True)
    _, session, _, message = await start(bco, other)
    ticket = await tickets_service.get_ticket(bco.app, session.ticket_id)
    assert ticket is not None
    watch = Watch(bco, message)
    await service.staff_close_ticket(bco.app, ticket, staffer.id, None)
    assert watch.swapped("cancellation")


async def test_a_resent_workflow_message_carries_its_banner(bco: TestEnv) -> None:
    customer = bco.member()
    _, session, ch, message = await start(bco, customer)
    await message.delete()
    assert await service.resend_workflow(bco.app, await reload_session(bco, session.id), ch)
    resent = workflow_message(ch)
    assert resent is not message and shows(bco, resent, "selection-and-cart")


# -- the submitted order on the same message, and its notifications ---------------------------------------------------


async def test_the_order_banner_follows_its_status_on_the_message_and_in_notifications(bco: TestEnv) -> None:
    customer = bco.member()
    staffer = bco.member(name="staffer", staff=True)
    p1 = await add_product(bco, "[p1]", price=1000)
    _, session, _, message = await start(bco, customer)
    await to_review(bco, customer, message, session.id, [(p1.id, 1)])
    watch = Watch(bco, message)
    await submit(bco, customer, message, session.id)
    placed = await order(bco, (await reload_session(bco, session.id)).order_id or 0)
    assert watch.swapped("pending-review")
    assert shows(bco, customer.dms[-1], "pending-review")  # submission received
    # The customer's cancel question and going back.
    await press(bco, customer, message, views.order_cancel_id(placed.id))
    assert watch.swapped("general-response")
    await press(bco, customer, message, views.order_cancel_no_id(placed.id))
    assert watch.swapped("pending-review")
    # Staff move the order on: the message and the notification follow each status.
    steps: list[tuple[str, BannerId]] = [
        (m.PAID, "successful-confirmation"),
        (m.PROCESSING, "submission-and-processing"),
        (m.FULFILLED, "completed-or-dispatched"),
    ]
    for status, banner_id in steps:
        dms = len(customer.dms)
        outcome = await service.change_status(bco.app, placed.id, status, actor_id=staffer.id)
        assert outcome.applied, status
        assert watch.swapped(banner_id), status
        assert len(customer.dms) == dms + 1 and shows(bco, customer.dms[-1], banner_id), status
    # Showing the unchanged status view again keeps the banner without uploading it again.
    assert await service.refresh_customer_message(bco.app, placed.id)
    assert watch.kept("completed-or-dispatched")
    # Tracking details entered after completion: their own notification.
    await staff.set_tracking(bco.app, await order(bco, placed.id), actor_id=staffer.id, text="[sample tracking]")
    assert shows(bco, customer.dms[-1], "order-status")
    # A partial refund, then the rest: the order is settled.
    await service.refund(bco.app, placed.id, amount_minor=400, to_credit=False, reason=None, actor_id=staffer.id)
    assert shows(bco, customer.dms[-1], "order-status")
    assert shows(bco, message, "completed-or-dispatched")  # still fulfilled
    await service.refund(bco.app, placed.id, amount_minor=600, to_credit=False, reason=None, actor_id=staffer.id)
    assert (await order(bco, placed.id)).status == m.REFUNDED
    assert watch.swapped("resolution")
    assert shows(bco, customer.dms[-1], "resolution")
    assert len(message.attachments) == 1


async def test_cancelled_rejected_and_expired_orders(bco: TestEnv) -> None:
    staffer = bco.member(name="staffer", staff=True)
    p1 = await add_product(bco, "[p1]")
    # The customer cancels their unpaid order.
    first = bco.member(name="first")
    placed, _, message = await place_order(bco, first, [(p1.id, 1)])
    await press(bco, first, message, views.order_cancel_id(placed.id))
    watch = Watch(bco, message)
    await press(bco, first, message, views.order_cancel_yes_id(placed.id))
    assert (await order(bco, placed.id)).status == m.CANCELLED
    assert watch.swapped("cancellation") and len(message.attachments) == 1
    assert shows(bco, first.dms[-1], "cancellation")
    # Staff ask for a correction, then reject the order with a reason.
    second = bco.member(name="second")
    placed, _, message = await place_order(bco, second, [(p1.id, 1)])
    await staff.request_correction(bco.app, placed, actor_id=staffer.id, text="[sample correction]")
    assert shows(bco, second.dms[-1], "details-and-options")
    watch = Watch(bco, message)
    await reject(bco, staffer, staff_message(bco, await order(bco, placed.id)), placed.id)
    assert (await order(bco, placed.id)).status == m.REJECTED
    assert watch.swapped("rejected-or-unsuccessful-submission")
    assert shows(bco, second.dms[-1], "rejected-or-unsuccessful-submission")
    # An unpaid order expires.
    third = bco.member(name="third")
    placed, _, message = await place_order(bco, third, [(p1.id, 1)])
    watch = Watch(bco, message)
    await service.change_status(bco.app, placed.id, m.EXPIRED, actor_id=None)
    assert watch.swapped("cancellation")
    assert shows(bco, third.dms[-1], "cancellation")


async def test_notifications_in_the_ticket_keep_their_banner(tmp_path: Path) -> None:
    tenv = await make_banner_checkout_env(tmp_path, {"checkout": {"payment_reminder_minutes": 30}})
    try:
        customer = tenv.member()
        customer.dm_forbidden = True  # every notification falls back to the order ticket
        p1 = await add_product(tenv, "[p1]")
        _, ch, message = await place_order(tenv, customer, [(p1.id, 1)])
        submitted = ch.messages[-1]
        assert submitted is not message and submitted.content == customer.mention
        assert shows(tenv, submitted, "pending-review")
        tenv.clock.advance(30 * 60)
        await tenv.app.scheduler.run_due()  # the payment reminder
        reminder = ch.messages[-1]
        assert reminder is not submitted and len(reminder.embeds) == 2  # with the payment instructions
        assert shows(tenv, reminder, "reminder")
        assert shows(tenv, message, "pending-review")  # the live message is unchanged
    finally:
        await tenv.close()


async def test_the_expiry_warning_shows_the_reminder_banner(tmp_path: Path) -> None:
    overrides = {"checkout": {"session_expiry_minutes": 30, "session_expiry_warning_minutes": 10}}
    tenv = await make_banner_checkout_env(tmp_path, overrides)
    try:
        customer = tenv.member()
        _, session, ch, message = await start(tenv, customer)
        assert await service.warn_session_expiry(tenv.app, session) == "channel"
        warning = ch.messages[-1]
        assert warning is not message and shows(tenv, warning, "reminder")
    finally:
        await tenv.close()


async def test_order_continuation_messages_show_no_banner(tmp_path: Path) -> None:
    """Configured text that alone fills the message: the payment instructions follow as a separate message."""
    content = {
        k.SUBMITTED_DESCRIPTION: ("[d] " * 1100)[:4096],
        "checkout.payment.manual.instructions": ("[i] " * 1100)[:4096],
    }
    tenv = await make_banner_checkout_env(tmp_path, content=content)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        _, ch, message = await place_order(tenv, customer, [(p1.id, 1)])
        assert shows(tenv, message, "pending-review")
        separate = [msg for msg in ch.messages[2:] if msg.author is ch.guild.me]
        assert separate and all(shows(tenv, msg, None) for msg in separate)
    finally:
        await tenv.close()


# -- preview states ---------------------------------------------------------------------------------------------------

_SELECTION: BannerId = "selection-and-cart"
_DETAILS: BannerId = "details-and-options"
_REVIEW: BannerId = "review-and-summary"
EXPECTED: dict[str, BannerId] = {
    "shop_panel": "purchase-panel",
    "reply_started": "ticket-created",
    "reply_started_with_link": "ticket-created",
    "reply_session_exists": "ticket-created",
    "reply_buy_started_sold_out": "ticket-created",
    "reply_buy_added": _SELECTION,
    "reply_order_exists": "order-status",
    "reply_limit_reached": "rejected-or-unsuccessful-submission",
    "ticket_intro": "ticket-created",
    "workflow_browse_empty_cart": _SELECTION,
    "workflow_browse_items_page_2": _SELECTION,
    "workflow_browse_empty_catalog": _SELECTION,
    "workflow_cart": _SELECTION,
    "workflow_cart_full": _SELECTION,
    "workflow_cart_empty": _SELECTION,
    "workflow_cart_minimum_not_met": _SELECTION,
    "workflow_cart_largest": _SELECTION,
    "workflow_fulfillment_unchosen": _DETAILS,
    "workflow_fulfillment_chosen": _DETAILS,
    "workflow_fulfillment_largest": _DETAILS,
    "workflow_details_empty": _DETAILS,
    "workflow_details_answered": _DETAILS,
    "workflow_details_largest": _DETAILS,
    "workflow_review": _REVIEW,
    "workflow_review_credits_applied": _REVIEW,
    "workflow_review_price_changed": _REVIEW,
    "workflow_review_largest": _REVIEW,
    "workflow_cancel_confirm": "general-response",
    "workflow_cancelled": "cancellation",
    "workflow_expired": "cancellation",
    "workflow_closed_by_staff": "cancellation",
    "order_submitted": "pending-review",
    "order_submitted_largest": "pending-review",
    "order_cancel_confirm": "general-response",
    "order_payment_submitted": "pending-review",
    "order_paid": "successful-confirmation",
    "order_processing": "submission-and-processing",
    "order_shipped": "completed-or-dispatched",
    "order_fulfilled": "completed-or-dispatched",
    "order_cancelled": "cancellation",
    "order_rejected": "rejected-or-unsuccessful-submission",
    "order_expired": "cancellation",
    "order_refunded": "resolution",
    "notify_submitted": "pending-review",
    "notify_payment_submitted": "pending-review",
    "notify_paid": "successful-confirmation",
    "notify_processing": "submission-and-processing",
    "notify_shipped": "completed-or-dispatched",
    "notify_shipped_with_tracking": "completed-or-dispatched",
    "notify_fulfilled": "completed-or-dispatched",
    "notify_fulfilled_with_tracking": "completed-or-dispatched",
    "notify_cancelled": "cancellation",
    "notify_rejected": "rejected-or-unsuccessful-submission",
    "notify_rejected_with_reason": "rejected-or-unsuccessful-submission",
    "notify_expired": "cancellation",
    "notify_refunded": "resolution",
    "notify_refund": "order-status",
    "notify_correction": "details-and-options",
    "notify_tracking": "order-status",
    "notify_payment_reminder": "reminder",
    "notify_session_expiry_warning": "reminder",
    "error_session_closed": "error",
    "error_out_of_stock": "error",
    "error_quantity_invalid": "error",
    "error_cart_full": "error",
    "error_product_unavailable": "error",
    "error_order_not_cancellable": "error",
    "error_minimum_not_met": "error",
}


async def test_every_customer_preview_state_shows_its_banner(bcof: TestEnv) -> None:
    states = {s.name: s.spec for s in checkout_preview.preview(bcof.app)}
    assert set(EXPECTED) <= set(states), sorted(set(EXPECTED) - set(states))
    for name, spec in states.items():
        if not isinstance(spec, MessageSpec) or name.startswith("staff_"):
            continue  # forms show no pictures; the staff side has its own banners
        if name.startswith("order_submitted_largest_part_"):
            assert spec.banner is None, name  # continuation of the workflow message
            continue
        assert name in EXPECTED, f"{name}: no banner expected for this state"
        assert spec.banner == bcof.app.ui.banner(EXPECTED[name]), name


async def test_the_staff_activity_notifications_match_their_preview(bco: TestEnv) -> None:
    """The correction and tracking notifications carry their banner from their builders; the preview shows the same."""
    customer = bco.member()
    p1 = await add_product(bco, "[p1]")
    placed, _, _ = await place_order(bco, customer, [(p1.id, 1)])
    view = await service.order_view(bco.app, placed.id)
    assert view is not None
    states = {s.name: s.spec for s in checkout_preview.preview(bco.app)}
    sent = {
        "notify_correction": staff_views.correction_notification(
            bco.app.ui, bco.app.settings, view, "[sample message]"
        ),
        "notify_tracking": staff_views.tracking_notification(bco.app.ui, bco.app.settings, view, "[sample tracking]"),
    }
    for name, spec in sent.items():
        shown = states[name]
        assert isinstance(shown, MessageSpec) and spec.banner is not None
        assert shown.banner == spec.banner, name
