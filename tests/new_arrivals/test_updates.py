"""Automatic product updates: qualifying vs non-qualifying catalog changes, the durable task, mentions, duplicates,
Discord failures, restarts, and readiness."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.events import PRODUCT_CHANGED
from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.repos import tasks as task_repo
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog import service as catalog_service
from shopbot.features.catalog.views import escape
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals import repo, service, tasks
from tests.catalog.util import buttons
from tests.fakes import FakeMessage, http_error
from tests.helpers import GUILD_ID
from tests.new_arrivals.util import (
    STAFF_ID,
    NaEnv,
    add_product,
    announcements,
    audit,
    form,
    na_env,
    product,
    run_tasks,
    set_stock,
    update_tasks,
)
from tests.setup.util import restarted_app


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[NaEnv]:
    na = await na_env(tmp_path)
    try:
        yield na
    finally:
        await na.close()


def title(env: NaEnv, key: str, name: str) -> str:
    return env.text(key).replace("{name}", escape(name))


def only_update(env: NaEnv) -> FakeMessage:
    [update] = env.updates()
    return update


# --------------------------------------------------------------------------------------------------
# New products
# --------------------------------------------------------------------------------------------------


async def test_added_product_is_announced_with_its_card_and_a_role_mention(env: NaEnv) -> None:
    product_id = await add_product(env, "[fresh *item*]", stock="5", description="[the description]")
    assert await update_tasks(env) == [(f"new_arrivals.announce:{product_id}:new:1", "pending")]
    assert env.updates() == []  # posted by the durable task, not inline
    assert await run_tasks(env) == 1

    update = only_update(env)
    embed = update.embeds[0]
    assert embed.title == title(env, keys.NEW_PRODUCT_TITLE, "[fresh *item*]")
    assert "\\*item\\*" in (embed.title or "")  # the staff-entered name is markdown-escaped
    assert embed.description == "[the description]"
    names = [f.name for f in embed.fields]
    assert names[:2] == [env.text("catalog.field.price"), env.text("catalog.field.availability")]
    assert embed.fields[0].value == env.app.ui.money(1250)
    assert embed.fields[1].value == env.text("catalog.availability.in_stock").replace("{count}", "5")
    assert embed.timestamp is not None
    assert embed.colour is not None and embed.colour.value == env.app.ui.color("primary")
    assert env.text(keys.NEW_PRODUCT_FOOTER) in (embed.footer.text or "")
    # mention of exactly the notification role, nothing else may ping
    assert update.content == f"<@&{env.role.id}>"
    mentions = update.allowed_mentions
    assert mentions is not None
    assert [r.id for r in mentions.roles] == [env.role.id]  # type: ignore[union-attr]
    assert mentions.users is False and mentions.everyone is False
    # link button to the catalog listing channel
    [link] = update.components()
    assert link.label == env.text(keys.BUTTON_VIEW_CATALOG)  # type: ignore[attr-defined]
    catalog_id = env.app.resources.channel_id("catalog")
    assert link.url == f"https://discord.com/channels/{GUILD_ID}/{catalog_id}"  # type: ignore[attr-defined]

    [record] = await announcements(env, product_id)
    assert (record.event, record.product_version, record.status) == ("new", 1, "posted")
    assert (record.message_id, record.channel_id, record.mentioned) == (update.id, env.channel.id, True)
    [announced] = await audit(env, "new_arrivals.announced")
    assert announced.target_id == str(product_id) and announced.actor_id is None
    assert announced.details["event"] == "new"


async def test_product_image_is_shown(env: NaEnv) -> None:
    product_id = await add_product(env, stock="1")
    await run_tasks(env)
    await catalog_service.set_image(
        env.app, actor_id=STAFF_ID, product_id=product_id, url="https://cdn.example.com/a.png"
    )
    await set_stock(env, product_id, "set", 0)
    await set_stock(env, product_id, "set", 2)
    await run_tasks(env)
    restock = env.updates()[-1]
    assert restock.embeds[0].image.url == "https://cdn.example.com/a.png"


async def test_uploaded_product_image_is_attached_to_the_update(env: NaEnv) -> None:
    from shopbot.features.catalog import images
    from tests.catalog.test_images import png

    product_id = await add_product(env, stock="1")
    await run_tasks(env)
    uploaded = images.validate_bytes("photo.png", png())
    await env.app.db.transaction(images.store_image, GUILD_ID, product_id, uploaded, actor_id=STAFF_ID, now=1)
    await set_stock(env, product_id, "set", 0)
    await set_stock(env, product_id, "set", 2)
    await run_tasks(env)
    restock = env.updates()[-1]
    url = restock.embeds[0].image.url or ""
    assert url.startswith("attachment://")
    assert [f.filename for f in restock.files] == [url.removeprefix("attachment://")]


async def test_switching_a_product_back_on_is_announced_as_new(env: NaEnv) -> None:
    product_id = await add_product(env, stock="3")
    await run_tasks(env)
    await catalog_service.toggle_active(env.app, actor_id=STAFF_ID, product_id=product_id)  # off: nothing
    await run_tasks(env)
    assert len(env.updates()) == 1
    await catalog_service.toggle_active(env.app, actor_id=STAFF_ID, product_id=product_id)  # on: new arrival
    await run_tasks(env)
    assert len(env.updates()) == 2
    assert env.updates()[-1].embeds[0].title == title(env, keys.NEW_PRODUCT_TITLE, "[new product]")
    assert [(a.event, a.product_version) for a in await announcements(env, product_id)] == [("new", 1), ("new", 3)]


async def test_unlimited_product_shows_unlimited_availability(env: NaEnv) -> None:
    await add_product(env)
    await run_tasks(env)
    assert only_update(env).embeds[0].fields[1].value == env.text("catalog.availability.unlimited")


# --------------------------------------------------------------------------------------------------
# Restocks
# --------------------------------------------------------------------------------------------------


async def test_sold_out_product_restocked_is_announced(env: NaEnv) -> None:
    product_id = await add_product(env, stock="0")
    await run_tasks(env)
    assert len(env.updates()) == 1  # the new arrival (shown as sold out)
    assert env.updates()[0].embeds[0].fields[1].value == env.text("catalog.availability.out_of_stock")
    await set_stock(env, product_id, "add", 4)
    await run_tasks(env)
    restock = env.updates()[-1]
    assert restock.embeds[0].title == title(env, keys.RESTOCK_TITLE, "[new product]")
    assert restock.embeds[0].fields[1].value == env.text("catalog.availability.in_stock").replace("{count}", "4")
    assert restock.embeds[0].colour.value == env.app.ui.color("success")  # type: ignore[union-attr]
    assert env.text(keys.RESTOCK_FOOTER) in (restock.embeds[0].footer.text or "")
    assert restock.content == f"<@&{env.role.id}>"


@pytest.mark.parametrize(("mode", "amount"), [("set", 7), ("unlimited", None)])
async def test_restock_by_setting_stock_or_unlimited(env: NaEnv, mode: str, amount: int | None) -> None:
    product_id = await add_product(env, stock="0")
    await run_tasks(env)
    await set_stock(env, product_id, mode, amount)
    await run_tasks(env)
    assert [a.event for a in await announcements(env, product_id)] == ["new", "restock"]


async def test_restock_through_the_edit_form(env: NaEnv) -> None:
    product_id = await add_product(env, stock="0")
    await run_tasks(env)
    current = await product(env, product_id)
    reply = await catalog_service.edit_from_form(
        env.app, actor_id=STAFF_ID, product_id=product_id, expected_version=current.version, fields=form(stock="9")
    )
    assert reply.tone == "success"
    await run_tasks(env)
    assert [a.event for a in await announcements(env, product_id)] == ["new", "restock"]


async def test_product_sold_out_by_reservations_then_restocked(env: NaEnv) -> None:
    product_id = await add_product(env, stock="2")
    await run_tasks(env)
    now = env.tenv.clock.now()
    assert await env.app.db.transaction(catalog_repo.reserve, product_id, 2, now)
    assert (await product(env, product_id)).available == 0
    await set_stock(env, product_id, "add", 3)
    await run_tasks(env)
    assert [a.event for a in await announcements(env, product_id)] == ["new", "restock"]
    assert env.updates()[-1].embeds[0].fields[1].value == env.text("catalog.availability.in_stock").replace(
        "{count}", "3"
    )


async def test_repeated_restocks_are_each_announced_once(env: NaEnv) -> None:
    product_id = await add_product(env, stock="0")
    for _ in range(2):
        await set_stock(env, product_id, "set", 5)
        await set_stock(env, product_id, "set", 0)
    await run_tasks(env)
    events = [(a.event, a.status) for a in await announcements(env, product_id)]
    # the restocks no longer qualify when their task runs (sold out again): skipped, never shown as available
    assert events == [("new", "posted")]
    await set_stock(env, product_id, "set", 5)
    await run_tasks(env)
    assert [a.event for a in await announcements(env, product_id)] == ["new", "restock"]


# --------------------------------------------------------------------------------------------------
# Changes that never qualify
# --------------------------------------------------------------------------------------------------


async def test_non_qualifying_changes_post_nothing(env: NaEnv) -> None:
    product_id = await add_product(env, stock="5")
    await run_tasks(env)
    baseline = len(env.updates())
    app = env.app
    await catalog_service.set_price(app, actor_id=STAFF_ID, product_id=product_id, amount="99.00")
    await catalog_service.set_image(app, actor_id=STAFF_ID, product_id=product_id, url="https://cdn.example.com/b.png")
    await catalog_service.set_position(app, actor_id=STAFF_ID, product_id=product_id, position=3)
    await set_stock(env, product_id, "add", 5)  # still available: not a restock
    await set_stock(env, product_id, "set", 0)  # sold out: no update
    current = await product(env, product_id)
    await catalog_service.edit_from_form(
        app,
        actor_id=STAFF_ID,
        product_id=product_id,
        expected_version=current.version,
        fields=form("[renamed]", stock="0", description="[changed]"),
    )
    await catalog_service.toggle_active(app, actor_id=STAFF_ID, product_id=product_id)  # switched off
    await set_stock(env, product_id, "set", 8)  # restocked while switched off: not announced
    await catalog_service.archive(app, actor_id=STAFF_ID, product_id=product_id, confirm=True)
    await run_tasks(env)
    assert len(env.updates()) == baseline
    assert [k for k, _ in await update_tasks(env)] == [f"new_arrivals.announce:{product_id}:new:1"]


async def test_inactive_new_product_is_not_announced(env: NaEnv) -> None:
    now = env.tenv.clock.now()
    created = await env.app.db.transaction(
        catalog_repo.create_product,
        GUILD_ID,
        name="[hidden]",
        price_minor=100,
        stock_on_hand=None,
        now=now,
        active=False,
    )
    await env.app.events.publish(PRODUCT_CHANGED, action="add", before=None, after=created, actor_id=STAFF_ID)
    await run_tasks(env)
    assert env.updates() == [] and await update_tasks(env) == []


def _product(**overrides: Any) -> catalog_repo.Product:
    values: dict[str, Any] = {
        "id": 1,
        "guild_id": GUILD_ID,
        "name": "[p]",
        "description": "",
        "price_minor": 100,
        "image_url": None,
        "stock_on_hand": 0,
        "reserved": 0,
        "max_per_order": None,
        "active": True,
        "archived": False,
        "sort_order": 0,
        "version": 1,
        "created_at": 0,
        "updated_at": 0,
    }
    values.update(overrides)
    return catalog_repo.Product(**values)


@pytest.mark.parametrize(
    ("action", "before", "after", "expected"),
    [
        ("add", None, {"stock_on_hand": 0}, "new"),
        ("add", None, {"active": False}, None),
        ("add", None, {"archived": True}, None),
        ("toggle", {"active": False}, {"active": True}, "new"),
        ("toggle", {"active": True}, {"active": False}, None),
        ("stock", {"stock_on_hand": 0}, {"stock_on_hand": 3}, "restock"),
        ("stock", {"stock_on_hand": 0}, {"stock_on_hand": None}, "restock"),
        ("stock", {"stock_on_hand": 2, "reserved": 2}, {"stock_on_hand": 4, "reserved": 2}, "restock"),
        ("stock", {"stock_on_hand": 1}, {"stock_on_hand": 3}, None),
        ("stock", {"stock_on_hand": None}, {"stock_on_hand": 3}, None),
        ("stock", {"stock_on_hand": 0}, {"stock_on_hand": 0}, None),
        ("stock", {"stock_on_hand": 0, "active": False}, {"stock_on_hand": 3, "active": False}, None),
        ("edit", {"stock_on_hand": 0}, {"stock_on_hand": 3}, "restock"),
        ("edit", {"stock_on_hand": 3}, {"stock_on_hand": 4}, None),
        ("price", {"stock_on_hand": 0}, {"stock_on_hand": 3}, None),
        ("image", {}, {}, None),
        ("sort", {}, {}, None),
        ("archive", {}, {"archived": True}, None),
        ("unknown", {}, {}, None),
    ],
)
def test_classification(
    env: NaEnv, action: str, before: dict[str, Any] | None, after: dict[str, Any], expected: str | None
) -> None:
    old = None if before is None else _product(**before)
    assert service.classify(env.app.settings, action, old, _product(version=2, **after)) == expected


@pytest.mark.parametrize(
    ("overrides", "new", "restock"),
    [
        ({"announce_new_products": False}, None, "restock"),
        ({"announce_restocks": False}, "new", None),
    ],
)
def test_update_kinds_can_be_switched_off(
    tmp_path: Path, overrides: dict[str, Any], new: str | None, restock: str | None
) -> None:
    from tests.new_arrivals.util import na_settings

    settings = na_settings({"new_arrivals": overrides})
    assert service.classify(settings, "add", None, _product()) == new
    assert service.classify(settings, "stock", _product(), _product(stock_on_hand=2)) == restock


async def test_switched_off_kind_scheduled_earlier_is_skipped(tmp_path: Path) -> None:
    env = await na_env(tmp_path)
    try:
        await add_product(env, stock="1")
        settings = env.app.settings
        env.app.settings = replace(settings, new_arrivals=replace(settings.new_arrivals, announce_new_products=False))
        env.app.recompute_readiness()
        assert env.app.ready("new_arrivals")
        await run_tasks(env)
        assert env.updates() == []
        assert [s for _, s in await update_tasks(env)] == ["done"]
    finally:
        await env.close()


# --------------------------------------------------------------------------------------------------
# Mentions
# --------------------------------------------------------------------------------------------------


async def test_no_mention_when_mentions_are_off(tmp_path: Path) -> None:
    env = await na_env(tmp_path, {"new_arrivals": {"mention_role": False}})
    try:
        product_id = await add_product(env)
        await run_tasks(env)
        update = only_update(env)
        assert update.content == ""
        mentions = update.allowed_mentions
        assert mentions is not None and mentions.roles is False and mentions.users is False
        assert not (await announcements(env, product_id))[0].mentioned
    finally:
        await env.close()


async def test_no_mention_when_the_role_does_not_exist(env: NaEnv) -> None:
    await env.role.delete()
    await add_product(env)
    await run_tasks(env)
    update = only_update(env)
    assert update.content == ""
    assert update.allowed_mentions is not None and update.allowed_mentions.roles is False


async def test_catalog_link_button_is_optional(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.BUTTON_VIEW_CATALOG: "", keys.NEW_PRODUCT_FOOTER: ""})
    try:
        await add_product(env)
        await run_tasks(env)
        update = only_update(env)
        assert buttons(update) == []
        # only the branding footer remains (the optional update footer is left out cleanly)
        assert update.embeds[0].footer.text == env.text("branding.footer_text")
    finally:
        await env.close()


# --------------------------------------------------------------------------------------------------
# Duplicates, restarts, failures
# --------------------------------------------------------------------------------------------------


async def test_same_event_is_scheduled_once_and_a_rerun_posts_nothing(env: NaEnv) -> None:
    product_id = await add_product(env)
    current = await product(env, product_id)
    # the same change reported again (e.g. a second subscriber or retry) does not create a second task
    assert await service.schedule_announcement(env.app, current, "new") is None
    await run_tasks(env)
    assert await service.announce(env.app, product_id=product_id, event="new", version=1) == "duplicate"
    await env.app.events.publish(PRODUCT_CHANGED, action="add", before=None, after=current, actor_id=STAFF_ID)
    await run_tasks(env)
    assert len(env.updates()) == 1
    assert len(await audit(env, "new_arrivals.announced")) == 1


async def test_task_interrupted_after_posting_does_not_post_again(env: NaEnv) -> None:
    """The process stopped after Discord posted the update but before it was recorded."""
    product_id = await add_product(env)
    current = await product(env, product_id)
    guild: Any = env.tenv.guild
    spec = service.build_announcement(env.app, guild, "new", current)
    from shopbot.ui.discord_render import send_spec

    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="new",
        version=1,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )
    posted = await send_spec(env.channel, spec)  # the earlier attempt's post
    await run_tasks(env)
    assert env.updates() == [posted]
    [record] = await announcements(env, product_id)
    assert record.status == "posted" and record.message_id == posted.id
    assert (await audit(env, "new_arrivals.announced"))[0].details["adopted"] is True


async def test_stale_claim_without_a_post_posts_once(env: NaEnv) -> None:
    product_id = await add_product(env)
    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="new",
        version=1,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )
    await run_tasks(env)
    assert len(env.updates()) == 1
    assert [a.status for a in await announcements(env, product_id)] == ["posted"]


async def test_stale_claim_never_adopts_an_already_recorded_update(env: NaEnv) -> None:
    product_id = await add_product(env, stock="0")
    await run_tasks(env)
    await set_stock(env, product_id, "set", 2)
    await run_tasks(env)  # restock posted and recorded
    await set_stock(env, product_id, "set", 0)
    await set_stock(env, product_id, "set", 2)
    restocked = await product(env, product_id)
    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="restock",
        version=restocked.version,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )
    await run_tasks(env)
    restocks = [a for a in await announcements(env, product_id) if a.event == "restock"]
    assert [a.status for a in restocks] == ["posted", "posted"]
    assert len({a.message_id for a in restocks}) == 2
    assert len(env.updates()) == 3


async def test_unreadable_history_waits_instead_of_risking_a_duplicate(env: NaEnv) -> None:
    product_id = await add_product(env)
    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="new",
        version=1,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )

    async def no_history(**kwargs: Any) -> Any:
        raise http_error(discord.Forbidden, 403, 50001, "Missing Access")
        yield  # pragma: no cover

    env.channel.history = no_history  # type: ignore[method-assign,assignment]
    await run_tasks(env)
    assert env.updates() == []
    [(_, status)] = await update_tasks(env)
    assert status == "pending"  # retried later with backoff


async def test_refused_post_is_retried_and_posted_once(env: NaEnv) -> None:
    product_id = await add_product(env)
    env.channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    await run_tasks(env)
    assert env.updates() == []
    assert await announcements(env, product_id) == []  # the claim was released: nothing was posted
    [(_, status)] = await update_tasks(env)
    assert status == "pending"
    env.channel.fail_send = None
    env.tenv.clock.advance(3600)
    await run_tasks(env)
    assert len(env.updates()) == 1
    [(_, status)] = await update_tasks(env)
    assert status == "done"


async def test_server_error_keeps_the_claim_and_checks_history_before_retrying(env: NaEnv) -> None:
    product_id = await add_product(env)
    env.channel.fail_send = http_error(discord.HTTPException, 503, 0, "unavailable")
    await run_tasks(env)
    [record] = await announcements(env, product_id)
    assert record.status == "claimed"  # Discord may have created it: the retry looks first
    env.channel.fail_send = None
    env.tenv.clock.advance(3600)
    await run_tasks(env)
    assert len(env.updates()) == 1
    assert [a.status for a in await announcements(env, product_id)] == ["posted"]


async def test_missing_channel_is_retried(env: NaEnv) -> None:
    await env.app.resources.forget("channel", "new_arrivals")
    await add_product(env)
    await run_tasks(env)
    [(_, status)] = await update_tasks(env)
    assert status == "pending"
    await env.app.resources.remember("channel", "new_arrivals", env.channel.id)
    env.tenv.clock.advance(3600)
    await run_tasks(env)
    assert len(env.updates()) == 1


async def test_update_over_discord_limits_fails_permanently(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.NEW_PRODUCT_TITLE: "x" * 200 + " {name}"})
    try:
        product_id = await add_product(env, "n" * 100)
        await run_tasks(env)
        assert env.updates() == []
        assert await announcements(env, product_id) == []
        [(_, status)] = await update_tasks(env)
        assert status == "failed"
        assert [e.action for e in await audit(env, "task.failed")] == ["task.failed"]
    finally:
        await env.close()


async def test_scheduled_update_survives_a_restart(env: NaEnv) -> None:
    await add_product(env)
    fresh = restarted_app(env.tenv)
    await fresh.resources.refresh()
    await fresh.scheduler.recover()
    assert await run_tasks(env, fresh) == 1
    assert len(env.updates()) == 1
    assert await run_tasks(env, fresh) == 0


async def test_product_gone_or_no_longer_listed_is_skipped(env: NaEnv) -> None:
    product_id = await add_product(env)
    await catalog_service.toggle_active(env.app, actor_id=STAFF_ID, product_id=product_id)
    await run_tasks(env)
    assert env.updates() == []
    assert await service.announce(env.app, product_id=12345, event="new", version=1) == "skipped"


async def test_invalid_task_payload_fails_permanently(env: NaEnv) -> None:
    for payload in ({}, {"product_id": 1, "event": "sale", "version": 1}, {"product_id": "1", "event": "new"}):
        task = task_repo.Task(
            id=1,
            kind=service.ANNOUNCE_TASK,
            run_at=0,
            payload=payload,
            status="running",
            attempts=1,
            max_attempts=5,
            dedupe_key=None,
            last_error=None,
        )
        with pytest.raises(PermanentTaskError):
            await tasks.run_announce_task(env.app, task)


# --------------------------------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------------------------------


async def test_not_ready_feature_schedules_and_posts_nothing(tmp_path: Path) -> None:
    env = await na_env(tmp_path, content={keys.PANEL_TITLE: ""})
    try:
        assert not env.app.ready("new_arrivals")
        await add_product(env)
        await run_tasks(env)
        assert env.updates() == [] and await update_tasks(env) == []
    finally:
        await env.close()


async def test_feature_not_ready_when_the_task_runs_posts_nothing(env: NaEnv) -> None:
    product_id = await add_product(env)
    env.app.content.replace(file_values={**env.tenv.content, keys.NEW_PRODUCT_TITLE: ""})
    env.app.recompute_readiness()
    assert not env.app.ready("new_arrivals")
    await run_tasks(env)
    assert env.updates() == [] and await announcements(env, product_id) == []
    [(_, status)] = await update_tasks(env)
    assert status == "done"


async def test_disabled_feature_ignores_catalog_changes(tmp_path: Path) -> None:
    env = await na_env(tmp_path, {"features": {"new_arrivals": False}})
    try:
        await add_product(env)
        await run_tasks(env)
        assert env.updates() == [] and await update_tasks(env) == []
    finally:
        await env.close()


async def test_scheduling_failure_never_breaks_the_catalog_change(env: NaEnv, monkeypatch: Any) -> None:
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(service, "schedule_announcement", broken)
    product_id = await add_product(env)  # asserts the catalog reply is a success
    assert (await product(env, product_id)).listed


async def test_low_stock_reads_the_same_as_on_the_catalog_card(tmp_path: Path) -> None:
    from shopbot.features.catalog import content_keys as catalog_keys

    na = await na_env(
        tmp_path,
        {"catalog": {"low_stock_threshold": 5}},
        content={catalog_keys.AVAILABILITY_LOW_STOCK: "[only {count} left]"},
    )
    try:
        product_id = await add_product(na, stock="0")
        await run_tasks(na)
        await set_stock(na, product_id, "set", 2)
        await run_tasks(na)
        restock = na.updates()[-1]
        values = [f.value for f in restock.embeds[0].fields]
        assert "[only 2 left]" in values
    finally:
        await na.close()
