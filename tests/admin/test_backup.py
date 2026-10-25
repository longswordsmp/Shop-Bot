"""/backup: consistent copy on disk, rotation of the newest 10, never uploaded to Discord."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from shopbot.db.repos.audit import recent
from shopbot.features.admin import service
from shopbot.features.admin.state import state_for
from tests.admin.util import admin, audit_actions, is_ephemeral, run_command, sent
from tests.helpers import TestEnv


def backups_dir(tenv: TestEnv) -> Path:
    return tenv.app.env.db_path.parent / "backups"


async def test_backup_creates_consistent_copy_and_reports_path_and_size(tenv: TestEnv) -> None:
    await tenv.app.audit.record("test.marker", actor_id=None, target_type="t", target_id=1)
    interaction = await run_command(tenv, "backup", user=admin(tenv))
    assert interaction.response_kinds() == ["defer"]
    message = sent(interaction)
    assert is_ephemeral(message)
    assert message.files == []  # the database is never attached to a Discord message
    files = list(backups_dir(tenv).iterdir())
    assert len(files) == 1
    backup = files[0]
    assert service.BACKUP_NAME_RE.match(backup.name)
    assert backup.name == f"bot-{service.backup_stamp(tenv.clock.now())}.db"
    text = message.text()
    assert message.embeds[0].title == "Backup created"
    assert f"File: `{backup}`" in text
    assert f"Size: {backup.stat().st_size} bytes" in text
    assert "never uploaded to Discord" in text

    conn = sqlite3.connect(str(backup))
    try:
        actions = [row[0] for row in conn.execute("SELECT action FROM audit_events")]
        assert "test.marker" in actions
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    events = await tenv.app.db.run(recent, tenv.app.env.guild_id, limit=1)
    assert events[0].action == "admin.backup"
    assert events[0].details["file"] == backup.name


async def test_backup_rotation_keeps_newest_ten_and_ignores_other_files(tenv: TestEnv) -> None:
    directory = backups_dir(tenv)
    directory.mkdir(parents=True)
    old = [directory / f"bot-202001{day:02d}T000000Z.db" for day in range(1, 13)]
    for path in old:
        path.write_bytes(b"old")
    unrelated = [directory / "notes.txt", directory / "bot-latest.db", directory / "other-20200101T000000Z.db"]
    for path in unrelated:
        path.write_bytes(b"keep me")

    interaction = await run_command(tenv, "backup", user=admin(tenv))
    remaining = service.list_backups(directory)
    assert len(remaining) == 10
    newest = directory / f"bot-{service.backup_stamp(tenv.clock.now())}.db"
    assert remaining[-1] == newest
    assert remaining[:-1] == old[-9:]
    assert all(not path.exists() for path in old[:3])
    assert all(path.exists() for path in unrelated)
    assert "Backups kept: 10 · Older backups removed: 3" in sent(interaction).text()


async def test_backups_in_the_same_second_do_not_overwrite(tenv: TestEnv) -> None:
    user = admin(tenv)
    await run_command(tenv, "backup", user=user)
    await run_command(tenv, "backup", user=user)
    names = [p.name for p in service.list_backups(backups_dir(tenv))]
    stamp = service.backup_stamp(tenv.clock.now())
    assert names == [f"bot-{stamp}.db", f"bot-{stamp}-1.db"]
    assert (await audit_actions(tenv.app)).count("admin.backup") == 2


async def test_backup_failure_is_reported_and_leaves_no_partial_file(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_backup(destination: Path) -> Path:
        await asyncio.to_thread(destination.write_bytes, b"partial")
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(tenv.app.db, "backup_to", broken_backup)
    interaction = await run_command(tenv, "backup", user=admin(tenv))
    message = sent(interaction)
    assert message.embeds[0].title == "Backup failed"
    assert "OperationalError: disk I/O error" in (message.embeds[0].description or "")
    assert service.list_backups(backups_dir(tenv)) == []
    assert "admin.backup" not in await audit_actions(tenv.app)


async def test_concurrent_backup_is_refused(tenv: TestEnv) -> None:
    lock = state_for(tenv.app).backup_lock
    await lock.acquire()
    try:
        interaction = await run_command(tenv, "backup", user=admin(tenv))
    finally:
        lock.release()
    assert sent(interaction).embeds[0].title == "A backup is already running. Try again in a moment."
    assert not backups_dir(tenv).exists()


async def test_in_memory_database_cannot_be_backed_up(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    monkeypatch.setattr(tenv.app, "env", replace(tenv.app.env, db_path=Path(":memory:")))
    with pytest.raises(service.BackupUnavailable):
        await service.create_backup(tenv.app, actor_id=1)


def test_rotation_orders_same_second_suffixes(tmp_path: Path) -> None:
    names = ["bot-20240101T000000Z-2.db", "bot-20240101T000000Z.db", "bot-20240101T000000Z-1.db"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    kept, removed = service.rotate_backups(tmp_path, keep=2)
    assert [p.name for p in removed] == ["bot-20240101T000000Z.db"]
    assert [p.name for p in kept] == ["bot-20240101T000000Z-1.db", "bot-20240101T000000Z-2.db"]


async def test_backup_completes_when_the_confirmation_cannot_be_sent(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    import discord

    from tests.fakes import FakeFollowup, http_error

    async def forbidden(self: FakeFollowup, content: str | None = None, **kwargs: object) -> None:
        raise http_error(discord.Forbidden, 403, 50001, "Missing Access")

    monkeypatch.setattr(FakeFollowup, "send", forbidden)
    interaction = await run_command(tenv, "backup", user=admin(tenv))
    assert interaction.sent == []
    assert len(service.list_backups(backups_dir(tenv))) == 1
    assert (await audit_actions(tenv.app)).count("admin.backup") == 1
