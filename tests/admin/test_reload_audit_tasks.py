"""/reload, /audit and /tasks."""

from __future__ import annotations

import shutil

from shopbot.db.repos import tasks as task_repo
from shopbot.features import core_keys as ck
from tests.admin.util import admin, audit_actions, is_ephemeral, run_command, sent
from tests.helpers import BASE_SETTINGS, TestEnv

# --------------------------------------------------------------------------------------------------
# /reload
# --------------------------------------------------------------------------------------------------


async def test_reload_failure_keeps_running_configuration(tenv: TestEnv) -> None:
    config_dir = tenv.app.config_dir
    config_dir.mkdir(parents=True)
    (config_dir / "settings.toml").write_text("schema_version = 1\n[bot]\nunknown_setting = 1\n", encoding="utf-8")
    settings_before = tenv.app.settings
    interaction = await run_command(tenv, "reload", user=admin(tenv))
    message = sent(interaction)
    assert is_ephemeral(message)
    assert message.embeds[0].title == "Reload failed; the previous configuration is still active"
    assert "bot.unknown\\_setting: unknown setting (check spelling)" in message.text()
    assert tenv.app.settings is settings_before
    assert tenv.app.text(ck.ERROR_GENERIC) == tenv.text(ck.ERROR_GENERIC)
    assert "admin.reload" in await audit_actions(tenv.app)


async def test_reload_success_applies_files_and_shows_readiness(tenv: TestEnv) -> None:
    config_dir = tenv.app.config_dir
    config_dir.mkdir(parents=True)
    shutil.copy(BASE_SETTINGS, config_dir / "settings.toml")  # test base settings: every feature off
    (config_dir / "content.toml").write_text('[common.error]\ngeneric = "From the file"\n', encoding="utf-8")
    interaction = await run_command(tenv, "reload", user=admin(tenv))
    messages = interaction.sent
    assert messages[0].embeds[0].title == "Configuration reloaded"
    text = "\n".join(m.text() for m in messages)
    assert "Run /setup to create missing channels and republish panels" in text
    assert "▫️ `tickets` — disabled" in text
    assert "✅ `admin` — ready" in text
    assert not tenv.app.settings.features.tickets
    assert tenv.app.text(ck.ERROR_GENERIC) == "From the file"


async def test_reload_keeps_database_overrides(tenv: TestEnv) -> None:
    from shopbot.db.repos import state as state_repo

    await tenv.app.db.transaction(state_repo.override_set, ck.ERROR_GENERIC, "override", 1, 1)
    config_dir = tenv.app.config_dir
    config_dir.mkdir(parents=True)
    shutil.copy(BASE_SETTINGS, config_dir / "settings.toml")
    (config_dir / "content.toml").write_text('[common.error]\ngeneric = "From the file"\n', encoding="utf-8")
    await run_command(tenv, "reload", user=admin(tenv))
    assert tenv.app.text(ck.ERROR_GENERIC) == "override"


# --------------------------------------------------------------------------------------------------
# /audit
# --------------------------------------------------------------------------------------------------


async def test_audit_lists_recent_events_with_filters(tenv: TestEnv) -> None:
    app = tenv.app
    actor = admin(tenv)
    await app.audit.record("tickets.close", actor_id=actor.id, target_type="ticket", target_id=5, details={"a": 1})
    await app.audit.record("tickets.close", actor_id=actor.id, target_type="ticket", target_id=6)
    await app.audit.record("orders.paid", actor_id=None, target_type="order", target_id=5)

    everything = await run_command(tenv, "audit", user=actor)
    assert everything.response_kinds() == ["defer"]
    text = sent(everything).text()
    assert text.index("**orders.paid**") < text.index("**tickets.close**")
    assert "actor: system" in text
    assert f"actor: <@{actor.id}>" in text
    assert 'details: `{"a": 1}`' in text

    limited = await run_command(tenv, "audit", user=actor, limit=1)
    assert sent(limited).text().count("`#") == 1

    filtered = await run_command(tenv, "audit", user=actor, target_type="ticket", target_id="5")
    text = sent(filtered).text()
    assert text.count("`#") == 1 and "`ticket 5`" in text


async def test_audit_empty_and_untrusted_details_do_not_ping(tenv: TestEnv) -> None:
    empty = await run_command(tenv, "audit", user=admin(tenv), target_type="nothing")
    assert sent(empty).embeds[0].description == "No matching audit events."
    await tenv.app.audit.record(
        "catalog.product_update",
        actor_id=None,
        target_type="product",
        target_id=1,
        details={"name": "@everyone <@&123> `tick` " + "x" * 2000},
    )
    interaction = await run_command(tenv, "audit", user=admin(tenv))
    message = sent(interaction)
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False and mentions.roles is False
    assert "x" * 400 not in message.text()  # long details are clipped (operator diagnostics only)


async def test_audit_paginates_large_results_across_messages(tenv: TestEnv) -> None:
    for i in range(100):
        await tenv.app.audit.record(
            "test.event", actor_id=None, target_type="thing", target_id=i, details={"payload": "y" * 250}
        )
    interaction = await run_command(tenv, "audit", user=admin(tenv), limit=100)
    assert len(interaction.sent) > 1
    footers = [m.embeds[0].footer.text for m in interaction.sent]
    assert footers[0] == f"Page 1 of {len(footers)}"
    assert sum(m.text().count("`#") for m in interaction.sent) == 100
    assert all(is_ephemeral(m) for m in interaction.sent)


# --------------------------------------------------------------------------------------------------
# /tasks
# --------------------------------------------------------------------------------------------------


async def test_tasks_lists_tasks_with_errors_of_failed_ones(tenv: TestEnv) -> None:
    app = tenv.app
    now = app.clock.now()
    await app.scheduler.schedule("admin.maintenance", run_at=now + 60, dedupe_key="t-pending")
    failed = await app.db.transaction(
        task_repo.schedule,
        kind="admin.maintenance",
        run_at=now,
        payload={},
        max_attempts=1,
        dedupe_key="t-failed",
        replace=False,
        now=now,
    )
    assert failed is not None
    await app.db.transaction(task_repo.mark_failed_attempt, failed, "RuntimeError: kaput", None, now)

    interaction = await run_command(tenv, "tasks", user=admin(tenv))
    message = sent(interaction)
    assert message.embeds[0].title == "Scheduled tasks"
    assert message.embeds[0].description == "Pending: 1 · Running: 0 · Failed: 1"
    text = message.text()
    assert "**admin.maintenance** · failed" in text
    assert "last error: `RuntimeError: kaput`" in text

    only_failed = await run_command(tenv, "tasks", user=admin(tenv), status="failed")
    assert sent(only_failed).text().count("`#") == 1

    none = await run_command(tenv, "tasks", user=admin(tenv), status="cancelled")
    assert "No matching tasks." in sent(none).text()
