"""Daily maintenance: purge old finished tasks and expired audit events; self-rescheduling and idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shopbot.context import App
from shopbot.db.repos import tasks as task_repo
from shopbot.db.repos.audit import recent
from shopbot.features.admin import MODULE, service
from tests.admin.util import restarted_app
from tests.helpers import TestEnv, make_settings, make_test_env

DAY = 86_400


def _insert_task(conn: sqlite3.Connection, key: str, status: str, updated_at: int) -> None:
    conn.execute(
        "INSERT INTO scheduled_tasks(kind, run_at, payload, status, attempts, max_attempts, dedupe_key, "
        "created_at, updated_at) VALUES ('test.kind', ?, '{}', ?, 1, 5, ?, 0, ?)",
        (updated_at + 100 * DAY, status, key, updated_at),
    )


def _insert_audit(conn: sqlite3.Connection, guild_id: int, action: str, created_at: int) -> None:
    conn.execute(
        "INSERT INTO audit_events(guild_id, actor_id, action, details, created_at) VALUES (?, NULL, ?, '{}', ?)",
        (guild_id, action, created_at),
    )


async def _seed(tenv: TestEnv) -> None:
    now = tenv.clock.now()
    guild_id = tenv.app.env.guild_id

    def seed(conn: sqlite3.Connection) -> None:
        _insert_task(conn, "done-old", "done", now - 40 * DAY)
        _insert_task(conn, "cancelled-old", "cancelled", now - 31 * DAY)
        _insert_task(conn, "done-recent", "done", now - 10 * DAY)
        _insert_task(conn, "failed-old", "failed", now - 40 * DAY)
        _insert_task(conn, "pending-old", "pending", now - 40 * DAY)
        _insert_audit(conn, guild_id, "old.event", now - 9 * DAY)
        _insert_audit(conn, guild_id, "recent.event", now - 2 * DAY)

    await tenv.app.db.transaction(seed)


async def _task_keys(tenv: TestEnv) -> set[str]:
    tasks = await tenv.app.db.run(task_repo.list_tasks, limit=100)
    return {t.dedupe_key for t in tasks if t.dedupe_key}


async def _audit_actions(tenv: TestEnv) -> list[str]:
    return [e.action for e in await tenv.app.db.run(recent, tenv.app.env.guild_id, limit=100)]


async def test_maintenance_is_scheduled_on_ready_and_runs_daily(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"audit": {"retention_days": 7}}))
    try:
        app = tenv.app
        await _seed(tenv)
        assert MODULE.on_ready is not None
        await MODULE.on_ready(app)
        task = await app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
        assert task is not None and task.status == "pending" and task.kind == "admin.maintenance"
        assert task.run_at == tenv.clock.now() + service.MAINTENANCE_FIRST_RUN_DELAY_SECONDS

        assert await app.scheduler.run_due() == 0  # not due yet
        tenv.clock.advance(service.MAINTENANCE_FIRST_RUN_DELAY_SECONDS)
        assert await app.scheduler.run_due() == 1

        keys = await _task_keys(tenv)
        assert "done-old" not in keys and "cancelled-old" not in keys
        assert {"done-recent", "failed-old", "pending-old", "admin.maintenance"} <= keys
        actions = await _audit_actions(tenv)
        assert "old.event" not in actions and "recent.event" in actions
        assert actions[0] == "admin.maintenance"
        maintenance_event = (await app.db.run(recent, app.env.guild_id, limit=1))[0]
        assert maintenance_event.details == {"tasks_purged": 2, "audit_purged": 1, "retention_days": 7}

        # The same row is rescheduled for tomorrow (single row, attempts reset).
        task = await app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
        assert task is not None and task.status == "pending" and task.attempts == 0
        assert task.run_at == tenv.clock.now() + DAY

        # Next day: nothing left to purge (idempotent), and no new audit event is recorded.
        tenv.clock.advance(DAY)
        assert await app.scheduler.run_due() == 1
        assert (await _audit_actions(tenv)).count("admin.maintenance") == 1
        task = await app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
        assert task is not None and task.status == "pending" and task.run_at == tenv.clock.now() + DAY
    finally:
        await tenv.close()


async def test_audit_events_are_kept_forever_when_retention_is_zero(tenv: TestEnv) -> None:
    assert tenv.app.settings.audit.retention_days == 0
    await _seed(tenv)
    result = await service.run_maintenance(tenv.app)
    assert result.audit_purged == 0 and result.tasks_purged == 2
    assert "old.event" in await _audit_actions(tenv)


async def test_on_ready_does_not_duplicate_or_postpone(tenv: TestEnv) -> None:
    assert MODULE.on_ready is not None
    await MODULE.on_ready(tenv.app)
    first = await tenv.app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    tenv.clock.advance(120)
    await MODULE.on_ready(tenv.app)
    app = restarted_app(tenv)  # restart recovery against existing database state
    assert MODULE.on_ready is not None
    await MODULE.on_ready(app)
    tasks = [t for t in await tenv.app.db.run(task_repo.list_tasks, limit=100) if t.kind == "admin.maintenance"]
    assert len(tasks) == 1
    assert first is not None and tasks[0].run_at == first.run_at


async def test_on_ready_reschedules_a_finished_or_failed_task(tenv: TestEnv) -> None:
    assert MODULE.on_ready is not None
    await MODULE.on_ready(tenv.app)
    task = await tenv.app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    assert task is not None
    await tenv.app.db.transaction(task_repo.mark_failed_attempt, task.id, "gave up", None, tenv.clock.now())
    tenv.clock.advance(DAY)
    await MODULE.on_ready(tenv.app)
    task = await tenv.app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    assert task is not None and task.status == "pending"
    assert task.run_at == tenv.clock.now() + service.MAINTENANCE_FIRST_RUN_DELAY_SECONDS


async def test_failed_run_is_retried_and_work_is_not_doubled(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tenv.app
    assert MODULE.on_ready is not None
    await MODULE.on_ready(app)
    await _seed(tenv)
    calls: list[int] = []
    original = service.run_maintenance

    async def flaky(target: App) -> service.MaintenanceResult:
        result = await original(target)
        calls.append(result.tasks_purged)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return result

    monkeypatch.setattr(service, "run_maintenance", flaky)
    tenv.clock.advance(service.MAINTENANCE_FIRST_RUN_DELAY_SECONDS)
    await app.scheduler.run_due()
    task = await app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    assert task is not None and task.status == "pending" and task.last_error is not None
    tenv.clock.advance(3600)
    await app.scheduler.run_due()
    assert calls == [2, 0]  # the retry found nothing left to purge
    task = await app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    assert task is not None and task.status == "pending" and task.attempts == 0
    assert task.run_at == tenv.clock.now() + DAY


async def test_handler_started_outside_scheduler_ensures_next_run(tenv: TestEnv) -> None:
    from shopbot.features.admin.tasks import maintenance_task

    fake = task_repo.Task(
        id=999_999,
        kind="admin.maintenance",
        run_at=0,
        payload={},
        status="running",
        attempts=1,
        max_attempts=5,
        dedupe_key="admin.maintenance",
        last_error=None,
    )
    await maintenance_task(tenv.app, fake)
    task = await tenv.app.db.run(task_repo.get_by_dedupe, "admin.maintenance")
    assert task is not None and task.status == "pending"
