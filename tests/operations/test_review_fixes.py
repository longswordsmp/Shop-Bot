"""Regressions found by the review of the specification build (staff record limits, notification alerts, panels)."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features.checkout import staff
from shopbot.features.checkout import views as checkout_views
from shopbot.ui.limits import validate_message
from tests.checkout.util import (
    add_product,
    order,
    place_order,
    reject,
    session_of,
    staff_message,
    start,
    ticket_channel_of,
    workflow_message,
)
from tests.fakes import click
from tests.helpers import TestEnv
from tests.operations.util import audit_events, make_ops, mark, run_tasks, staff_channel


async def test_a_very_long_rejection_reason_never_breaks_the_full_record(ops: TestEnv) -> None:
    customer, staffer = ops.member(), ops.member(name="staffer", staff=True)
    product = await add_product(ops, "[p1]", stock=3)
    placed, _, _ = await place_order(ops, customer, [(product.id, 1)])
    await reject(ops, staffer, staff_message(ops, placed), placed.id, reason="*" * 1000)  # escaping doubles it
    rejected = await order(ops, placed.id)
    for spec in await staff.lookup_messages(ops.app, rejected):
        assert validate_message(spec) == []


async def test_a_review_prompt_delivered_in_the_order_channel_raises_no_failure_alert(tmp_path: Path) -> None:
    from shopbot.features.checkout import models as m

    ops = await make_ops(tmp_path, {"reviews": {"prompt_via": "dm_then_channel"}})
    try:
        customer, staffer = ops.member(), ops.member(name="staffer", staff=True)
        product = await add_product(ops, "[p1]", stock=3)
        placed, _, _ = await place_order(ops, customer, [(product.id, 1)])
        await run_tasks(ops)
        await mark(ops, staffer, placed.id, m.PAID)
        await run_tasks(ops)
        customer.dm_forbidden = True
        before = len(staff_channel(ops).messages)
        await mark(ops, staffer, placed.id, m.FULFILLED)
        await run_tasks(ops, rounds=6)
        failed = await audit_events(ops, "notifications.failed")
        assert all(e.details.get("kind") != "reviews.prompt" for e in failed)
        new_alerts = [msg for msg in staff_channel(ops).messages[before:] if "reviews.prompt" in msg.text()]
        assert new_alerts == []
    finally:
        await ops.close()


async def test_start_reposts_a_deleted_live_panel(ops: TestEnv) -> None:
    customer = ops.member()
    _, session, channel, message = await start(ops, customer)
    await message.delete()
    again = await click(ops.app, user=customer, custom_id=checkout_views.start_id())
    assert again.last_sent is not None
    reposted = workflow_message(await ticket_channel_of(ops, session.ticket_id))
    assert reposted.id != message.id and not reposted.deleted
    assert (await session_of(ops, customer)).message_id == reposted.id
    assert isinstance(channel, discord.abc.Messageable)
