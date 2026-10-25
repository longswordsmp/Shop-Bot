"""Concurrent lifecycle changes: delete vs reopen, close vs reopen, automatic close vs reopen."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import repo, service, tasks
from shopbot.features.tickets.models import CLOSED, DELETED, OPEN
from shopbot.ui.custom_ids import cid
from tests.fakes import FakeTextChannel, click, http_error
from tests.helpers import TestEnv
from tests.tickets.util import (
    audit_actions,
    bot_messages,
    control_message,
    make_env,
    open_ticket,
    overwrite_for,
    render,
    task,
    ticket_of,
)

AUTO = {"tickets": {"auto_close_inactive_minutes": 60}}


async def _closed_ticket(tenv: TestEnv) -> tuple[Any, Any, FakeTextChannel]:
    customer = tenv.member()
    _, ticket, ch = await open_ticket(tenv, customer)
    ch.add_user_message(customer, "hello")
    assert await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id) is not None
    return customer, ticket, ch


async def _reopen_in_db(tenv: TestEnv, ticket_id: int) -> Any:
    """A reopen committed by another path, bypassing this process's lifecycle lock."""
    return await tenv.app.db.transaction(repo.reopen, ticket_id, tenv.clock.now())


def _closed_messages(ch: FakeTextChannel, ticket_id: int) -> list[Any]:
    return [m for m in ch.messages if cid("tk", "delete", ticket_id) in m.custom_ids()]


# -- delete vs reopen -------------------------------------------------------------------------------------


