from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shopbot.core.instance_lock import InstanceLock, InstanceLockError
from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.database import Database
from shopbot.db.migrate import Migration, MigrationError, apply_migrations, discover, split_statements
from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
from tests.helpers import TestEnv


async def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    await db.run(apply_migrations, db_path=tmp_path / "t.db")

    def write_then_fail(conn: sqlite3.Connection) -> None:
        state.kv_set(conn, "k", "v", 1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await db.transaction(write_then_fail)
    assert await db.run(state.kv_get, "k") is None
    await db.transaction(state.kv_set, "k", "v2", 2)
    assert await db.run(state.kv_get, "k") == "v2"
    await db.close()


async def test_data_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    db = Database(path)
    await db.open()
    await db.run(apply_migrations, db_path=path)
    await db.transaction(state.kv_set, "survives", "yes", 1)
    await db.close()
    again = Database(path)
    await again.open()
    assert await again.run(apply_migrations, db_path=path) == []  # nothing re-applied
    assert await again.run(state.kv_get, "survives") == "yes"
    await again.close()


def test_migration_downgrade_is_refused(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    conn.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (99, 'future', 0)")
    with pytest.raises(MigrationError, match="newer"):
        apply_migrations(conn)


def test_failed_migration_rolls_back_and_backup_is_written(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    conn = sqlite3.connect(str(path), isolation_level=None)
    apply_migrations(conn, db_path=path)
    conn.execute("INSERT INTO kv_state(key, value, updated_at) VALUES ('x', 'y', 0)")
    known = discover()
    next_version = len(known) + 1
    bad = tmp_path / f"{next_version:04d}_bad.sql"
    bad.write_text("CREATE TABLE extra (id INTEGER);\nINSERT INTO nope VALUES (1);\n")
    migrations = [*known, Migration(next_version, "bad", bad)]
    with pytest.raises(MigrationError):
        apply_migrations(conn, db_path=path, migrations=migrations)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extra" not in tables  # rolled back
    assert conn.execute("SELECT value FROM kv_state WHERE key='x'").fetchone()[0] == "y"  # data intact
    assert list(tmp_path.glob(f"m.db.pre-v{next_version:04d}-*.bak")), "backup must be written before migrating data"


def test_split_statements_handles_triggers() -> None:
    sql = "CREATE TABLE a (x);\n-- comment\nCREATE TRIGGER t AFTER INSERT ON a BEGIN SELECT 1; SELECT 2; END;\n"
    assert len(split_statements(sql)) == 2


def test_schema_constraints_enforce_invariants() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO products(guild_id, name, price_minor, stock_on_hand, reserved, created_at, updated_at) "
            "VALUES (1, 'p', 100, 1, 2, 0, 0)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO products(guild_id, name, price_minor, created_at, updated_at) VALUES (1, 'p', -1, 0, 0)"
        )


def test_instance_lock_prevents_second_process(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "bot.db.lock")
    first.acquire(wait_seconds=0)
    second = InstanceLock(tmp_path / "bot.db.lock")
    with pytest.raises(InstanceLockError):
        second.acquire(wait_seconds=0.1, poll_seconds=0.05)
    first.release()
    second.acquire(wait_seconds=0)
    second.release()


async def test_scheduler_runs_due_tasks_once_and_retries(tenv: TestEnv) -> None:
    app = tenv.app
    calls: list[int] = []

    async def ok(app_: object, task: task_repo.Task) -> None:
        calls.append(task.id)

    attempts: list[int] = []

    async def flaky(app_: object, task: task_repo.Task) -> None:
        attempts.append(task.attempts)
        if task.attempts < 2:
            raise RuntimeError("transient")

    app.scheduler.register("test.ok", ok)
    app.scheduler.register("test.flaky", flaky)
    now = tenv.clock.now()
    first = await app.scheduler.schedule("test.ok", run_at=now, dedupe_key="d1")
    duplicate = await app.scheduler.schedule("test.ok", run_at=now, dedupe_key="d1")
    assert first is not None and duplicate is None
    await app.scheduler.schedule("test.ok", run_at=now + 100, dedupe_key="later")
    flaky_id = await app.scheduler.schedule("test.flaky", run_at=now)

    assert await app.scheduler.run_due() == 2
    assert calls == [first]
    task = await app.db.run(task_repo.list_tasks, status="pending")
    retry = next(t for t in task if t.id == flaky_id)
    assert retry.run_at > now and retry.last_error

    tenv.clock.advance(10_000)
    await app.scheduler.run_due()
    assert attempts == [1, 2]
    done = await app.db.run(task_repo.list_tasks, status="done")
    assert {t.id for t in done} >= {first, flaky_id}


async def test_scheduler_permanent_failure_and_recovery(tenv: TestEnv) -> None:
    app = tenv.app

    async def broken(app_: object, task: task_repo.Task) -> None:
        raise PermanentTaskError("cannot proceed")

    app.scheduler.register("test.broken", broken)
    task_id = await app.scheduler.schedule("test.broken", run_at=tenv.clock.now())
    await app.scheduler.run_due()
    failed = await app.db.run(task_repo.list_tasks, status="failed")
    assert [t.id for t in failed] == [task_id]

    # A task left 'running' by a crash is recovered to pending.
    async def ok(app_: object, task: task_repo.Task) -> None:
        return None

    app.scheduler.register("test.ok2", ok)
    stuck = await app.scheduler.schedule("test.ok2", run_at=tenv.clock.now())
    await app.db.transaction(task_repo.claim_due, tenv.clock.now(), 10)
    assert await app.scheduler.recover() == 1
    await app.scheduler.run_due()
    done = await app.db.run(task_repo.list_tasks, status="done")
    assert stuck in {t.id for t in done}


async def test_scheduler_replace_reschedules_pending(tenv: TestEnv) -> None:
    app = tenv.app

    async def ok(app_: object, task: task_repo.Task) -> None:
        return None

    app.scheduler.register("test.re", ok)
    now = tenv.clock.now()
    first = await app.scheduler.schedule("test.re", run_at=now + 50, dedupe_key="r")
    second = await app.scheduler.schedule("test.re", run_at=now + 500, dedupe_key="r", replace=True)
    assert first == second
    task = await app.db.run(task_repo.get_by_dedupe, "r")
    assert task is not None and task.run_at == now + 500
    assert await app.scheduler.cancel("r") == 1


async def test_scheduler_reschedule_signal_keeps_one_row_cycling(tenv: TestEnv) -> None:
    from shopbot.core.scheduler import RescheduleTask

    app = tenv.app
    runs: list[int] = []

    async def recurring(app_: object, task: task_repo.Task) -> None:
        runs.append(task.id)
        raise RescheduleTask(tenv.clock.now() + 60)

    app.scheduler.register("test.recurring", recurring)
    task_id = await app.scheduler.schedule("test.recurring", run_at=tenv.clock.now(), dedupe_key="rec")
    await app.scheduler.run_due()
    tenv.clock.advance(30)
    assert await app.scheduler.run_due() == 0
    tenv.clock.advance(31)
    await app.scheduler.run_due()
    assert runs == [task_id, task_id]
    task = await app.db.run(task_repo.get_by_dedupe, "rec")
    assert task is not None and task.status == "pending" and task.attempts == 0
