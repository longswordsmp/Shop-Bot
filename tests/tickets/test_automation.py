from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features.tickets import MODULE, repo, service
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets.models import KIND_SUPPORT
from tests.fakes import http_error
from tests.helpers import TestEnv
from tests.tickets.util import (
    audit_actions,
    bot_messages,
    channel,
    control_message,
    make_env,
    number,
    open_ticket,
    render,
    task,
    ticket_of,
    tickets,
)

AUTO = {"tickets": {"auto_close_inactive_minutes": 60, "auto_close_warning_minutes": 15}}


async def test_auto_close_with_activity_reschedule_and_warning(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        app = tenv.app
        start = tenv.clock.now()
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        close_task = await task(tenv, service.dedupe_auto_close(ticket.id))
        warn_task = await task(tenv, service.dedupe_auto_warn(ticket.id))
        assert close_task.run_at == start + 3600 and warn_task.run_at == start + 45 * 60

        # Activity after 30 minutes pushes both tasks back.
        tenv.clock.advance(1800)
        await tenv.bot.emit("message", ch.add_user_message(customer, "still there"))
        assert (await ticket_of(tenv, ticket.id)).last_activity_at == start + 1800
        assert (await task(tenv, service.dedupe_auto_close(ticket.id))).run_at == start + 1800 + 3600
        # Bot messages and throttled repeats do not count as activity.
        tenv.clock.advance(30)
        await tenv.bot.emit("message", ch.add_user_message(customer, "again"))
        await tenv.bot.emit("message", ch.add_user_message(tenv.guild.me, "bot"))
        assert (await ticket_of(tenv, ticket.id)).last_activity_at == start + 1800

        # At the original deadline nothing happens.
        tenv.clock.advance(3600 - 1830 + 1)
        assert await app.scheduler.run_due() == 0
        assert (await ticket_of(tenv, ticket.id)).status == "open"

        # Warning 15 minutes before the new deadline (pings the customer).
        tenv.clock.advance((start + 1800 + 45 * 60) - tenv.clock.now())
        assert await app.scheduler.run_due() == 1
        warning = bot_messages(ch)[-1]
        assert warning.embeds[0].description == render(
            tenv, k.AUTO_CLOSE_WARNING, minutes=15, user_mention=customer.mention
        )
        assert warning.content == customer.mention

        # Then the automatic close: normal close path, no actor, reason 'inactive'.
        tenv.clock.advance(15 * 60)
        assert await app.scheduler.run_due() == 1
        closed = await ticket_of(tenv, ticket.id)
        assert closed.status == "closed" and closed.closed_by is None and closed.close_reason == "inactive"
        closed_message = next(m for m in ch.messages if m.custom_ids() and "delete" in m.custom_ids()[-1])
        assert closed_message.embeds[0].description == render(tenv, k.AUTO_CLOSED, number=number(tenv, 1))
        assert control_message(ch, ticket).view is None
        assert "tickets.closed" in await audit_actions(tenv)
    finally:
        await tenv.close()


async def test_task_that_runs_early_is_rearmed(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        # Activity was recorded but the reschedule was lost (e.g. the task was running at that moment).
        tenv.clock.advance(3600)
        later = tenv.clock.now() - 600

        def bump(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE tickets SET last_activity_at = ? WHERE id = ?", (later, ticket.id))

        await tenv.app.db.transaction(bump)
        await tenv.app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).status == "open"
        rearmed = await task(tenv, service.dedupe_auto_close(ticket.id))
        assert rearmed.status == "pending" and rearmed.run_at == later + 3600
        tenv.clock.advance(3000 + 1)
        await tenv.app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).status == "closed"
    finally:
        await tenv.close()


async def test_close_cancels_inactivity_tasks(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        assert (await task(tenv, service.dedupe_auto_close(ticket.id))).status == "cancelled"
        assert (await task(tenv, service.dedupe_auto_warn(ticket.id))).status == "cancelled"
        reopened = await service.reopen_ticket(tenv.app, ticket.id, actor_id=customer.id)
        assert reopened is not None
        assert (await task(tenv, service.dedupe_auto_close(ticket.id))).status == "pending"
    finally:
        await tenv.close()


async def test_deletion_task_waits_for_transcript(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"delete_after_close_minutes": 5}})
    try:
        app = tenv.app
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        transcripts = channel(tenv, "transcripts")
        transcripts.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        await service.close_ticket(app, ticket.id, actor_id=customer.id)
        tenv.clock.advance(5 * 60 + 1)
        await app.scheduler.run_due()
        current = await ticket_of(tenv, ticket.id)
        assert current.status == "closed" and current.transcript_status == "pending"
        assert not ch.deleted
        delete_task = await task(tenv, service.dedupe_delete(ticket.id))
        assert delete_task.status == "pending" and delete_task.attempts == 1

        transcripts.fail_send = None
        tenv.clock.advance(120)
        await app.scheduler.run_due()
        done = await ticket_of(tenv, ticket.id)
        assert done.status == "deleted" and done.transcript_status == "done"
        assert ch.deleted and len(transcripts.messages) == 1
        assert (await audit_actions(tenv)).count("tickets.deleted") == 1
    finally:
        await tenv.close()


