"""Content keys, settings problems, readiness, operator text, panel publishing, and every preview state."""

from __future__ import annotations

import re
from pathlib import Path

from shopbot.config.content_keys import Kind
from shopbot.features import core_keys as core
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.checkout import MODULE, panel, views
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout.text import OPERATOR_TEXT
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import ButtonSpec, MessageSpec, ModalSpec
from tests.checkout.util import FULL_SETTINGS, channel, make_env, render
from tests.helpers import TestEnv, deep_merge, make_settings, make_test_env
from tools.check_limits import unrouted_components

PACKAGE = Path(__file__).resolve().parents[2] / "shopbot" / "features" / "checkout"


def _keys(overrides: dict[str, object] | None = None) -> dict[str, object]:
    return {key.key: key for key in k.content_keys(make_settings(overrides))}


def test_content_keys_are_documented_and_namespaced() -> None:
    for settings in (make_settings(), make_settings(FULL_SETTINGS)):
        for key in k.content_keys(settings):
            assert key.feature == "checkout" and key.key.startswith("checkout.")
            assert key.group and key.doc
    full = {key.key: key for key in k.content_keys(make_settings(FULL_SETTINGS))}
    assert full[k.CART_LINE].kind is Kind.FORMAT
    assert full[k.CART_LINE].placeholders == ("name", "quantity", "unit_price", "line_total")
    assert full[k.CHANNEL_PREFIX].kind is Kind.CHANNEL_PREFIX and full[k.CHANNEL_PREFIX].required
    assert not full[k.CHANNEL_TOPIC].required
    assert full[k.option_key("delivery", "label")].required
    assert not full[k.option_key("delivery", "description")].required
    assert full[k.option_form_key("delivery", "address", "label")].required
    assert full[k.form_field_key("name", "label")].required
    assert full[k.fee_label_key("tax")].kind is Kind.FIELD_NAME
    assert full[k.FULFILLMENT_FEE_FIELD].required  # 'delivery' has a fee
    assert not full[k.BUTTON_ENTER_MORE_DETAILS].required  # 1 + 2 fields fit in one form
    assert full["checkout.payment.manual.instructions"].required


def test_conditional_requirements() -> None:
    plain = _keys()
    for key in (
        k.FULFILLMENT_TITLE,
        k.FULFILLMENT_CHOSEN_FIELD,
        k.FULFILLMENT_FEE_FIELD,
        k.DETAILS_TITLE,
        k.DETAILS_MODAL_TITLE,
        k.BUTTON_ENTER_DETAILS,
        k.BUTTON_ENTER_MORE_DETAILS,
        k.CART_MINIMUM_NOT_MET,
        k.EXPIRED_DESCRIPTION,
        k.status_title_key(m.EXPIRED),
        k.notify_description_key(m.EXPIRED),
        k.notify_description_key(k.NOTIFY_PAYMENT_REMINDER),
    ):
        assert not plain[key].required, key  # type: ignore[attr-defined]
    for key in (k.REVIEW_CREDITS_FIELD, k.BUTTON_APPLY_CREDITS, k.BUTTON_REMOVE_CREDITS, k.STAFF_BUTTON_REFUND_CREDIT):
        assert plain[key].required, key  # type: ignore[attr-defined]
    no_credits = _keys({"features": {"credits": False}})
    for key in (k.REVIEW_CREDITS_FIELD, k.BUTTON_APPLY_CREDITS, k.STAFF_BUTTON_REFUND_CREDIT):
        assert not no_credits[key].required, key  # type: ignore[attr-defined]
    timers = _keys(
        {
            "checkout": {
                "min_order_total_minor": 100,
                "session_expiry_minutes": 30,
                "payment_expiry_minutes": 60,
                "payment_reminder_minutes": 30,
            }
        }
    )
    for key in (
        k.CART_MINIMUM_NOT_MET,
        k.EXPIRED_DESCRIPTION,
        k.status_title_key(m.EXPIRED),
        k.notify_description_key(m.EXPIRED),
        k.notify_description_key(k.NOTIFY_PAYMENT_REMINDER),
    ):
        assert timers[key].required, key  # type: ignore[attr-defined]
    six_fields = deep_merge(
        FULL_SETTINGS,
        {
            "checkout": {
                "details_form": [{"id": f"f{i}", "required": False} for i in range(4)],
            }
        },
    )
    assert _keys(six_fields)[k.BUTTON_ENTER_MORE_DETAILS].required  # type: ignore[attr-defined]
    no_provider = _keys({"payments": {"provider": ""}})
    assert "checkout.payment.manual.instructions" not in no_provider


def test_shared_keys() -> None:
    assert MODULE.shared_keys(make_settings()) == [
        core.MONEY_FORMAT,
        core.BUTTON_BACK,
        core.BUTTON_CONTINUE,
        core.BUTTON_CANCEL,
        core.BUTTON_CONFIRM,
        core.BUTTON_PREV,
        core.BUTTON_NEXT,
    ]
    assert MODULE.depends_on == ("catalog",)
    assert MODULE.panel_prefixes == ("checkout:panel",)


