"""Creation guards: channel-name length, interrupted creations and their durable recovery, the daily
creation cap, and the open-ticket limit on owner reopen."""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.db.repos import audit as audit_repo
from shopbot.features import core_keys as core
from shopbot.features.tickets import MODULE, repo, service, tasks
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets.models import CLOSED, CREATING, FAILED, KIND_SUPPORT, OPEN
from shopbot.ui.custom_ids import cid
from tests.fakes import click, http_error
from tests.helpers import TestEnv
from tests.tickets.util import (
    advance_past_cooldown,
    audit_actions,
    control_message,
    form_overrides,
    make_env,
    open_ticket,
    render,
    task,
    ticket_of,
    tickets,
)

PREFIX_KEY = k.category_key("general", "channel_prefix")
LONG_NAMES = {"tickets": {"channel_name_pattern": "{prefix}-{user}-{number}", "number_padding": 10}}


def _set_daily_cap(tenv: TestEnv, value: int) -> None:
    """Set tickets.max_created_per_user_per_day on the running settings."""
    t = dataclasses.replace(tenv.app.settings.tickets, max_created_per_user_per_day=value)
    tenv.app.settings = dataclasses.replace(tenv.app.settings, tickets=t)


def _general(tenv: TestEnv) -> Any:
    category = tenv.app.settings.tickets.category("general")
    assert category is not None
    return category


async def _create_order(tenv: TestEnv, member: Any) -> service.CreatedChannel:
    return await service.create_private_channel(
        tenv.app,
        member=member,
        kind="order",
        category_id="checkout",
        discord_category_key="orders",
        channel_prefix_key=PREFIX_KEY,
        extra_staff_role_keys=(),
        topic=None,
        limit=1,
    )


async def _create_failed_reasons(tenv: TestEnv) -> list[str]:
    events = await tenv.app.db.run(audit_repo.recent, tenv.app.env.guild_id, limit=100)
    return [str(e.details.get("reason")) for e in events if e.action == "tickets.create_failed"]


# -- channel names ------------------------------------------------------------------------------------------


async def test_too_long_channel_name_aborts_instead_of_cutting(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, LONG_NAMES)
    try:
        assert tenv.app.ready("tickets")  # the fixture prefix is short
        values = dict(tenv.content)
        values[PREFIX_KEY] = "p" * 60  # 60 + 1 + 32 + 1 + 10 = 104 characters
        tenv.app.content.replace(file_values=values)  # readiness not recomputed: the runtime check decides
        interaction = await click(tenv.app, user=tenv.member(name="u" * 40), custom_id=cid("tk", "open", "general"))
        assert interaction.last_sent is not None
        assert interaction.last_sent.embeds[0].description == tenv.text(core.ERROR_GENERIC)
        [ticket] = await tickets(tenv)
        assert ticket.status == FAILED and ticket.channel_id is None
        assert [c for kind, c in tenv.guild.audit if kind == "create_text_channel"] == []
        assert await _create_failed_reasons(tenv) == ["channel_name_too_long"]
        # A name that fits is used unchanged.
        values[PREFIX_KEY] = "p" * 56
        tenv.app.content.replace(file_values=values)
        member = tenv.member(name="u" * 40)
        _, created, ch = await open_ticket(tenv, member)
        assert ch.name == f"{'p' * 56}-{'u' * 32}-{created.number:010d}" and len(ch.name) == 100
    finally:
        await tenv.close()


async def test_worst_case_channel_name_is_a_content_problem(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, LONG_NAMES)
    try:
        app = tenv.app
        assert MODULE.content_problems(app.settings, app.content) == []
        values = dict(tenv.content)
        values[PREFIX_KEY] = "p" * 57  # 57 + 2 dashes + 32 ({user}) + 10 (padding) = 101
        app.content.replace(file_values=values)
        [problem] = MODULE.content_problems(app.settings, app.content)
        assert "101" in problem and PREFIX_KEY in problem
        app.recompute_readiness()
        assert not app.ready("tickets")
        values[PREFIX_KEY] = "p" * 56
        app.content.replace(file_values=values)
        app.recompute_readiness()
        assert app.ready("tickets")
    finally:
        await tenv.close()


def test_worst_case_counts_at_least_seven_number_digits_and_no_user_unless_used() -> None:
    assert k.channel_name_length("{prefix}-{number}", prefix=92, number=7, user=0) == 100
    assert k.channel_name_length("{prefix}-{number}-{prefix}", prefix=10, number=4, user=32) == 26
    assert k.channel_name_length("ticket {user}", prefix=0, number=0, user=32) == 39


