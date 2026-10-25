"""Staff reminders: unclaimed, unpaid and unfulfilled orders, unanswered support tickets; repeats, restarts."""

from __future__ import annotations

from pathlib import Path

from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_views
from shopbot.features.operations import content_keys as ok
from shopbot.features.operations import reminders, repo
from tests.checkout.util import add_product, place_order, restarted_app
from tests.helpers import TestEnv
from tests.operations.util import click_record, make_ops, mark, staff_channel
from tests.tickets.util import open_ticket

MINUTE = 60


async def _env(tmp_path: Path, **operations: int) -> TestEnv:
    tenv = await make_ops(tmp_path, {"operations": {"owner_dm_alerts": False, **operations}})
    assert tenv.app.ready("operations"), tenv.app.readiness.statuses["operations"].problems
    return tenv


async def _order(tenv: TestEnv) -> m.Order:
    product = await add_product(tenv, "[p1]", price=1000, stock=5)
    placed, _, _ = await place_order(tenv, tenv.member(), [(product.id, 1)])
    return placed


def _reminders(tenv: TestEnv, kind: str) -> list[str]:
    """Descriptions of the reminders of ``kind`` in the staff channel (fixture texts start with the key's token)."""
    token = tenv.text(ok.reminder_key(kind, "description")).split(" ", 1)[0]
    return [
        msg.embeds[0].description or ""
        for msg in staff_channel(tenv).messages
        if (msg.embeds[0].description or "").startswith(token)
    ]


async def test_unclaimed_and_unpaid_orders_are_reminded_once(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unclaimed_order_minutes=30, unpaid_order_minutes=60)
    try:
        placed = await _order(tenv)
        tenv.clock.advance(29 * MINUTE)
        assert await reminders.sweep(tenv.app) == 0
        tenv.clock.advance(MINUTE)
        assert await reminders.sweep(tenv.app) == 1
        [unclaimed] = _reminders(tenv, repo.UNCLAIMED)
        assert f"<t:{placed.created_at}:R>" in unclaimed and " 30" in unclaimed
        tenv.clock.advance(30 * MINUTE)
        assert await reminders.sweep(tenv.app) == 1
        assert len(_reminders(tenv, repo.UNPAID)) == 1
        # No repeat interval: never again, however often the sweep runs.
        tenv.clock.advance(24 * 60 * MINUTE)
        assert await reminders.sweep(tenv.app) == 0
        assert len(_reminders(tenv, repo.UNCLAIMED)) == 1 and len(_reminders(tenv, repo.UNPAID)) == 1
    finally:
        await tenv.close()


async def test_reminders_repeat_while_the_record_still_waits(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unclaimed_order_minutes=10, reminder_repeat_minutes=20)
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed = await _order(tenv)
        tenv.clock.advance(10 * MINUTE)
        await reminders.sweep(tenv.app)
        tenv.clock.advance(19 * MINUTE)
        await reminders.sweep(tenv.app)
        assert len(_reminders(tenv, repo.UNCLAIMED)) == 1
        tenv.clock.advance(MINUTE)
        await reminders.sweep(tenv.app)
        assert len(_reminders(tenv, repo.UNCLAIMED)) == 2
        # Claiming stops the reminders; releasing the claim starts a new waiting period.
        await click_record(tenv, staffer, placed.id, staff_views.claim_id(placed.id))
        tenv.clock.advance(60 * MINUTE)
        await reminders.sweep(tenv.app)
        assert len(_reminders(tenv, repo.UNCLAIMED)) == 2
        await click_record(tenv, staffer, placed.id, staff_views.unclaim_id(placed.id))
        released_at = tenv.clock.now()
        tenv.clock.advance(10 * MINUTE)
        await reminders.sweep(tenv.app)
        latest = _reminders(tenv, repo.UNCLAIMED)
        assert len(latest) == 3 and f"<t:{released_at}:R>" in latest[-1]
    finally:
        await tenv.close()


