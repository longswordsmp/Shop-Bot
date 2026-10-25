"""Preview states of the customer panel and /myorders: every new or changed state exists, stays within Discord's
limits and has a registered handler for every component (with full example settings)."""

from __future__ import annotations

from pathlib import Path

from shopbot.config.content import MissingContent
from shopbot.features.checkout import MODULE as CHECKOUT
from shopbot.features.checkout import views as checkout_views
from shopbot.features.my_orders import MODULE as MY_ORDERS
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState
from tests.checkout.util import FULL_SETTINGS
from tests.customer_panel.util import SUBMISSION_FORM, WARNING, env
from tests.helpers import TestEnv, make_test_env
from tools.check_limits import unrouted_components

NEW_CHECKOUT_STATES = {
    "reply_order_exists",
    "reply_started_with_link",
    "ticket_intro",
    "workflow_fulfillment_largest",
    "workflow_details_largest",
    "submission_form",
    "submission_form_prefilled",
    "notify_submitted",
    "notify_session_expiry_warning",
}
MY_ORDERS_STATES = {
    "my_orders_empty",
    "my_orders_single_page",
    "my_orders_first_page",
    "my_orders_middle_page",
    "my_orders_last_page",
    "my_orders_largest_page",
}


def _checked(tenv: TestEnv, states: list[PreviewState], feature: str) -> dict[str, MessageSpec | ModalSpec]:
    names = [s.name for s in states]
    assert len(names) == len(set(names))
    for state in states:
        assert state.feature == feature and state.note
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert validate_message(state.spec) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name
    return {s.name: s.spec for s in states}


async def test_every_new_state_is_built_within_limits_and_routed(tmp_path: Path) -> None:
    tenv = await env(tmp_path, FULL_SETTINGS, SUBMISSION_FORM, WARNING)
    try:
        assert tenv.app.ready("checkout") and tenv.app.ready("my_orders")
        assert CHECKOUT.preview is not None and MY_ORDERS.preview is not None
        checkout_states = _checked(tenv, CHECKOUT.preview(tenv.app), "checkout")
        assert set(checkout_states) >= NEW_CHECKOUT_STATES, NEW_CHECKOUT_STATES - set(checkout_states)
        my_states = _checked(tenv, MY_ORDERS.preview(tenv.app), "my_orders")
        assert set(my_states) == MY_ORDERS_STATES
        # The paging buttons of the middle page are both enabled and routed to the my_orders handler.
        middle = my_states["my_orders_middle_page"]
        assert isinstance(middle, MessageSpec)
        assert [b.disabled for row in middle.rows for b in row] == [False, False]  # type: ignore[union-attr]
        submission = checkout_states["submission_form"]
        assert isinstance(submission, ModalSpec) and [i.custom_id for i in submission.inputs] == [
            "s.reference",
            "s.note",
        ]
        # Every live panel step carries the reference and the status.
        for name in ("workflow_cart", "workflow_fulfillment_chosen", "workflow_details_answered", "workflow_review"):
            spec = checkout_states[name]
            assert isinstance(spec, MessageSpec)
            names = [f.name for f in spec.embeds[0].fields[:2]]
            assert names == [tenv.text("checkout.ticket.reference_field"), tenv.text("checkout.ticket.status_field")]
        for name in ("order_submitted", "order_paid", "order_fulfilled", "order_refunded"):
            spec = checkout_states[name]
            assert isinstance(spec, MessageSpec)
            names = {f.name for e in spec.embeds for f in e.fields}
            assert tenv.text("checkout.ticket.timeline_field") in names, name
    finally:
        await tenv.close()


async def test_without_content_no_customer_state_is_built(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert MY_ORDERS.preview is not None
        assert MY_ORDERS.preview(tenv.app) == []
    finally:
        await tenv.close()


async def test_missing_stage_text_raises_instead_of_inventing(cp: TestEnv) -> None:
    values = dict(cp.content)
    values["checkout.ticket.stage.paid"] = ""
    cp.app.content.replace(file_values=values)
    try:
        checkout_views.stage_name(cp.app.ui, "paid")
    except MissingContent as exc:
        assert exc.key == "checkout.ticket.stage.paid"
    else:
        raise AssertionError("a missing stage name must not be replaced")