async def test_reopen_during_the_transcript_step_wins_and_the_channel_is_kept(
    tk: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ticket, ch = await _closed_ticket(tk)
    assert (await ticket_of(tk, ticket.id)).transcript_status == "pending"
    original = service.generate_transcript

    async def transcript_then_reopen(app: Any, ticket_id: int, **kwargs: Any) -> bool:
        result = await original(app, ticket_id, **kwargs)
        assert await _reopen_in_db(tk, ticket_id) is not None  # lands while the deletion is in progress
        return result

    monkeypatch.setattr(service, "generate_transcript", transcript_then_reopen)
    with pytest.raises(service.TicketStateError) as exc:
        await service.delete_ticket(tk.app, ticket.id, actor_id=None)
    assert exc.value.status == OPEN
    assert not ch.deleted
    current = await ticket_of(tk, ticket.id)
    assert current.status == OPEN and current.deleted_at is None
    assert "tickets.deleted" not in await audit_actions(tk)


async def test_a_reopen_after_the_deletion_claim_no_longer_matches(tk: TestEnv) -> None:
    _, ticket, ch = await _closed_ticket(tk)
    original_delete = ch.delete
    reopen_results: list[Any] = []

    async def reopen_then_delete(*, reason: str | None = None) -> None:
        reopen_results.append(await _reopen_in_db(tk, ticket.id))  # the claim already committed
        reopen_results.append(await service.get_ticket(tk.app, ticket.id))
        await original_delete(reason=reason)

    ch.delete = reopen_then_delete  # type: ignore[method-assign]
    assert await service.delete_ticket(tk.app, ticket.id, actor_id=None)
    assert reopen_results[0] is None and reopen_results[1].status == DELETED
    assert ch.deleted and (await ticket_of(tk, ticket.id)).status == DELETED


async def test_discord_refusing_the_deletion_keeps_the_ticket_closed_and_reopenable(tk: TestEnv) -> None:
    _, ticket, ch = await _closed_ticket(tk)
    tk.guild.fail_channel_delete = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    with pytest.raises(discord.Forbidden):
        await service.delete_ticket(tk.app, ticket.id, actor_id=None)
    current = await ticket_of(tk, ticket.id)
    assert current.status == CLOSED and current.deleted_at is None and current.transcript_status == "done"
    assert not ch.deleted

    staff = tk.member(name="staff", staff=True)
    refused = await click(tk.app, user=staff, custom_id=cid("tk", "delok", ticket.id))
    assert refused.last_sent is not None
    assert refused.last_sent.embeds[0].description == tk.text(core.ERROR_GENERIC)
    assert (await ticket_of(tk, ticket.id)).status == CLOSED
    assert "tickets.deleted" not in await audit_actions(tk)

    tk.guild.fail_channel_delete = None
    assert await service.reopen_ticket(tk.app, ticket.id, actor_id=staff.id) is not None
    assert (await ticket_of(tk, ticket.id)).status == OPEN


@pytest.mark.parametrize("delete_first", [True, False])
async def test_concurrent_delete_and_reopen_end_consistent(tk: TestEnv, delete_first: bool) -> None:
    _, ticket, ch = await _closed_ticket(tk)  # transcript pending: the deletion has real work to do first
    staff = tk.member(name="staff", staff=True)
    delete = service.delete_ticket(tk.app, ticket.id, actor_id=staff.id)
    reopen = service.reopen_ticket(tk.app, ticket.id, actor_id=staff.id)
    coros = (delete, reopen) if delete_first else (reopen, delete)
    results = await asyncio.gather(*coros, return_exceptions=True)
    deleted_result, reopened_result = results if delete_first else results[::-1]
    current = await ticket_of(tk, ticket.id)
    if current.status == DELETED:
        assert deleted_result is True and reopened_result is None and ch.deleted
    else:
        assert current.status == OPEN and not ch.deleted
        assert isinstance(deleted_result, service.TicketStateError) and reopened_result is not None
        owner = overwrite_for(ch, ticket.user_id)
        assert owner is not None and owner.send_messages is True
    actions = await audit_actions(tk)
    assert actions.count("tickets.deleted") + actions.count("tickets.reopened") == 1


async def test_concurrent_delete_and_reopen_clicks(tk: TestEnv) -> None:
    _, ticket, ch = await _closed_ticket(tk)
    staff_a = tk.member(name="a", staff=True)
    staff_b = tk.member(name="b", staff=True)
    delete_click, reopen_click = await asyncio.gather(
        click(tk.app, user=staff_a, custom_id=cid("tk", "delok", ticket.id)),
        click(tk.app, user=staff_b, custom_id=cid("tk", "reopen", ticket.id)),
    )
    current = await ticket_of(tk, ticket.id)
    assert current.status in (DELETED, OPEN)
    assert ch.deleted == (current.status == DELETED)
    loser = reopen_click if current.status == DELETED else delete_click
    assert loser.last_sent is not None and loser.last_sent.embeds[0].description == tk.text(k.NOT_OPEN)


async def test_internal_deletion_only_claims_closed_tickets(tk: TestEnv) -> None:
    _, open_one, _ = await open_ticket(tk, tk.member())
    now = tk.clock.now()
    assert await tk.app.db.transaction(repo.mark_deleted, open_one.id, now, external=False) is None
    _, closed_one, _ = await _closed_ticket(tk)
    # A transcript is still outstanding: the bot never claims the deletion then.
    assert await tk.app.db.transaction(repo.mark_deleted, closed_one.id, now, external=False) is None
    assert (await ticket_of(tk, closed_one.id)).status == CLOSED
    # The channel disappearing without us still records an open ticket as deleted.
    result = await tk.app.db.transaction(repo.mark_deleted, open_one.id, now, external=True, reason="channel_deleted")
    assert result is not None and result[1].status == DELETED


async def test_restore_only_undoes_its_own_claim(tk: TestEnv) -> None:
    _, ticket, _ = await _closed_ticket(tk)
    assert await service.generate_transcript(tk.app, ticket.id)
    claim = await tk.app.db.transaction(repo.mark_deleted, ticket.id, tk.clock.now(), external=False)
    assert claim is not None
    deleted_at = claim[1].deleted_at
    assert deleted_at is not None
    assert await tk.app.db.transaction(repo.restore_deleted, ticket.id, deleted_at - 1, tk.clock.now()) is None
    restored = await tk.app.db.transaction(repo.restore_deleted, ticket.id, deleted_at, tk.clock.now())
    assert restored is not None and restored.status == CLOSED and restored.deleted_at is None


# -- close vs reopen ----------------------------------------------------------------------------------------


async def test_close_skips_closed_ui_and_deletion_after_a_concurrent_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"delete_after_close_minutes": 10}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        original = service._set_access

        async def lock_then_reopen(app: Any, channel: Any, closed: Any, *, can_send: bool) -> None:
            await original(app, channel, closed, can_send=can_send)
            if not can_send:
                assert await _reopen_in_db(tenv, closed.id) is not None

        monkeypatch.setattr(service, "_set_access", lock_then_reopen)
        assert await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id) is not None
        assert (await ticket_of(tenv, ticket.id)).status == OPEN
        assert _closed_messages(ch, ticket.id) == []
        notice = render(tenv, k.DELETION_SCHEDULED, minutes=10)
        assert all(m.embeds[0].description != notice for m in bot_messages(ch) if m.embeds)
        assert await task(tenv, service.dedupe_delete(ticket.id)) is None
        assert control_message(ch, ticket).custom_ids()[0] == cid("tk", "close", ticket.id)  # controls kept
    finally:
        await tenv.close()