def test_settings_problems() -> None:
    assert k.settings_problems(make_settings()) == []
    assert k.settings_problems(make_settings(FULL_SETTINGS)) == []
    problems = k.settings_problems(make_settings({"checkout": {"currency": ""}, "payments": {"provider": ""}}))
    assert "settings.checkout.currency is not set (business decision)" in problems
    assert "settings.payments.provider is not set (business decision)" in problems
    unknown = k.settings_problems(make_settings({"payments": {"provider": "acme"}}))
    assert any("'acme' is not a supported payment provider" in p for p in unknown)
    refs = k.settings_problems(
        make_settings(
            {
                "checkout": {
                    "panel_channel": "nope",
                    "staff_channel": "",
                    "order_category": "missing",
                    "staff_roles": ["ghost"],
                }
            }
        )
    )
    assert "checkout.panel_channel: channel 'nope' is not defined in server.channels" in refs
    assert any(p.startswith("checkout.staff_channel: required") for p in refs)
    assert "checkout.order_category: category 'missing' is not defined in server.categories" in refs
    assert "role 'ghost' is not defined in server.roles" in refs
    fees = k.settings_problems(
        make_settings(
            {
                "checkout": {
                    "fees": [
                        {"id": "a", "kind": "percent", "basis_points": 0},
                        {"id": "b", "kind": "flat", "amount_minor": 0},
                    ]
                }
            }
        )
    )
    assert any("'a': a percent fee needs basis_points > 0" in p for p in fees)
    assert any("'b': a flat fee needs amount_minor > 0" in p for p in fees)
    timers = k.settings_problems(
        make_settings({"checkout": {"payment_reminder_minutes": 60, "payment_expiry_minutes": 60}})
    )
    assert any(p.startswith("checkout.payment_reminder_minutes") for p in timers)
    assert k.settings_problems(make_settings({"checkout": {"payment_reminder_minutes": 60}})) == []


async def test_readiness(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path / "a")
    try:
        assert tenv.app.ready("checkout")
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "b", settings=make_settings({"payments": {"provider": ""}}))
    try:
        status = tenv.app.readiness.statuses["checkout"]
        assert not status.ready and any("payments.provider" in p.message for p in status.problems)
        assert not tenv.app.ready("credits")  # credits depend on checkout
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "c", settings=make_settings({"features": {"catalog": False}}))
    try:
        status = tenv.app.readiness.statuses["checkout"]
        assert any(p.kind == "dependency" and "catalog" in p.message for p in status.problems)
    finally:
        await tenv.close()
    tenv = await make_test_env(tmp_path / "d", content={k.BUTTON_SUBMIT: ""})
    try:
        status = tenv.app.readiness.statuses["checkout"]
        assert not status.ready and any(p.key == k.BUTTON_SUBMIT for p in status.problems)
    finally:
        await tenv.close()


async def test_optional_keys_do_not_block_readiness(tmp_path: Path) -> None:
    optional = [key.key for key in k.content_keys(make_settings()) if not key.required]
    tenv = await make_test_env(tmp_path, content=dict.fromkeys(optional, ""))
    try:
        assert tenv.app.ready("checkout"), tenv.app.readiness.statuses["checkout"].problems
    finally:
        await tenv.close()


def test_operator_text_keys_are_prefixed_and_all_used() -> None:
    assert all(key.startswith("checkout.") for key in OPERATOR_TEXT)
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py") if path.name != "text.py")
    used = set(re.findall(r'"(checkout\.(?:op|refund|list|lookup|publish|amount)[a-z_.]*)"', source))
    assert used <= set(OPERATOR_TEXT), used - set(OPERATOR_TEXT)
    assert set(OPERATOR_TEXT) <= used, set(OPERATOR_TEXT) - used


def test_every_content_key_constant_is_declared() -> None:
    files = [PACKAGE / "content_keys.py", *(PACKAGE / "payments").glob("*.py")]
    source = "\n".join(path.read_text(encoding="utf-8") for path in files)
    constants = set(re.findall(r'= "(checkout\.[a-z_]+\.[a-z_.]+)"', source))
    assert len(constants) > 50
    declared = {key.key for key in k.content_keys(make_settings())}
    assert constants <= declared, constants - declared


def _check_states(tenv: TestEnv) -> dict[str, MessageSpec | ModalSpec]:
    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    names = [s.name for s in states]
    assert len(names) == len(set(names))
    for state in states:
        assert state.feature == "checkout" and state.note
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert validate_message(state.spec) == [], state.name
        assert unrouted_components(state, tenv.app.router) == [], state.name
    return {s.name: s.spec for s in states}