async def test_deletion_task_after_reopen_does_nothing(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"delete_after_close_minutes": 5, "transcript_channel": ""}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        assert (await ticket_of(tenv, ticket.id)).transcript_status == "skipped"
        await service.reopen_ticket(tenv.app, ticket.id, actor_id=customer.id)
        tenv.clock.advance(600)
        await tenv.app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).status == "open" and not ch.deleted
    finally:
        await tenv.close()


async def test_channel_deleted_externally(tk: TestEnv) -> None:
    customer = tk.member()
    _, open_one, open_channel = await open_ticket(tk, customer)
    _, closed_one, closed_channel = await open_ticket(tk, tk.member(name="second"))
    await service.close_ticket(tk.app, closed_one.id, actor_id=customer.id)

    for ch in (open_channel, closed_channel):
        await ch.delete()
        await tk.bot.emit("guild_channel_delete", ch)
    gone = await ticket_of(tk, open_one.id)
    assert gone.status == "deleted" and gone.closed_at is not None and gone.close_reason == "channel_deleted"
    closed = await ticket_of(tk, closed_one.id)
    assert closed.status == "deleted" and closed.transcript_status == "failed"
    assert (await task(tk, service.dedupe_transcript(closed_one.id))).status == "cancelled"
    assert (await audit_actions(tk)).count("tickets.channel_deleted") == 2
    # A repeated event changes nothing.
    await tk.bot.emit("guild_channel_delete", open_channel)
    assert (await audit_actions(tk)).count("tickets.channel_deleted") == 2


