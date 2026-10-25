"""Restart persistence of every control, and recovery of creations/decisions interrupted by a stopped process."""

from __future__ import annotations

import pytest

from shopbot.db.repos.tasks import get_by_dedupe as repo_task
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import repo, service, views
from shopbot.features.tickets import content_keys as tk
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import FAILED, OPEN
from shopbot.ui.discord_render import send_spec
from tests.fakes import click, submit_modal
from tests.helpers import GUILD_ID, TestEnv
from tests.problems.util import (
    add_order,
    audit_actions,
    channel,
    continue_id_of,
    decide,
    panel_message,
    pick,
    private_step,
    report_of,
    report_without_order,
    reports,
    restarted_app,
    start,
    submit_form,
    ticket_of,
)


async def test_every_control_keeps_working_after_a_restart(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    order_id = await add_order(pr, user)
    step = private_step(await start(pr, user))  # rendered by the old process

    app = await restarted_app(pr)
    await pick(pr, user, step, "order", str(order_id), app=app)
    assert [o.default for o in step.components()[0].item.options] == [True, False]
    proceed = await click(app, user=user, custom_id=continue_id_of(step), message=step)
    assert proceed.response.modal is not None
    form_id = str(proceed.response.modal.custom_id)

    app = await restarted_app(pr)  # restart between opening and submitting the form
    await submit_form(pr, user, form_id, message=step, app=app)
    (report,) = await reports(pr)
    assert report.order_id == order_id

    app = await restarted_app(pr)
    panel = await panel_message(pr, report)
    pressed = await click(app, user=staff, custom_id=views.reject_id(report.id), message=panel)
    assert pressed.response.modal is not None
    app = await restarted_app(pr)
    await submit_modal(
        app, user=staff, custom_id=views.reject_form_id(report.id), fields={views.REASON_INPUT: "no"}, message=panel
    )
    assert (await report_of(pr, report.id)).status == k.REJECTED

    # The ticket's own Close button on the panel works in a fresh process as well.
    ticket = await ticket_of(pr, report)
    app = await restarted_app(pr)
    asked = await click(app, user=user, custom_id=views.close_id(ticket.id), message=panel)
    assert asked.last_sent is not None and asked.last_sent.embeds[0].description == pr.text(
        tk.CLOSE_CONFIRM_DESCRIPTION
    )


async def test_the_resolve_button_after_a_restart(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    app = await restarted_app(pr)
    await decide(pr, staff, report, "resolve", "ok", app=app)
    assert (await report_of(pr, report.id)).status == k.RESOLVED


async def _reserve(tenv: TestEnv, user_id: int, nonce: str = "a" * 16) -> repo.Reservation:
    p = tenv.app.settings.problems
    return await tenv.app.db.transaction(
        repo.reserve,
        guild_id=GUILD_ID,
        user_id=user_id,
        nonce=nonce,
        order_id=None,
        issue_type="general",
        answers={"details": "abc"},
        eligible_statuses=p.eligible_statuses,
        max_open=p.max_open_per_user,
        daily_limit=0,
        now=tenv.clock.now(),
        task_attempts=3,
    )


async def test_a_creation_interrupted_before_the_channel_is_cleaned_up(pr: TestEnv) -> None:
    user = pr.member()
    reservation = await _reserve(pr, user.id)  # the process stops right after the reservation
    assert await reports(pr) != []
    pr.clock.advance(tickets_service.CREATING_STALE_SECONDS + 1)
    await pr.app.scheduler.run_due()  # the ticket system fails the ticket (no channel)
    assert (await ticket_of(pr, reservation.report)).status == FAILED
    pr.clock.advance(repo.FINISH_DELAY_SECONDS)
    await pr.app.scheduler.run_due()  # the finishing task removes the report
    assert await reports(pr) == []
    # The open-report limit does not count it, and the customer can report again.
    opened = await start(pr, user)
    assert opened.response.modal is not None


async def test_a_creation_interrupted_after_the_panel_was_posted_is_completed(pr: TestEnv) -> None:
    user = pr.member()
    reservation = await _reserve(pr, user.id)
    problem_type = pr.app.settings.problems.types[0]
    prepared = await service._prepare(
        pr.app, reservation.report, reservation.ticket, user_name=user.display_name, problem_type=problem_type
    )
    ch = await tickets_service.create_ticket_channel(
        pr.app,
        reservation.ticket,
        member=user,
        discord_category_key="tickets",
        channel_prefix_key=k.CHANNEL_PREFIX,
        extra_staff_role_keys=(),
        topic=None,
    )
    posted = await send_spec(ch, prepared.panel)  # ... and the process stops before recording it

    pr.clock.advance(tickets_service.CREATING_STALE_SECONDS + 1)
    await pr.app.scheduler.run_due()  # the ticket system adopts the bot's first message and opens the ticket
    ticket = await ticket_of(pr, reservation.report)
    assert ticket.status == OPEN and ticket.control_message_id == posted.id
    pr.clock.advance(repo.FINISH_DELAY_SECONDS)
    await pr.app.scheduler.run_due()

    report = await report_of(pr, reservation.report.id)
    assert report.panel_message_id == posted.id
    ticket = await ticket_of(pr, report)
    assert ticket.control_message_id is None  # closing/reopening will not replace the panel's buttons
    assert len(channel(pr, "staff_alerts").messages) == 1
    assert await audit_actions(pr) == ["problems.reported"]
    # Running the finishing step again changes nothing.
    assert await service.finish_creation(pr.app, report.id) == "done"
    assert len(channel(pr, "staff_alerts").messages) == 1
    assert await audit_actions(pr) == ["problems.reported"]


async def test_finish_waits_while_the_ticket_is_still_being_created(pr: TestEnv) -> None:
    user = pr.member()
    reservation = await _reserve(pr, user.id)
    assert await service.finish_creation(pr.app, reservation.report.id) == "wait"
    assert await reports(pr) != []


async def test_a_decision_interrupted_after_its_commit_is_completed(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    decided = await pr.app.db.transaction(
        repo.decide,
        report.id,
        status=k.RESOLVED,
        reason="fixed",
        actor_id=staff.id,
        order_note=None,
        now=pr.clock.now(),
        task_attempts=3,
    )  # ... and the process stops before the follow-ups
    assert decided is not None
    panel = await panel_message(pr, report)
    assert panel.custom_ids()[0] == views.resolve_id(report.id)  # not updated yet
    assert user.dms == []

    app = await restarted_app(pr)
    pr.clock.advance(repo.FOLLOWUP_DELAY_SECONDS + 1)
    await app.scheduler.run_due()
    panel = await panel_message(pr, report)
    assert views.resolve_id(report.id) not in panel.custom_ids()
    assert len(user.dms) == 1
    assert await audit_actions(pr) == ["problems.reported", "problems.resolved"]
    # Repeating the follow-ups never notifies or audits twice.
    await service.run_decision_followups(app, report.id)
    assert len(user.dms) == 1
    assert await audit_actions(pr) == ["problems.reported", "problems.resolved"]


async def test_decision_followups_that_fail_are_retried_by_the_task(
    pr: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    original = service.refresh_panel

    async def broken(*args: object, **kwargs: object) -> bool:
        raise ConnectionResetError("network")

    monkeypatch.setattr(service, "refresh_panel", broken)
    done = await decide(pr, staff, report, "resolve", "ok")
    assert (await report_of(pr, report.id)).status == k.RESOLVED  # the decision itself is kept
    assert pr.app.op("problems.op.delivery.failed") in str(done.last_sent and done.last_sent.embeds[0].title)
    assert user.dms == []
    pending = await pr.app.db.run(repo_task, f"problems.after_decision:{report.id}")
    assert pending is not None and pending.status == "pending"

    monkeypatch.setattr(service, "refresh_panel", original)
    pr.clock.advance(repo.FOLLOWUP_DELAY_SECONDS + 1)
    await pr.app.scheduler.run_due()
    panel = await panel_message(pr, report)
    assert views.resolve_id(report.id) not in panel.custom_ids()
    assert len(user.dms) == 1
    assert await audit_actions(pr) == ["problems.reported", "problems.resolved"]
