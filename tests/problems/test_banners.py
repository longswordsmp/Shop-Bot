"""Banner pictures on the Report a Problem messages (banners switched on, a tiny picture per banner id).

The private step and the report panel are each ONE message whose banner follows its state: an edit that keeps the
state keeps the banner without uploading it again, a state change swaps it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import views
from shopbot.features.problems.preview import preview
from tests.fakes import FakeMessage, click
from tests.helpers import TestEnv, banner_names, deep_merge, make_banner_env, provision_channels, uploaded
from tests.problems.util import (
    PROBLEM_SETTINGS,
    add_order,
    channel,
    continue_id_of,
    decide,
    panel_message,
    pick,
    private_step,
    report_without_order,
    reports,
    start,
    submit_form,
    ticket_channel,
)

# No pause between two reports, so one test can walk through several flows.
NO_COOLDOWN: dict[str, Any] = {"tickets": {"creation_cooldown_seconds": 0}}


async def banner_env(tmp_path: Path, overrides: dict[str, Any] | None = None) -> TestEnv:
    settings = deep_merge(deep_merge(PROBLEM_SETTINGS, NO_COOLDOWN), overrides or {})
    tenv = await make_banner_env(tmp_path, settings)
    await provision_channels(tenv)
    return tenv


@pytest.fixture
async def bp(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    found = tenv.app.ui.banner(banner_id)
    assert found is not None, banner_id
    return found.filename


def _uploaded(message: FakeMessage) -> list[str]:
    """Names of the files the message's last edit uploaded (kept attachments are not uploads)."""
    return [getattr(item, "filename", "") for item in message.edits[-1].get("attachments", []) if hasattr(item, "fp")]


async def test_the_private_step_banner_follows_the_step(bp: TestEnv) -> None:
    user = bp.member()
    order_id = await add_order(bp, user)
    step = private_step(await start(bp, user))
    assert banner_names(step) == [name(bp, "support-request")]

    # Picking re-renders the same step: the banner is kept, not uploaded again.
    await pick(bp, user, step, "order", str(order_id))
    assert step.edits and uploaded(step.edits[-1]) == []
    assert banner_names(step) == [name(bp, "support-request")]

    # A second flow creates a report meanwhile; Continue on the first step now shows the limit (banner swapped).
    other = private_step(await start(bp, user))
    await pick(bp, user, other, "order", views.NO_ORDER)
    modal = (await click(bp.app, user=user, custom_id=continue_id_of(other), message=other)).response.modal
    assert modal is not None
    await submit_form(bp, user, str(modal.custom_id), message=other)
    assert banner_names(other) == [name(bp, "ticket-created")]  # the step became the confirmation
    assert _uploaded(other) == [name(bp, "ticket-created")] and len(other.attachments) == 1

    await click(bp.app, user=user, custom_id=continue_id_of(step), message=step)
    assert step.embeds[0].description == bp.app.content.render(
        k.LIMIT_REACHED, limit=1, channel_mention=(await ticket_channel(bp, (await reports(bp))[0])).mention
    )
    assert banner_names(step) == [name(bp, "rejected-or-unsuccessful-submission")]
    assert len(step.attachments) == 1


async def test_replies_outside_the_step(bp: TestEnv) -> None:
    user = bp.member()
    report, submitted = await report_without_order(bp, user)  # form right away: the reply is a new message
    assert submitted.last_sent is not None and banner_names(submitted.last_sent) == [name(bp, "ticket-created")]
    limited = (await start(bp, user)).last_sent
    assert limited is not None and banner_names(limited) == [name(bp, "rejected-or-unsuccessful-submission")]
    # An invalid form submission is a shared error reply (a routine refusal: the general picture).
    invalid = await submit_form(bp, user, views.form_id("f" * 16, views.NO_ORDER, "general"), {"details": ""})
    assert invalid.last_sent is not None and banner_names(invalid.last_sent) == [name(bp, "general-response")]
    assert len(await reports(bp)) == 1 and (await reports(bp))[0].id == report.id


async def test_no_orders_reply(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, {"problems": {"allow_without_order": False}})
    try:
        reply = (await start(tenv, tenv.member())).last_sent
        assert reply is not None and banner_names(reply) == [name(tenv, "rejected-or-unsuccessful-submission")]
    finally:
        await tenv.close()