async def test_shipped_configuration_has_no_channel_name_problem(tk: TestEnv) -> None:
    assert MODULE.content_problems(tk.app.settings, tk.app.content) == []
    tk.app.settings = dataclasses.replace(
        tk.app.settings,
        tickets=dataclasses.replace(tk.app.settings.tickets, channel_name_pattern="x" * 60 + "{prefix}{user}{number}"),
    )
    [problem] = MODULE.content_problems(tk.app.settings, tk.app.content)
    assert "tickets.channel_name_pattern" in problem


# -- interrupted creations ------------------------------------------------------------------------------------


async def test_unexpected_error_creating_the_channel_aborts_the_ticket(tk: TestEnv) -> None:
    tk.guild.fail_channel_create = OSError("connection reset by peer")
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tk.text(core.ERROR_GENERIC)
    [ticket] = await tickets(tk)
    assert ticket.status == FAILED
    assert await _create_failed_reasons(tk) == ["unexpected:OSError"]
    assert (await task(tk, service.dedupe_recover_creating(ticket.id))).status == "cancelled"
    tk.guild.fail_channel_create = None
    await open_ticket(tk, tk.member(name="next"))


async def test_unexpected_error_sending_the_welcome_removes_the_channel(tk: TestEnv) -> None:
    original = tk.guild.create_text_channel

    async def create_then_break(name, **kwargs):  # type: ignore[no-untyped-def]
        created = await original(name, **kwargs)
        created.fail_send = RuntimeError("Session is closed")  # e.g. the HTTP session closing on shutdown
        return created

    tk.guild.create_text_channel = create_then_break  # type: ignore[method-assign]
    customer = tk.member()
    general = _general(tk)
    with pytest.raises(service.TicketCreateFailed):
        await service.open_support_ticket(tk.app, member=customer, category=general, answers={})
    [ticket] = await tickets(tk)
    created = [c for kind, c in tk.guild.audit if kind == "create_text_channel"]
    assert ticket.status == FAILED and len(created) == 1 and created[0].deleted
    assert await _create_failed_reasons(tk) == ["unexpected:RuntimeError"]


