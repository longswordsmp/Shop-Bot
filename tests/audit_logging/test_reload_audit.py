"""#bot-log detail for /reload: the ``admin.reload`` audit entry lists the top-level settings sections that changed and
how many content keys changed - never a value."""

from __future__ import annotations

import dataclasses
import shutil

from shopbot.features import core_keys as ck
from shopbot.features.admin.service import changed_content_count, changed_sections
from tests.admin.util import admin, run_command, sent
from tests.audit_logging.util import audit_events
from tests.helpers import BASE_SETTINGS, TestEnv, make_settings, provision_channels

FILE_TEXT = "Text from the reloaded file"


def _write_config(tenv: TestEnv) -> None:
    config_dir = tenv.app.config_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE_SETTINGS, config_dir / "settings.toml")  # the test base: every feature off
    (config_dir / "content.toml").write_text(f'[common.error]\ngeneric = "{FILE_TEXT}"\n', encoding="utf-8")


async def test_reload_lists_changed_sections_and_counts_changed_content(tenv: TestEnv) -> None:
    await provision_channels(tenv)
    fixture_keys = {key for key, value in tenv.content.items() if value}
    _write_config(tenv)
    interaction = await run_command(tenv, "reload", user=admin(tenv))
    assert sent(interaction).embeds[0].title == tenv.app.op("admin.reload.ok_title")
    [event] = await audit_events(tenv.app, "admin.reload")
    details = event.details
    assert details["ok"] is True and details["errors"] == 0
    # The test environment adds technical and business settings on top of the base file; the file drops them.
    assert details["settings_sections_changed"] == ["bot", "features", "checkout", "payments", "reviews"]
    # Every fixture text disappeared (or changed): the count only, never a value.
    assert details["content_keys_changed"] == len(fixture_keys)
    assert FILE_TEXT not in str(details)
    assert tenv.text(ck.ERROR_GENERIC) not in str(details)
    # Posted in the audit channel too.
    audit_channel = tenv.app.resources.text_channel(tenv.guild, "audit_log")  # type: ignore[arg-type]
    assert audit_channel is not None
    posted = audit_channel.messages[-1]  # type: ignore[attr-defined]
    assert "settings_sections_changed" in posted.text() and FILE_TEXT not in posted.text()


async def test_reloading_unchanged_files_lists_nothing(tenv: TestEnv) -> None:
    _write_config(tenv)
    await run_command(tenv, "reload", user=admin(tenv))
    await run_command(tenv, "reload", user=admin(tenv))
    first, second = await audit_events(tenv.app, "admin.reload")
    assert first.details["settings_sections_changed"]
    assert second.details["settings_sections_changed"] == []
    assert second.details["content_keys_changed"] == 0


async def test_a_failed_reload_changes_and_lists_nothing(tenv: TestEnv) -> None:
    config_dir = tenv.app.config_dir
    config_dir.mkdir(parents=True)
    (config_dir / "settings.toml").write_text("schema_version = 1\n[bot]\nunknown_setting = 1\n", encoding="utf-8")
    await run_command(tenv, "reload", user=admin(tenv))
    [event] = await audit_events(tenv.app, "admin.reload")
    assert event.details == {
        "ok": False,
        "errors": 1,
        "settings_sections_changed": [],
        "content_keys_changed": 0,
    }


async def test_reload_is_admin_only_and_unaudited_when_refused(tenv: TestEnv) -> None:
    _write_config(tenv)
    customer = tenv.member(name="customer")
    await run_command(tenv, "reload", user=customer)
    assert await audit_events(tenv.app, "admin.reload") == []
    assert tenv.app.settings.features.tickets  # nothing was reloaded


def test_changed_sections_compares_top_level_sections_in_schema_order() -> None:
    before = make_settings()
    tickets = dataclasses.replace(before.tickets, max_open_per_user=3)
    after = dataclasses.replace(before, tickets=tickets, audit=dataclasses.replace(before.audit, retention_days=9))
    assert changed_sections(before, after) == ["tickets", "audit"]
    assert changed_sections(before, make_settings()) == []


def test_changed_content_count_counts_added_removed_and_edited_keys() -> None:
    before = {"a": "1", "b": "2", "c": "3"}
    after = {"a": "1", "b": "two", "d": "4"}
    assert changed_content_count(before, after) == 3  # b edited, c removed, d added
    assert changed_content_count(before, dict(before)) == 0
