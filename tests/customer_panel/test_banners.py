"""Banners on the live ticket panel through the submission form: the form keeps the review banner while it is open
and refused, and the submitted order shows the pending banner on the same message."""

from __future__ import annotations

from pathlib import Path

from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, views
from tests.checkout.banner_util import banner_file, make_banner_checkout_env
from tests.checkout.util import add_product, orders, press, start, to_review
from tests.customer_panel.util import SUBMISSION_FORM
from tests.fakes import modal_of, submit_modal
from tests.helpers import GUILD_ID, banner_names, uploaded


async def test_the_submission_form_keeps_the_review_banner_until_the_order_is_submitted(tmp_path: Path) -> None:
    tenv = await make_banner_checkout_env(tmp_path, SUBMISSION_FORM)
    try:
        review, pending = banner_file(tenv, "review-and-summary"), banner_file(tenv, "order-status")
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]", price=1000)
        _, session, _, message = await start(tenv, customer)
        await to_review(tenv, customer, message, session.id, [(p1.id, 1)])
        assert banner_names(message) == [review]
        data = await tenv.app.db.run(
            repo.load_workflow, GUILD_ID, session.id, tenv.app.settings, True, tenv.clock.now()
        )
        assert data is not None
        total, gross = data.totals.total_minor, data.totals.gross_minor
        edits = len(message.edits)
        opened = await press(tenv, customer, message, views.submit_id(session.id, total, gross))
        assert modal_of(opened) is not None and len(message.edits) == edits  # the form opens; nothing is edited
        fields = {"s.reference": "[ref-123]", "s.note": ""}
        # A total that changed meanwhile: the notice is shown on the review step, with the same banner.
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.submission_form_id(session.id, total + 1, gross + 1),
            fields=fields,
            message=message,
        )
        assert await orders(tenv) == []
        assert uploaded(message.edits[-1]) == [] and banner_names(message) == [review]
        # Submitted with the shown totals: the same message becomes the pending order, with its banner.
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=views.submission_form_id(session.id, total, gross),
            fields=fields,
            message=message,
        )
        (placed,) = await orders(tenv)
        assert placed.status == m.PENDING
        assert banner_names(message) == [pending] and len(message.attachments) == 1
        assert [getattr(a, "filename", None) for a in message.edits[-1]["attachments"]] == [pending]
    finally:
        await tenv.close()
