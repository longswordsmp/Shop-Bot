"""Cross-feature flow through the real router: shop → order channel → cart → submit → staff payment →
fulfillment → review prompt by DM → rating + comment → published review. Also checks stock, credits, the
order channel lifecycle, and the audit trail. Uses fixture content (tests only) and Discord fakes."""

from __future__ import annotations

from pathlib import Path

from shopbot.db.repos import audit as audit_repo
from shopbot.features.checkout import models as m
from shopbot.features.checkout import views as checkout_views
from shopbot.features.reviews import repo as reviews_repo
from shopbot.features.reviews import service as reviews_service
from shopbot.features.reviews import views as review_views
from tests.checkout.util import (
    add_product,
    give_credits,
    make_env,
    order,
    place_order,
    press,
    product,
    staff_message,
    ticket_status,
)
from tests.fakes import click, submit_modal


async def test_purchase_to_published_review(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        app = tenv.app
        for feature in ("tickets", "catalog", "checkout", "credits", "reviews"):
            assert app.ready(feature), app.readiness.statuses[feature].problems

        customer = tenv.member(name="buyer")
        staffer = tenv.member(name="staff", staff=True)
        item = await add_product(tenv, "[sample product 1]", price=2500, stock=3)
        await give_credits(tenv, customer.id, 500)

        placed, order_channel, workflow = await place_order(tenv, customer, [(item.id, 2)])
        assert placed.status == m.PENDING
        assert placed.subtotal_minor == 5000
        assert (await product(tenv, item.id)).reserved == 2
        assert workflow.channel is order_channel  # one editable workflow message in the private order channel
        submitted_view = workflow.text()

        # A customer cannot mark their own order paid; staff can.
        control = staff_message(tenv, placed)
        await press(tenv, customer, control, checkout_views.staff_action_id(m.PAID, placed.id))
        assert (await order(tenv, placed.id)).status == m.PENDING
        await press(tenv, staffer, control, checkout_views.staff_action_id(m.PAID, placed.id))
        paid = await order(tenv, placed.id)
        assert paid.status == m.PAID
        stocked = await product(tenv, item.id)
        assert stocked.stock_on_hand == 1 and stocked.reserved == 0  # stock committed on payment

        await press(tenv, staffer, staff_message(tenv, paid), checkout_views.staff_action_id(m.FULFILLED, placed.id))
        fulfilled = await order(tenv, placed.id)
        assert fulfilled.status == m.FULFILLED
        assert fulfilled.ticket_id is not None
        assert await ticket_status(tenv, fulfilled.ticket_id) == "closed"  # order channel closed
        assert workflow.text() != submitted_view  # the customer's workflow message shows the new status

        # Review prompt (delay 0 in test settings) arrives by DM.
        await app.scheduler.run_due()
        review = await app.db.run(reviews_repo.get_review_by_order, placed.id)
        assert review is not None and review.status == "prompted"
        prompt = customer.dms[-1]
        assert review_views.rate_id(review.id) in prompt.custom_ids()

        # Another member cannot rate this review; the buyer can, in the DM.
        await click(
            app, user=tenv.member(name="other"), custom_id=review_views.rate_id(review.id), values=["5"], dm=True
        )
        assert (await app.db.run(reviews_repo.get_review_by_order, placed.id)).status == "prompted"
        rated = await click(
            app, user=customer, custom_id=review_views.rate_id(review.id), values=["5"], dm=True, message=prompt
        )
        assert rated.response.modal is not None  # comment form opened
        await submit_modal(
            app,
            user=customer,
            custom_id=review_views.comment_id(review.id),
            fields={"comment": "[sample comment]"},
            dm=True,
            message=prompt,
        )
        published = await app.db.run(reviews_repo.get_review_by_order, placed.id)
        assert published is not None and published.status == "published" and published.rating == 5
        reviews_channel = app.resources.text_channel(tenv.guild, app.settings.reviews.channel)
        assert reviews_channel is not None
        assert any("[sample comment]" in message.text() for message in reviews_channel.messages)

        # Order numbers are shown the same way by checkout and reviews.
        assert reviews_service.order_number_text(app, placed.number) == str(placed.number)

        actions = {event.action for event in await app.db.run(audit_repo.recent, app.env.guild_id, limit=100)}
        assert {
            "checkout.session_started",
            "checkout.order_submitted",
            "checkout.order_paid",
            "checkout.order_fulfilled",
            "tickets.closed",
            "reviews.prompted",
            "reviews.published",
        } <= actions
    finally:
        await tenv.close()
