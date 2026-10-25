from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shopbot.config.settings import parse_settings
from shopbot.features.tickets import MODULE
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets.panel import PANEL_KEY
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import MessageSpec, ModalSpec
from tests.helpers import TestEnv, deep_merge, make_settings, shipped_settings_dict
from tests.tickets.util import channel, form_overrides, make_env


def _required(settings_overrides: dict[str, Any]) -> dict[str, bool]:
    settings = make_settings(settings_overrides)
    return {key.key: key.required for key in k.content_keys(settings)}


def test_conditional_requiredness() -> None:
    base = _required({})
    assert base[k.PANEL_TITLE] and base[k.PANEL_DESCRIPTION]
    assert not base[k.PANEL_SELECT_PLACEHOLDER]
    assert base[k.CLOSE_CONFIRM_DESCRIPTION] and not base[k.CLOSE_CONFIRM_TITLE]
    assert base[k.BUTTON_REOPEN] and base[k.REOPENED]
    assert base[k.BUTTON_TRANSCRIPT] and base[k.TRANSCRIPT_DESCRIPTION]
    assert base[k.STAFF_ALERT_TITLE] and base[k.STAFF_ALERT_DESCRIPTION]
    assert not base[k.TRANSCRIPT_DM_DESCRIPTION]
    assert not base[k.DELETION_SCHEDULED] and not base[k.AUTO_CLOSED] and not base[k.AUTO_CLOSE_WARNING]
    assert base[k.category_key("general", "button_label")] and not base[k.category_key("general", "button_emoji")]
    assert k.category_key("general", "form_title") not in base

    changed = _required(
        {
            "tickets": {
                "panel_style": "select",
                "close_confirmation": False,
                "allow_reopen": False,
                "transcript_channel": "",
                "staff_alert_channel": "",
                "delete_after_close_minutes": 5,
                "auto_close_inactive_minutes": 60,
                "auto_close_warning_minutes": 10,
            }
        }
    )
    assert changed[k.PANEL_SELECT_PLACEHOLDER]
    assert not changed[k.CLOSE_CONFIRM_DESCRIPTION]
    assert not changed[k.BUTTON_REOPEN] and not changed[k.REOPENED]
    assert not changed[k.BUTTON_TRANSCRIPT] and not changed[k.TRANSCRIPT_DESCRIPTION]
    assert not changed[k.STAFF_ALERT_DESCRIPTION]
    assert changed[k.DELETION_SCHEDULED] and changed[k.AUTO_CLOSED] and changed[k.AUTO_CLOSE_WARNING]

    form = _required(form_overrides())
    assert form[k.category_key("billing", "form_title")]
    assert form[k.form_field_key("billing", "details", "label")]
    assert not form[k.form_field_key("billing", "details", "placeholder")]
    assert not form[k.form_field_key("billing", "details", "description")]
    assert MODULE.shared_keys(make_settings()) == ["common.button.confirm", "common.button.cancel"]


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"tickets": {"panel_channel": ""}}, "tickets.panel_channel: required"),
        ({"tickets": {"transcript_channel": "nope"}}, "tickets.transcript_channel: channel 'nope'"),
        ({"tickets": {"staff_alert_channel": "nope"}}, "tickets.staff_alert_channel: channel 'nope'"),
        ({"tickets": {"closed_category": "nope"}}, "tickets.closed_category: category 'nope'"),
        ({"tickets": {"staff_roles": ["ghost"]}}, "role 'ghost' is not defined"),
        ({"tickets": {"categories": []}}, "at least one ticket category"),
        (
            {"tickets": {"categories": [{"id": "general", "discord_category": "nope"}]}},
            "discord_category: category 'nope'",
        ),
        (
            {"tickets": {"categories": [{"id": "general", "staff_roles": ["ghost"]}]}},
            "role 'ghost' is not defined",
        ),
        (
            {"tickets": {"auto_close_inactive_minutes": 10, "auto_close_warning_minutes": 10}},
            "auto_close_warning_minutes: must be less than",
        ),
    ],
)
def test_settings_problems(override: dict[str, Any], fragment: str) -> None:
    settings = parse_settings(deep_merge(shipped_settings_dict(), override))
    problems = MODULE.settings_problems(settings)
    assert any(fragment in p for p in problems), problems


def test_shipped_settings_have_no_ticket_problems() -> None:
    assert MODULE.settings_problems(make_settings()) == []
    assert MODULE.settings_problems(make_settings(form_overrides())) == []


