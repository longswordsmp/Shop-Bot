"""Notification failures (spec 6): a DM that failed but reached the user another way is audited as
``notifications.dm_failed`` (no staff alert); anything undeliverable - including every ``notify_channel`` failure -
publishes ``FAILURE`` exactly once, which operations audits and alerts staff about."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.db.repos import notifications as notif_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features.operations import content_keys as ok
from shopbot.features.reviews import service as reviews_service
from shopbot.ui.spec import MessageSpec
from tests.audit_logging.util import FailureRecorder, audit_events
from tests.fakes import FakeMember, FakeTextChannel, http_error
from tests.helpers import TestEnv, make_settings, make_test_env
from tests.setup.util import restarted_app

KIND = "test.notice"


def _spec(tenv: TestEnv) -> MessageSpec:
    return MessageSpec(embeds=(tenv.app.ui.operator_embed("[sample notice]"),))


def _fallback(tenv: TestEnv) -> FakeTextChannel:
    return tenv.guild.add_text_channel(name="fallback")


def _http_500(*_: Any, **__: Any) -> Any:
    raise http_error(discord.HTTPException, 500, 0, "Server Error")


async def _status(tenv: TestEnv, dedupe_key: str) -> tuple[str, str] | None:
    def read(conn: Any) -> tuple[str, str] | None:
        row = conn.execute("SELECT delivery, status FROM notifications WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    return await tenv.app.db.run(read)


# -- DM failed, delivered another way: audited, not alerted ----------------------------------------------------------


async def test_a_dm_failure_delivered_in_the_fallback_channel_is_audited_without_alert(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    member = tenv.member()
    member.dm_forbidden = True
    channel = _fallback(tenv)
    delivery = await tenv.app.notifier.notify_user(
        user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="n1", fallback_channel_id=channel.id
    )
    assert delivery == "channel" and len(channel.messages) == 1
    [event] = await audit_events(tenv.app, "notifications.dm_failed")
    assert event.target_type == "user" and event.target_id == str(member.id)
    assert event.details == {"kind": KIND, "reason": "dm_forbidden"}  # no content
    assert recorder.events == [] and await audit_events(tenv.app, "notifications.failed") == []
    # Delivered once: a repeat with the same key changes nothing and audits nothing.
    again = await tenv.app.notifier.notify_user(
        user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="n1", fallback_channel_id=channel.id
    )
    assert again == "duplicate" and len(channel.messages) == 1
    assert len(await audit_events(tenv.app, "notifications.dm_failed")) == 1


async def test_a_dm_http_error_is_audited_with_its_type_only(tenv: TestEnv) -> None:
    member = tenv.member()
    member.send = _http_500  # type: ignore[method-assign]
    channel = _fallback(tenv)
    delivery = await tenv.app.notifier.notify_user(
        user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="n2", fallback_channel_id=channel.id
    )
    assert delivery == "channel"
    [event] = await audit_events(tenv.app, "notifications.dm_failed")
    assert event.details == {"kind": KIND, "reason": "dm_failed: HTTPException"}
    assert "Server Error" not in str(event.details)


async def test_successful_dms_and_channel_only_deliveries_are_not_audited(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    off = await make_test_env(tmp_path / "off", settings=make_settings({"features": {"dm_notifications": False}}))
    try:
        member = tenv.member()
        assert (
            await tenv.app.notifier.notify_user(user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="a") == "dm"
        )
        # DMs switched off: the fallback channel is the configured way, not a DM failure.
        other = off.member()
        channel = _fallback(off)
        delivery = await off.app.notifier.notify_user(
            user_id=other.id, spec=_spec(off), kind=KIND, dedupe_key="b", fallback_channel_id=channel.id
        )
        assert delivery == "channel" and other.dms == []
        for app in (tenv.app, off.app):
            assert await audit_events(app, "notifications.dm_failed") == []
    finally:
        await tenv.close()
        await off.close()


async def test_a_caller_with_its_own_fallback_gets_the_dm_failure_audited_only(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    member = tenv.member()
    member.dm_forbidden = True
    delivery = await tenv.app.notifier.notify_user(
        user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="n3", alert_on_failure=False
    )
    assert delivery == "none"
    [event] = await audit_events(tenv.app, "notifications.dm_failed")
    assert event.details == {"kind": KIND, "reason": "dm_forbidden"}
    assert recorder.events == []


# -- undeliverable: FAILURE exactly once ------------------------------------------------------------------------------


async def test_an_undeliverable_dm_is_reported_once_and_not_as_a_dm_failure(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    member = tenv.member()
    member.dm_forbidden = True
    assert (
        await tenv.app.notifier.notify_user(user_id=member.id, spec=_spec(tenv), kind=KIND, dedupe_key="n4") == "none"
    )
    assert recorder.events == [{"kind": "notification", "source": KIND, "user_id": member.id, "channel_id": None}]
    [failed] = await audit_events(tenv.app, "notifications.failed")
    assert failed.target_id == str(member.id) and failed.details == {"kind": KIND}
    assert await audit_events(tenv.app, "notifications.dm_failed") == []


async def test_notify_channel_reports_a_missing_channel(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    message = await tenv.app.notifier.notify_channel(
        channel_id=123456789, spec=_spec(tenv), kind="tickets.staff_alert", dedupe_key="c1"
    )
    assert message is None
    assert recorder.events == [
        {"kind": "notification", "source": "tickets.staff_alert", "user_id": None, "channel_id": 123456789}
    ]
    [failed] = await audit_events(tenv.app, "notifications.failed")
    assert failed.target_type == "channel" and failed.target_id == "123456789"
    assert failed.details == {"kind": "tickets.staff_alert"}
    assert await _status(tenv, "c1") == ("none", "failed")


async def test_notify_channel_reports_a_discord_error_naming_the_member_it_was_for(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    member = tenv.member()
    channel = _fallback(tenv)
    channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    message = await tenv.app.notifier.notify_channel(
        channel_id=channel.id, spec=_spec(tenv), kind="tickets.auto_close_warning", dedupe_key="c2", user_id=member.id
    )
    assert message is None
    assert recorder.of("tickets.auto_close_warning") == [
        {"kind": "notification", "source": "tickets.auto_close_warning", "user_id": member.id, "channel_id": channel.id}
    ]
    [failed] = await audit_events(tenv.app, "notifications.failed")
    assert failed.target_type == "user" and failed.target_id == str(member.id)
    assert "Missing Permissions" not in str(failed.details)
    # The next attempt (the failure was not recorded as sent) delivers it; success reports nothing.
    channel.fail_send = None
    assert await tenv.app.notifier.notify_channel(
        channel_id=channel.id, spec=_spec(tenv), kind="tickets.auto_close_warning", dedupe_key="c2", user_id=member.id
    )
    assert len(recorder.events) == 1


async def test_duplicates_and_successes_report_nothing_even_after_a_restart(tenv: TestEnv) -> None:
    recorder = FailureRecorder(tenv.app)
    channel = _fallback(tenv)
    assert await tenv.app.notifier.notify_channel(channel_id=channel.id, spec=_spec(tenv), kind=KIND, dedupe_key="d")
    app = restarted_app(tenv)
    restarted = FailureRecorder(app)
    assert await app.notifier.notify_channel(channel_id=channel.id, spec=_spec(tenv), kind=KIND, dedupe_key="d") is None
    assert len(channel.messages) == 1
    assert recorder.events == [] and restarted.events == []


async def test_a_channel_post_failure_alerts_staff_without_a_member(tmp_path: Path) -> None:
    from tests.operations.util import make_ops, run_tasks, staff_channel

    ops = await make_ops(tmp_path)
    try:
        await ops.app.notifier.notify_channel(
            channel_id=987654321, spec=_spec(ops), kind="tickets.staff_alert", dedupe_key="c3"
        )
        await run_tasks(ops)
        values = {"user_mention": "-", "notification": "tickets.staff_alert"}
        expected = ops.app.content.render(ok.failure_key(ok.FAILURE_NOTIFICATION, "description"), **values)
        assert [m.embeds[0].description for m in staff_channel(ops).messages] == [expected]
    finally:
        await ops.close()


async def test_failures_of_operations_own_posts_are_audited_but_never_alerted(tmp_path: Path) -> None:
    from tests.operations.util import make_ops, run_tasks, staff_channel

    ops = await make_ops(tmp_path)
    try:
        staff_channel(ops).fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        await ops.app.notifier.notify_channel(
            channel_id=staff_channel(ops).id,
            spec=_spec(ops),
            kind="operations.reminder.unclaimed",
            dedupe_key="c4",
        )
        await run_tasks(ops)
        [failed] = await audit_events(ops.app, "notifications.failed")
        assert failed.details == {"kind": "operations.reminder.unclaimed"}
        tasks = await ops.app.db.run(task_repo.list_tasks, limit=500)
        assert [t for t in tasks if (t.dedupe_key or "").startswith("operations.alert:failure")] == []
    finally:
        await ops.close()


# -- callers do not report twice ------------------------------------------------------------------------------------


async def test_a_failed_review_request_channel_post_is_reported_exactly_once(tmp_path: Path) -> None:
    from tests.reviews.util import make_env, make_order, prompt_now

    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "channel"}})
    try:
        recorder = FailureRecorder(tenv.app)
        customer = tenv.member()
        order = await make_order(tenv, customer)
        assert order.channel is not None
        order.channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        await prompt_now(tenv, order)
        assert recorder.of(reviews_service.PROMPT_KIND) == [
            {
                "kind": "notification",
                "source": reviews_service.PROMPT_KIND,
                "user_id": customer.id,
                "channel_id": order.channel.id,
            }
        ]
        assert len(await audit_events(tenv.app, "notifications.failed")) == 1
        # A vanished order channel (no post attempted): reviews reports it itself, also once.
        other = tenv.member(name="other")
        vanished = await make_order(tenv, other)
        assert vanished.channel is not None
        await vanished.channel.delete()
        await prompt_now(tenv, vanished)
        assert len(recorder.of(reviews_service.PROMPT_KIND)) == 2
    finally:
        await tenv.close()


async def test_a_dm_then_channel_review_request_audits_the_dm_failure_only(tmp_path: Path) -> None:
    from tests.reviews.util import make_env, make_order, prompt_now

    tenv = await make_env(tmp_path, {"reviews": {"prompt_via": "dm_then_channel"}})
    try:
        recorder = FailureRecorder(tenv.app)
        customer: FakeMember = tenv.member()
        customer.dm_forbidden = True
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None and len(order.channel.messages) == 1  # delivered in the order channel
        [event] = await audit_events(tenv.app, "notifications.dm_failed")
        assert event.details == {"kind": reviews_service.PROMPT_KIND, "reason": "dm_forbidden"}
        assert recorder.events == []
        assert await tenv.app.db.run(notif_repo.already_sent, reviews_service.prompt_dedupe(order.id))
    finally:
        await tenv.close()


async def test_a_failed_ticket_staff_alert_is_reported_exactly_once(tmp_path: Path) -> None:
    from tests.tickets.util import channel, make_env, open_ticket

    tenv = await make_env(tmp_path)
    try:
        recorder = FailureRecorder(tenv.app)
        channel(tenv, "staff_alerts").fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        _, ticket, _ = await open_ticket(tenv, tenv.member())
        assert ticket.is_open
        assert len(recorder.of("tickets.staff_alert")) == 1
        assert recorder.of("tickets.staff_alert")[0]["user_id"] is None  # addressed to staff
    finally:
        await tenv.close()
