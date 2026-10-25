"""Discord failures and missing text: nothing half-made is left behind, the customer gets the configured error, and
decisions survive failed Discord calls."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from shopbot.features import core_keys as core
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import service, views
from shopbot.features.tickets.models import FAILED
from tests.fakes import FakeMessage, click, http_error
from tests.helpers import TestEnv
from tests.problems.util import (
    audit_actions,
    channel,
    decide,
    last_description,
    report_of,
    report_without_order,
    reports,
    start,
    submit_form,
    ticket_channel,
)


def _ticket_channels(tenv: TestEnv) -> list[Any]:
    prefix = tenv.text(k.CHANNEL_PREFIX)
    return [c for c in tenv.guild.text_channels if c.name.startswith(prefix)]


async def _all_tickets(tenv: TestEnv) -> list[Any]:
    from shopbot.features.tickets import repo as tickets_repo

    return await tenv.app.db.run(tickets_repo.list_tickets, tenv.app.env.guild_id, limit=100)


async def test_channel_creation_refused_by_discord(pr: TestEnv) -> None:
    user = pr.member()
    opened = await start(pr, user)
    assert opened.response.modal is not None
    custom_id = str(opened.response.modal.custom_id)
    pr.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    failed = await submit_form(pr, user, custom_id)
    assert failed.last_sent is not None and failed.last_sent.embeds[0].title == pr.text(core.ERROR_TITLE)
    assert await reports(pr) == []  # nothing half-made: the report was removed ...
    assert [t.status for t in await _all_tickets(pr)] == [FAILED]  # ... and its ticket failed (audited)
    assert _ticket_channels(pr) == []
    # The same flow can be retried once Discord allows it.
    pr.guild.fail_channel_create = None
    await submit_form(pr, user, custom_id)
    assert len(await reports(pr)) == 1


async def test_panel_that_cannot_be_posted_removes_the_channel(pr: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(channel: Any, spec: Any) -> FakeMessage:
        raise http_error(discord.HTTPException, 500, 0, "Internal Server Error")

    monkeypatch.setattr(service, "send_spec", refuse)
    user = pr.member()
    opened = await start(pr, user)
    assert opened.response.modal is not None
    failed = await submit_form(pr, user, str(opened.response.modal.custom_id))
    assert failed.last_sent is not None and failed.last_sent.embeds[0].title == pr.text(core.ERROR_TITLE)
    assert await reports(pr) == []
    assert _ticket_channels(pr) == []
    assert [t.status for t in await _all_tickets(pr)] == [FAILED]
    assert channel(pr, "staff_alerts").messages == []


async def test_text_removed_after_startup_creates_nothing(pr: TestEnv) -> None:
    user = pr.member()
    opened = await start(pr, user)
    assert opened.response.modal is not None
    values = dict(pr.content)
    del values[k.TICKET_TITLE]
    pr.app.content.replace(file_values=values)  # readiness is not recomputed: the runtime guard must hold
    failed = await submit_form(pr, user, str(opened.response.modal.custom_id))
    assert last_description(failed) == pr.text(core.ERROR_UNAVAILABLE)
    assert await reports(pr) == []
    assert _ticket_channels(pr) == []


async def test_missing_staff_alert_channel_still_creates_the_report(pr: TestEnv) -> None:
    await pr.app.resources.forget("channel", "staff_alerts")
    report, _ = await report_without_order(pr, pr.member())
    assert report.finished
    assert await audit_actions(pr) == ["problems.reported"]


async def test_panel_edit_failure_keeps_the_decision_and_tells_the_customer(
    pr: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, staff = pr.member(), pr.member(name="staff", staff=True)
    report, _ = await report_without_order(pr, user)

    async def refuse(message: Any, spec: Any) -> Any:
        raise http_error(discord.HTTPException, 500, 0, "Internal Server Error")

    monkeypatch.setattr(service, "edit_spec", refuse)
    user.dm_forbidden = True  # and the DM fails too: the decision is posted in the ticket
    done = await decide(pr, staff, report, "reject", "reason")
    assert (await report_of(pr, report.id)).status == k.REJECTED
    ch = await ticket_channel(pr, report)
    assert ch.messages[-1].content == f"<@{user.id}>"
    assert ch.messages[-1].embeds[0].description is not None
    assert pr.app.op("problems.op.delivery.channel") in str(done.last_sent and done.last_sent.embeds[0].title)
    assert await audit_actions(pr) == ["problems.reported", "problems.rejected"]


async def test_feature_not_ready_answers_unavailable_and_hides_the_button(pr: TestEnv) -> None:
    from shopbot.features.checkout import panel as checkout_panel

    spec = checkout_panel.panel_spec(pr.app)
    ids = [c.custom_id for row in spec.rows for c in row]
    assert views.start_id() in ids  # ready: the button is on the #purchase panel

    values = dict(pr.content)
    values.pop(k.BUTTON_RESOLVE)
    pr.app.content.replace(file_values=values)
    pr.app.recompute_readiness()
    assert not pr.app.ready(k.FEATURE)
    spec = checkout_panel.panel_spec(pr.app)
    assert views.start_id() not in [c.custom_id for row in spec.rows for c in row]
    # A button still showing on an old panel answers with the configured 'unavailable' text.
    clicked = await click(pr.app, user=pr.member(), custom_id=views.start_id())
    assert last_description(clicked) == pr.text(core.ERROR_UNAVAILABLE)
    assert clicked.response.modal is None