async def test_creation_reserves_its_recovery_task_in_the_same_transaction(tkf: TestEnv) -> None:
    customer = tkf.member()
    created = await _create_order(tkf, customer)
    recovery = await task(tkf, service.dedupe_recover_creating(created.ticket.id))
    assert recovery.kind == service.TASK_RECOVER_CREATING and recovery.status == "pending"
    assert recovery.run_at == created.ticket.created_at + service.CREATING_STALE_SECONDS
    assert recovery.payload == {"ticket_id": created.ticket.id}
    with pytest.raises(service.TicketLimitReached):
        await _create_order(tkf, customer)

    def count(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE kind = ?", (service.TASK_RECOVER_CREATING,))
        return int(row.fetchone()[0])

    assert await tkf.app.db.run(count) == 1  # the refused reservation left nothing behind
    await service.activate_ticket(tkf.app, created.ticket.id, control_message_id=None)
    assert (await task(tkf, service.dedupe_recover_creating(created.ticket.id))).status == "cancelled"


async def test_creation_interrupted_by_a_restart_is_finished_by_its_task(
    tk: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = tk.member()

    async def cancelled(channel: Any, spec: Any) -> Any:
        raise asyncio.CancelledError  # the process stops while the welcome is being sent

    with monkeypatch.context() as patch:
        patch.setattr(service, "send_spec", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await service.open_support_ticket(tk.app, member=customer, category=_general(tk), answers={})
    [ticket] = await tickets(tk)
    assert ticket.status == CREATING and ticket.channel_id is not None
    ch = tk.guild.get_channel(ticket.channel_id)
    assert ch.messages == []
    advance_past_cooldown(tk)
    blocked = await click(tk.app, user=customer, custom_id=cid("tk", "open", "general"))
    assert blocked.last_sent is not None
    assert blocked.last_sent.embeds[0].description == render(tk, k.LIMIT_REACHED, limit=1)

    tk.clock.advance(service.CREATING_STALE_SECONDS)
    assert await tk.app.scheduler.run_due() == 1
    recovered = await ticket_of(tk, ticket.id)
    assert recovered.status == OPEN and recovered.control_message_id is not None
    assert control_message(ch, recovered).custom_ids() == [cid("tk", "close", ticket.id), cid("tk", "claim", ticket.id)]
    assert (await task(tk, service.dedupe_recover_creating(ticket.id))).status == "done"
    assert "tickets.recovered" in await audit_actions(tk)


async def test_recovery_task_fails_a_creation_that_cannot_finish(tk: TestEnv) -> None:
    customer = tk.member()
    no_channel = await service.reserve_ticket(
        tk.app, user_id=customer.id, kind=KIND_SUPPORT, category_id="general", limit=5
    )
    gone = tk.member(name="gone")
    orphan = await service.reserve_ticket(tk.app, user_id=gone.id, kind=KIND_SUPPORT, category_id="general", limit=5)
    orphan_channel = tk.guild.add_text_channel(name="orphan")
    await tk.app.db.transaction(repo.set_channel, orphan.id, orphan_channel.id, tk.clock.now())
    tk.guild._members.pop(gone.id)  # the customer left: no welcome can be sent

    tk.clock.advance(service.CREATING_STALE_SECONDS)
    assert await tk.app.scheduler.run_due() == 2
    assert (await ticket_of(tk, no_channel.id)).status == FAILED
    assert (await ticket_of(tk, orphan.id)).status == FAILED
    assert orphan_channel.deleted  # no orphan channel without controls stays behind


async def test_recovery_task_waits_while_discord_cannot_tell(tk: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    customer = tk.member()
    ticket = await service.reserve_ticket(
        tk.app, user_id=customer.id, kind=KIND_SUPPORT, category_id="general", limit=5
    )
    vanished_id = 999_000_000_000_000_001
    await tk.app.db.transaction(repo.set_channel, ticket.id, vanished_id, tk.clock.now())
    tk.clock.advance(service.CREATING_STALE_SECONDS)

    tk.guild.unavailable = True  # type: ignore[attr-defined]
    await tk.app.scheduler.run_due()
    waiting = await task(tk, service.dedupe_recover_creating(ticket.id))
    assert waiting.status == "pending" and waiting.attempts == 0
    assert waiting.run_at == tk.clock.now() + service.CREATING_STALE_SECONDS
    assert (await ticket_of(tk, ticket.id)).status == CREATING

    tk.guild.unavailable = False  # type: ignore[attr-defined]

    async def server_error(channel_id: int) -> Any:
        raise http_error(discord.DiscordServerError, 503, 0, "Service Unavailable")

    monkeypatch.setattr(tk.bot, "fetch_channel", server_error)
    tk.clock.advance(service.CREATING_STALE_SECONDS)
    await tk.app.scheduler.run_due()
    assert (await ticket_of(tk, ticket.id)).status == CREATING
    assert (await task(tk, service.dedupe_recover_creating(ticket.id))).status == "pending"

    monkeypatch.undo()  # Discord answers again: the channel is confirmed gone
    tk.clock.advance(service.CREATING_STALE_SECONDS)
    await tk.app.scheduler.run_due()
    assert (await ticket_of(tk, ticket.id)).status == FAILED


async def test_recovery_task_is_a_no_op_once_the_ticket_left_creating(tk: TestEnv) -> None:
    _, ticket, ch = await open_ticket(tk, tk.member())
    before = len(ch.messages)
    recovery = await task(tk, service.dedupe_recover_creating(ticket.id))
    assert recovery.status == "cancelled"
    await tasks.recover_creating_task(tk.app, recovery)  # e.g. a copy that ran anyway
    assert (await ticket_of(tk, ticket.id)).status == OPEN and len(ch.messages) == before


# -- daily creation cap -------------------------------------------------------------------------------------


async def _open_and_close(tenv: TestEnv, member: Any, category_id: str = "general") -> None:
    category = tenv.app.settings.tickets.category(category_id)
    assert category is not None
    created = await service.open_support_ticket(tenv.app, member=member, category=category, answers={})
    assert await service.close_ticket(tenv.app, created.ticket.id, actor_id=member.id) is not None


async def test_daily_creation_cap_defaults_to_ten(tk: TestEnv) -> None:
    customer = tk.member()
    for _ in range(10):
        await _open_and_close(tk, customer)
    advance_past_cooldown(tk)
    refused = await click(tk.app, user=customer, custom_id=cid("tk", "open", "general"))
    assert refused.last_sent is not None
    assert refused.last_sent.embeds[0].description == render(tk, k.LIMIT_REACHED, limit=10)
    with pytest.raises(service.TicketLimitReached) as exc:
        await service.open_support_ticket(tk.app, member=customer, category=_general(tk), answers={})
    assert exc.value.limit == 10
    assert len(await tickets(tk)) == 10
    await open_ticket(tk, tk.member(name="other"))  # other members are not affected
    tk.clock.advance(repo.DAY_SECONDS)
    await open_ticket(tk, customer)  # 24 hours later the earlier tickets no longer count


async def test_daily_cap_is_configurable_and_zero_is_unlimited(tk: TestEnv) -> None:
    customer = tk.member()
    _set_daily_cap(tk, 2)
    tk.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    with pytest.raises(service.TicketCreateFailed):
        await service.open_support_ticket(tk.app, member=customer, category=_general(tk), answers={})
    tk.guild.fail_channel_create = None
    for _ in range(2):  # the failed attempt does not count
        await _open_and_close(tk, customer)
    with pytest.raises(service.TicketLimitReached) as exc:
        await service.open_support_ticket(tk.app, member=customer, category=_general(tk), answers={})
    assert exc.value.limit == 2
    # Order channels (checkout) are not support tickets: the cap does not apply to them.
    assert (await _create_order(tk, customer)).ticket.status == CREATING
    _set_daily_cap(tk, 0)
    for _ in range(12):
        await _open_and_close(tk, customer)


async def test_daily_cap_is_atomic_across_categories(tkf: TestEnv) -> None:
    _set_daily_cap(tkf, 1)
    customer = tkf.member()
    general = tkf.app.settings.tickets.category("general")
    billing = tkf.app.settings.tickets.category("billing")
    assert general is not None and billing is not None
    results = await asyncio.gather(
        service.open_support_ticket(tkf.app, member=customer, category=general, answers={}),
        service.open_support_ticket(tkf.app, member=customer, category=billing, answers={"details": "help"}),
        return_exceptions=True,
    )
    assert sum(isinstance(r, service.CreatedChannel) for r in results) == 1
    [refused] = [r for r in results if isinstance(r, service.TicketLimitReached)]
    assert refused.limit == 1
    assert len(await tickets(tkf)) == 1


async def test_daily_cap_is_checked_before_the_form_is_shown(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, form_overrides())
    try:
        _set_daily_cap(tenv, 1)
        customer = tenv.member()
        await _open_and_close(tenv, customer)
        advance_past_cooldown(tenv)
        refused = await click(tenv.app, user=customer, custom_id=cid("tk", "open", "billing"))
        assert refused.response.modal is None
        assert refused.last_sent is not None
        assert refused.last_sent.embeds[0].description == render(tenv, k.LIMIT_REACHED, limit=1)
    finally:
        await tenv.close()


# -- owner reopen and the open-ticket limit --------------------------------------------------------------------


async def test_owner_reopen_respects_the_open_ticket_limit(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"user_can_reopen": True}})
    try:
        customer = tenv.member()
        _, first, _ = await open_ticket(tenv, customer)
        await service.close_ticket(tenv.app, first.id, actor_id=customer.id)
        advance_past_cooldown(tenv)
        await open_ticket(tenv, customer)  # the one open ticket the limit allows

        refused = await click(tenv.app, user=customer, custom_id=cid("tk", "reopen", first.id))
        assert refused.last_sent is not None
        assert refused.last_sent.embeds[0].description == render(tenv, k.LIMIT_REACHED, limit=1)
        assert (await ticket_of(tenv, first.id)).status == CLOSED

        staff = tenv.member(name="staff", staff=True)
        await click(tenv.app, user=staff, custom_id=cid("tk", "reopen", first.id))
        assert (await ticket_of(tenv, first.id)).status == OPEN  # staff reopen is not limited
    finally:
        await tenv.close()


async def test_concurrent_owner_reopens_respect_the_limit_atomically(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"user_can_reopen": True}})
    try:
        customer = tenv.member()
        closed_ids = []
        for _ in range(2):
            _, ticket, _ = await open_ticket(tenv, customer)
            await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
            advance_past_cooldown(tenv)
            closed_ids.append(ticket.id)
        results = await asyncio.gather(
            *(service.reopen_ticket(tenv.app, i, actor_id=customer.id, limit=1) for i in closed_ids),
            return_exceptions=True,
        )
        assert sum(r is not None and not isinstance(r, BaseException) for r in results) == 1
        assert sum(isinstance(r, service.TicketLimitReached) for r in results) == 1
        assert sorted(t.status for t in await tickets(tenv)) == [CLOSED, OPEN]
    finally:
        await tenv.close()
