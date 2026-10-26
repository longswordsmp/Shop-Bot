"""/reset: owner only, a private preview of what is deleted, a typed confirmation, a backup first, the progress and
result by DM; afterwards /setup builds the server anew (new channels and roles, every panel, the staff cards of open
orders, the Customer role)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
from shopbot.features.admin import service as admin_service
from shopbot.features.checkout import models as m
from shopbot.features.setup import reset, reset_store
from shopbot.features.setup import reset_views as views
from shopbot.features.setup.service import setup_lock
from tests.checkout.util import add_product, channel, order, place_order
from tests.fakes import FakeInteraction, click, http_error, submit_modal
from tests.helpers import GUILD_ID, TestEnv
from tests.operations.util import customer_role, make_ops, mark, owner, run_tasks, staff_channel
from tests.setup.util import audit_events, confirm, continue_id, phrase, preview, setup


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset, "PACE_SECONDS", 0)


@pytest.fixture
async def ops(tmp_path: Path) -> Any:
    tenv = await make_ops(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def titles(interaction: FakeInteraction) -> list[str]:
    return [e.title or "" for message in interaction.sent for e in message.embeds]


def backups(tenv: TestEnv) -> list[str]:
    directory = admin_service.backup_directory(Path(tenv.app.env.db_path))
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


async def recorded(tenv: TestEnv) -> tuple[dict[tuple[str, str], int], list[Any]]:
    resources = await tenv.app.db.run(state.resource_all, GUILD_ID)
    published = await tenv.app.db.run(state.published_with_prefix, GUILD_ID, "")
    return resources, published


async def emit_deletions(tenv: TestEnv, channels: list[Any]) -> None:
    """Discord's channel-delete events for the deleted channels (the fakes do not send them by themselves)."""
    for deleted in channels:
        if deleted.deleted:
            await tenv.bot.emit("guild_channel_delete", deleted)


# -- command and preview ---------------------------------------------------------------------------------------------


async def test_command_is_for_administrators_and_keeps_data_by_default(ops: TestEnv) -> None:
    command = ops.bot.tree.get_command("reset", guild=ops.app.guild_object)
    assert command is not None
    assert command.default_permissions == discord.Permissions(administrator=True)
    params = {p.name: p for p in command.parameters}
    assert params["wipe_shop_data"].default is False
    assert command.description and all(p.description for p in command.parameters)


async def test_an_administrator_who_is_not_the_owner_cannot_reset(ops: TestEnv) -> None:
    admin = ops.member(name="admin", admin=True)
    before = len(ops.guild.channels)
    message = await preview(ops, admin)
    assert message.embeds[0].title == ops.app.op("server.reset.owner_only")
    assert message.custom_ids() == []
    # Neither can the controls of an owner's preview be used by someone else.
    owner_preview = await preview(ops, owner(ops))
    interaction = await click(ops.app, user=admin, custom_id=continue_id(owner_preview), message=owner_preview)
    assert interaction.response.modal is None
    assert titles(interaction) == [ops.app.op("server.reset.owner_only")]
    forged = await submit_modal(
        ops.app,
        user=admin,
        custom_id=views.confirm_modal(ops.app.op, ops.guild.name, wipe=False, fingerprint="x").custom_id,
        fields={views.PHRASE_INPUT_ID: phrase(ops)},
    )
    assert titles(forged) == [ops.app.op("server.reset.owner_only")]
    assert len(ops.guild.channels) == before and backups(ops) == []


async def test_the_server_owner_may_reset_too(ops: TestEnv) -> None:
    server_owner = ops.guild.add_member(name="server-owner", administrator=True)  # Discord: owners have every right
    ops.guild.owner_id = server_owner.id
    message = await preview(ops, server_owner)
    assert message.embeds[0].title == ops.app.op("server.reset.preview_title")


