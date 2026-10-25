"""Settings, readiness, content keys and preview states of the operations package (alerts, reminders, the live order
record, staff forms, staff notifications)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shopbot.config.settings import SettingsError, parse_settings
from shopbot.features import all_modules
from shopbot.features.checkout import MODULE as CHECKOUT
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys, staff_preview, staff_views
from shopbot.features.operations import MODULE as OPERATIONS
from shopbot.features.operations import content_keys as ok
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState
from tests.helpers import (
    TestEnv,
    base_settings_dict,
    deep_merge,
    make_settings,
    make_test_env,
    shipped_settings_dict,
)
from tools.check_limits import unrouted_components


def test_operations_settings_parse_with_defaults_and_values() -> None:
    settings = make_settings()
    o = settings.operations
    assert (o.staff_channel, o.ping_roles, o.owner_dm_alerts) == ("", (), False)
    assert not o.reminders_enabled()
    assert (settings.checkout.customer_role, settings.checkout.customer_role_on) == ("", "fulfilled")
    assert settings.checkout.customer_role_remove_on_refund is True
    custom = make_settings(
        {
            "operations": {
                "staff_channel": "staff_alerts",
                "ping_roles": ["staff"],
                "owner_dm_alerts": True,
                "unclaimed_order_minutes": 30,
                "reminder_repeat_minutes": 60,
            },
            "checkout": {"customer_role_on": "paid", "customer_role_remove_on_refund": False},
        }
    )
    assert custom.operations.unclaimed_order_minutes == 30 and custom.operations.reminders_enabled()
    assert custom.checkout.customer_role_on == "paid" and not custom.checkout.customer_role_remove_on_refund


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"operations": {"staff_channel": "Not A Key"}}, "operations.staff_channel: must match"),
        ({"operations": {"ping_roles": "staff"}}, "operations.ping_roles: expected a list of strings"),
        ({"operations": {"owner_dm_alerts": "yes"}}, "operations.owner_dm_alerts: expected true or false"),
        ({"operations": {"unpaid_order_minutes": -1}}, "operations.unpaid_order_minutes: must be >= 0"),
        ({"operations": {"reminder_repeat_minutes": 10**7}}, "operations.reminder_repeat_minutes: must be <= 525600"),
        ({"operations": {"unknown": 1}}, "operations.unknown: unknown setting"),
        ({"checkout": {"customer_role_on": "submitted"}}, "checkout.customer_role_on: must be one of"),
        ({"checkout": {"customer_role_remove_on_refund": 1}}, "checkout.customer_role_remove_on_refund: expected"),
    ],
)
def test_invalid_operations_settings_are_rejected(override: dict[str, object], message: str) -> None:
    with pytest.raises(SettingsError) as raised:
        parse_settings(deep_merge(base_settings_dict(), override))
    assert any(error.startswith(message) for error in raised.value.errors), raised.value.errors


def test_settings_problems_name_every_broken_reference() -> None:
    problems = OPERATIONS.settings_problems(make_settings())
    assert problems == ["operations.staff_channel: required (staff alerts and reminders are posted there)"]
    problems = OPERATIONS.settings_problems(
        make_settings(
            {
                "operations": {"staff_channel": "shop", "ping_roles": ["ghost"]},
                "checkout": {"customer_role": "nobody"},
            }
        )
    )
    assert "role 'ghost' is not defined in server.roles" in problems
    assert "checkout.customer_role: role 'nobody' is not defined in server.roles" in problems
    assert any("operations.staff_channel: channel 'shop' must have staff or admin access" in p for p in problems)
    missing = OPERATIONS.settings_problems(make_settings({"operations": {"staff_channel": "nowhere"}}))
    assert "operations.staff_channel: channel 'nowhere' is not defined in server.channels" in missing


def test_shipped_configuration_is_valid_for_operations() -> None:
    settings = parse_settings(shipped_settings_dict())
    o = settings.operations
    assert (o.staff_channel, o.ping_roles, o.owner_dm_alerts) == ("staff_chat", ("staff",), True)
    assert (o.unclaimed_order_minutes, o.unpaid_order_minutes, o.unfulfilled_order_minutes) == (60, 1440, 2880)
    assert o.unanswered_ticket_minutes == 240 and o.reminders_enabled()
    assert (settings.checkout.customer_role, settings.checkout.customer_role_on) == ("customer", "fulfilled")
    assert OPERATIONS.settings_problems(settings) == []
    assert OPERATIONS.is_enabled(settings)


def test_operations_is_enabled_with_checkout_or_tickets_and_is_registered_last() -> None:
    assert not OPERATIONS.is_enabled(make_settings(all_features=False))
    assert OPERATIONS.is_enabled(make_settings({"features": {"tickets": True}}, all_features=False))
    names = [module.name for module in all_modules()]
    assert names[-1] == "operations" and names.index("checkout") < names.index("operations")
    assert not OPERATIONS.customer_facing


def test_reminder_keys_are_required_only_with_their_threshold() -> None:
    keys = {key.key: key for key in OPERATIONS.content_keys(make_settings())}
    assert not keys[ok.reminder_key("unpaid", "description")].required
    assert keys[ok.alert_key(ok.NEW_ORDER, "description")].required
    assert not keys[ok.alert_key(ok.NEW_ORDER, "title")].required
    owner = keys[ok.owner_key(ok.NEW_ORDER, "description")]
    assert owner.fallback == ok.alert_key(ok.NEW_ORDER, "description") and not owner.required
    on = {key.key: key for key in OPERATIONS.content_keys(make_settings({"operations": {"unpaid_order_minutes": 5}}))}
    assert on[ok.reminder_key("unpaid", "description")].required


# -- preview states ----------------------------------------------------------------------------------------------


def _check(tenv: TestEnv, states: list[PreviewState]) -> dict[str, MessageSpec | ModalSpec]:
    for state in states:
        spec = state.spec
        violations = validate_modal(spec) if isinstance(spec, ModalSpec) else validate_message(spec)
        assert violations == [], (state.name, violations)
        assert unrouted_components(state, tenv.app.router) == [], state.name
    return {state.name: state.spec for state in states}


async def test_every_operations_preview_state_is_within_limits(tmp_path: Path) -> None:
    tenv = await make_test_env(
        tmp_path, settings=make_settings({"operations": {"staff_channel": "staff_alerts", "ping_roles": ["staff"]}})
    )
    try:
        assert OPERATIONS.preview is not None
        states = _check(tenv, OPERATIONS.preview(tenv.app))
        expected = {
            *(f"alert_{a}" for a in ok.ORDER_ALERTS),
            *(f"owner_{a}" for a in ok.ORDER_ALERTS),
            *(f"reminder_{kind}" for kind in ("unclaimed", "unpaid", "unfulfilled", "unanswered")),
            *(f"failure_{f}" for f in ok.FAILURES),
            "owner_failure_task",
        }
        assert set(states) == expected
        sample = states["alert_new_order"]
        assert isinstance(sample, MessageSpec) and sample.mention_role_ids and sample.rows[0][0].style == "link"
    finally:
        await tenv.close()


async def test_every_staff_record_preview_state_is_within_limits_and_routed(tenv: TestEnv) -> None:
    assert CHECKOUT.preview is not None
    states = _check(tenv, CHECKOUT.preview(tenv.app))
    for name in (
        *(f"staff_{s}" for s in m.ORDER_STATUSES),
        "staff_pending_payment_claimed",
        "staff_paid_claimed",
        "staff_processing_claimed",
        "staff_paid_tracking",
        "staff_fulfilled_long_history",
        "staff_note_form",
        "staff_correction_form",
        "staff_reject_form",
        "staff_tracking_form",
        "staff_record_details_1",
        "notify_rejected_with_reason",
        "notify_fulfilled_with_tracking",
        "notify_correction",
        "notify_tracking",
        "staff_order_list",
    ):
        assert name in states, name


async def test_operations_preview_without_content_is_empty(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, fill_content=False)
    try:
        assert OPERATIONS.preview is not None
        assert OPERATIONS.preview(tenv.app) == []
    finally:
        await tenv.close()


async def test_the_record_leaves_out_the_oldest_notes_when_discord_limits_are_reached(tenv: TestEnv) -> None:
    """Notes full of markdown characters (escaping doubles them): the oldest are left out, configured text stays."""
    settings, ui = tenv.app.settings, tenv.app.ui
    from shopbot.features.checkout import preview as checkout_preview

    view = checkout_preview._order(settings, m.PAID, checkout_preview._largest_cart(settings)[:4])
    data = staff_preview.sample_data(view.order, claimed=True, claims=15)  # a full timeline
    notes = tuple(
        h.Activity(100 + i, view.order.id, h.NOTE, 1, f"<{i}>" + "*" * 475, 1_700_000_000 + i) for i in range(5)
    )
    data = h.StaffData(handling=data.handling, events=data.events, activity=(*data.activity, *notes))
    [spec, *extra] = staff_views.staff_messages(
        ui, settings, view, data, staff_confirms_payment=True, credit_refunds=True, tracking=True
    )
    assert validate_message(spec) == [] and extra  # the order lines moved to their own message(s)
    assert all(validate_message(part) == [] for part in extra)
    names = [f.name for e in spec.embeds for f in e.fields]
    notes_name = tenv.text(staff_keys.NOTES_FIELD)
    listed = "\n".join(f.value for e in spec.embeds for f in e.fields if f.name == notes_name)
    kept = [i for i in range(5) if f"<{i}>" in listed]
    assert kept and kept == list(range(5 - len(kept), 5)) and len(kept) < 5  # the oldest notes were left out
    assert tenv.text(staff_keys.TIMELINE_FIELD) in names
    assert tenv.text(staff_keys.REFERENCE_FIELD) in names and tenv.text(staff_keys.STATUS_FIELD) in names