async def test_bot_deleting_its_own_channel_is_not_reported_as_external(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    await service.close_ticket(tk.app, ticket.id, actor_id=customer.id)
    original_delete = ch.delete

    async def delete_and_emit(*, reason: str | None = None) -> None:
        await original_delete(reason=reason)
        await tk.bot.emit("guild_channel_delete", ch)  # gateway event arrives while we are deleting

    ch.delete = delete_and_emit  # type: ignore[method-assign]
    assert await service.delete_ticket(tk.app, ticket.id, actor_id=None)
    actions = await audit_actions(tk)
    assert actions.count("tickets.deleted") == 1 and "tickets.channel_deleted" not in actions


async def _insert_creating(tenv: TestEnv, user_id: int, channel_id: int | None) -> int:
    reserved = await tenv.app.db.transaction(
        repo.reserve,
        guild_id=tenv.app.env.guild_id,
        kind=KIND_SUPPORT,
        category_id="general",
        user_id=user_id,
        form_answers={},
        limit=5,
        now=tenv.clock.now(),
    )
    assert reserved is not None
    if channel_id is not None:
        await tenv.app.db.transaction(repo.set_channel, reserved.id, channel_id, tenv.clock.now())
    return reserved.id


async def test_restart_recovery(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"auto_close_inactive_minutes": 60, "delete_after_close_minutes": 30}})
    try:
        app = tenv.app
        customer = tenv.member()
        interrupted_channel = tenv.guild.add_text_channel(name="t-0001")
        interrupted = await _insert_creating(tenv, customer.id, interrupted_channel.id)
        no_channel = await _insert_creating(tenv, customer.id, None)
        adopted_channel = tenv.guild.add_text_channel(name="t-0003")
        adopted = await _insert_creating(tenv, tenv.member(name="a").id, adopted_channel.id)
        already_sent = await adopted_channel.send(content="welcome sent before the crash")
        tenv.clock.advance(400)
        fresh = await _insert_creating(tenv, tenv.member(name="fresh").id, None)

        _, vanished, vanished_channel = await open_ticket(tenv, tenv.member(name="v"))
        _, kept, _ = await open_ticket(tenv, tenv.member(name="k"))
        _, closed, _ = await open_ticket(tenv, tenv.member(name="c"))
        await service.close_ticket(app, closed.id, actor_id=customer.id)
        tenv.guild._channels.pop(vanished_channel.id)  # deleted while the bot was offline

        def lose_tasks(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM scheduled_tasks WHERE dedupe_key IN (?, ?, ?)",
                (
                    service.dedupe_auto_close(kept.id),
                    service.dedupe_delete(closed.id),
                    service.dedupe_transcript(closed.id),
                ),
            )

        await app.db.transaction(lose_tasks)
        assert MODULE.on_ready is not None
        await MODULE.on_ready(app)
        await MODULE.on_ready(app)  # idempotent

        recovered = await ticket_of(tenv, interrupted)
        assert recovered.status == "open" and recovered.control_message_id is not None
        welcome = control_message(interrupted_channel, recovered)  # type: ignore[arg-type]
        assert welcome.custom_ids()[0].endswith(f":{interrupted}")
        assert len(interrupted_channel.messages) == 1  # sent once, even though recovery ran twice
        assert (await ticket_of(tenv, no_channel)).status == "failed"
        adopted_row = await ticket_of(tenv, adopted)
        assert adopted_row.status == "open" and adopted_row.control_message_id == already_sent.id
        assert len(adopted_channel.messages) == 1  # the existing welcome is reused, not duplicated
        assert (await ticket_of(tenv, fresh)).status == "creating"  # younger than 5 minutes
        assert (await ticket_of(tenv, vanished.id)).status == "deleted"
        close_task = await task(tenv, service.dedupe_auto_close(kept.id))
        assert close_task.status == "pending"
        assert close_task.run_at == (await ticket_of(tenv, kept.id)).last_activity_at + 3600
        delete_task = await task(tenv, service.dedupe_delete(closed.id))
        closed_row = await ticket_of(tenv, closed.id)
        assert delete_task.status == "pending" and delete_task.run_at == (closed_row.closed_at or 0) + 30 * 60
        assert (await task(tenv, service.dedupe_transcript(closed.id))).status == "pending"

        def count_pending(conn: sqlite3.Connection) -> int:
            return int(conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE status = 'pending'").fetchone()[0])

        pending_before = await app.db.run(count_pending)
        await MODULE.on_ready(app)
        assert await app.db.run(count_pending) == pending_before
        assert "tickets.recovered" in await audit_actions(tenv)
        statuses = sorted(t.status for t in await tickets(tenv))
        assert statuses.count("open") == 3
    finally:
        await tenv.close()


async def test_auto_close_task_does_nothing_when_feature_is_switched_off(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, AUTO)
    try:
        _, ticket, _ = await open_ticket(tenv, tenv.member())
        app = tenv.app
        app.settings = replace(app.settings, features=replace(app.settings.features, tickets=False))
        tenv.clock.advance(3600 + 1)
        await app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).status == "open"
        assert (await task(tenv, service.dedupe_auto_close(ticket.id))).status == "done"
    finally:
        await tenv.close()