async def test_concurrent_close_and_reopen_never_leave_a_reopened_ticket_locked(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)
    closed, reopened = await asyncio.gather(
        service.close_ticket(tk.app, ticket.id, actor_id=staff.id),
        service.reopen_ticket(tk.app, ticket.id, actor_id=staff.id),
    )
    assert closed is not None and reopened is not None  # the reopen waited for the close to finish
    current = await ticket_of(tk, ticket.id)
    assert current.status == OPEN
    owner = overwrite_for(ch, customer.id)
    assert owner is not None and owner.send_messages is True
    assert control_message(ch, ticket).custom_ids() == [cid("tk", "close", ticket.id), cid("tk", "claim", ticket.id)]
    assert all(m.view is None for m in _closed_messages(ch, ticket.id))
    assert bot_messages(ch)[-1].embeds[0].description == render(tk, k.REOPENED, actor_mention=staff.mention)


# -- automatic close ------------------------------------------------------------------------------------------


async def test_auto_close_reopened_while_closing_is_rearmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        app = tenv.app
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        original = service.close_ticket

        async def close_then_reopen(app_: Any, ticket_id: int, **kwargs: Any) -> Any:
            closed = await original(app_, ticket_id, **kwargs)
            tenv.clock.advance(5)
            # The owner's click lands right after the close: the auto-close task is still running.
            assert await service.reopen_ticket(app_, ticket_id, actor_id=customer.id) is not None
            return closed

        monkeypatch.setattr(service, "close_ticket", close_then_reopen)
        tenv.clock.advance(3600 + 1)
        assert await app.scheduler.run_due() == 1
        reopened = await ticket_of(tenv, ticket.id)
        assert reopened.status == OPEN
        rearmed = await task(tenv, service.dedupe_auto_close(ticket.id))
        assert rearmed.status == "pending" and rearmed.run_at == reopened.last_activity_at + 3600
    finally:
        await tenv.close()


async def test_auto_close_waits_without_using_attempts_while_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        app = tenv.app
        _, ticket, _ = await open_ticket(tenv, tenv.member())
        values = dict(tenv.content)
        del values[k.PANEL_TITLE]
        app.content.replace(file_values=values)
        app.recompute_readiness()
        assert not app.ready("tickets")
        tenv.clock.advance(3600 + 1)
        for _ in range(app.scheduler.max_attempts + 2):  # far more checks than retry attempts
            await app.scheduler.run_due()
            postponed = await task(tenv, service.dedupe_auto_close(ticket.id))
            assert postponed.status == "pending" and postponed.attempts == 0
            assert postponed.run_at == tenv.clock.now() + tasks.NOT_READY_RETRY_SECONDS
            tenv.clock.advance(tasks.NOT_READY_RETRY_SECONDS)
        assert (await ticket_of(tenv, ticket.id)).status == OPEN

        app.content.replace(file_values=tenv.content)
        app.recompute_readiness()
        await app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).status == CLOSED
    finally:
        await tenv.close()


async def test_deletion_claim_retries_after_reopen_and_reclose(tk: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reopened and closed again while the first transcript was produced: the new transcript comes first."""
    customer, ticket, ch = await _closed_ticket(tk)
    original = service.generate_transcript
    calls: list[int] = []

    async def transcript_then_cycle(app: Any, ticket_id: int, **kwargs: Any) -> bool:
        result = await original(app, ticket_id, **kwargs)
        calls.append(ticket_id)
        if len(calls) == 1:
            assert await _reopen_in_db(tk, ticket_id) is not None

            def close_again(conn: sqlite3.Connection) -> None:
                repo.close(conn, ticket_id, closed_by=customer.id, reason=None, transcript_status="pending", now=1)

            await tk.app.db.transaction(close_again)
        return result

    monkeypatch.setattr(service, "generate_transcript", transcript_then_cycle)
    assert await service.delete_ticket(tk.app, ticket.id, actor_id=None)
    assert len(calls) == 2 and ch.deleted
    deleted = await ticket_of(tk, ticket.id)
    assert deleted.status == DELETED and deleted.transcript_status == "done"