async def test_the_preview_lists_what_is_deleted_and_what_is_kept(ops: TestEnv) -> None:
    high = ops.guild.add_role(name="Above the bot", position=60)
    rules = ops.guild.add_text_channel(name="rules")
    ops.guild.rules_channel = rules  # type: ignore[attr-defined]  # a Community server's required channel
    message = await preview(ops, owner(ops))
    embed = message.embeds[0]
    assert embed.title == ops.app.op("server.reset.preview_title")
    assert f"`{phrase(ops)}`" in (embed.description or "")
    assert ops.app.op("server.reset.data_kept") in (embed.description or "")
    fields = {f.name: f.value for f in embed.fields}
    plan = reset.plan_reset(ops.guild)  # type: ignore[arg-type]
    channels = fields[ops.app.op("server.reset.field.channels", count=len(plan.channels))]
    shown = views.display_name(reset.CHANNEL, staff_channel(ops).name)
    assert channels is not None and shown in channels and "#rules" not in channels
    roles = fields[ops.app.op("server.reset.field.roles", count=len(plan.roles))]
    assert roles is not None and "@customer" in roles and "@staff" in roles
    kept = fields[ops.app.op("server.reset.field.kept")] or ""
    assert "@Above the bot" in kept and "@ShopBot" in kept and "#rules" in kept and "@everyone" in kept
    assert high in ops.guild.roles
    assert message.custom_ids() == [continue_id(message), "sb:rs:no"]
    assert continue_id(message) == f"sb:rs:go:0:{plan.fingerprint}"


async def test_cancel_changes_nothing(ops: TestEnv) -> None:
    message = await preview(ops, owner(ops))
    before = len(ops.guild.channels)
    interaction = await click(ops.app, user=owner(ops), custom_id="sb:rs:no", message=message)
    assert interaction.response_kinds() == ["edit_message"]
    assert message.embeds[0].title == ops.app.op("server.reset.cancelled")
    assert len(ops.guild.channels) == before


async def test_a_wrong_confirmation_changes_nothing(ops: TestEnv) -> None:
    message = await preview(ops, owner(ops))
    before = len(ops.guild.channels), len(ops.guild.roles)
    interaction = await confirm(ops, owner(ops), message, f"RESET not {ops.guild.name}")
    assert titles(interaction) == [ops.app.op("server.reset.mismatch", phrase=phrase(ops))]
    assert (len(ops.guild.channels), len(ops.guild.roles)) == before
    assert backups(ops) == [] and owner(ops).dms == []


def test_the_phrase_ignores_case_and_extra_spaces() -> None:
    assert reset.phrase_matches("  reset   My  Shop ", "My Shop")
    assert not reset.phrase_matches("RESET My Shop!", "My Shop")
    assert not reset.phrase_matches("My Shop", "My Shop")


# -- the reset -------------------------------------------------------------------------------------------------------


async def test_the_reset_deletes_everything_it_listed_and_reports_by_dm(ops: TestEnv) -> None:
    await setup(ops)  # panels posted and recorded
    me = owner(ops)
    high = ops.guild.add_role(name="Above the bot", position=60)
    message = await preview(ops, me)
    plan = reset.plan_reset(ops.guild)  # type: ignore[arg-type]
    channels_before = list(ops.guild.channels)
    interaction = await confirm(ops, me, message, phrase(ops).lower())
    assert titles(interaction) == [ops.app.op("server.reset.started")]
    # Everything listed is gone; only the kept roles and the new private channel remain.
    assert all(c.deleted for c in channels_before)
    assert {r.id for r in ops.guild.roles} == {ops.guild.default_role.id, ops.guild.bot_role.id, high.id}
    [setup_channel] = ops.guild.channels
    assert setup_channel.name == ops.app.op("server.reset.setup_channel_name")
    assert not setup_channel.permissions_for(ops.guild.default_role).view_channel
    assert setup_channel.permissions_for(me).view_channel and setup_channel.permissions_for(me).send_messages
    # The progress and the result came by DM.
    progress, result = me.dms
    assert progress.embeds[0].title == ops.app.op("server.reset.progress_title")
    assert ops.app.op("server.reset.progress_description", done=plan.total, total=plan.total, failed=0) == (
        progress.embeds[0].description
    )
    assert result.embeds[0].title == ops.app.op("server.reset.result_title")
    assert f"<#{setup_channel.id}>" in (result.embeds[0].description or "")
    # A backup was made first, under a name the /backup rotation never removes.
    [backup] = backups(ops)
    assert backup.startswith("before-reset-") and backup in (result.embeds[0].description or "")
    assert admin_service.list_backups(admin_service.backup_directory(Path(ops.app.env.db_path))) == []
    # Everything recorded is forgotten, so /setup creates it anew.
    resources, published = await recorded(ops)
    assert resources == {} and published == []
    [event] = await audit_events(ops, "server.reset")
    assert event.actor_id == me.id
    assert event.details["deleted"] == {
        "channel": len(plan.channels),
        "category": len(plan.categories),
        "role": len(plan.roles),
    }
    assert event.details["backup"] == backup and event.details["wipe_shop_data"] is False


