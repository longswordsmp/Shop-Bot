"""The start button: private order channel, workflow message, existing sessions, limits, cooldown, failures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service, views
from shopbot.features.tickets import service as tickets_service
from tests.checkout.util import (
    add_product,
    audit_actions,
    channel,
    error_text,
    make_env,
    place_order,
    press,
    reload_session,
    render,
    session_of,
    start,
    task,
    ticket_status,
)
from tests.fakes import FakeTextChannel, click, http_error
from tests.helpers import TestEnv


async def test_start_creates_private_channel_and_workflow(co: TestEnv) -> None:
    customer = co.member()
    await add_product(co, "[p1]")
    interaction, session, ch, message = await start(co, customer)
    assert session.status == m.ACTIVE and session.step == m.BROWSE and session.user_id == customer.id
    assert session.message_id == message.id
    assert interaction.response_kinds() == ["defer"]
    assert error_text(interaction) == render(co, k.STARTED, channel_mention=f"<#{ch.id}>")
    assert interaction.last_sent is not None and interaction.last_sent.flags.ephemeral
    # private channel in the orders category, named with the configured prefix
    orders_category = co.app.resources.category(co.guild, "orders")  # type: ignore[arg-type]
    assert orders_category is not None and ch.category_id == orders_category.id
    assert ch.name.startswith(co.text(k.CHANNEL_PREFIX))
    assert ch.topic == render(co, k.CHANNEL_TOPIC, user_name=customer.display_name)
    assert ch.permissions_for(customer).view_channel
    assert ch.permissions_for(co.member(name="staffer", staff=True)).view_channel
    assert not ch.permissions_for(co.member(name="other")).view_channel
    # the workflow message: browse step with exact configured text
    assert message.embeds[0].title == co.text(k.BROWSE_TITLE)
    assert message.embeds[0].description == co.text(k.BROWSE_DESCRIPTION)
    assert views.pick_id(session.id, 0) in message.custom_ids()
    ticket = await tickets_service.get_ticket(co.app, session.ticket_id)
    assert ticket is not None and ticket.status == "open" and ticket.kind == "order"
    assert ticket.control_message_id == message.id
    assert "checkout.session_started" in await audit_actions(co)


async def test_second_start_reuses_the_existing_session(co: TestEnv) -> None:
    customer = co.member()
    _, session, ch, _ = await start(co, customer)
    co.clock.advance(60)
    again = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(again) == render(co, k.SESSION_EXISTS, channel_mention=f"<#{ch.id}>")
    assert (await session_of(co, customer)).id == session.id
    assert len([c for c in co.guild.text_channels if c.category_id == ch.category_id]) == 1


async def test_session_whose_channel_vanished_is_replaced(co: TestEnv) -> None:
    customer = co.member()
    _, session, ch, _ = await start(co, customer)
    del co.guild._channels[ch.id]  # deleted while the bot did not see it
    co.clock.advance(60)
    again = await click(co.app, user=customer, custom_id=views.start_id())
    replacement = await session_of(co, customer)
    assert replacement.id != session.id
    assert (await reload_session(co, session.id)).status == m.SESSION_EXPIRED
    assert error_text(again) is not None and error_text(again) != co.text(k.SESSION_EXISTS)


async def test_open_order_limit(co: TestEnv) -> None:
    customer = co.member()
    product = await add_product(co, "[p1]")
    placed, ch, _ = await place_order(co, customer, [(product.id, 1)])
    co.clock.advance(60)
    blocked = await click(co.app, user=customer, custom_id=views.start_id())
    # The open order's ticket is linked privately instead of creating a second one.
    assert error_text(blocked) == render(
        co, k.ORDER_EXISTS, channel_mention=f"<#{ch.id}>", order_number=str(placed.number), limit=1
    )


async def test_limit_counts_only_open_orders(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"max_open_orders_per_user": 2}})
    try:
        customer = tenv.member()
        product = await add_product(tenv, "[p1]")
        await place_order(tenv, customer, [(product.id, 1)])
        tenv.clock.advance(60)
        newest, ch, _ = await place_order(tenv, customer, [(product.id, 1)])
        tenv.clock.advance(60)
        blocked = await click(tenv.app, user=customer, custom_id=views.start_id())
        assert error_text(blocked) == render(
            tenv, k.ORDER_EXISTS, channel_mention=f"<#{ch.id}>", order_number=str(newest.number), limit=2
        )
    finally:
        await tenv.close()


async def test_start_cooldown(co: TestEnv) -> None:
    customer = co.member()
    _, session, _, message = await start(co, customer)
    await press(co, customer, message, views.cancel_id(session.id))
    await press(co, customer, message, views.cancel_yes_id(session.id))
    assert (await reload_session(co, session.id)).status == m.SESSION_CANCELLED
    blocked = await click(co.app, user=customer, custom_id=views.start_id())
    retry = service.START_COOLDOWN_SECONDS
    assert error_text(blocked) == render(co, core.ERROR_COOLDOWN, retry_after=retry)
    co.clock.advance(retry)
    allowed = await click(co.app, user=customer, custom_id=views.start_id())
    assert (await session_of(co, customer)).id != session.id
    assert error_text(allowed) is not None and "<#" in (error_text(allowed) or "")


async def test_channel_creation_failure(co: TestEnv) -> None:
    customer = co.member()
    co.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    failed = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(failed) == co.text(core.ERROR_GENERIC)
    sessions = await co.app.db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM checkout_sessions").fetchone()[0])
    assert sessions == 0
    tickets = await co.app.db.run(lambda conn: [r[0] for r in conn.execute("SELECT status FROM tickets")])
    assert tickets == ["failed"]


async def test_workflow_message_failure_rolls_back(co: TestEnv, monkeypatch: Any) -> None:
    customer = co.member()
    original = co.guild.create_text_channel

    async def broken_channel(*args: Any, **kwargs: Any) -> FakeTextChannel:
        created = await original(*args, **kwargs)
        created.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
        return created

    monkeypatch.setattr(co.guild, "create_text_channel", broken_channel)
    failed = await click(co.app, user=customer, custom_id=views.start_id())
    assert error_text(failed) == co.text(core.ERROR_GENERIC)
    session = await session_of(co, customer)
    assert session.status == m.SESSION_CANCELLED
    assert await ticket_status(co, session.ticket_id) == "failed"
    assert [c for c in co.guild.text_channels if c.name.startswith(co.text(k.CHANNEL_PREFIX))] == []


async def test_not_ready_and_missing_content(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.BROWSE_TITLE: ""})
    try:
        assert not tenv.app.ready("checkout")
        denied = await click(tenv.app, user=tenv.member(), custom_id=views.start_id())
        assert error_text(denied) == tenv.text(core.ERROR_UNAVAILABLE)
        assert [c for c in tenv.guild.audit if c[0] == "create_text_channel"] == []
    finally:
        await tenv.close()
    tenv = await make_env(tmp_path / "b")
    try:
        # Content removed at runtime (after readiness): nothing is created, the unavailable message answers.
        values = dict(tenv.content)
        values[k.BROWSE_TITLE] = ""
        tenv.app.content.replace(file_values=values)
        denied = await click(tenv.app, user=tenv.member(), custom_id=views.start_id())
        assert error_text(denied) == tenv.text(core.ERROR_UNAVAILABLE)
        assert [c for c in tenv.guild.audit if c[0] == "create_text_channel"] == []
    finally:
        await tenv.close()


async def test_nothing_is_answered_when_even_unavailable_text_is_missing(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, content={k.BROWSE_TITLE: "", core.ERROR_UNAVAILABLE: ""})
    try:
        silent = await click(tenv.app, user=tenv.member(), custom_id=views.start_id())
        assert silent.sent == [] and silent.response_kinds() == ["defer"]
    finally:
        await tenv.close()


async def test_session_expiry_task_is_scheduled(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"checkout": {"session_expiry_minutes": 30}})
    try:
        _, session, _, _ = await start(tenv, tenv.member())
        scheduled = await task(tenv, service.session_task_key(session.id))
        assert scheduled is not None and scheduled.run_at == session.updated_at + 30 * 60
    finally:
        await tenv.close()


async def test_foreign_guild_and_dm_are_ignored(co: TestEnv) -> None:
    from tests.fakes import FakeGuild

    other = FakeGuild(id=999)
    member = other.add_member(name="x")
    foreign = await click(co.app, user=member, custom_id=views.start_id(), guild=other)
    assert foreign.sent == []
    dm = await click(co.app, user=co.member(), custom_id=views.start_id(), dm=True)
    assert dm.sent == []
    assert channel(co, "shop").messages == []
