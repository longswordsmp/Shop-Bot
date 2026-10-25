"""Banners on the operations messages: staff alerts (new and paid orders, failures) show the system-notifications
banner, reminders the reminder banner, and the Owner's private alerts the new-message banner - also when one is
posted in the staff channel because the direct message failed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.core.events import FAILURE
from shopbot.features.checkout import models as m
from shopbot.features.operations import MODULE as OPERATIONS
from shopbot.features.operations import content_keys as ok
from shopbot.features.operations import reminders, repo
from shopbot.ui.spec import MessageSpec
from tests.checkout.util import add_product, place_order
from tests.fakes import FakeMessage
from tests.helpers import OWNER_ID, TestEnv, banner_names
from tests.operations.util import make_banner_ops, mark, owner, run_tasks, staff_channel
from tests.tickets.util import open_ticket

MINUTE = 60


@pytest.fixture
async def bops(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Operations ready with banners on: staff alerts in staff_alerts, the Owner alerted by direct message."""
    env = await make_banner_ops(tmp_path)
    try:
        assert env.app.ready("operations"), env.app.readiness.statuses["operations"].problems
        yield env
    finally:
        await env.close()


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def token(tenv: TestEnv, key: str) -> str:
    """The fixture token a text of ``key`` starts with (fixture texts are ``[tN] {placeholders}``)."""
    return tenv.text(key).split(" ", 1)[0]


def described(tenv: TestEnv, key: str) -> list[FakeMessage]:
    """Staff channel messages whose description is the text of ``key``."""
    return [
        msg for msg in staff_channel(tenv).messages if (msg.embeds[0].description or "").startswith(token(tenv, key))
    ]


async def _order(tenv: TestEnv, name: str = "customer") -> m.Order:
    product = await add_product(tenv, f"[{name}]", price=1000, stock=5)
    placed, _, _ = await place_order(tenv, tenv.member(name=name), [(product.id, 1)])
    return placed


# -- order alerts and the Owner's private alerts -----------------------------------------------------------------


async def test_order_alerts_show_the_staff_banner_and_the_owners_dm_the_message_banner(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    placed = await _order(bops)
    await run_tasks(bops)
    [new_order] = described(bops, ok.alert_key(ok.NEW_ORDER, "description"))
    assert banner_names(new_order) == [banner_file(bops, "staff-notifications")]
    assert banner_names(owner(bops).dms[-1]) == [banner_file(bops, "direct-message-notification")]
    await mark(bops, staffer, placed.id, m.PAID)
    await run_tasks(bops)
    [paid] = described(bops, ok.alert_key(ok.PAID_ORDER, "description"))
    assert banner_names(paid) == [banner_file(bops, "staff-notifications")]
    assert len(owner(bops).dms) == 2
    assert banner_names(owner(bops).dms[-1]) == [banner_file(bops, "direct-message-notification")]


async def test_the_owners_alert_keeps_its_banner_when_posted_in_the_staff_channel(bops: TestEnv) -> None:
    owner(bops).dm_forbidden = True
    await _order(bops)
    await run_tasks(bops)
    [fallback] = [msg for msg in staff_channel(bops).messages if msg.content == f"<@{OWNER_ID}>"]
    assert banner_names(fallback) == [banner_file(bops, "direct-message-notification")]
    [staff_alert] = described(bops, ok.alert_key(ok.NEW_ORDER, "description"))
    assert staff_alert is not fallback
    assert banner_names(staff_alert) == [banner_file(bops, "staff-notifications")]


# -- failure alerts ----------------------------------------------------------------------------------------------------


async def test_failure_alerts_show_the_staff_banner(bops: TestEnv) -> None:
    events = {
        ok.FAILURE_NOTIFICATION: {"kind": "notification", "source": "checkout.paid", "user_id": 5},
        ok.FAILURE_ERROR: {"kind": "interaction", "source": "zz:boom", "error_id": "abc", "error": "RuntimeError"},
        ok.FAILURE_ROLE: {"kind": "role", "source": "customer_role", "user_id": 5, "role_id": 6},
        ok.FAILURE_TASK: {"kind": "task", "source": "test.task", "task_id": 42, "attempts": 5},
    }
    for payload in events.values():
        await bops.app.events.publish(FAILURE, **payload)
    await run_tasks(bops)
    for failure in events:
        [alert] = described(bops, ok.failure_key(failure, "description"))
        assert banner_names(alert) == [banner_file(bops, "staff-notifications")], failure
    # A failed task is critical: the Owner's private alert shows the new-message banner.
    [dm] = owner(bops).dms
    assert (dm.embeds[0].description or "").startswith(
        token(bops, ok.owner_failure_key(ok.FAILURE_TASK, "description"))
    )
    assert banner_names(dm) == [banner_file(bops, "direct-message-notification")]


# -- reminders ---------------------------------------------------------------------------------------------------------


async def test_every_reminder_round_is_a_new_message_with_the_reminder_banner(tmp_path: Path) -> None:
    tenv = await make_banner_ops(
        tmp_path,
        {
            "operations": {
                "owner_dm_alerts": False,
                "unclaimed_order_minutes": 10,
                "unpaid_order_minutes": 10,
                "unanswered_ticket_minutes": 10,
                "reminder_repeat_minutes": 20,
            }
        },
    )
    try:
        await _order(tenv)
        await open_ticket(tenv, tenv.member(name="ticket-customer"))
        tenv.clock.advance(10 * MINUTE)
        await reminders.sweep(tenv.app)
        tenv.clock.advance(20 * MINUTE)
        await reminders.sweep(tenv.app)
        for kind in (repo.UNCLAIMED, repo.UNPAID, repo.UNANSWERED):
            rounds = described(tenv, ok.reminder_key(kind, "description"))
            assert len(rounds) == 2, kind
            assert all(banner_names(msg) == [banner_file(tenv, "reminder")] for msg in rounds), kind
    finally:
        await tenv.close()


async def test_the_unfulfilled_reminder_shows_the_reminder_banner(tmp_path: Path) -> None:
    tenv = await make_banner_ops(tmp_path, {"operations": {"owner_dm_alerts": False, "unfulfilled_order_minutes": 10}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed = await _order(tenv)
        await mark(tenv, staffer, placed.id, m.PAID)
        tenv.clock.advance(10 * MINUTE)
        await reminders.sweep(tenv.app)
        [reminder] = described(tenv, ok.reminder_key(repo.UNFULFILLED, "description"))
        assert banner_names(reminder) == [banner_file(tenv, "reminder")]
    finally:
        await tenv.close()


# -- the preview shows what is sent ------------------------------------------------------------------------------


async def test_operations_preview_states_carry_their_banners(bops: TestEnv) -> None:
    assert OPERATIONS.preview is not None
    states = {state.name: state.spec for state in OPERATIONS.preview(bops.app)}
    expected: dict[str, BannerId] = {}
    for alert in ok.ORDER_ALERTS:
        expected[f"alert_{alert}"] = "staff-notifications"
        expected[f"owner_{alert}"] = "direct-message-notification"
    for kind in repo.REMINDER_KINDS:
        expected[f"reminder_{kind}"] = "reminder"
    for failure in ok.FAILURES:
        expected[f"failure_{failure}"] = "staff-notifications"
    for failure in ok.CRITICAL_FAILURES:
        expected[f"owner_failure_{failure}"] = "direct-message-notification"
    assert set(states) == set(expected)
    for name, banner_id in expected.items():
        spec = states[name]
        assert isinstance(spec, MessageSpec) and spec.banner is not None, name
        assert spec.banner.filename == banner_file(bops, banner_id), name
