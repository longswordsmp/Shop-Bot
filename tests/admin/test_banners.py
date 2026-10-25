"""Banner pictures on the admin replies: dashboards, confirmations, refusals and errors; paging keeps the banner of
one message, and a reply split across messages shows it on the first message only."""

from __future__ import annotations

import shutil
import tomllib
import types
from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.config.readiness import Problem
from shopbot.features import core_keys as ck
from shopbot.features.admin import service, views
from shopbot.features.admin.state import state_for
from shopbot.features.guides import how_to_buy
from tests.admin.test_edit_text import as_discord_message, run_edit_text
from tests.admin.util import admin, attachment_text, customer, run_command, sent
from tests.fakes import FakeMember, FakeMessage, click, submit_modal
from tests.guides.util import guide_env, messages
from tests.helpers import BASE_SETTINGS, TestEnv, banner_names, make_banner_env


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await make_banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def assert_banner(tenv: TestEnv, message: FakeMessage, banner_id: BannerId) -> None:
    assert banner_names(message) == [banner_file(tenv, banner_id)], message.text()


async def open_content_form(tenv: TestEnv, user: FakeMember, key: str) -> str:
    interaction = await run_command(tenv, "set", group="content", user=user, key=key)
    assert interaction.response.modal is not None
    return str(interaction.response.modal.custom_id)


async def set_value(tenv: TestEnv, user: FakeMember, key: str, value: str) -> FakeMessage:
    custom_id = await open_content_form(tenv, user, key)
    return sent(await submit_modal(tenv.app, user=user, custom_id=custom_id, fields={"value": value}))


# -- /status ----------------------------------------------------------------------------------------------------


async def test_status_replies(benv: TestEnv) -> None:
    assert_banner(benv, sent(await run_command(benv, "status", user=admin(benv))), "staff-dashboard")
    assert_banner(benv, sent(await run_command(benv, "status", user=admin(benv), feature="admin")), "staff-dashboard")
    assert_banner(benv, sent(await run_command(benv, "status", user=admin(benv), feature="nope")), "error")


async def test_paging_keeps_the_banner_without_uploading_it_again(benv: TestEnv) -> None:
    status = benv.app.readiness.statuses["tickets"]
    status.ready = False
    status.problems = [
        Problem("tickets", "missing_content", "not configured", f"tickets.synthetic.k{i:03d}") for i in range(200)
    ]
    user = admin(benv)
    message = sent(await run_command(benv, "status", user=user, feature="tickets"))
    assert_banner(benv, message, "staff-dashboard")
    uploaded = message.attachments[0]
    for page in (2, 3, 1):
        clicked = await click(benv.app, user=user, custom_id=f"sb:ad:status:tickets:{page}", message=message)
        assert clicked.response_kinds() == ["edit_message"]
        assert "attachments" not in message.edits[-1]  # the banner on the message is kept as it is
        assert message.attachments == [uploaded]
    assert len(message.edits) == 3


# -- /content ---------------------------------------------------------------------------------------------------


async def test_content_set_replies(benv: TestEnv) -> None:
    user = admin(benv)
    assert_banner(benv, await set_value(benv, user, ck.ERROR_GENERIC, "[new text]"), "successful-confirmation")
    fenced = await set_value(benv, user, ck.ERROR_GENERIC, "``` fences")
    assert [f.filename for f in fenced.files] == [banner_file(benv, "successful-confirmation"), "value.txt"]
    assert attachment_text(fenced) == "``` fences"
    invalid = await set_value(benv, user, ck.ERROR_TITLE, "x" * 257)  # an embed title holds at most 256
    assert invalid.embeds[0].title == f"Not saved: {ck.ERROR_TITLE}"
    assert_banner(benv, invalid, "rejected-or-unsuccessful-submission")
    custom_id = await open_content_form(benv, user, ck.ERROR_GENERIC)
    benv.clock.advance(3601)
    expired = sent(await submit_modal(benv.app, user=user, custom_id=custom_id, fields={"value": "[late]"}))
    assert_banner(benv, expired, "cancellation")
    unknown = await run_command(benv, "set", group="content", user=user, key="common.error.generik")
    assert_banner(benv, sent(unknown), "error")


async def test_content_show_clear_missing_and_keys(benv: TestEnv) -> None:
    user = admin(benv)
    shown = await run_command(benv, "show", group="content", user=user, key=ck.ERROR_GENERIC)
    assert_banner(benv, sent(shown), "staff-dashboard")
    await set_value(benv, user, ck.ERROR_GENERIC, "[override]")
    cleared = await run_command(benv, "clear", group="content", user=user, key=ck.ERROR_GENERIC)
    assert_banner(benv, sent(cleared), "successful-confirmation")
    again = await run_command(benv, "clear", group="content", user=user, key=ck.ERROR_GENERIC)
    assert_banner(benv, sent(again), "general-response")
    assert_banner(benv, sent(await run_command(benv, "missing", group="content", user=user)), "staff-dashboard")
    assert_banner(benv, sent(await run_command(benv, "keys", group="content", user=user)), "staff-dashboard")
    unknown = await run_command(benv, "keys", group="content", user=user, feature="nope")
    assert_banner(benv, sent(unknown), "error")


