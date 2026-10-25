"""The Customer role: given on checkout.customer_role_on, removed after a full refund, idempotent, audited, failures
reported."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.features.checkout import models as m
from shopbot.features.checkout import views
from shopbot.features.operations import content_keys as ok
from shopbot.features.operations import customer_role
from tests.checkout.util import add_product, order, place_order, submit_modal
from tests.fakes import FakeMember, http_error
from tests.helpers import TestEnv
from tests.operations.util import audit_events, make_ops, mark, run_tasks, staff_channel
from tests.operations.util import customer_role as role_of


async def _order(tenv: TestEnv, customer: FakeMember | None = None) -> tuple[m.Order, FakeMember]:
    customer = customer or tenv.member()
    product = await add_product(tenv, f"[p{tenv.clock.now()}]", price=1000, stock=50)
    placed, _, _ = await place_order(tenv, customer, [(product.id, 1)])
    return placed, customer


def _has(member: FakeMember, tenv: TestEnv) -> bool:
    return role_of(tenv) in member.roles


async def _refund_fully(tenv: TestEnv, staffer: FakeMember, placed: m.Order) -> None:
    await submit_modal(
        tenv.app,
        user=staffer,
        custom_id=views.refund_form_id(placed.id, views.MODE_MONEY),
        fields={views.REFUND_AMOUNT_INPUT: "10.00", views.REFUND_REASON_INPUT: ""},
    )
    assert (await order(tenv, placed.id)).status == m.REFUNDED


async def test_role_is_given_on_fulfilment_and_removed_after_a_full_refund(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, customer = await _order(ops)
    await mark(ops, staffer, placed.id, m.PAID)
    await run_tasks(ops)
    assert not _has(customer, ops)  # paid is not enough with customer_role_on = "fulfilled"
    await mark(ops, staffer, placed.id, m.FULFILLED)
    await run_tasks(ops)
    assert _has(customer, ops)
    [added] = await audit_events(ops, "checkout.customer_role_added")
    assert added.target_id == str(customer.id) and added.details["number"] == placed.number
    await _refund_fully(ops, staffer, placed)
    await run_tasks(ops)
    assert not _has(customer, ops)
    assert len(await audit_events(ops, "checkout.customer_role_removed")) == 1


async def test_role_on_payment_when_configured(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, {"checkout": {"customer_role_on": "paid"}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed, customer = await _order(tenv)
        await mark(tenv, staffer, placed.id, m.PAID)
        await run_tasks(tenv)
        assert _has(customer, tenv)
    finally:
        await tenv.close()


async def test_role_stays_while_another_order_qualifies(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    first, customer = await _order(ops)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, first.id, target)
    ops.clock.advance(60)
    second, _ = await _order(ops, customer)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, second.id, target)
    await run_tasks(ops)
    assert _has(customer, ops)
    await _refund_fully(ops, staffer, first)
    await run_tasks(ops)
    assert _has(customer, ops)  # the second order still qualifies
    assert await audit_events(ops, "checkout.customer_role_removed") == []


async def test_removal_can_be_switched_off(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, {"checkout": {"customer_role_remove_on_refund": False}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed, customer = await _order(tenv)
        for target in (m.PAID, m.FULFILLED):
            await mark(tenv, staffer, placed.id, target)
        await run_tasks(tenv)
        assert _has(customer, tenv)
        await _refund_fully(tenv, staffer, placed)
        await run_tasks(tenv)
        assert _has(customer, tenv)
    finally:
        await tenv.close()


async def test_an_order_refunded_before_the_role_task_ran_gives_no_role(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, customer = await _order(ops)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, placed.id, target)
    await _refund_fully(ops, staffer, placed)
    await run_tasks(ops)  # the "add" task re-checks: no order qualifies any more
    assert not _has(customer, ops)
    assert await audit_events(ops, "checkout.customer_role_added") == []


async def test_repeating_the_task_changes_nothing(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, customer = await _order(ops)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, placed.id, target)
    await run_tasks(ops)
    calls: list[Any] = []
    original = customer.add_roles

    async def counting(*roles: Any, **kwargs: Any) -> None:
        calls.append(roles)
        await original(*roles, **kwargs)

    customer.add_roles = counting  # type: ignore[method-assign]
    # The status-change follow-up runs again (as after a crash): the deduplicated task is not repeated, and a forced
    # re-run of the handler sees the role already given.
    await customer_role.on_order_status_changed(ops.app, order_id=placed.id, status=m.FULFILLED)
    from shopbot.db.repos.tasks import Task

    task = Task(
        1, customer_role.TASK_CUSTOMER_ROLE, 0, {"order_id": placed.id, "action": "add"}, "running", 1, 5, None, None
    )
    await customer_role.customer_role_task(ops.app, task)
    await run_tasks(ops)
    assert calls == [] and _has(customer, ops)
    assert len(await audit_events(ops, "checkout.customer_role_added")) == 1


async def test_forbidden_is_audited_and_reported_to_staff(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, customer = await _order(ops)

    async def refuse(*roles: Any, **kwargs: Any) -> None:
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    customer.add_roles = refuse  # type: ignore[method-assign]
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, placed.id, target)
    await run_tasks(ops)
    assert not _has(customer, ops)
    [failed] = await audit_events(ops, "checkout.customer_role_failed")
    assert failed.details == {"number": placed.number, "action": "add", "reason": "forbidden"}
    values = {"user_mention": f"<@{customer.id}>", "role_mention": role_of(ops).mention}
    expected = ops.app.content.render(ok.failure_key(ok.FAILURE_ROLE, "description"), **values)
    assert expected in [msg.embeds[0].description for msg in staff_channel(ops).messages]


async def test_role_above_the_bot_is_reported_not_attempted(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    role_of(ops).position = 99  # above the bot's role
    placed, customer = await _order(ops)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, placed.id, target)
    await run_tasks(ops)
    assert not _has(customer, ops)
    [failed] = await audit_events(ops, "checkout.customer_role_failed")
    assert failed.details["reason"] == "role_hierarchy"


async def test_a_member_who_left_is_skipped(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, customer = await _order(ops)
    for target in (m.PAID, m.FULFILLED):
        await mark(ops, staffer, placed.id, target)
    ops.guild._members.pop(customer.id)
    await run_tasks(ops)
    assert await audit_events(ops, "checkout.customer_role_added") == []
    assert await audit_events(ops, "checkout.customer_role_failed") == []


async def test_no_role_configured_does_nothing(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, {"checkout": {"customer_role": ""}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        placed, customer = await _order(tenv)
        for target in (m.PAID, m.FULFILLED):
            await mark(tenv, staffer, placed.id, target)
        await run_tasks(tenv)
        assert not _has(customer, tenv)
        from tests.operations.util import tasks_by_prefix

        assert await tasks_by_prefix(tenv, customer_role.TASK_CUSTOMER_ROLE) == []
    finally:
        await tenv.close()