async def test_the_backup_holds_the_data_from_before_the_reset(ops: TestEnv) -> None:
    await setup(ops)
    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    [backup] = backups(ops)
    path = admin_service.backup_directory(Path(ops.app.env.db_path)) / backup
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM guild_resources").fetchone()[0] > 0


async def test_a_server_that_changed_since_the_preview_is_not_reset(ops: TestEnv) -> None:
    message = await preview(ops, owner(ops))
    ops.guild.add_text_channel(name="created-after-the-preview")
    before = len(ops.guild.channels)
    interaction = await confirm(ops, owner(ops), message, phrase(ops))
    assert titles(interaction) == [ops.app.op("server.reset.changed")]
    assert len(ops.guild.channels) == before and backups(ops) == []


async def test_closed_dms_stop_the_reset_before_anything_happens(ops: TestEnv) -> None:
    me = owner(ops)
    me.dm_forbidden = True
    before = len(ops.guild.channels)
    interaction = await confirm(ops, me, await preview(ops, me), phrase(ops))
    assert titles(interaction) == [ops.app.op("server.reset.dm_closed")]
    assert len(ops.guild.channels) == before and backups(ops) == []
    assert await audit_events(ops, "server.reset") == []


async def test_a_failed_backup_stops_the_reset(ops: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(admin_service, "create_backup", broken)
    me = owner(ops)
    before = len(ops.guild.channels)
    interaction = await confirm(ops, me, await preview(ops, me), phrase(ops))
    expected = ops.app.op("server.reset.backup_failed", error="OSError")
    assert titles(interaction) == [expected]
    assert len(ops.guild.channels) == before
    assert me.dms[-1].embeds[0].title == expected


async def test_a_reset_waits_for_a_running_setup(ops: TestEnv) -> None:
    message = await preview(ops, owner(ops))
    async with setup_lock(ops.app):
        interaction = await confirm(ops, owner(ops), message, phrase(ops))
    assert titles(interaction) == [ops.app.op("server.reset.busy")]


async def test_failed_deletions_are_reported_and_the_run_continues(ops: TestEnv) -> None:
    ops.guild.fail_channel_delete = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    me = owner(ops)
    channels = len(reset.plan_reset(ops.guild).channels)  # type: ignore[arg-type]
    await confirm(ops, me, await preview(ops, me), phrase(ops))
    result = me.dms[-1].embeds[0]
    assert result.title == ops.app.op("server.reset.result_title_problems")
    failed = {f.name: f.value for f in result.fields}
    assert ops.app.op("server.reset.field.failed", count=len(ops.guild.channels) - 1) in failed
    assert "HTTP 403 (Discord code 50013)" in (failed[next(iter(failed))] or "")
    # The roles were still deleted.
    assert [r.name for r in ops.guild.roles if not r.is_default()] == ["ShopBot"]
    [event] = await audit_events(ops, "server.reset")
    assert event.details["failed"] == len(ops.guild.channels) - 1 >= channels


# -- after the reset: /setup builds everything again -----------------------------------------------------------------


async def test_setup_after_a_reset_rebuilds_the_server_and_restores_open_orders(ops: TestEnv) -> None:
    customer = ops.member(name="buyer")
    product = await add_product(ops, "[p]", price=1000)
    open_order, _, _ = await place_order(ops, customer, [(product.id, 1)])
    done_order, _, _ = await place_order(ops, ops.member(name="regular"), [(product.id, 1)])
    await run_tasks(ops)
    for target in ("paid", "fulfilled"):
        await mark(ops, owner(ops), done_order.id, target)
    await run_tasks(ops)
    regular = ops.guild.get_member(done_order.user_id)
    assert regular is not None and customer_role(ops) in regular.roles
    old_staff_channel = channel(ops, "order_queue")
    await setup(ops)
    channels_before = list(ops.guild.channels)

    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    await emit_deletions(ops, channels_before)
    assert ops.app.resources.role(ops.guild, "customer") is None  # type: ignore[arg-type]
    assert (await order(ops, open_order.id)).status == m.PENDING  # orders keep their status

    report = await setup(ops)
    assert report.counts.get("failed", 0) == 0, report.lines
    new_staff_channel = channel(ops, "order_queue")
    assert new_staff_channel.id != old_staff_channel.id
    # The open order's staff card is in the new orders channel.
    reposted = await order(ops, open_order.id)
    assert reposted.staff_channel_id == new_staff_channel.id
    assert any(msg.id == reposted.staff_message_id for msg in new_staff_channel.messages)
    assert any(ops.app.op("checkout.publish.staff_reposted", count=1) in line for line in report.lines["panels"]), (
        report.lines["panels"]
    )
    # The customer gets the new Customer role back.
    await run_tasks(ops)
    assert customer_role(ops) in regular.roles
    assert await ops.app.db.run(state.kv_get, "operations.customer_role.restore") is None
    # A second /setup posts nothing twice (the audit log gets its entry for the run).
    audit_log = channel(ops, "audit_log").id
    counts = {c.id: len(c.messages) for c in ops.guild.text_channels if c.id != audit_log}
    await setup(ops)
    assert {c.id: len(c.messages) for c in ops.guild.text_channels if c.id != audit_log} == counts


# -- the data wipe ---------------------------------------------------------------------------------------------------


def test_every_table_is_either_wiped_or_kept(tmp_path: Path) -> None:
    from shopbot.db.migrate import apply_migrations

    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tables -= {"sqlite_sequence"}
    assert set(reset_store.WIPED_TABLES).isdisjoint(reset_store.KEPT_TABLES)
    assert tables == set(reset_store.WIPED_TABLES) | set(reset_store.KEPT_TABLES)


async def test_kept_task_kinds_are_registered(ops: TestEnv) -> None:
    for kind in reset_store.KEPT_TASK_KINDS:
        assert kind in ops.app.scheduler._handlers


async def test_the_wipe_deletes_the_shop_data_and_keeps_the_rest(ops: TestEnv) -> None:
    product = await add_product(ops, "[p]", price=1000)
    await place_order(ops, ops.member(name="buyer"), [(product.id, 1)])
    await run_tasks(ops)
    await admin_service.ensure_maintenance_scheduled(ops.app)
    await ops.app.db.transaction(state.override_set, "catalog.panel.title", "Kept text", None, 0)
    counters = await ops.app.db.run(lambda conn: conn.execute("SELECT * FROM counters").fetchall())
    me = owner(ops)
    message = await preview(ops, me, wipe=True)
    assert ops.app.op("server.reset.data_wiped") in (message.embeds[0].description or "")
    assert continue_id(message).startswith("sb:rs:go:1:")
    await confirm(ops, me, message, phrase(ops))

    def snapshot(conn: sqlite3.Connection) -> dict[str, int]:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in reset_store.WIPED_TABLES}

    assert set((await ops.app.db.run(snapshot)).values()) == {0}
    assert await ops.app.db.run(lambda conn: conn.execute("PRAGMA foreign_key_check").fetchall()) == []
    assert await ops.app.db.run(lambda conn: conn.execute("SELECT * FROM counters").fetchall()) == counters
    assert await ops.app.db.run(state.overrides_all) == {"catalog.panel.title": "Kept text"}
    pending_tasks = await ops.app.db.run(task_repo.list_tasks, status="pending", limit=500)
    assert {t.kind for t in pending_tasks} <= set(reset_store.KEPT_TASK_KINDS)
    assert "admin.maintenance" in {t.kind for t in pending_tasks}
    result = me.dms[-1].embeds[0].description or ""
    assert ops.app.op("server.reset.result_data_wiped", orders=1, products=1, reviews=0) in result
    # Nothing to give back: there are no orders any more.
    assert await ops.app.db.run(state.kv_get, "operations.customer_role.restore") is None


# -- message limits --------------------------------------------------------------------------------------------------


def test_long_name_lists_are_cut_to_fit_a_field() -> None:
    def op(key: str, **values: object) -> str:
        return {"server.reset.more": "… and {count} more", "server.reset.none": "None"}[key].format(**values)

    names = [f"channel-{i:03d}" for i in range(300)]
    text = views.name_list(op, reset.CHANNEL, names)
    assert len(text) <= 1024 and text.endswith(" more")
    shown = text.count("#channel-")
    assert text.endswith(f"… and {300 - shown} more")
    assert views.name_list(op, reset.CHANNEL, names[:3]) == "#channel-000, #channel-001, #channel-002"
    assert views.name_list(op, reset.ROLE, []) == "None"
