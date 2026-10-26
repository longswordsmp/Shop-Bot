"""/reset: owner only, a private preview of what is deleted and kept, a typed confirmation, a backup and the private
setup channel first, the progress and result by DM; an interrupted run is recorded and reported; afterwards /setup
builds the server anew (new channels and roles, every panel, the staff cards of open orders, the Customer role)."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
from shopbot.features.admin import service as admin_service
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service as checkout_service
from shopbot.features.checkout import views as checkout_views
from shopbot.features.core_keys import ERROR_UNAVAILABLE
from shopbot.features.operations import customer_role as customer_role_module
from shopbot.features.setup import listeners, reset, reset_store
from shopbot.features.setup import reset_views as views
from shopbot.features.setup.service import setup_lock
from shopbot.ui.spec import EmbedSpec, MessageSpec
from tests.checkout.util import add_product, channel, order, place_order, post_answers_in_channel
from tests.fakes import FakeInteraction, FakeMember, click, http_error, submit_modal
from tests.helpers import GUILD_ID, TestEnv
from tests.operations.util import customer_role, make_ops, mark, owner, run_tasks, staff_channel
from tests.setup.util import audit_events, confirm, continue_id, phrase, preview, setup

HISTORY = ("transcripts", "audit_log", "reviews")  # channel keys kept for their history


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


def plan_of(tenv: TestEnv, actor: FakeMember) -> reset.ResetPlan:
    guild: Any = tenv.guild
    return reset.plan_reset(guild, reset.keep_ids(tenv.app, guild, actor))


def history_channels(tenv: TestEnv) -> set[int]:
    return {channel(tenv, key).id for key in HISTORY}


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
    me = owner(ops)
    high = ops.guild.add_role(name="Above the bot", position=60)
    rules = ops.guild.add_text_channel(name="rules")
    ops.guild.rules_channel = rules  # type: ignore[attr-defined]  # a Community server's required channel
    bot_extra = ops.guild.add_role(name="Bot permissions", position=20)
    ops.guild.me.add_role(bot_extra)
    admin_role = ops.guild.add_role(name="Admins", position=15, permissions=discord.Permissions(administrator=True))
    me.add_role(admin_role)  # OWNER_ID is not the server owner here: its Administrator role is kept
    message = await preview(ops, me)
    embed = message.embeds[0]
    assert embed.title == ops.app.op("server.reset.preview_title")
    assert f"``{phrase(ops)}``" in (embed.description or "")
    assert ops.app.op("server.reset.data_kept") in (embed.description or "")
    fields = {f.name: f.value or "" for f in embed.fields}
    plan = plan_of(ops, me)
    channels = fields[ops.app.op("server.reset.field.channels", count=len(plan.channels))]
    assert views.display_name(reset.CHANNEL, staff_channel(ops).name) in channels and "#rules" not in channels
    assert all(views.display_name(reset.CHANNEL, channel(ops, key).name) not in channels for key in HISTORY)
    roles = fields[ops.app.op("server.reset.field.roles", count=len(plan.roles))]
    assert "@customer" in roles and "@staff" in roles and "@Bot permissions" not in roles and "@Admins" not in roles
    kept = fields[ops.app.op("server.reset.field.kept")]
    for name in ("@Above the bot", "@ShopBot", "@Bot permissions", "@Admins", "#rules", "@everyone"):
        assert name in kept, name
    assert all(views.display_name(reset.CHANNEL, channel(ops, key).name) in kept for key in HISTORY)
    work = fields[ops.app.op("server.reset.field.work")]
    assert work == ops.app.op("server.reset.work_kept", orders=0, unpaid=0, tickets=0)
    assert high in ops.guild.roles
    assert message.custom_ids() == [continue_id(message), "sb:rs:no"]
    assert continue_id(message) == f"sb:rs:go:0:{plan.fingerprint}"


async def test_objects_set_by_id_in_settings_are_kept(ops: TestEnv) -> None:
    fixed = ops.guild.add_text_channel(name="fixed")
    settings = ops.app.settings
    channels = tuple(replace(c, id=fixed.id) if c.key == "shop" else c for c in settings.server.channels)
    ops.app.settings = replace(settings, server=replace(settings.server, channels=channels))
    plan = plan_of(ops, owner(ops))
    assert fixed.id not in {c.id for c in plan.channels}
    assert reset.Kept(reset.CHANNEL, "fixed", reset.KEEP_CONFIGURED) in plan.kept


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


async def test_a_name_with_markdown_is_shown_as_it_must_be_typed(ops: TestEnv) -> None:
    ops.guild.name = "Shop_Bot *Store*"
    message = await preview(ops, owner(ops))
    assert "``RESET Shop_Bot *Store*``" in (message.embeds[0].description or "")
    await confirm(ops, owner(ops), message, "RESET Shop_Bot *Store*")
    assert backups(ops) != []


# -- the reset -------------------------------------------------------------------------------------------------------


async def test_the_reset_deletes_everything_it_listed_and_reports_by_dm(ops: TestEnv) -> None:
    await setup(ops)  # panels posted and recorded
    me = owner(ops)
    high = ops.guild.add_role(name="Above the bot", position=60)
    message = await preview(ops, me)
    plan = plan_of(ops, me)
    history = history_channels(ops)
    deleted = [c for c in ops.guild.channels if c.id not in history]
    interaction = await confirm(ops, me, message, phrase(ops).lower())
    assert titles(interaction) == [ops.app.op("server.reset.started")]
    # Everything listed is gone; the history channels, the kept roles and the new private channel remain.
    assert all(c.deleted for c in deleted)
    assert {r.id for r in ops.guild.roles} == {ops.guild.default_role.id, ops.guild.bot_role.id, high.id}
    remaining = {c.id: c for c in ops.guild.channels}
    [setup_channel] = [c for i, c in remaining.items() if i not in history]
    assert set(remaining) - {setup_channel.id} == history
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
    # The records of what was deleted are forgotten; those of the history channels stay (/setup moves them).
    resources, published = await recorded(ops)
    assert set(resources) == {("channel", key) for key in HISTORY}
    assert all(p.channel_id in history for p in published)
    [event] = await audit_events(ops, "server.reset")
    assert event.actor_id == me.id
    assert event.details["deleted"] == {
        "channel": len(plan.channels),
        "category": len(plan.categories),
        "role": len(plan.roles),
    }
    assert event.details["backup"] == backup and event.details["wipe_shop_data"] is False
    assert event.details["completed"] is True
    assert await ops.app.db.run(reset_store.running) is None


async def test_the_backup_holds_the_data_from_before_the_reset(ops: TestEnv) -> None:
    await setup(ops)
    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    [backup] = backups(ops)
    path = admin_service.backup_directory(Path(ops.app.env.db_path)) / backup
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM guild_resources").fetchone()[0] > len(HISTORY)


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


async def test_no_setup_channel_no_reset(ops: TestEnv) -> None:
    ops.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    me = owner(ops)
    before = len(ops.guild.channels), len(ops.guild.roles)
    interaction = await confirm(ops, me, await preview(ops, me), phrase(ops))
    expected = ops.app.op("server.reset.setup_channel_failed", status=403)
    assert titles(interaction) == [expected] and me.dms[-1].embeds[0].title == expected
    assert (len(ops.guild.channels), len(ops.guild.roles)) == before


async def test_a_reset_waits_for_a_running_setup(ops: TestEnv) -> None:
    message = await preview(ops, owner(ops))
    async with setup_lock(ops.app):
        interaction = await confirm(ops, owner(ops), message, phrase(ops))
    assert titles(interaction) == [ops.app.op("server.reset.busy")]


async def test_failed_deletions_are_reported_and_the_run_continues(ops: TestEnv) -> None:
    ops.guild.fail_channel_delete = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    me = owner(ops)
    plan = plan_of(ops, me)
    await confirm(ops, me, await preview(ops, me), phrase(ops))
    result = me.dms[-1].embeds[0]
    assert result.title == ops.app.op("server.reset.result_title_problems")
    failed = {f.name: f.value or "" for f in result.fields}
    not_deleted = len(plan.channels) + len(plan.categories)
    assert "HTTP 403 (Discord code 50013)" in failed[ops.app.op("server.reset.field.failed", count=not_deleted)]
    # The roles were still deleted.
    assert [r.name for r in ops.guild.roles if not r.is_default()] == ["ShopBot"]
    [event] = await audit_events(ops, "server.reset")
    assert event.details["failed"] == not_deleted and event.details["completed"] is True


async def test_a_connection_error_on_one_deletion_does_not_stop_the_run(ops: TestEnv) -> None:
    me = owner(ops)
    victim = staff_channel(ops)

    async def broken(*, reason: str | None = None) -> None:
        raise OSError("connection reset")

    victim.delete = broken  # type: ignore[method-assign]
    await confirm(ops, me, await preview(ops, me), phrase(ops))
    assert victim in ops.guild.channels and len(ops.guild.channels) == len(HISTORY) + 2  # + the setup channel
    assert "HTTP 0" in me.dms[-1].text()


async def test_buttons_are_refused_while_the_reset_deletes(ops: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    customer = ops.member(name="customer")
    seen: list[FakeInteraction] = []
    real_sleep = asyncio.sleep

    async def sleep_and_click(delay: float) -> None:
        if not seen:
            seen.append(await click(ops.app, user=customer, custom_id="sb:rs:no"))
        await real_sleep(0)

    monkeypatch.setattr(reset.asyncio, "sleep", sleep_and_click)
    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    [refused] = seen
    assert refused.last_sent is not None
    assert refused.last_sent.embeds[0].description == ops.text(ERROR_UNAVAILABLE)
    # After the reset, buttons work again.
    after = await click(ops.app, user=customer, custom_id="sb:rs:no")
    assert after.last_sent is None or after.last_sent.embeds[0].description != ops.text(ERROR_UNAVAILABLE)


async def test_an_interrupted_reset_is_recorded_and_reported_at_the_next_start(
    ops: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup(ops)
    me = owner(ops)
    calls = 0
    real_sleep = asyncio.sleep

    async def restart_after_two(delay: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError  # the bot shuts down in the middle
        await real_sleep(0)

    monkeypatch.setattr(reset.asyncio, "sleep", restart_after_two)
    with pytest.raises(asyncio.CancelledError):
        await confirm(ops, me, await preview(ops, me), phrase(ops))
    # The setup channel exists, the records of what was deleted are forgotten, the run is audited as incomplete.
    assert any(c.name == ops.app.op("server.reset.setup_channel_name") for c in ops.guild.channels)
    [event] = await audit_events(ops, "server.reset")
    assert event.details["completed"] is False and sum(event.details["deleted"].values()) == 3
    resources, _ = await recorded(ops)
    assert all(ops.guild.get_channel(i) or ops.guild.get_role(i) for i in resources.values())
    assert await ops.app.db.run(reset_store.running) is not None
    # The next start tells the owner what to do.
    await listeners.on_ready(ops.app)
    assert me.dms[-1].embeds[0].title == ops.app.op("server.reset.interrupted_title")
    assert await ops.app.db.run(reset_store.running) is None


async def test_an_error_after_the_deletions_is_reported_by_dm(ops: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(conn: sqlite3.Connection, now: int) -> dict[str, int]:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(reset_store, "wipe_shop_data", broken)
    await add_product(ops, "[p]")
    me = owner(ops)
    await confirm(ops, me, await preview(ops, me, wipe=True), phrase(ops))
    assert me.dms[-1].embeds[0].title == ops.app.op("server.reset.interrupted_title")
    assert await ops.app.db.run(reset_store.running) is None
    count = await ops.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    assert count == 1  # nothing was wiped


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
    transcripts = channel(ops, "transcripts")
    await setup(ops)
    channels_before = list(ops.guild.channels)

    message = await preview(ops, owner(ops))
    work = next(f.value for f in message.embeds[0].fields if f.name == ops.app.op("server.reset.field.work"))
    assert work == ops.app.op("server.reset.work_kept", orders=1, unpaid=1, tickets=1)  # the other was delivered
    await confirm(ops, owner(ops), message, phrase(ops))
    await emit_deletions(ops, channels_before)
    assert ops.app.resources.role(ops.guild, "customer") is None  # type: ignore[arg-type]
    assert (await order(ops, open_order.id)).status == m.PENDING  # orders keep their status

    report = await setup(ops)
    assert report.counts.get("failed", 0) == 0, report.lines
    new_staff_channel = channel(ops, "order_queue")
    assert new_staff_channel.id != old_staff_channel.id
    assert channel(ops, "transcripts").id == transcripts.id  # kept, with its history
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
    assert await ops.app.db.run(state.kv_get, customer_role_module.RESTORE_KEY) is None
    # A second /setup posts nothing twice (the audit log gets its entry for the run).
    audit_log = channel(ops, "audit_log").id
    counts = {c.id: len(c.messages) for c in ops.guild.text_channels if c.id != audit_log}
    await setup(ops)
    assert {c.id: len(c.messages) for c in ops.guild.text_channels if c.id != audit_log} == counts


async def test_the_customer_role_comes_back_without_the_member_cache(
    ops: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = await add_product(ops, "[p]", price=1000)
    placed, _, _ = await place_order(ops, ops.member(name="regular"), [(product.id, 1)])
    await run_tasks(ops)
    for target in ("paid", "fulfilled"):
        await mark(ops, owner(ops), placed.id, target)
    await run_tasks(ops)
    member = ops.guild.get_member(placed.user_id)
    assert member is not None
    await member.remove_roles(customer_role(ops))
    await ops.app.db.transaction(customer_role_module.request_restore, 0)
    # Without the Server Members intent the cache has no members (the task fetches them).
    monkeypatch.setattr(ops.guild, "get_member", lambda member_id: None)
    assert await customer_role_module.restore(ops.app) == 1
    await run_tasks(ops)
    assert customer_role(ops) in member.roles


async def test_the_customer_role_is_restored_only_when_the_reset_deleted_it(ops: TestEnv) -> None:
    keep = customer_role(ops)
    keep.position = 60  # above the bot: kept
    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    assert keep in ops.guild.roles
    assert await ops.app.db.run(state.kv_get, customer_role_module.RESTORE_KEY) is None


async def test_a_reposted_card_brings_its_extra_messages(ops: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    # The customer's answers are posted under the card (operations.answers_in_orders_channel); one message here.
    post_answers_in_channel(ops)
    answers = MessageSpec(embeds=(EmbedSpec(description="[answers]"),))
    monkeypatch.setattr(checkout_views, "staff_details_messages", lambda ui, settings, order: [answers])
    product = await add_product(ops, "[p]", price=1000)
    placed, _, _ = await place_order(ops, ops.member(name="buyer"), [(product.id, 1)])
    await ops.app.db.transaction(
        lambda conn: conn.execute("UPDATE orders SET details = ? WHERE id = ?", ('{"d.name": "[n]"}', placed.id))
    )
    await run_tasks(ops)
    await checkout_service.post_staff_details(ops.app, placed.id)
    first = channel(ops, "order_queue")
    assert [m_.text() for m_ in first.messages][1:] == ["[answers]"]
    await confirm(ops, owner(ops), await preview(ops, owner(ops)), phrase(ops))
    await setup(ops)
    second = channel(ops, "order_queue")
    assert second.id != first.id and [m_.text() for m_ in second.messages][1:] == ["[answers]"]
    # A later change edits the card and posts nothing again.
    await checkout_service.refresh_staff_message(ops.app, placed.id)
    assert len(second.messages) == 2


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
    assert await ops.app.db.run(state.kv_get, customer_role_module.RESTORE_KEY) is None


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
