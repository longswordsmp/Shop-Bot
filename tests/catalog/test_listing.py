"""Live listing: publishing, splitting, idempotent refresh, ordering, failures, coalescing, tasks, restarts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from shopbot.core.panels import upsert_message
from shopbot.db.repos import state
from shopbot.features import core_keys as ck
from shopbot.features.catalog import MODULE, repo, service, views
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog import tasks as catalog_tasks
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import ButtonSpec
from tests.catalog.util import (
    add_product,
    audit_actions,
    buttons,
    listing_channel,
    menu,
    restarted_app,
)
from tests.fakes import http_error
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env


def _escaped(name: str) -> str:
    return discord.utils.escape_markdown(name)


async def _records(tenv: TestEnv) -> list[state.PublishedMessage]:
    return await tenv.app.db.run(state.published_with_prefix, GUILD_ID, service.LISTING_PREFIX)


async def test_publish_posts_header_products_and_browse_button(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    limited = await add_product(tenv, "[limited*]", price=1250, stock=12, reserved=2, description="[d1]")
    unlimited = await add_product(tenv, "[unlimited]", price=500)
    sold_out = await add_product(tenv, "[sold out]", price=99, stock=3, reserved=3, max_per_order=2)
    hidden = await add_product(tenv, "[hidden]", active=False)

    lines = await MODULE.publish(tenv.app)  # type: ignore[misc]
    assert len(lines) == 1 and "2 message(s)" in lines[0] and "3 product(s)" in lines[0]

    assert len(channel.messages) == 2
    head, message = channel.messages
    (header,) = head.embeds
    assert header.title == tenv.text(keys.LISTING_TITLE)
    # No category yet: no menu (it would only offer All, the cards below) and no line about it.
    assert header.description == tenv.text(keys.LISTING_DESCRIPTION)
    assert menu(head) == []
    assert buttons(head) == [("sb:ct:browse:0", tenv.text(keys.BUTTON_BROWSE), False)]
    cards = message.embeds
    assert [c.title for c in cards] == [_escaped(p.name) for p in (limited, unlimited, sold_out)]
    assert hidden.name not in message.text() and hidden.name not in head.text()
    assert cards[0].description == "[d1]"
    assert cards[1].description is None
    money = tenv.text(ck.MONEY_FORMAT)
    price_field, availability_field = cards[0].fields
    assert price_field.name == tenv.text(keys.FIELD_PRICE)
    assert price_field.value == money.replace("{amount}", "12.50").replace("{currency}", "USD")
    assert availability_field.name == tenv.text(keys.FIELD_AVAILABILITY)
    assert availability_field.value == tenv.text(keys.AVAILABILITY_IN_STOCK).replace("{count}", "10")
    assert cards[1].fields[1].value == tenv.text(keys.AVAILABILITY_UNLIMITED)
    assert cards[2].fields[1].value == tenv.text(keys.AVAILABILITY_OUT_OF_STOCK)
    assert cards[2].fields[2].name == tenv.text(keys.FIELD_MAX_PER_ORDER)
    assert cards[2].fields[2].value == "2"
    buy = tenv.text(keys.BUTTON_BUY)
    assert buttons(message) == [
        (f"sb:co:buy:{limited.id}", buy.replace("{name}", limited.name), False),
        (f"sb:co:buy:{unlimited.id}", buy.replace("{name}", unlimited.name), False),
        (f"sb:co:buy:{sold_out.id}", buy.replace("{name}", sold_out.name), True),  # out of stock: disabled
    ]
    colors = tenv.app.settings.branding.colors
    assert [c.color.value if c.color else None for c in cards] == [
        colors["success"],
        colors["success"],
        colors["danger"],
    ]
    assert message.files == [] and head.files == []
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False


async def test_listing_splits_by_product_count_and_moves_button_to_last_message(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 2}}))
    try:
        channel = await listing_channel(tenv)
        products = [await add_product(tenv, f"[p{i}]") for i in range(5)]
        await service.request_listing_refresh(tenv.app)
        assert [len(m.embeds) for m in channel.messages] == [1, 2, 2, 1]  # header, then 2, 2, 1 products
        titles = [e.title for m in channel.messages for e in m.embeds][1:]
        assert titles == [p.name for p in products]
        assert [len(buttons(m)) for m in channel.messages] == [1, 2, 2, 1]  # browse; one buy button per product
        assert [b[0] for m in channel.messages[1:] for b in buttons(m)] == [f"sb:co:buy:{p.id}" for p in products]
        assert [r.key for r in await _records(tenv)] == [service.listing_key(i) for i in range(4)]
    finally:
        await tenv.close()


async def test_listing_splits_at_ten_embeds_and_6000_characters(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 10}}))
    try:
        channel = await listing_channel(tenv)
        for i in range(12):
            await add_product(tenv, f"[p{i}]")
        await service.request_listing_refresh(tenv.app)
        assert [len(m.embeds) for m in channel.messages] == [1, 10, 2]  # header, 10 (embed cap), then 2
        # header: the browse button (no category, no menu); then 2 rows of 5 buy buttons, then 2
        assert [len(m.view.children) if m.view else 0 for m in channel.messages] == [1, 10, 2]
        for message in channel.messages:
            assert len(message.embeds) <= 10

        long_text = "x" * 3500
        for i in range(3):
            await add_product(tenv, f"[long{i}]", description=long_text)
        await service.request_listing_refresh(tenv.app)
        for message in channel.messages:
            total = sum(len(e) for e in message.embeds)
            assert total <= 6000
        descriptions = [e.description for m in channel.messages for e in m.embeds if e.description == long_text]
        assert len(descriptions) == 3  # nothing truncated
    finally:
        await tenv.close()


async def test_refresh_is_idempotent_and_removes_extra_messages(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 1}}))
    try:
        channel = await listing_channel(tenv)
        products = [await add_product(tenv, f"[p{i}]") for i in range(3)]
        await service.request_listing_refresh(tenv.app)
        first_ids = [m.id for m in channel.messages]
        assert len(first_ids) == 4  # header + one message per product

        report = await service.request_listing_refresh(tenv.app)
        assert report.ok and "0 changed, 4 unchanged, 0 removed" in report.lines[0]
        assert [m.id for m in channel.messages] == first_ids
        assert all(not m.edits for m in channel.messages)

        # Two products go away: the surplus listing messages are deleted and forgotten.
        for product in products[1:]:
            await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=2, active=False)
        report = await service.request_listing_refresh(tenv.app)
        assert "2 removed" in report.lines[0]
        assert [m.id for m in channel.messages] == first_ids[:2]
        assert [r.key for r in await _records(tenv)] == [service.listing_key(0), service.listing_key(1)]
        assert buttons(channel.messages[0])[0][0] == "sb:ct:browse:0"  # the browse button stays on the header
    finally:
        await tenv.close()


async def test_stock_change_edits_in_place(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, stock=5)
    await service.request_listing_refresh(tenv.app)
    head, message = channel.messages
    assert buttons(message)[0][2] is False
    await tenv.app.db.transaction(repo.reserve, product.id, 5, 3)
    await service.request_listing_refresh(tenv.app)
    assert channel.messages == [head, message] and len(message.edits) == 1 and head.edits == []
    assert message.embeds[0].fields[1].value == tenv.text(keys.AVAILABILITY_OUT_OF_STOCK)
    assert buttons(message)[0][2] is True  # sold out: the buy button is disabled
    danger = tenv.app.settings.branding.colors["danger"]
    assert message.embeds[0].color is not None and message.embeds[0].color.value == danger


async def test_empty_catalog_shows_the_header_with_the_empty_text_and_no_button(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await service.request_listing_refresh(tenv.app)
    message = channel.messages[0]
    assert [e.description for e in message.embeds] == [  # one card: the empty text is its last paragraph
        f"{tenv.text(keys.LISTING_DESCRIPTION)}\n\n{tenv.text(keys.LISTING_EMPTY)}"
    ]
    assert buttons(message) == []


async def test_deleted_middle_message_is_reposted_in_order(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 1}}))
    try:
        channel = await listing_channel(tenv)
        for i in range(3):
            await add_product(tenv, f"[p{i}]")
        await service.request_listing_refresh(tenv.app)
        await channel.messages[2].delete()
        await service.request_listing_refresh(tenv.app)
        assert [m.embeds[-1].title for m in channel.messages[1:]] == ["[p0]", "[p1]", "[p2]"]
        assert channel.messages[0].embeds[0].title == tenv.text(keys.LISTING_TITLE)
        records = await _records(tenv)
        assert [r.message_id for r in records] == [m.id for m in channel.messages]
    finally:
        await tenv.close()


async def test_browse_disabled_listing_has_no_button(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"browse_enabled": False}}))
    try:
        channel = await listing_channel(tenv)
        await add_product(tenv)
        await service.request_listing_refresh(tenv.app)
        assert buttons(channel.messages[0]) == []
    finally:
        await tenv.close()


async def test_disabling_the_listing_channel_removes_old_messages(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    try:
        channel = await listing_channel(tenv)
        await add_product(tenv)
        await service.request_listing_refresh(tenv.app)
        assert len(channel.messages) == 2
        tenv.app.settings = make_settings({"catalog": {"listing_channel": ""}})
        tenv.app.recompute_readiness()
        report = await service.request_listing_refresh(tenv.app)
        assert report.ok and "2 old listing" in report.lines[0]
        assert channel.messages == [] and await _records(tenv) == []
    finally:
        await tenv.close()


async def test_missing_channel_is_reported_and_audited_once(tenv: TestEnv) -> None:
    await add_product(tenv)
    first = await service.request_listing_refresh(tenv.app)
    second = await service.request_listing_refresh(tenv.app)
    for report in (first, second):
        assert not report.ok and report.lines == (tenv.app.op("catalog.refresh.channel_missing", channel="catalog"),)
    events = [e for e in await audit_actions(tenv) if e.action == "catalog.listing_refresh_failed"]
    assert len(events) == 1 and events[0].details == {"reason": "channel_missing"}


async def test_forbidden_and_missing_permissions_are_reported_not_raised(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    channel.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok and not report.retry
    assert report.lines[0] == tenv.app.op("catalog.refresh.forbidden", channel=channel.mention)
    assert channel.messages == []

    channel.fail_send = http_error(discord.HTTPException, 500, 0, "Server Error")
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok and report.retry

    channel.fail_send = None
    await channel.set_permissions(tenv.guild.bot_role, send_messages=False)
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok and "send_messages" in report.lines[0]
    assert channel.messages == []
    reasons = [e.details["reason"] for e in await audit_actions(tenv) if e.action == "catalog.listing_refresh_failed"]
    assert reasons == ["forbidden", "http_500", "missing_permissions"]


async def test_not_ready_refresh_sends_nothing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    values = dict(tenv.content)
    del values[keys.LISTING_TITLE]
    tenv.app.content.replace(file_values=values)
    tenv.app.recompute_readiness()
    assert not tenv.app.ready("catalog")
    report = await service.request_listing_refresh(tenv.app)
    assert report.lines == (tenv.app.op("catalog.refresh.not_ready"),)
    assert channel.messages == []


async def test_runtime_missing_content_sends_nothing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    values = dict(tenv.content)
    del values[keys.AVAILABILITY_UNLIMITED]  # readiness deliberately not recomputed (stale)
    tenv.app.content.replace(file_values=values)
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok
    assert report.lines == (tenv.app.op("catalog.refresh.missing_content", content_key=keys.AVAILABILITY_UNLIMITED),)
    assert channel.messages == []


async def test_oversized_listing_is_refused_before_anything_changes(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv, "[p]")
    await service.request_listing_refresh(tenv.app)
    before = [m.embeds for m in channel.messages]
    values = dict(tenv.content)
    values[ck.FOOTER_TEXT] = "f" * 2048  # repeated on every card: one card alone exceeds 6000 characters
    values[keys.LISTING_DESCRIPTION] = "d" * 4000
    tenv.app.content.replace(file_values=values)
    await add_product(tenv, "[q]", description="x" * 4000)
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok and "exceeds Discord limits" in report.lines[0]
    assert [m.embeds for m in channel.messages] == before


async def test_concurrent_refresh_requests_are_serialized_and_coalesced(tenv: TestEnv, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await listing_channel(tenv)
    await add_product(tenv)
    active = 0
    peak = 0
    runs = 0
    original = service._refresh_listing

    async def tracked(app, st):  # type: ignore[no-untyped-def]
        nonlocal active, peak, runs
        active += 1
        runs += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        try:
            return await original(app, st)
        finally:
            active -= 1

    monkeypatch.setattr(service, "_refresh_listing", tracked)
    reports = await asyncio.gather(*(service.request_listing_refresh(tenv.app) for _ in range(10)))
    assert peak == 1  # never two refreshes at once
    assert runs <= 3  # a burst of 10 requests is coalesced
    assert all(r.ok for r in reports)


async def test_scheduled_refresh_task_is_deduplicated_and_runs(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, stock=2)
    for _ in range(5):
        await service.schedule_listing_refresh(tenv.app)
    assert await tenv.app.scheduler.run_due() == 1
    assert len(channel.messages) == 2
    # A later stock change re-arms the same task row.
    await tenv.app.db.transaction(repo.reserve, product.id, 2, 5)
    await service.schedule_listing_refresh(tenv.app)
    assert await tenv.app.scheduler.run_due() == 1
    assert channel.messages[1].embeds[0].fields[1].value == tenv.text(keys.AVAILABILITY_OUT_OF_STOCK)
    assert await tenv.app.scheduler.run_due() == 0


async def test_scheduled_refresh_retries_transient_failures(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    channel.fail_send = http_error(discord.HTTPException, 503, 0, "Unavailable")
    await service.schedule_listing_refresh(tenv.app)
    assert await tenv.app.scheduler.run_due() == 1
    assert channel.messages == []
    channel.fail_send = None
    tenv.clock.advance(3600)
    assert await tenv.app.scheduler.run_due() == 1
    assert len(channel.messages) == 2


async def test_change_while_task_running_is_picked_up(tenv: TestEnv, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    channel = await listing_channel(tenv)
    product = await add_product(tenv, stock=1)
    await service.schedule_listing_refresh(tenv.app)
    original = service._refresh_listing
    changed = False

    async def change_during_refresh(app, st):  # type: ignore[no-untyped-def]
        nonlocal changed
        report = await original(app, st)
        if not changed:  # a reservation commits while the task is still running
            changed = True
            await app.db.transaction(repo.reserve, product.id, 1, 9)
            await service.schedule_listing_refresh(app)
        return report

    monkeypatch.setattr(service, "_refresh_listing", change_during_refresh)
    await tenv.app.scheduler.run_due()
    await tenv.app.scheduler.run_due()
    assert channel.messages[1].embeds[0].fields[1].value == tenv.text(keys.AVAILABILITY_OUT_OF_STOCK)


async def test_on_ready_verifies_existing_listing_without_duplicates(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv, "[a]")
    await service.request_listing_refresh(tenv.app)
    original_ids = [m.id for m in channel.messages]
    assert len(original_ids) == 2

    app = restarted_app(tenv)
    await app.resources.refresh()
    await MODULE.on_ready(app)  # type: ignore[misc]
    assert await app.scheduler.run_due() == 1
    assert [m.id for m in channel.messages] == original_ids

    await channel.messages[1].delete()  # deleted while the bot was offline
    app2 = restarted_app(tenv)
    await app2.resources.refresh()
    await MODULE.on_ready(app2)  # type: ignore[misc]
    await app2.scheduler.run_due()
    assert len(channel.messages) == 2 and channel.messages[1].id != original_ids[1]
    assert [r.message_id for r in await _records(tenv)] == [m.id for m in channel.messages]


async def test_on_ready_does_nothing_when_never_published(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    await MODULE.on_ready(tenv.app)  # type: ignore[misc]
    assert await tenv.app.scheduler.run_due() == 0
    assert channel.messages == []


async def test_deleted_listing_message_event_triggers_repost(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    await service.request_listing_refresh(tenv.app)
    message = channel.messages[1]
    await message.delete()
    other = SimpleNamespace(message_id=123, guild_id=GUILD_ID)
    await catalog_tasks.on_raw_message_delete(tenv.app, other)  # type: ignore[arg-type]
    assert await tenv.app.scheduler.run_due() == 0
    await tenv.bot.emit("raw_message_delete", SimpleNamespace(message_id=message.id, guild_id=GUILD_ID))
    assert await tenv.app.scheduler.run_due() == 1
    assert len(channel.messages) == 2 and channel.messages[1].id != message.id


async def test_channel_change_moves_the_listing(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    await add_product(tenv)
    await service.request_listing_refresh(tenv.app)
    other = tenv.app.resources.text_channel(tenv.guild, "shop")  # type: ignore[arg-type]
    assert other is not None
    tenv.app.settings = make_settings({"catalog": {"listing_channel": "shop"}})
    tenv.app.recompute_readiness()
    await service.request_listing_refresh(tenv.app)
    assert channel.messages == [] and len(other.messages) == 2  # type: ignore[attr-defined]


async def test_listing_messages_respect_limits(tenv: TestEnv) -> None:
    products = [await add_product(tenv, "*" * 100, description="_" * 4000, image_url="https://example.com/i.png")]
    products += [await add_product(tenv, f"[p{i}]") for i in range(20)]
    specs = views.listing_messages(tenv.app.ui, tenv.app.settings, products)
    for spec in specs:
        assert validate_message(spec) == []
    assert specs[1].embeds[0].image_url == "https://example.com/i.png"
    assert specs[1].embeds[0].title == _escaped("*" * 100)
    # "[tN] {name}" with a 100-character name is over 80 characters: the short label is used, never a cut one
    first_button = specs[1].rows[0][0]
    assert isinstance(first_button, ButtonSpec) and first_button.label == tenv.text(keys.BUTTON_BUY_SHORT)


async def test_upsert_helper_contract_unchanged_message_not_edited(tenv: TestEnv) -> None:
    # Guard against regressions in the shared helper the listing relies on.
    channel = await listing_channel(tenv)
    await add_product(tenv)
    await service.request_listing_refresh(tenv.app)
    spec_message = channel.messages[0]
    products = await tenv.app.db.run(repo.list_products, GUILD_ID)
    spec = views.listing_messages(tenv.app.ui, tenv.app.settings, products)[0]
    _, action = await upsert_message(tenv.app, service.listing_key(0), channel, spec)
    assert action == "unchanged" and spec_message.edits == []
