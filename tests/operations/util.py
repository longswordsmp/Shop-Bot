"""Helpers for the operations tests: an environment with the staff alert channel, ping role, customer role and the
Owner in the fake guild, plus shortcuts for the live order record's controls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_views
from shopbot.features.operations import alerts
from tests.checkout.util import channel, make_env, order, press, staff_message
from tests.fakes import FakeInteraction, FakeMember, FakeMessage, FakeRole, FakeTextChannel, submit_modal
from tests.helpers import (
    GUILD_ID,
    OWNER_ID,
    TestEnv,
    deep_merge,
    make_banner_env,
    provision_channels,
    server_extension,
)

STAFF_CHANNEL = "staff_alerts"
# As ``tests.checkout.util.make_env``: many clicks by the same fake user happen within milliseconds.
CHECKOUT_TEST_SETTINGS: dict[str, Any] = {"bot": {"rate_limit_per_10s": 100}}

# Operations on: alerts in the base layout's staff_alerts channel, the staff role pinged, the Owner alerted, the
# customer role given on fulfilment. Thresholds stay 0 unless a test switches them on.
OPS_SETTINGS: dict[str, Any] = deep_merge(
    server_extension(roles=[{"key": "customer"}]),
    {
        "operations": {"staff_channel": STAFF_CHANNEL, "ping_roles": ["staff"], "owner_dm_alerts": True},
        "checkout": {"customer_role": "customer", "customer_role_on": "fulfilled"},
    },
)


async def make_ops(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    tenv = await make_env(tmp_path, deep_merge(OPS_SETTINGS, overrides or {}), **kwargs)
    return await _with_roles_and_owner(tenv)


async def make_banner_ops(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    """``make_ops`` with banners switched on (``make_banner_env``: a tiny picture for every banner id)."""
    settings = deep_merge(deep_merge(CHECKOUT_TEST_SETTINGS, OPS_SETTINGS), overrides or {})
    tenv = await make_banner_env(tmp_path, settings, **kwargs)
    await provision_channels(tenv)
    return await _with_roles_and_owner(tenv)


async def _with_roles_and_owner(tenv: TestEnv) -> TestEnv:
    role = tenv.guild.add_role(name="customer", position=5)
    await tenv.app.resources.remember("role", "customer", role.id)
    tenv.guild.add_member(id=OWNER_ID, name="owner")
    return tenv


def customer_role(tenv: TestEnv) -> FakeRole:
    role = tenv.app.resources.role(tenv.guild, "customer")  # type: ignore[arg-type]
    assert isinstance(role, FakeRole)
    return role


def staff_channel(tenv: TestEnv) -> FakeTextChannel:
    return channel(tenv, STAFF_CHANNEL)


def owner(tenv: TestEnv) -> FakeMember:
    found = tenv.guild.get_member(OWNER_ID)
    assert found is not None
    return found


def texts(messages: list[FakeMessage]) -> list[str]:
    return [message.text() for message in messages]


async def run_tasks(tenv: TestEnv, *, rounds: int = 3) -> None:
    """Run every due task (a few rounds, so tasks scheduled by tasks run too)."""
    for _ in range(rounds):
        if not await tenv.app.scheduler.run_due():
            return


async def tasks_by_prefix(tenv: TestEnv, prefix: str) -> list[task_repo.Task]:
    found = await tenv.app.db.run(task_repo.list_tasks, limit=500)
    return [t for t in found if (t.dedupe_key or "").startswith(prefix)]


async def audit_events(tenv: TestEnv, action: str) -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=100)
    return [e for e in reversed(events) if e.action == action]


async def activity(tenv: TestEnv, order_id: int, kind: str | None = None) -> list[h.Activity]:
    return await tenv.app.db.run(h.activity, order_id, None if kind is None else (kind,))


async def handling(tenv: TestEnv, order_id: int) -> h.Handling:
    return await tenv.app.db.run(h.get_handling, order_id)


async def record(tenv: TestEnv, order_id: int) -> FakeMessage:
    return staff_message(tenv, await order(tenv, order_id))


async def click_record(tenv: TestEnv, user: FakeMember, order_id: int, custom_id: str) -> FakeInteraction:
    return await press(tenv, user, await record(tenv, order_id), custom_id)


async def submit_form(
    tenv: TestEnv, user: FakeMember, custom_id: str, input_id: str, text: str, *, app: Any = None
) -> FakeInteraction:
    return await submit_modal(app or tenv.app, user=user, custom_id=custom_id, fields={input_id: text})


async def add_note(tenv: TestEnv, user: FakeMember, order_id: int, text: str) -> FakeInteraction:
    return await submit_form(tenv, user, staff_views.note_form_id(order_id), staff_views.NOTE_INPUT, text)


async def request_correction(tenv: TestEnv, user: FakeMember, order_id: int, text: str) -> FakeInteraction:
    return await submit_form(tenv, user, staff_views.correction_form_id(order_id), staff_views.MESSAGE_INPUT, text)


async def set_tracking(tenv: TestEnv, user: FakeMember, order_id: int, text: str) -> FakeInteraction:
    return await submit_form(tenv, user, staff_views.tracking_form_id(order_id), staff_views.TRACKING_INPUT, text)


async def mark(tenv: TestEnv, user: FakeMember, order_id: int, target: str) -> FakeInteraction:
    """Press a status button of the live record as rendered for the order's current status."""
    from shopbot.features.checkout import views

    current = await order(tenv, order_id)
    return await press(
        tenv, user, staff_message(tenv, current), views.staff_action_id(target, order_id, current.status)
    )


def field_values(message: FakeMessage, name: str) -> list[str]:
    return [f.value or "" for e in message.embeds for f in e.fields if f.name == name]


def alert_key(alert: str, order_id: int) -> str:
    return alerts.order_alert_key(alert, order_id)


ORDER_FINAL = (m.FULFILLED, m.CANCELLED, m.REJECTED, m.EXPIRED, m.REFUNDED)
