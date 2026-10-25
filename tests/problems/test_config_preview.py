"""Settings parsing and checks, content keys (requiredness, shared keys, cross-key rules), readiness, /setup
diagnostics, and the preview states (within Discord's limits, every component routed, synthetic sample data)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.content import ContentStore
from shopbot.config.loader import load_config
from shopbot.config.readiness import build_registry, compute_readiness
from shopbot.config.settings import SettingsError, parse_settings
from shopbot.features import all_modules
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.problems import MODULE
from shopbot.features.problems import content_keys as k
from shopbot.features.tickets import content_keys as tk
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.helpers import ROOT, TestEnv, base_settings_dict, deep_merge, fixture_content, make_settings
from tests.problems.util import MULTI_TYPES, PROBLEM_SETTINGS, make_env
from tools.check_limits import unrouted_components


def _settings(overrides: dict[str, Any] | None = None) -> Any:
    return make_settings(deep_merge(PROBLEM_SETTINGS, overrides or {}))


def _required(overrides: dict[str, Any] | None = None) -> dict[str, bool]:
    return {key.key: key.required for key in k.content_keys(_settings(overrides))}


def test_settings_are_parsed() -> None:
    p = _settings(MULTI_TYPES).problems
    assert p.discord_category == "tickets"
    assert p.eligible_statuses == ("paid", "processing", "fulfilled")
    assert [t.id for t in p.types] == ["general", "billing", "other"]
    assert [f.id for f in p.types[1].form] == ["what", "extra"]
    assert p.type("other") is not None and p.type("nope") is None
    defaults = parse_settings(base_settings_dict()).problems
    assert defaults.max_open_per_user == 1 and defaults.require_reason_on_reject and defaults.allow_without_order
    assert not defaults.ping_staff and defaults.types == ()


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"problems": {"colour": "x"}}, "problems.colour: unknown setting"),
        ({"problems": {"max_open_per_user": 0}}, "problems.max_open_per_user: must be >= 1"),
        ({"problems": {"discord_category": "Not A Key"}}, "problems.discord_category: must match"),
        ({"problems": {"eligible_statuses": ["paid", "paid"]}}, "problems.eligible_statuses: contains duplicates"),
        ({"problems": {"types": [{"id": "a"}, {"id": "a"}]}}, "problems.types: duplicate id 'a'"),
        ({"problems": {"types": [{"form": []}]}}, "problems.types[0].id: is required"),
        ({"problems": {"types": [{"id": "a", "label": "x"}]}}, "problems.types[0].label: unknown setting"),
        (
            {"problems": {"types": [{"id": "a", "form": [{"id": f"f{i}"} for i in range(6)]}]}},
            "at most 5 fields fit in one Discord form",
        ),
        ({"problems": {"types": [{"id": f"t{i}"} for i in range(26)]}}, "at most 25 issue types"),
    ],
)
def test_settings_errors(override: dict[str, Any], fragment: str) -> None:
    with pytest.raises(SettingsError) as exc:
        parse_settings(deep_merge(base_settings_dict(), override))
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"problems": {"discord_category": ""}}, "problems.discord_category: required"),
        ({"problems": {"discord_category": "nope"}}, "problems.discord_category: category 'nope'"),
        ({"problems": {"types": []}}, "problems.types: define at least one issue type"),
        ({"problems": {"eligible_statuses": ["shipped"]}}, "unknown order statuses ['shipped']"),
        ({"problems": {"staff_roles": ["ghost"]}}, "role 'ghost' is not defined"),
        (
            {"problems": {"eligible_statuses": [], "allow_without_order": False}},
            "no report could ever be made",
        ),
        ({"tickets": {"staff_alert_channel": "catalog"}}, "tickets.staff_alert_channel: channel 'catalog' must have"),
    ],
)
def test_settings_problems(override: dict[str, Any], fragment: str) -> None:
    problems = MODULE.settings_problems(_settings(override))
    assert any(fragment in p for p in problems), problems


def test_test_and_shipped_settings_have_no_problems() -> None:
    assert MODULE.settings_problems(_settings()) == []
    assert MODULE.settings_problems(_settings(MULTI_TYPES)) == []
    shipped = load_config(ROOT / "config").settings
    assert MODULE.settings_problems(shipped) == []
    assert shipped.problems.discord_category == "purchase"
    assert [t.id for t in shipped.problems.types] == ["general"]
    assert [f.id for f in shipped.problems.types[0].form] == ["details"]
    assert shipped.tickets.panel_channel == "" and shipped.tickets.categories == ()


def test_conditional_requiredness() -> None:
    base = _required()
    assert base[k.CHOOSE_DESCRIPTION] and base[k.ORDER_PLACEHOLDER] and base[k.ORDER_OPTION]
    assert base[k.NO_ORDER_OPTION] and not base[k.NO_ORDERS]
    assert not base[k.TYPE_PLACEHOLDER]  # one issue type: no type menu
    assert base[k.FIELD_ORDER] and base[k.ORDER_VALUE]
    assert base[k.STAFF_ALERT_TITLE] and base[k.STAFF_ALERT_DESCRIPTION]
    assert base[k.type_key("general", "label")] and base[k.type_key("general", "form_title")]
    assert base[k.form_field_key("general", "details", "label")]
    for optional in (
        k.CHOOSE_TITLE,
        k.ORDER_OPTION_DESCRIPTION,
        k.NO_ORDER_OPTION_DESCRIPTION,
        k.BUTTON_REPORT_EMOJI,
        k.CHANNEL_TOPIC,
        k.FIELD_DECIDED_BY,
        k.notify_key(k.RESOLVED, "title"),
        k.type_key("general", "description"),
        k.form_field_key("general", "details", "placeholder"),
    ):
        assert not base[optional], optional
    for status in k.STATUSES:
        assert base[k.status_key(status)]

    multi = _required(MULTI_TYPES)
    assert multi[k.TYPE_PLACEHOLDER]
    assert k.type_key("other", "form_title") not in multi  # the type has no form

    no_orders = _required({"problems": {"eligible_statuses": []}})
    assert not no_orders[k.ORDER_OPTION] and not no_orders[k.FIELD_ORDER] and not no_orders[k.NO_ORDER_OPTION]
    assert not no_orders[k.CHOOSE_DESCRIPTION]  # nothing to choose ever: the form opens right away

    strict = _required({"problems": {"allow_without_order": False}})
    assert strict[k.NO_ORDERS] and not strict[k.NO_ORDER_OPTION]

    no_alerts = _required({"tickets": {"staff_alert_channel": ""}})
    assert not no_alerts[k.STAFF_ALERT_TITLE] and not no_alerts[k.STAFF_ALERT_DESCRIPTION]


def test_shared_keys() -> None:
    shared = MODULE.shared_keys(_settings())
    assert tk.BUTTON_CLOSE in shared and core.BUTTON_CONTINUE in shared and core.MONEY_FORMAT in shared
    assert checkout_keys.status_label_key("fulfilled") in shared
    plain = MODULE.shared_keys(_settings({"problems": {"eligible_statuses": []}}))
    assert plain == [tk.BUTTON_CLOSE]


def test_channel_names_must_fit() -> None:
    settings = _settings({"tickets": {"channel_name_pattern": "{prefix}-{number}-{user}"}})
    registry = build_registry(settings, all_modules())
    values = fixture_content(registry)
    store = ContentStore(registry, values)
    assert MODULE.content_problems(settings, store) == []
    values[k.CHANNEL_PREFIX] = "p" * 60
    problems = MODULE.content_problems(settings, ContentStore(registry, values))
    assert problems and "problem-report channel names can reach" in problems[0]


def test_readiness_needs_tickets_checkout_and_every_required_key() -> None:
    settings = _settings()
    modules = all_modules()
    registry = build_registry(settings, modules)
    values = fixture_content(registry)
    assert compute_readiness(settings, ContentStore(registry, values), modules).is_ready(k.FEATURE)
    for key in (k.BUTTON_REPORT, k.ATTACHMENTS_NOTE, k.status_key(k.REJECTED), tk.BUTTON_CLOSE):
        missing = {name: value for name, value in values.items() if name != key}
        readiness = compute_readiness(settings, ContentStore(registry, missing), modules)
        assert not readiness.is_ready(k.FEATURE), key
    no_tickets = make_settings(deep_merge(PROBLEM_SETTINGS, {"features": {"tickets": False}}))
    registry = build_registry(no_tickets, modules)
    readiness = compute_readiness(no_tickets, ContentStore(registry, fixture_content(registry)), modules)
    assert not readiness.is_ready(k.FEATURE)
    assert any("requires the 'tickets' feature" in p.message for p in readiness.statuses[k.FEATURE].problems)


def test_shipped_configuration_keeps_problems_hidden_until_text_exists() -> None:
    loaded = load_config(ROOT / "config")
    modules = all_modules()
    registry = build_registry(loaded.settings, modules)
    readiness = compute_readiness(loaded.settings, ContentStore(registry, loaded.content_values), modules)
    status = readiness.statuses[k.FEATURE]
    assert status.enabled and not status.ready
    assert {p.kind for p in status.problems} <= {"missing_content", "dependency"}


async def test_setup_diagnostics(pr: TestEnv) -> None:
    assert MODULE.publish is not None
    lines = await MODULE.publish(pr.app)
    assert lines == [pr.app.op("problems.publish.ready")]
    await pr.app.resources.forget("category", "tickets")
    lines = await MODULE.publish(pr.app)
    assert pr.app.op("problems.publish.category_missing", name="tickets") in lines


async def test_setup_diagnostics_without_alert_channel_and_unpingable_roles(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"staff_alert_channel": ""}, "problems": {"ping_staff": True}})
    try:
        assert MODULE.publish is not None
        lines = await MODULE.publish(tenv.app)
        assert tenv.app.op("problems.publish.no_alert_channel") in lines
        assert tenv.app.op("problems.publish.ping_not_mentionable", roles=tenv.staff_role.mention) in lines
        tenv.staff_role.mentionable = True
        assert not any("ping_staff" in line for line in await MODULE.publish(tenv.app))
    finally:
        await tenv.close()


def _assert_valid(states: list[Any], router: Any) -> None:
    for state in states:
        assert unrouted_components(state, router) == [], state.name
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name


async def test_preview_states_within_limits_and_routed(pr: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(pr.app)
    names = {s.name for s in states}
    assert {
        "purchase_panel",
        "choose",
        "choose.complete",
        "form.general",
        "created",
        "limit_reached",
        "ticket.open",
        "ticket.open_without_order",
        "ticket.resolved",
        "ticket.rejected",
        "details.general",
        "details.general.longest.1",
        "notify.resolved",
        "notify.rejected",
        "staff_alert",
        "staff.resolve_form",
        "staff.reject_form",
        "staff.decided",
        "staff.already_decided",
    } <= names
    _assert_valid(states, pr.app.router)
    choose = next(s.spec for s in states if s.name == "choose")
    assert isinstance(choose, MessageSpec)
    menu = choose.rows[0][0]
    assert len(menu.options) == 25  # type: ignore[union-attr]
    panel = next(s.spec for s in states if s.name == "purchase_panel")
    assert isinstance(panel, MessageSpec)
    assert "sb:pr:start" in [c.custom_id for row in panel.rows for c in row]


async def test_preview_with_every_optional_setting(tmp_path: Path) -> None:
    overrides = deep_merge(
        MULTI_TYPES,
        {
            "problems": {"allow_without_order": False, "ping_staff": True},
            "tickets": {"staff_alert_channel": ""},
        },
    )
    tenv = await make_env(tmp_path, overrides)
    try:
        assert MODULE.preview is not None
        states = MODULE.preview(tenv.app)
        names = {s.name for s in states}
        assert {"no_orders", "form.general", "form.billing", "details.billing"} <= names
        assert "form.other" not in names and "staff_alert" in names
        # The longest answers of the billing form (4000 characters) continue in further messages; nothing is cut.
        longest = [s for s in states if s.name.startswith("details.billing.longest.")]
        values = "".join(f.value for s in longest for e in s.spec.embeds for f in e.fields)  # type: ignore[union-attr]
        assert values.count("[sample answer]") > 100
        _assert_valid(states, tenv.app.router)
    finally:
        await tenv.close()


async def test_preview_sample_data_is_synthetic(pr: TestEnv) -> None:
    assert MODULE.preview is not None
    states = {s.name: s.spec for s in MODULE.preview(pr.app)}
    details = states["details.general"]
    assert isinstance(details, MessageSpec)
    assert "[sample answer]" in details.embeds[0].fields[0].value
    open_panel = states["ticket.open"]
    assert isinstance(open_panel, MessageSpec)
    assert "sample member" in (open_panel.embeds[0].title or "")
    assert all(str(i) not in str(open_panel) for i in (111_111_111_111_111_111, 222_222_222_222_222_222))