async def test_content_missing_paging_keeps_the_banner(benv: TestEnv) -> None:
    benv.app.content.replace(file_values={})
    benv.app.recompute_readiness()
    user = admin(benv)
    message = sent(await run_command(benv, "missing", group="content", user=user))
    assert "sb:ad:miss:-:2" in message.custom_ids()
    await click(benv.app, user=user, custom_id="sb:ad:miss:-:2", message=message)
    assert "attachments" not in message.edits[-1]
    assert_banner(benv, message, "staff-dashboard")


async def test_export_uploads_the_banner_first_and_the_file_after_it(benv: TestEnv) -> None:
    message = sent(await run_command(benv, "export", group="content", user=admin(benv)))
    names = [f.filename for f in message.files]
    assert names[0] == banner_file(benv, "successful-confirmation")
    assert len(names) == 2 and names[1].startswith("content-") and names[1].endswith(".toml")
    assert tomllib.loads(attachment_text(message))


# -- /reload, /backup, /audit, /tasks ---------------------------------------------------------------------------


async def test_reload_replies(benv: TestEnv) -> None:
    config_dir = benv.app.config_dir
    (config_dir / "settings.toml").write_text("schema_version = 1\n[bot]\nunknown_setting = 1\n", encoding="utf-8")
    failed = await run_command(benv, "reload", user=admin(benv))
    assert_banner(benv, failed.sent[0], "error")
    assert all(banner_names(m) == [] for m in failed.sent[1:])
    shutil.copy(BASE_SETTINGS, config_dir / "settings.toml")
    with (config_dir / "settings.toml").open("a", encoding="utf-8") as handle:
        handle.write("\n[banners]\nenabled = true\n")
    reloaded = await run_command(benv, "reload", user=admin(benv))
    assert reloaded.sent[0].embeds[0].title == "Configuration reloaded"
    assert_banner(benv, reloaded.sent[0], "successful-confirmation")
    assert all(banner_names(m) == [] for m in reloaded.sent[1:])


async def test_long_reload_report_has_the_banner_on_its_first_message_only(benv: TestEnv) -> None:
    report = service.ReloadReport(
        ok=False,
        errors=tuple(f"settings.toml: problem {i} " + "e" * 300 for i in range(300)),
        features=tuple(service.FeatureLine(f"f{i}", True, False, i) for i in range(10)),
    )
    specs = views.reload_report(benv.app.ui, benv.app.op, report)
    assert len(specs) > 1
    assert specs[0].banner == benv.app.ui.banner("error")
    assert all(spec.banner is None for spec in specs[1:])


async def test_backup_replies(benv: TestEnv) -> None:
    done = sent(await run_command(benv, "backup", user=admin(benv)))
    assert_banner(benv, done, "successful-confirmation")
    assert [f.filename for f in done.files] == [banner_file(benv, "successful-confirmation")]  # never the database
    lock = state_for(benv.app).backup_lock
    await lock.acquire()
    try:
        busy = sent(await run_command(benv, "backup", user=admin(benv)))
    finally:
        lock.release()
    assert_banner(benv, busy, "submission-and-processing")


async def test_audit_list_shows_the_activity_banner_on_its_first_message(benv: TestEnv) -> None:
    for i in range(100):
        await benv.app.audit.record(
            "test.event", actor_id=None, target_type="thing", target_id=i, details={"payload": "y" * 250}
        )
    interaction = await run_command(benv, "audit", user=admin(benv), limit=100)
    first, *rest = interaction.sent
    assert rest, "the list is long enough to need several messages"
    assert_banner(benv, first, "activity-log")
    assert all(banner_names(message) == [] for message in rest)


async def test_tasks_list_shows_the_dashboard_banner(benv: TestEnv) -> None:
    assert_banner(benv, sent(await run_command(benv, "tasks", user=admin(benv))), "staff-dashboard")


# -- "Edit text" --------------------------------------------------------------------------------------------------


async def test_edit_text_replies_are_general_responses(tmp_path: Path) -> None:
    genv = await guide_env(tmp_path, banners=True)
    try:
        await how_to_buy.publish(genv.app)
        panel = messages(genv, "how_to_buy")[0]
        listing = sent(await run_edit_text(genv, admin(genv), as_discord_message(panel)))
        assert_banner(genv, listing, "general-response")
        other = types.SimpleNamespace(id=1, author=customer(genv), embeds=[], components=[])
        assert_banner(genv, sent(await run_edit_text(genv, admin(genv), other)), "general-response")
        empty = types.SimpleNamespace(
            id=1, author=genv.guild.me, embeds=[discord.Embed(title="Typed by staff")], components=[]
        )
        assert_banner(genv, sent(await run_edit_text(genv, admin(genv), empty)), "general-response")
    finally:
        await genv.close()