async def test_every_preview_state_passes_limits(tenv: TestEnv) -> None:
    states = _check_states(tenv)
    expected = {
        "shop_panel",
        "reply_started",
        "reply_session_exists",
        "reply_limit_reached",
        "workflow_browse_empty_cart",
        "workflow_browse_items_page_2",
        "workflow_browse_empty_catalog",
        "workflow_cart",
        "workflow_cart_full",
        "workflow_cart_empty",
        "workflow_cart_minimum_not_met",
        "workflow_review",
        "workflow_review_credits_applied",
        "workflow_review_price_changed",
        "workflow_cancel_confirm",
        "workflow_cancelled",
        "workflow_expired",
        "quantity_form_new",
        "quantity_form_edit",
        "order_submitted",
        "order_cancel_confirm",
        *(f"order_{s}" for s in m.STATUS_VIEWS),
        *(f"staff_{s}" for s in m.ORDER_STATUSES),
        "staff_paid_partially_refunded",
        *(f"notify_{s}" for s in k.NOTIFIED_STATUSES),
        "notify_payment_reminder",
        "notify_refund",
        "error_session_closed",
        "error_out_of_stock",
        "error_quantity_invalid",
        "error_cart_full",
        "error_product_unavailable",
        "error_order_not_cancellable",
        "error_minimum_not_met",
        "staff_refund_form",
        "staff_refund_credit_form",
        "staff_order_list",
    }
    assert expected <= set(states), expected - set(states)


async def test_full_workflow_preview_states(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, FULL_SETTINGS)
    try:
        states = _check_states(tenv)
        for name in (
            "workflow_fulfillment_unchosen",
            "workflow_fulfillment_chosen",
            "workflow_details_empty",
            "workflow_details_answered",
            "details_form_1",
            "staff_details_1",
        ):
            assert name in states, name
        review = states["workflow_review"]
        assert isinstance(review, MessageSpec)
        names = [f.name for f in review.embeds[0].fields]
        assert tenv.text(k.fee_label_key("service")) in names and tenv.text(k.fee_label_key("tax")) in names
        assert tenv.text(k.FULFILLMENT_FEE_FIELD) in names
    finally:
        await tenv.close()


async def test_preview_without_content_has_only_operator_states(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert MODULE.preview is not None
        names = {s.name for s in MODULE.preview(tenv.app)}
        assert names == {"staff_refund_form", "staff_refund_credit_form", "staff_order_list"}
    finally:
        await tenv.close()


async def test_preview_sample_data_is_synthetic(tenv: TestEnv) -> None:
    assert MODULE.preview is not None
    states = {s.name: s.spec for s in MODULE.preview(tenv.app)}
    cart = states["workflow_cart"]
    assert isinstance(cart, MessageSpec)
    assert "sample product 1" in cart.embeds[0].fields[0].value


async def test_panel_spec_and_publish(co: TestEnv) -> None:
    spec = panel.panel_spec(co.app)
    assert spec.embeds[0].title == co.text(k.PANEL_TITLE)
    assert spec.embeds[0].description == co.text(k.PANEL_DESCRIPTION)
    buttons = [c for row in spec.rows for c in row if isinstance(c, ButtonSpec)]
    assert [b.custom_id for b in buttons] == [views.start_id(), "sb:ct:browse:0", "sb:cr:balance"]
    assert buttons[0].label == co.text(k.BUTTON_START)
    assert buttons[1].label == co.text(catalog_keys.BUTTON_BROWSE)

    lines = await panel.publish(co.app)
    shop = channel(co, "shop")
    assert len(shop.messages) == 1
    assert lines[0] == co.app.op("checkout.publish.panel", action="created", channel=shop.mention)
    again = await panel.publish(co.app)
    assert again[0] == co.app.op("checkout.publish.panel", action="unchanged", channel=shop.mention)
    assert len(shop.messages) == 1


async def test_panel_omits_optional_buttons(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"catalog": {"browse_enabled": False}, "features": {"credits": False}})
    try:
        spec = panel.panel_spec(tenv.app)
        ids = [c.custom_id for row in spec.rows for c in row]
        assert ids == [views.start_id()]
    finally:
        await tenv.close()
    tenv = await make_env(
        tmp_path / "b", content={catalog_keys.BUTTON_BROWSE: ""}, overrides={"catalog": {"listing_channel": ""}}
    )
    try:
        ids = [c.custom_id for row in panel.panel_spec(tenv.app).rows for c in row]
        assert "sb:ct:browse:0" not in ids
    finally:
        await tenv.close()


async def test_publish_reports_missing_channel(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)  # channels not provisioned
    try:
        lines = await panel.publish(tenv.app)
        assert lines == [tenv.app.op("checkout.publish.no_channel", name="shop")]
    finally:
        await tenv.close()


async def test_panel_text_is_exact(co: TestEnv) -> None:
    started = views.started_reply(co.app.ui, 42)
    assert started.embeds[0].description == render(co, k.STARTED, channel_mention="<#42>")
