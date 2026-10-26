"""Staff decisions: resolve / reject (with a reason), permissions, exactly-once, notifications, records, closing."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from shopbot.config.settings import ProblemType
from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import service, views
from shopbot.features.tickets import content_keys as tk
from shopbot.features.tickets.models import CLOSED
from shopbot.ui.custom_ids import cid
from tests.fakes import click, submit_modal
from tests.helpers import TestEnv
from tests.problems.util import (
    add_order,
    audit_actions,
    decide,
    last_description,
    make_env,
    order_notes,
    panel_message,
    render,
    report_about_order,
    report_of,
    report_without_order,
    task,
    ticket_channel,
    ticket_of,
)


def _fields(message: object) -> dict[str, str]:
    embed = message.embeds[0]  # type: ignore[attr-defined]
    return {f.name: f.value for f in embed.fields}


def _op_reply(interaction: object) -> tuple[str | None, str | None]:
    """(title, text) of the staff-only private reply: what happened, over its details."""
    sent = interaction.last_sent  # type: ignore[attr-defined]
    if sent is None:
        return None, None
    return sent.embeds[0].title, sent.embeds[0].description


def _op_text(interaction: object) -> str:
    return str(_op_reply(interaction)[1])


async def test_staff_resolves_with_a_note(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    order_id = await add_order(pr, user)
    report, _ = await report_about_order(pr, user, order_id)
    ticket = await ticket_of(pr, report)
    number = service.number_text(pr.app, ticket.number)

    done = await decide(pr, staff, report, "resolve", "  replaced it  ")
    decided = await report_of(pr, report.id)
    assert decided.status == k.RESOLVED and decided.reason == "replaced it" and decided.decided_by == staff.id
    assert decided.decided_at is not None
    assert _op_reply(done) == (
        pr.app.op("problems.op.decided_title", number=number, status=pr.app.op("problems.op.status.resolved")),
        pr.app.op("problems.op.decided", delivery=pr.app.op("problems.op.delivery.dm")),
    )
    assert done.last_sent is not None and done.last_sent.flags.ephemeral

    # The panel shows the decision; Resolve/Reject are gone, the ticket's Close button stays.
    panel = await panel_message(pr, decided)
    fields = _fields(panel)
    assert fields[pr.text(k.FIELD_STATUS)] == pr.text(k.status_key(k.RESOLVED))
    assert fields[pr.text(k.FIELD_REASON)] == "replaced it"
    assert fields[pr.text(k.FIELD_DECIDED_BY)] == f"<@{staff.id}>"
    assert panel.custom_ids() == [cid("tk", "close", ticket.id)]
    assert len(panel.embeds) == 1  # the attachments note is only shown while the report is open

    # The customer got a private message (DM) with the decision; nothing extra was posted in the ticket.
    dm = user.dms[-1]
    ch = await ticket_channel(pr, decided)
    label = pr.text(k.type_key("general", "label"))
    assert dm.embeds[0].description == render(
        pr, k.notify_key(k.RESOLVED, "description"), number=number, type_label=label, channel_mention=ch.mention
    )
    assert _fields(dm)[pr.text(k.FIELD_REASON)] == "replaced it"
    assert len(ch.messages) == 2  # panel + answers

    # The order's history and the audit log record it; the follow-up task is no longer needed.
    notes = await order_notes(pr, order_id)
    assert notes[-1] == pr.app.op("problems.order_note.resolved", number=number, reason="replaced it")
    assert await audit_actions(pr) == ["problems.reported", "problems.resolved"]
    followup = await task(pr, f"problems.after_decision:{report.id}")
    assert followup is not None and followup.status == "cancelled"


async def test_reject_requires_a_reason(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    panel = await panel_message(pr, report)
    pressed = await click(pr.app, user=staff, custom_id=views.reject_id(report.id), message=panel)
    modal = pressed.response.modal
    assert modal is not None
    assert modal.title == pr.app.op("problems.form.reject_title", number=service.number_text(pr.app, 1))

    empty = await decide(pr, staff, report, "reject", "   ")
    assert _op_reply(empty) == (
        pr.app.op("problems.op.reason_required_title"),
        pr.app.op("problems.op.reason_required"),
    )
    assert (await report_of(pr, report.id)).is_open
    too_long = await decide(pr, staff, report, "reject", "x" * (k.REASON_MAX + 1))
    assert _op_reply(too_long) == (
        pr.app.op("problems.op.reason_too_long_title"),
        pr.app.op("problems.op.reason_too_long", max=k.REASON_MAX, length=k.REASON_MAX + 1),
    )

    await decide(pr, staff, report, "reject", "not *our* fault")
    decided = await report_of(pr, report.id)
    assert decided.status == k.REJECTED and decided.reason == "not *our* fault"
    panel = await panel_message(pr, decided)
    assert _fields(panel)[pr.text(k.FIELD_REASON)] == r"not \*our\* fault"
    assert _fields(panel)[pr.text(k.FIELD_STATUS)] == pr.text(k.status_key(k.REJECTED))
    assert await audit_actions(pr) == ["problems.reported", "problems.rejected"]


async def test_reject_without_reason_when_not_required(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"problems": {"require_reason_on_reject": False}})
    try:
        user, staff = tenv.member(), tenv.member(name="staff", staff=True)
        report, _ = await report_without_order(tenv, user)
        await decide(tenv, staff, report, "reject", "")
        decided = await report_of(tenv, report.id)
        assert decided.status == k.REJECTED and decided.reason is None
        assert tenv.text(k.FIELD_REASON) not in _fields(await panel_message(tenv, decided))
    finally:
        await tenv.close()


async def test_customers_and_other_members_cannot_decide(pr: TestEnv) -> None:
    user = pr.member()
    report, _ = await report_without_order(pr, user)
    panel = await panel_message(pr, report)
    for who in (user, pr.member(name="other")):
        for button in (views.resolve_id(report.id), views.reject_id(report.id)):
            pressed = await click(pr.app, user=who, custom_id=button, message=panel)
            assert pressed.response.modal is None
            assert last_description(pressed) == pr.text(core.ERROR_PERMISSION)
        for form in (views.resolve_form_id(report.id), views.reject_form_id(report.id)):
            forged = await submit_modal(pr.app, user=who, custom_id=form, fields={views.REASON_INPUT: "ok"})
            assert last_description(forged) == pr.text(core.ERROR_PERMISSION)
    assert (await report_of(pr, report.id)).is_open
    assert await audit_actions(pr) == ["problems.reported"]


async def test_extra_staff_roles_can_decide_and_see_the_ticket(tmp_path: Path) -> None:
    from tests.helpers import server_extension

    overrides = {**server_extension(roles=[{"key": "support"}]), "problems": {"staff_roles": ["support"]}}
    tenv = await make_env(tmp_path, overrides)
    try:
        role = tenv.guild.add_role(name="support", position=4)
        await tenv.app.resources.remember("role", "support", role.id)
        helper = tenv.guild.add_member(name="helper", roles=[role])
        report, _ = await report_without_order(tenv, tenv.member())
        ch = await ticket_channel(tenv, report)
        assert ch.permissions_for(helper).view_channel
        await decide(tenv, helper, report, "resolve")
        assert (await report_of(tenv, report.id)).status == k.RESOLVED
        # The ticket system treats them as staff of report tickets too (e.g. deleting after closing).
        ticket = await ticket_of(tenv, report)
        from shopbot.features.tickets import service as tickets_service

        assert tickets_service.is_ticket_staff(tenv.app, helper, helper.id, ticket)
    finally:
        await tenv.close()


async def test_a_report_is_decided_exactly_once(pr: TestEnv) -> None:
    user = pr.member()
    first, second = pr.member(name="staff1", staff=True), pr.member(name="staff2", staff=True)
    report, _ = await report_without_order(pr, user)
    await decide(pr, first, report, "resolve", "done")
    again = await decide(pr, second, report, "reject", "no")
    number = service.number_text(pr.app, 1)
    status = pr.app.op("problems.op.status.resolved")
    already = (
        pr.app.op("problems.op.already_decided_title", number=number, status=status),
        pr.app.op("problems.op.already_decided"),
    )
    assert _op_reply(again) == already
    decided = await report_of(pr, report.id)
    assert decided.status == k.RESOLVED and decided.decided_by == first.id
    # A form submitted after the decision (opened before it) changes nothing either.
    late = await submit_modal(
        pr.app, user=second, custom_id=views.reject_form_id(report.id), fields={views.REASON_INPUT: "late"}
    )
    assert _op_reply(late) == already
    assert await audit_actions(pr) == ["problems.reported", "problems.resolved"]
    assert len(user.dms) == 1


async def test_concurrent_decisions_apply_once(pr: TestEnv) -> None:
    user = pr.member()
    a, b = pr.member(name="a", staff=True), pr.member(name="b", staff=True)
    report, _ = await report_without_order(pr, user)
    panel = await panel_message(pr, report)
    await asyncio.gather(
        submit_modal(
            pr.app, user=a, custom_id=views.resolve_form_id(report.id), fields={views.REASON_INPUT: ""}, message=panel
        ),
        submit_modal(
            pr.app, user=b, custom_id=views.reject_form_id(report.id), fields={views.REASON_INPUT: "x"}, message=panel
        ),
    )
    decided = await report_of(pr, report.id)
    assert decided.status in (k.RESOLVED, k.REJECTED)
    actions = await audit_actions(pr)
    assert actions.count("problems.resolved") + actions.count("problems.rejected") == 1
    assert len(user.dms) == 1


async def test_decision_without_direct_messages_uses_the_panel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"dm_notifications": False}})
    try:
        user, staff = tenv.member(), tenv.member(name="staff", staff=True)
        report, _ = await report_without_order(tenv, user)
        done = await decide(tenv, staff, report, "resolve")
        assert user.dms == []
        ch = await ticket_channel(tenv, report)
        assert len(ch.messages) == 2  # no extra message: the updated panel is the notification
        assert tenv.app.op("problems.op.delivery.panel") in _op_text(done)
    finally:
        await tenv.close()


async def test_closed_direct_messages_fall_back_to_the_panel(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    user.dm_forbidden = True
    report, _ = await report_without_order(pr, user)
    done = await decide(pr, staff, report, "reject", "reason")
    ch = await ticket_channel(pr, report)
    assert len(ch.messages) == 2  # the panel shows the decision; nothing else is posted
    assert _fields(await panel_message(pr, report))[pr.text(k.FIELD_STATUS)] == pr.text(k.status_key(k.REJECTED))
    assert pr.app.op("problems.op.delivery.none") in _op_text(done)


async def test_when_the_panel_is_gone_the_decision_is_posted_in_the_ticket(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"features": {"dm_notifications": False}})
    try:
        user, staff = tenv.member(), tenv.member(name="staff", staff=True)
        report, _ = await report_without_order(tenv, user)
        panel = await panel_message(tenv, report)
        await panel.delete()
        await click(tenv.app, user=staff, custom_id=views.resolve_id(report.id))
        done = await submit_modal(
            tenv.app, user=staff, custom_id=views.resolve_form_id(report.id), fields={views.REASON_INPUT: "fixed"}
        )
        ch = await ticket_channel(tenv, report)
        posted = ch.messages[-1]
        assert posted.content == f"<@{user.id}>"  # pings the customer
        assert posted.embeds[0].description is not None
        assert _fields(posted)[tenv.text(k.FIELD_REASON)] == "fixed"
        assert tenv.app.op("problems.op.delivery.channel") in _op_text(done)
        assert (await report_of(tenv, report.id)).status == k.RESOLVED
    finally:
        await tenv.close()


async def test_closing_afterwards_uses_the_ticket_system(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    await decide(pr, staff, report, "resolve")
    ticket = await ticket_of(pr, report)
    panel = await panel_message(pr, report)
    # The customer may close their own ticket (tickets.allow_user_close), with the ticket system's confirmation.
    asked = await click(pr.app, user=user, custom_id=cid("tk", "close", ticket.id), message=panel)
    assert asked.last_sent is not None
    assert asked.last_sent.embeds[0].description == pr.text(tk.CLOSE_CONFIRM_DESCRIPTION)
    await click(pr.app, user=user, custom_id=cid("tk", "closeok", ticket.id), message=asked.last_sent)
    closed = await ticket_of(pr, report)
    assert closed.status == CLOSED
    assert closed.transcript_status == "pending"  # the normal transcript follows
    ch = await ticket_channel(pr, report)
    assert ch.messages[-1].embeds[0].title == render(pr, tk.CLOSED_TITLE, number=service.number_text(pr.app, 1))
    await pr.app.scheduler.run_due()
    transcripts = (pr.app.resources.text_channel(pr.guild, "transcripts")).messages  # type: ignore[arg-type,union-attr]
    assert len(transcripts) == 1
    # Closing twice does nothing more.
    again = await click(pr.app, user=user, custom_id=cid("tk", "close", ticket.id), message=panel)
    assert last_description(again) == pr.text(tk.NOT_OPEN)


async def test_staff_can_decide_after_the_ticket_was_closed(pr: TestEnv) -> None:
    from shopbot.features.tickets import service as tickets_service

    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    await tickets_service.close_ticket(pr.app, report.ticket_id, actor_id=user.id)
    await decide(pr, staff, report, "reject", "too late")
    decided = await report_of(pr, report.id)
    assert decided.status == k.REJECTED
    panel = await panel_message(pr, decided)
    # Close stays on the panel (its state is checked when clicked), so after a reopen the ticket can be closed again.
    assert panel.custom_ids() == [cid("tk", "close", decided.ticket_id)]
    await tickets_service.reopen_ticket(pr.app, decided.ticket_id, actor_id=staff.id)
    closing = await click(pr.app, user=user, custom_id=cid("tk", "close", decided.ticket_id))
    assert closing.last_sent is not None or closing.response_kinds()  # handled, not ignored


async def test_a_decision_stops_unanswered_ticket_reminders(pr: TestEnv) -> None:
    from shopbot.features.operations import repo as operations_repo

    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    now = pr.clock.now()
    await pr.app.db.transaction(operations_repo.record_customer_message, report.ticket_id, opened_at=now, now=now)
    await decide(pr, staff, report, "resolve", "fixed")
    row = await pr.app.db.run(
        lambda conn: conn.execute(
            "SELECT awaiting_since FROM ticket_replies WHERE ticket_id = ?", (report.ticket_id,)
        ).fetchone()
    )
    assert row is not None and row["awaiting_since"] is None


async def test_a_long_reason_still_leaves_the_order_note(pr: TestEnv) -> None:
    from tests.problems.util import order_notes

    user, staff = pr.member(), pr.member(name="staff", staff=True)
    from tests.problems.util import add_order, report_about_order

    order_id = await add_order(pr, user, status="fulfilled")
    report, _ = await report_about_order(pr, user, order_id)
    await decide(pr, staff, report, "reject", "r" * k.REASON_MAX)
    notes = await order_notes(pr, order_id)
    assert notes and len(notes[-1]) <= 500 and notes[-1].endswith("…")
    assert len(user.dms) == 1


async def test_a_report_whose_type_was_removed_can_still_be_decided(pr: TestEnv) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)
    p = pr.app.settings.problems
    pr.app.settings = replace(pr.app.settings, problems=replace(p, types=(ProblemType(id="replacement"),)))
    await decide(pr, staff, report, "resolve")
    decided = await report_of(pr, report.id)
    assert decided.status == k.RESOLVED
    fields = _fields(await panel_message(pr, decided))
    assert pr.text(k.FIELD_TYPE) not in fields  # the type's label is no longer configured: omitted, never invented
    assert fields[pr.text(k.FIELD_STATUS)] == pr.text(k.status_key(k.RESOLVED))


async def test_a_reopened_report_ticket_moves_back_to_the_report_category(tmp_path: Path) -> None:
    from shopbot.features.tickets import service as tickets_service
    from tests.problems.util import make_env

    env = await make_env(tmp_path, {"tickets": {"closed_category": "staff"}})
    try:
        user, staff = env.member(), env.member(name="staff", staff=True)
        report, _ = await report_without_order(env, user)
        ticket = await tickets_service.get_ticket(env.app, report.ticket_id)
        assert ticket is not None and ticket.channel_id is not None
        channel = env.guild.get_channel(ticket.channel_id)
        report_category = env.app.resources.category(env.guild, env.app.settings.problems.discord_category)
        closed_category = env.app.resources.category(env.guild, "staff")
        assert report_category is not None and closed_category is not None
        await tickets_service.close_ticket(env.app, report.ticket_id, actor_id=staff.id)
        assert channel.category_id == closed_category.id
        await tickets_service.reopen_ticket(env.app, report.ticket_id, actor_id=staff.id)
        assert channel.category_id == report_category.id
    finally:
        await env.close()
