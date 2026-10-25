"""The Customer role (``checkout.customer_role``): given when one of a member's orders reaches
``checkout.customer_role_on`` ("paid" or "fulfilled"), removed when an order is fully refunded and none of their
other orders qualifies any more (``checkout.customer_role_remove_on_refund``).

Each change is a durable task (``operations.customer_role``, dedupe key per order and action) scheduled from the
order's status-change follow-up, so it survives a restart. The task re-reads the order and the member's other orders
and checks the member's current roles before acting, so repeating it changes nothing (idempotent). Every change is
audited; a role that does not exist, a role above the bot's highest role, or a refused request (403) is audited and
reported to staff (``FAILURE`` kind "role"); a member who left the server is skipped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from shopbot.core.events import FAILURE
from shopbot.core.permissions import can_manage_role
from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.repos.tasks import Task
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import repo
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_CUSTOMER_ROLE = "operations.customer_role"
ADD = "add"
REMOVE = "remove"


def task_key(order_id: int, action: str) -> str:
    return f"{TASK_CUSTOMER_ROLE}:{order_id}:{action}"


async def on_order_status_changed(app: App, *, order_id: int, status: str, **_: Any) -> None:
    """Schedule the role change an order's new status calls for (deduplicated per order and action)."""
    c = app.settings.checkout
    if not c.customer_role:
        return
    if status == c.customer_role_on:
        action = ADD
    elif status == checkout_models.REFUNDED and c.customer_role_remove_on_refund:
        action = REMOVE
    else:
        return
    await app.scheduler.schedule(
        TASK_CUSTOMER_ROLE,
        run_at=app.clock.now(),
        payload={"order_id": order_id, "action": action},
        dedupe_key=task_key(order_id, action),
    )


async def _report(app: App, order: checkout_models.Order, action: str, reason: str, role_id: int | None) -> None:
    log_event(log, logging.WARNING, "customer_role_failed", order_id=order.id, action=action, reason=reason)
    await app.audit.record(
        "checkout.customer_role_failed",
        actor_id=None,
        target_type="user",
        target_id=order.user_id,
        details={"number": order.number, "action": action, "reason": reason},
    )
    await app.events.publish(
        FAILURE, kind="role", source=f"{order.id}:{action}", user_id=order.user_id, role_id=role_id
    )


async def customer_role_task(app: App, task: Task) -> None:
    """``operations.customer_role``: give or remove the customer role for the member of one order."""
    try:
        order_id = int(task.payload["order_id"])
    except (KeyError, TypeError, ValueError):
        raise PermanentTaskError("payload has no order_id") from None
    action = task.payload.get("action")
    if action not in (ADD, REMOVE):
        raise PermanentTaskError("payload has no valid action")
    c = app.settings.checkout
    if not c.customer_role or (action == REMOVE and not c.customer_role_remove_on_refund):
        return  # switched off meanwhile
    order = await checkout_service.get_order(app, order_id)
    if order is None:
        return
    statuses = repo.qualifying_statuses(c.customer_role_on)
    qualifies = await app.db.run(repo.has_qualifying_order, app.env.guild_id, order.user_id, statuses)
    if (action == ADD) != qualifies:
        return  # nothing to do: e.g. refunded before the role was given, or another order still qualifies
    guild = app.guild
    if guild is None:
        raise RuntimeError("guild unavailable")  # retried later
    role = app.resources.role(guild, c.customer_role)
    if role is None:
        await _report(app, order, action, "role_missing", None)
        return
    try:
        member = guild.get_member(order.user_id) or await guild.fetch_member(order.user_id)
    except discord.NotFound:
        log_event(log, logging.INFO, "customer_role_member_left", order_id=order.id, action=action)
        return
    has_role = any(r.id == role.id for r in member.roles)
    if (action == ADD) == has_role:
        return  # already as it should be
    if not can_manage_role(guild.me, role):
        await _report(app, order, action, "role_hierarchy", role.id)
        return
    reason = f"order {order.number}: {action} customer role"
    try:
        if action == ADD:
            await member.add_roles(role, reason=reason)
        else:
            await member.remove_roles(role, reason=reason)
    except discord.Forbidden:
        await _report(app, order, action, "forbidden", role.id)
        return
    await app.audit.record(
        "checkout.customer_role_added" if action == ADD else "checkout.customer_role_removed",
        actor_id=None,
        target_type="user",
        target_id=order.user_id,
        details={"number": order.number, "role_id": role.id},
    )