def _assert_valid(states: list[Any]) -> None:
    for state in states:
        if isinstance(state.spec, ModalSpec):
            assert validate_modal(state.spec) == [], state.name
        else:
            assert isinstance(state.spec, MessageSpec)
            assert validate_message(state.spec) == [], state.name


async def test_preview_states_pass_limits(tk: TestEnv) -> None:
    assert MODULE.preview is not None
    states = MODULE.preview(tk.app)
    names = {s.name for s in states}
    expected = {
        "panel.buttons",
        "panel.select",
        "welcome.general",
        "controls.claimed",
        "close_confirm",
        "close_confirm.answered",
        "delete_confirm",
        "closed",
        "closed.no_transcripts",
        "closed.with_reason",
        "auto_closed",
        "claimed",
        "unclaimed",
        "reopened",
        "deletion_scheduled",
        "auto_close_warning",
        "user_added",
        "user_removed",
        "staff_alert",
        "transcript",
        "transcript_dm",
        "reply.created",
        "reply.limit_reached",
        "reply.not_open",
    }
    assert expected <= names
    assert len(names) == len(states)
    _assert_valid(states)


async def test_preview_with_forms_and_maximum_answers(tmp_path: Path) -> None:
    overrides = form_overrides({"tickets": {"panel_style": "select"}})
    big_form = [{"id": f"part{i}", "style": "paragraph", "max_length": 4000} for i in range(3)]
    overrides["tickets"]["categories"][1]["form"] = big_form
    tenv = await make_env(tmp_path, overrides)
    try:
        assert MODULE.preview is not None
        states = MODULE.preview(tenv.app)
        names = {s.name for s in states}
        assert {"form.billing", "welcome.billing", "welcome.billing.max_answers.1"} <= names
        assert "welcome.billing.max_answers.2" in names  # 12000 characters of answers need a second message
        _assert_valid(states)
        welcome = next(s for s in states if s.name == "welcome.billing")
        assert isinstance(welcome.spec, MessageSpec)
        assert "[sample answer 1]" in [f.value for f in welcome.spec.embeds[0].fields]
    finally:
        await tenv.close()


async def test_preview_skips_states_without_content(tk: TestEnv) -> None:
    values = dict(tk.content)
    del values[k.CLAIMED]
    tk.app.content.replace(file_values=values)
    assert MODULE.preview is not None
    names = {s.name for s in MODULE.preview(tk.app)}
    assert "claimed" not in names and "unclaimed" in names


async def test_publish_buttons_panel_is_idempotent(tkf: TestEnv) -> None:
    assert MODULE.publish is not None
    first = await MODULE.publish(tkf.app)
    panel_channel = channel(tkf, "ticket_panel")
    assert len(panel_channel.messages) == 1
    message = panel_channel.messages[0]
    assert message.embeds[0].title == tkf.text(k.PANEL_TITLE)
    assert message.custom_ids() == [cid("tk", "open", "general"), cid("tk", "open", "billing")]
    labels = [getattr(i, "item", i).label for i in message.components()]
    assert labels == [
        tkf.text(k.category_key("general", "button_label")),
        tkf.text(k.category_key("billing", "button_label")),
    ]
    assert first[0] == tkf.app.op("tickets.publish.panel", action="created", channel=panel_channel.mention)
    second = await MODULE.publish(tkf.app)
    assert second[0] == tkf.app.op("tickets.publish.panel", action="unchanged", channel=panel_channel.mention)
    assert len(panel_channel.messages) == 1


async def test_publish_select_panel_and_missing_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"panel_style": "select"}})
    try:
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        panel_channel = channel(tenv, "ticket_panel")
        message = panel_channel.messages[0]
        assert message.custom_ids() == [cid("tk", "pick")]
        select = message.components()[0].item
        assert select.placeholder == tenv.text(k.PANEL_SELECT_PLACEHOLDER)
        assert [o.value for o in select.options] == ["general"]
        assert [o.label for o in select.options] == [tenv.text(k.category_key("general", "button_label"))]

        tenv.guild._channels.pop(panel_channel.id)
        await tenv.app.resources.forget("channel", "ticket_panel")
        lines = await MODULE.publish(tenv.app)
        assert lines == [tenv.app.op("tickets.publish.no_channel", name="ticket_panel")]
    finally:
        await tenv.close()


async def test_publish_reports_missing_ticket_category(tk: TestEnv) -> None:
    await tk.app.resources.forget("category", "tickets")
    assert MODULE.publish is not None
    lines = await MODULE.publish(tk.app)
    expected = tk.app.op("tickets.publish.category_missing", category="general", discord_category="tickets")
    assert expected in lines
    assert PANEL_KEY == "tickets:panel"