async def test_the_report_panel_banner_follows_the_decision(bp: TestEnv) -> None:
    user, staff = bp.member(), bp.member(name="staff", staff=True)
    report, _ = await report_without_order(bp, user)
    ch = await ticket_channel(bp, report)
    panel, *details = ch.messages
    assert panel.id == report.panel_message_id and banner_names(panel) == [name(bp, "support-request")]
    assert details and all(m.files == [] for m in details)  # the answers continue the panel: no banner
    [alert] = channel(bp, "staff_alerts").messages
    assert banner_names(alert) == [name(bp, "staff-notifications")]

    done = await decide(bp, staff, report, "resolve", "replaced it")
    assert banner_names(panel) == [name(bp, "resolution")] and len(panel.attachments) == 1
    assert _uploaded(panel) == [name(bp, "resolution")]
    assert banner_names(user.dms[-1]) == [name(bp, "resolution")]
    assert done.last_sent is not None and banner_names(done.last_sent) == [name(bp, "general-response")]
    again = await click(bp.app, user=staff, custom_id=views.reject_id(report.id), message=panel)
    assert again.last_sent is not None and banner_names(again.last_sent) == [name(bp, "general-response")]


async def test_a_rejection_and_invalid_reasons(bp: TestEnv) -> None:
    user, staff = bp.member(), bp.member(name="staff", staff=True)
    report, _ = await report_without_order(bp, user)
    empty = await decide(bp, staff, report, "reject", "   ")
    too_long = await decide(bp, staff, report, "reject", "x" * (k.REASON_MAX + 1))
    for refused in (empty, too_long):
        assert refused.last_sent is not None and banner_names(refused.last_sent) == [name(bp, "error")]
    panel = await panel_message(bp, report)
    assert banner_names(panel) == [name(bp, "support-request")]
    await decide(bp, staff, report, "reject", "not our fault")
    assert banner_names(panel) == [name(bp, "rejected-or-unsuccessful-submission")]
    assert banner_names(user.dms[-1]) == [name(bp, "rejected-or-unsuccessful-submission")]


async def test_the_decision_posted_in_the_ticket_carries_its_banner(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, {"features": {"dm_notifications": False}})
    try:
        user, staff = tenv.member(), tenv.member(name="staff", staff=True)
        report, _ = await report_without_order(tenv, user)
        await (await panel_message(tenv, report)).delete()  # the panel cannot show the decision
        await decide_without_panel(tenv, staff, report.id)
        posted = (await ticket_channel(tenv, report)).messages[-1]
        assert posted.content == f"<@{user.id}>" and banner_names(posted) == [name(tenv, "resolution")]
    finally:
        await tenv.close()


async def decide_without_panel(tenv: TestEnv, staff: Any, report_id: int) -> None:
    from tests.fakes import submit_modal

    await click(tenv.app, user=staff, custom_id=views.resolve_id(report_id))
    await submit_modal(
        tenv.app, user=staff, custom_id=views.resolve_form_id(report_id), fields={views.REASON_INPUT: "fixed"}
    )


async def test_preview_states_show_the_mapped_banners(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, {"problems": {"allow_without_order": False}})
    try:
        states = {state.name: state.spec for state in preview(tenv.app)}
        expected: dict[str, BannerId | None] = {
            "choose": "support-request",
            "choose.complete": "support-request",
            "created": "ticket-created",
            "limit_reached": "rejected-or-unsuccessful-submission",
            "no_orders": "rejected-or-unsuccessful-submission",
            "ticket.open": "support-request",
            "ticket.open_without_order": "support-request",
            "ticket.resolved": "resolution",
            "ticket.rejected": "rejected-or-unsuccessful-submission",
            "details.general": None,
            "notify.resolved": "resolution",
            "notify.rejected": "rejected-or-unsuccessful-submission",
            "staff_alert": "staff-notifications",
            "staff.decided": "general-response",
            "staff.already_decided": "general-response",
        }
        for state, banner_id in expected.items():
            banner = getattr(states[state], "banner", None)
            if banner_id is None:
                assert banner is None, state
            else:
                assert banner is not None and banner.filename == name(tenv, banner_id), state
        refusal = views.error_reply(tenv.app.ui, core.ERROR_NOT_FOUND).banner
        assert refusal is not None and refusal.filename == name(tenv, "general-response")
        failure = views.error_reply(tenv.app.ui, core.ERROR_GENERIC, error_id="abc").banner
        assert failure is not None and failure.filename == name(tenv, "error")
    finally:
        await tenv.close()
