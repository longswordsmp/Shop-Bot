"""The running version: read from the deployment's commit variable, shown in /status and logged at startup."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from shopbot import app as app_module
from shopbot import version
from shopbot.core.instance_lock import InstanceLockError
from shopbot.env import Env
from tests.admin.util import admin, run_command, sent
from tests.helpers import GUILD_ID, TestEnv

SHA = "9F3c2b1a0d4e5f60718293a4b5c6d7e8f9012345"


@pytest.fixture
def no_commit(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The process environment without any commit variable (as in a local run)."""
    for name in version.COMMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_commit_comes_from_the_first_variable_that_holds_one() -> None:
    assert version.commit({"RAILWAY_GIT_COMMIT_SHA": SHA}) == SHA.lower()
    assert version.short_commit({"RAILWAY_GIT_COMMIT_SHA": SHA}) == "9f3c2b1"
    assert version.short_commit({"SOURCE_COMMIT": "abcdef1234", "GIT_COMMIT": "1234567"}) == "abcdef1"
    assert version.short_commit({"GIT_COMMIT": " 1234567 \n"}) == "1234567"
    both = {"RAILWAY_GIT_COMMIT_SHA": "0000000aaa", "SOURCE_COMMIT": "1111111bbb"}
    assert version.short_commit(both) == "0000000"  # Railway's own variable wins
    # Values that are not a commit hash are skipped.
    assert version.short_commit({"RAILWAY_GIT_COMMIT_SHA": "", "SOURCE_COMMIT": "main", "GIT_COMMIT": "abc"}) == (
        version.UNKNOWN
    )
    assert version.short_commit({"RAILWAY_GIT_COMMIT_SHA": "not-a-sha", "GIT_COMMIT": "fedcba9"}) == "fedcba9"
    assert version.commit({}) is None and version.short_commit({}) == "unknown"


async def test_status_shows_the_running_version(tenv: TestEnv, no_commit: pytest.MonkeyPatch) -> None:
    no_commit.setenv("RAILWAY_GIT_COMMIT_SHA", SHA)
    description = sent(await run_command(tenv, "status", user=admin(tenv))).embeds[0].description or ""
    assert "Running version: `9f3c2b1` (the commit this deployment was built from)." in description.split("\n")


async def test_status_says_unknown_without_a_commit_variable(tenv: TestEnv, no_commit: pytest.MonkeyPatch) -> None:
    del no_commit
    description = sent(await run_command(tenv, "status", user=admin(tenv))).embeds[0].description or ""
    assert tenv.app.op("admin.status.version_unknown") in description.split("\n")
    assert "Running version: unknown" in description


@pytest.mark.parametrize("sha", [SHA, None])
def test_the_starting_log_line_names_the_commit(
    tmp_path: Path, no_commit: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sha: str | None
) -> None:
    if sha is not None:
        no_commit.setenv("RAILWAY_GIT_COMMIT_SHA", sha)
    env = Env(
        discord_token="x" * 70,
        guild_id=GUILD_ID,
        owner_id=None,
        db_path=tmp_path / "bot.db",
        config_dir=tmp_path / "config",
        log_level="INFO",
        log_format="json",
        app_env="test",
        startup_failure_delay_seconds=0,
    )

    class Locked:
        def __init__(self, path: Path) -> None:
            del path

        def acquire(self) -> None:
            raise InstanceLockError("held by another process")

        def release(self) -> None:
            pass

    no_commit.setattr(app_module, "load_dotenv", lambda **kwargs: None)  # never read a local .env here
    no_commit.setattr(app_module, "load_env", lambda: env)
    no_commit.setattr(app_module, "configure_logging", lambda *args, **kwargs: None)  # keep pytest's log capture
    no_commit.setattr(app_module, "InstanceLock", Locked)  # stop right after the starting line
    with caplog.at_level(logging.INFO, logger="shopbot"):
        assert app_module.main() == app_module.EXIT_LOCKED
    (starting,) = [r for r in caplog.records if r.getMessage() == "starting"]
    assert starting.commit == ("9f3c2b1" if sha else "unknown")  # type: ignore[attr-defined]