async def test_paid_orders_not_completed_are_reminded_from_the_payment(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unfulfilled_order_minutes=60)
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed = await _order(tenv)
        tenv.clock.advance(120 * MINUTE)
        assert await reminders.sweep(tenv.app) == 0  # not paid yet
        await mark(tenv, staffer, placed.id, m.PAID)
        paid_at = tenv.clock.now()
        tenv.clock.advance(60 * MINUTE)
        assert await reminders.sweep(tenv.app) == 1
        [reminder] = _reminders(tenv, repo.UNFULFILLED)
        assert f"<t:{paid_at}:R>" in reminder
        reminder_message = staff_channel(tenv).messages[-1]
        assert reminder_message.content == f"<@&{tenv.staff_role.id}>"
        await mark(tenv, staffer, placed.id, m.FULFILLED)
        tenv.clock.advance(600 * MINUTE)
        assert await reminders.sweep(tenv.app) == 0
    finally:
        await tenv.close()


async def test_unanswered_tickets_until_staff_reply(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unanswered_ticket_minutes=15)
    try:
        staffer = tenv.member(name="staffer", staff=True)
        customer = tenv.member(name="ticket-customer")
        _, ticket, ticket_channel = await open_ticket(tenv, customer)
        tenv.clock.advance(15 * MINUTE)
        assert await reminders.sweep(tenv.app) == 1  # no staff reply since it was opened
        [first] = _reminders(tenv, repo.UNANSWERED)
        assert f"<#{ticket_channel.id}>" in first
        await reminders.on_message(tenv.app, ticket_channel.add_user_message(staffer, "[staff reply]"))
        tenv.clock.advance(60 * MINUTE)
        assert await reminders.sweep(tenv.app) == 0
        await reminders.on_message(tenv.app, ticket_channel.add_user_message(customer, "[question]"))
        asked_at = tenv.clock.now()
        await reminders.on_message(tenv.app, ticket_channel.add_user_message(customer, "[question again]"))
        tenv.clock.advance(15 * MINUTE)
        assert await reminders.sweep(tenv.app) == 1
        assert f"<t:{asked_at}:R>" in _reminders(tenv, repo.UNANSWERED)[-1]
        # Bot messages never count as a staff reply.
        await reminders.on_message(tenv.app, ticket_channel.add_user_message(tenv.guild.me, "[bot]"))
        assert await tenv.app.db.run(repo.awaiting_since, ticket.id, ticket.created_at) == asked_at
    finally:
        await tenv.close()


async def test_reminders_are_never_sent_twice_after_a_restart(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unpaid_order_minutes=5, reminder_repeat_minutes=60)
    try:
        await _order(tenv)
        tenv.clock.advance(5 * MINUTE)
        await reminders.sweep(tenv.app)
        app = restarted_app(tenv)
        await app.resources.refresh()
        assert await reminders.sweep(app) == 0
        assert len(_reminders(tenv, repo.UNPAID)) == 1
        tenv.clock.advance(60 * MINUTE)
        assert await reminders.sweep(app) == 1
        assert len(_reminders(tenv, repo.UNPAID)) == 2
    finally:
        await tenv.close()


async def test_the_sweep_task_reschedules_itself_and_respects_readiness(tmp_path: Path) -> None:
    tenv = await _env(tmp_path, unpaid_order_minutes=5)
    try:
        await _order(tenv)
        from shopbot.features.operations import on_ready

        await on_ready(tenv.app)
        tenv.clock.advance(reminders.SWEEP_SECONDS + 5 * MINUTE)
        await tenv.app.scheduler.run_due()
        assert len(_reminders(tenv, repo.UNPAID)) == 1
        task = await tenv.app.db.run(task_repo.get_by_dedupe, reminders.SWEEP_KEY)
        assert task is not None and task.status == "pending" and task.run_at > tenv.clock.now()
    finally:
        await tenv.close()


async def test_disabled_reminders_send_nothing(ops: TestEnv) -> None:
    await _order(ops)
    ops.clock.advance(10_000 * MINUTE)
    assert await reminders.sweep(ops.app) == 0
    assert reminders.active_kinds(ops.app) == ()


async def test_reminder_texts_are_needed_only_when_switched_on(tmp_path: Path) -> None:
    empty = {ok.reminder_key(kind, "description"): "" for kind in repo.REMINDER_KINDS}
    tenv = await make_ops(tmp_path, content=empty)
    try:
        assert tenv.app.ready("operations")
    finally:
        await tenv.close()
    tenv = await make_ops(tmp_path / "on", {"operations": {"unpaid_order_minutes": 5}}, content=empty)
    try:
        problems = tenv.app.readiness.statuses["operations"].problems
        assert [p.key for p in problems] == [ok.reminder_key(repo.UNPAID, "description")]
    finally:
        await tenv.close()