async def test_restart_recovery_skips_an_unavailable_guild(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"delete_after_close_minutes": 30}})
    try:
        _, open_one, _ = await open_ticket(tenv, tenv.member())
        _, closed_one, _ = await open_ticket(tenv, tenv.member(name="second"))
        await service.close_ticket(tenv.app, closed_one.id, actor_id=None)
        creating = await _insert_creating(tenv, tenv.member(name="third").id, None)
        tenv.clock.advance(400)
        # A guild stub from READY (GUILD_CREATE not received yet) has no channels in its cache.
        tenv.guild.unavailable = True  # type: ignore[attr-defined]
        tenv.guild._channels.clear()
        assert MODULE.on_ready is not None
        await MODULE.on_ready(tenv.app)
        assert (await ticket_of(tenv, open_one.id)).status == "open"
        assert (await ticket_of(tenv, closed_one.id)).status == "closed"
        assert (await ticket_of(tenv, creating)).status == "creating"
        assert "tickets.channel_deleted" not in await audit_actions(tenv)
    finally:
        await tenv.close()


async def test_restart_recovery_confirms_missing_channels_with_discord(
    tk: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cached_miss, cached_miss_channel = await open_ticket(tk, tk.member())
    _, unknown, unknown_channel = await open_ticket(tk, tk.member(name="second"))
    _, gone, gone_channel = await open_ticket(tk, tk.member(name="third"))
    original_get = tk.guild.get_channel
    hidden = {cached_miss_channel.id, unknown_channel.id}

    def cache_without(channel_id: int | None) -> Any:  # not in the cache, but still on Discord
        return None if channel_id in hidden else original_get(channel_id)

    original_fetch = tk.bot.fetch_channel
    fetched: list[int] = []

    async def fetch(channel_id: int) -> Any:
        fetched.append(channel_id)
        if channel_id == unknown_channel.id:
            raise http_error(discord.DiscordServerError, 503, 0, "Service Unavailable")
        return await original_fetch(channel_id)

    monkeypatch.setattr(tk.guild, "get_channel", cache_without)
    monkeypatch.setattr(tk.bot, "fetch_channel", fetch)
    tk.guild._channels.pop(gone_channel.id)  # really deleted while the bot was offline
    assert MODULE.on_ready is not None
    await MODULE.on_ready(tk.app)
    assert sorted(fetched) == sorted([cached_miss_channel.id, unknown_channel.id, gone_channel.id])
    assert (await ticket_of(tk, cached_miss.id)).status == "open"
    assert (await ticket_of(tk, unknown.id)).status == "open"  # Discord could not tell: nothing changes
    assert (await ticket_of(tk, gone.id)).status == "deleted"
    assert (await audit_actions(tk)).count("tickets.channel_deleted") == 1


async def test_restart_recovery_gives_young_creations_a_recovery_task(tk: TestEnv) -> None:
    young = await _insert_creating(tk, tk.member().id, None)  # inserted without its task (older version)
    assert await task(tk, service.dedupe_recover_creating(young)) is None
    assert MODULE.on_ready is not None
    await MODULE.on_ready(tk.app)
    recovery = await task(tk, service.dedupe_recover_creating(young))
    created = await ticket_of(tk, young)
    assert recovery.status == "pending" and recovery.run_at == created.created_at + service.CREATING_STALE_SECONDS
    assert created.status == "creating"
    tk.clock.advance(service.CREATING_STALE_SECONDS)
    await tk.app.scheduler.run_due()
    assert (await ticket_of(tk, young)).status == "failed"
