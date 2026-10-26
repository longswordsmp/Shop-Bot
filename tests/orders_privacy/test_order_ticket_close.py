"""Authorized manual closure of order tickets (specification section 4, "Closing Tickets"): ``/ticket close`` in an
order ticket whose order was never submitted cancels the unfinished order through the normal durable path and closes
the ticket; a submitted open order is refused (staff end it from #orders)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as core
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.tickets import commands as ticket_commands
from shopbot.features.tickets import service as tickets_service
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec
from tests.checkout.util import (
    add_product,
    channel,
    error_text,
    order,
    paragraphs,
    place_order,
    press,
    reload_session,
    restarted_app,
    set_qty,
    staff_message,
    start,
    submit,
    to_review,
)
from tests.fakes import FakeMember, FakeTextChannel, click, http_error
from tests.helpers import GUILD_ID, TestEnv, server_extension
from tests.orders_privacy.util import (
    TRANSCRIPTS,
    audit_actions,
    audit_details,
    check_task_status,
    env,
    number,
    run_tasks,
    ticket_of,
)
from tests.tickets.util import run_command


@pytest.fixture
async def op(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await env(tmp_path, TRANSCRIPTS)
    try:
        yield tenv
    finally:
        await tenv.close()


def _reply(interaction: Any) -> discord.Embed:
    assert interaction.last_sent is not None and interaction.last_sent.ephemeral
    return interaction.last_sent.embeds[0]


async def _abandoned_cart(tenv: TestEnv, customer: FakeMember) -> tuple[m.Session, FakeTextChannel, Any]:
    """A customer's order ticket with one product in the cart, never submitted."""
    product = await add_product(tenv, "[sample product 1]", price=1000, stock=5)
    _, session, ch, message = await start(tenv, customer)
    await set_qty(tenv, customer, message, session.id, product.id, 2)
    return session, ch, message


async def _close(tenv: TestEnv, user: FakeMember, ch: FakeTextChannel, **kwargs: Any) -> Any:
    return await run_command(tenv, "close", user=user, channel=ch, **kwargs)


# -- happy path ---------------------------------------------------------------------------------------------------


async def test_staff_close_cancels_the_unsubmitted_order_and_closes_the_ticket(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, message = await _abandoned_cart(tenv, customer)
    ticket = await ticket_of(tenv, session.ticket_id)
    assert ch.permissions_for(customer).send_messages

    reply = await _close(tenv, staffer, ch, reason="[sample reason]")

    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket))
    assert (await reload_session(tenv, session.id)).status == m.SESSION_CANCELLED
    closed = await ticket_of(tenv, ticket.id)
    assert (closed.status, closed.closed_by, closed.close_reason) == ("closed", staffer.id, m.SESSION_CANCELLED)
    # The live panel shows the staff-closed text under the ticket's reference line, without any control.
    reference = views.reference_line(tenv.app.ui, views.ticket_reference(tenv.app.settings, ticket.number))
    assert paragraphs(message) == [reference, tenv.text(k.STAFF_CLOSED_DESCRIPTION)]
    assert message.embeds[0].title == tenv.text(k.STAFF_CLOSED_TITLE)
    assert message.embeds[0].fields == []
    assert message.custom_ids() == []
    # The channel is locked for the customer (still readable), nothing was ordered or reserved.
    assert ch.permissions_for(customer).view_channel and not ch.permissions_for(customer).send_messages
    assert await tenv.app.db.run(repo.list_orders, GUILD_ID, limit=10, offset=0) == []
    products = await tenv.app.db.run(catalog_repo.list_products, GUILD_ID)
    assert [(p.stock_on_hand, p.reserved) for p in products] == [(5, 0)]
    # Audited (with the staff member's reason), the durable channel check is no longer needed.
    actions = await audit_actions(tenv)
    assert actions.count("checkout.session_closed_by_staff") == 1 and actions.count("tickets.closed") == 1
    assert "checkout.session_cancelled" not in actions
    (details,) = await audit_details(tenv, "checkout.session_closed_by_staff")
    assert details == {"ticket_id": ticket.id, "reason": "[sample reason]"}
    assert await check_task_status(tenv, ticket.id) == "cancelled"
    # One transcript with the final status.
    await run_tasks(tenv)
    transcripts = channel(tenv, "transcripts")
    assert len(transcripts.messages) == 1
    assert (await ticket_of(tenv, ticket.id)).transcript_status == "done"


async def test_the_staff_closed_panel_falls_back_to_the_cancelled_text(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.STAFF_CLOSED_TITLE: "", k.STAFF_CLOSED_DESCRIPTION: ""})
    try:
        assert tenv.app.ready(k.FEATURE)  # the staff-closed text is optional
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        _, ch, message = await _abandoned_cart(tenv, customer)
        await _close(tenv, staffer, ch)
        assert paragraphs(message)[1:] == [tenv.text(k.CANCELLED_DESCRIPTION)]
        assert message.embeds[0].title == tenv.text(k.CANCELLED_TITLE)
    finally:
        await tenv.close()


async def test_closing_works_without_a_reason_and_an_empty_cart(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    _, session, ch, _ = await start(tenv, customer)
    reply = await _close(tenv, staffer, ch)
    ticket = await ticket_of(tenv, session.ticket_id)
    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket))
    assert ticket.status == "closed"
    (details,) = await audit_details(tenv, "checkout.session_closed_by_staff")
    assert details["reason"] is None


async def test_old_panel_controls_are_refused_after_the_closure_also_after_a_restart(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, message = await _abandoned_cart(tenv, customer)
    submit_id = views.submit_id(session.id, 2000, 2000)
    await _close(tenv, staffer, ch)
    late = await press(tenv, customer, message, views.go_id(session.id, m.CART))
    assert error_text(late) == tenv.text(k.ERROR_SESSION_CLOSED)
    app = restarted_app(tenv)
    for custom_id in (submit_id, views.cancel_yes_id(session.id)):
        refused = await click(app, user=customer, custom_id=custom_id, message=message)
        assert error_text(refused) == tenv.text(k.ERROR_SESSION_CLOSED)
    assert await tenv.app.db.run(repo.list_orders, GUILD_ID, limit=10, offset=0) == []


async def test_a_ticket_whose_order_ended_without_closing_is_closed(op: TestEnv) -> None:
    """The session ended but the channel stayed open (the process stopped before closing it): staff close it."""
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, _ = await _abandoned_cart(tenv, customer)
    await tenv.app.db.transaction(repo.end_session, session.id, m.SESSION_EXPIRED, tenv.clock.now())
    ticket = await ticket_of(tenv, session.ticket_id)
    assert ticket.status == "open"
    reply = await _close(tenv, staffer, ch)
    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_closed", number=number(tenv, ticket))
    closed = await ticket_of(tenv, ticket.id)
    assert (closed.status, closed.close_reason, closed.closed_by) == ("closed", m.SESSION_EXPIRED, staffer.id)
    assert "checkout.session_closed_by_staff" not in await audit_actions(tenv)
    (details,) = await audit_details(tenv, "checkout.ticket_closed_by_staff")
    assert details == {"number": ticket.number, "close_reason": m.SESSION_EXPIRED, "reason": None}


async def test_an_order_channel_without_any_session_is_closed(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    created = await tickets_service.create_private_channel(
        tenv.app,
        member=customer,
        kind="order",
        category_id=service.TICKET_CATEGORY,
        discord_category_key=tenv.app.settings.checkout.order_category,
        channel_prefix_key=k.CHANNEL_PREFIX,
        extra_staff_role_keys=(),
        topic=None,
        limit=1,
    )
    await tickets_service.activate_ticket(tenv.app, created.ticket.id, control_message_id=None)
    reply = await _close(tenv, staffer, created.channel)
    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_closed", number=number(tenv, created.ticket))
    closed = await ticket_of(tenv, created.ticket.id)
    assert (closed.status, closed.close_reason) == ("closed", repo.ORPHAN)


# -- refused: a submitted order that is still open -----------------------------------------------------------------


@pytest.mark.parametrize("paid", [False, True])
async def test_a_submitted_open_order_is_refused(op: TestEnv, paid: bool) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    product = await add_product(tenv, "[sample product 1]", price=1000, stock=5)
    placed, ch, message = await place_order(tenv, customer, [(product.id, 1)])
    if paid:
        await service.change_status(tenv.app, placed.id, m.PAID, actor_id=staffer.id)
    current = await order(tenv, placed.id)
    ticket = await ticket_of(tenv, placed.ticket_id or 0)
    record = staff_message(tenv, current)

    reply = await _close(tenv, staffer, ch)

    embed = _reply(reply)
    assert embed.title == tenv.app.op(
        "checkout.op.ticket_order_open", number=number(tenv, ticket), order=placed.number, status=current.status
    )
    assert embed.description == tenv.app.op(
        "checkout.op.ticket_order_open_hint",
        record=f"https://discord.com/channels/{GUILD_ID}/{current.staff_channel_id}/{record.id}",
    )
    assert (await ticket_of(tenv, ticket.id)).status == "open"
    assert (await order(tenv, placed.id)).status == current.status
    actions = await audit_actions(tenv)
    assert "tickets.closed" not in actions and "checkout.session_closed_by_staff" not in actions
    if not paid:
        assert message.custom_ids()  # the customer's panel keeps its cancel control


async def test_the_order_is_refused_when_the_customer_submits_at_the_same_moment(
    op: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale reads: the closure saw no open order and an active session, but the customer had just submitted."""
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    product = await add_product(tenv, "[sample product 1]", price=1000)
    _, session, ch, message = await start(tenv, customer)
    stale_session = await reload_session(tenv, session.id)
    await to_review(tenv, customer, message, session.id, [(product.id, 1)])
    await submit(tenv, customer, message, session.id)
    placed = (await tenv.app.db.run(repo.list_orders, GUILD_ID, limit=10, offset=0))[0]
    real_open_order = repo.open_order_for_ticket
    calls = {"n": 0}

    def stale_open_order(conn: Any, ticket_id: int) -> m.Order | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else real_open_order(conn, ticket_id)

    monkeypatch.setattr(repo, "open_order_for_ticket", stale_open_order)
    monkeypatch.setattr(repo, "active_session_for_ticket", lambda conn, ticket_id: stale_session)

    reply = await _close(tenv, staffer, ch)

    ticket = await ticket_of(tenv, session.ticket_id)
    current = await order(tenv, placed.id)
    assert _reply(reply).title == tenv.app.op(
        "checkout.op.ticket_order_open", number=number(tenv, ticket), order=placed.number, status=m.PENDING
    )
    assert _reply(reply).description == tenv.app.op(
        "checkout.op.ticket_order_open_hint",
        record=f"https://discord.com/channels/{GUILD_ID}/{current.staff_channel_id}/{current.staff_message_id}",
    )
    assert calls["n"] == 2  # the second look found the order
    assert ticket.status == "open"
    assert (await order(tenv, placed.id)).status == m.PENDING
    assert (await reload_session(tenv, session.id)).status == m.SUBMITTED


# -- permissions --------------------------------------------------------------------------------------------------


async def test_customers_cannot_close_their_order_ticket_by_command(op: TestEnv) -> None:
    tenv = op
    customer = tenv.member()
    session, ch, _ = await _abandoned_cart(tenv, customer)
    reply = await _close(tenv, customer, ch)
    assert _reply(reply).description == tenv.text(core.ERROR_PERMISSION)
    assert (await reload_session(tenv, session.id)).status == m.ACTIVE
    assert (await ticket_of(tenv, session.ticket_id)).status == "open"


async def test_only_order_staff_can_close_order_tickets(tmp_path: Path) -> None:
    overrides = {
        **server_extension(roles=[{"key": "support_only"}, {"key": "order_staff"}]),
        "tickets": {"categories": [{"id": "general", "discord_category": "tickets", "staff_roles": ["support_only"]}]},
        "checkout": {"staff_roles": ["order_staff"]},
    }
    tenv = await env(tmp_path, overrides)
    try:
        roles = {}
        for key in ("support_only", "order_staff"):
            role = tenv.guild.add_role(name=key, position=4)
            await tenv.app.resources.remember("role", key, role.id)
            roles[key] = role
        support_agent = tenv.guild.add_member(name="agent", roles=[roles["support_only"]])
        order_agent = tenv.guild.add_member(name="orders", roles=[roles["order_staff"]])
        customer = tenv.member()
        session, ch, _ = await _abandoned_cart(tenv, customer)

        denied = await _close(tenv, support_agent, ch)
        assert _reply(denied).title == tenv.app.op("tickets.cmd.not_staff_here")
        assert (await reload_session(tenv, session.id)).status == m.ACTIVE

        # A checkout-only staff role passes the command check (checkout.staff_roles) and closes the order ticket.
        assert "order_staff" in ticket_commands.command_staff_keys(tenv.app)
        allowed = await _close(tenv, order_agent, ch)
        ticket = await ticket_of(tenv, session.ticket_id)
        assert _reply(allowed).title == tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket))
        assert ticket.status == "closed"
    finally:
        await tenv.close()


async def test_other_ticket_commands_still_refuse_order_tickets(op: TestEnv) -> None:
    tenv = op
    staffer = tenv.member(name="staffer", staff=True)
    _, ch, _ = await _abandoned_cart(tenv, tenv.member())
    for name in ("info", "claim", "transcript"):
        reply = await run_command(tenv, name, user=staffer, channel=ch)
        assert _reply(reply).title == tenv.app.op("tickets.cmd.not_ticket"), name


async def test_order_tickets_can_be_closed_while_the_support_ticket_texts_are_missing(tmp_path: Path) -> None:
    from shopbot.features.tickets import content_keys as tk

    tenv = await env(tmp_path, content={tk.PANEL_TITLE: ""})
    try:
        assert not tenv.app.ready(tk.FEATURE) and tenv.app.ready(k.FEATURE)
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        session, ch, _ = await _abandoned_cart(tenv, customer)
        reply = await _close(tenv, staffer, ch)
        ticket = await ticket_of(tenv, session.ticket_id)
        assert _reply(reply).title == tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket))
        # Support-ticket commands still need the ticket feature.
        info = await run_command(tenv, "info", user=staffer, channel=ch)
        assert _reply(info).title == tenv.app.op("command.feature_disabled", feature=tk.FEATURE)
    finally:
        await tenv.close()


async def test_order_tickets_cannot_be_closed_by_command_while_checkout_is_off(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, _ = await _abandoned_cart(tenv, customer)
    settings = tenv.app.settings
    tenv.app.settings = replace(settings, features=replace(settings.features, checkout=False))
    reply = await _close(tenv, staffer, ch)
    assert _reply(reply).title == tenv.app.op("command.feature_disabled", feature="checkout")
    assert (await reload_session(tenv, session.id)).status == m.ACTIVE
    assert (await ticket_of(tenv, session.ticket_id)).status == "open"


# -- duplicates and concurrency -----------------------------------------------------------------------------------


async def test_closing_twice_changes_nothing_the_second_time(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, _ = await _abandoned_cart(tenv, customer)
    await _close(tenv, staffer, ch)
    ticket = await ticket_of(tenv, session.ticket_id)
    again = await _close(tenv, staffer, ch)
    assert _reply(again).title == tenv.app.op("tickets.cmd.wrong_state", number=number(tenv, ticket), status="closed")
    await run_tasks(tenv)
    assert len(channel(tenv, "transcripts").messages) == 1
    actions = await audit_actions(tenv)
    assert actions.count("tickets.closed") == 1 and actions.count("checkout.session_closed_by_staff") == 1


async def test_concurrent_closures_apply_once(op: TestEnv) -> None:
    tenv = op
    customer = tenv.member()
    first, second = tenv.member(name="staff1", staff=True), tenv.member(name="staff2", staff=True)
    session, ch, _ = await _abandoned_cart(tenv, customer)
    replies = await asyncio.gather(_close(tenv, first, ch), _close(tenv, second, ch))
    ticket = await ticket_of(tenv, session.ticket_id)
    assert ticket.status == "closed"
    titles = sorted(_reply(r).title or "" for r in replies)
    assert tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket)) in titles
    await run_tasks(tenv)
    assert len(channel(tenv, "transcripts").messages) == 1
    actions = await audit_actions(tenv)
    assert actions.count("tickets.closed") == 1 and actions.count("checkout.session_closed_by_staff") == 1


async def test_staff_closing_while_the_customer_cancels_ends_the_session_once(op: TestEnv) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, message = await _abandoned_cart(tenv, customer)
    await press(tenv, customer, message, views.cancel_id(session.id))
    await asyncio.gather(_close(tenv, staffer, ch), press(tenv, customer, message, views.cancel_yes_id(session.id)))
    assert (await reload_session(tenv, session.id)).status == m.SESSION_CANCELLED
    assert (await ticket_of(tenv, session.ticket_id)).status == "closed"
    actions = await audit_actions(tenv)
    ended = actions.count("checkout.session_closed_by_staff") + actions.count("checkout.session_cancelled")
    assert ended == 1 and actions.count("tickets.closed") == 1


# -- failures and restarts ----------------------------------------------------------------------------------------


async def test_a_failed_channel_closure_is_finished_by_the_durable_check_after_a_restart(
    op: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, _ = await _abandoned_cart(tenv, customer)
    ticket = await ticket_of(tenv, session.ticket_id)

    async def broken_close(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("[sample failure]")

    monkeypatch.setattr(tickets_service, "close_ticket", broken_close)
    reply = await _close(tenv, staffer, ch)
    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_close_pending", number=number(tenv, ticket))
    assert (await reload_session(tenv, session.id)).status == m.SESSION_CANCELLED
    assert (await ticket_of(tenv, ticket.id)).status == "open"
    assert await check_task_status(tenv, ticket.id) == "pending"
    monkeypatch.undo()

    # The process restarts: the durable check closes the channel of the ended session.
    app = restarted_app(tenv)
    await app.resources.refresh()
    tenv.clock.advance(repo.FOLLOWUP_DELAY_SECONDS + 1)
    for _ in range(3):
        await app.scheduler.run_due()
    closed = await ticket_of(tenv, ticket.id)
    assert (closed.status, closed.close_reason) == ("closed", m.SESSION_CANCELLED)


async def test_discord_refusing_the_panel_edit_still_closes_the_ticket(
    op: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenv = op
    customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
    session, ch, message = await _abandoned_cart(tenv, customer)

    async def refuse(**kwargs: Any) -> None:
        raise http_error(discord.HTTPException, 500, 0, "Server Error")

    monkeypatch.setattr(message, "edit", refuse)
    reply = await _close(tenv, staffer, ch)
    ticket = await ticket_of(tenv, session.ticket_id)
    assert _reply(reply).title == tenv.app.op("checkout.op.ticket_session_closed", number=number(tenv, ticket))
    assert ticket.status == "closed"
    assert (await reload_session(tenv, session.id)).status == m.SESSION_CANCELLED


async def test_discord_errors_from_the_closer_are_answered_with_operator_text(
    op: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenv = op
    staffer = tenv.member(name="staffer", staff=True)
    _, ch, _ = await _abandoned_cart(tenv, tenv.member())

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    monkeypatch.setitem(tickets_service.runtime(tenv.app).staff_closers, "order", forbidden)
    reply = await _close(tenv, staffer, ch)
    assert _reply(reply).title == tenv.app.op("tickets.cmd.discord_error", status=403)


async def test_closing_works_while_checkout_text_is_missing(tmp_path: Path) -> None:
    """Staff can still close an abandoned ticket when checkout stopped being ready (the panel is then left as is)."""
    tenv = await env(tmp_path)
    try:
        customer, staffer = tenv.member(), tenv.member(name="staffer", staff=True)
        session, ch, message = await _abandoned_cart(tenv, customer)
        before = [embed.description for embed in message.embeds]
        tenv.app.content.replace(
            file_values={**tenv.content, k.CANCELLED_DESCRIPTION: "", k.STAFF_CLOSED_DESCRIPTION: ""}
        )
        tenv.app.recompute_readiness()
        assert not tenv.app.ready(k.FEATURE)
        await _close(tenv, staffer, ch)
        assert (await ticket_of(tenv, session.ticket_id)).status == "closed"
        assert (await reload_session(tenv, session.id)).status == m.SESSION_CANCELLED
        assert [embed.description for embed in message.embeds] == before  # nothing incomplete was shown
    finally:
        await tenv.close()


# -- views and the command ------------------------------------------------------------------------------------------


def test_the_close_command_text_fits_discord() -> None:
    assert len(ticket_commands.CLOSE_DESCRIPTION) <= 100
    assert len(ticket_commands.CLOSE_REASON_DESCRIPTION) <= 100


async def test_the_staff_closed_view_is_previewed_within_limits(op: TestEnv) -> None:
    from shopbot.features.checkout import MODULE

    assert MODULE.preview is not None
    states = {state.name: state for state in MODULE.preview(op.app)}
    spec = states["workflow_closed_by_staff"].spec
    assert isinstance(spec, MessageSpec)
    validate_message(spec)
    assert (spec.embeds[0].description or "").split("\n\n")[1:] == [op.text(k.STAFF_CLOSED_DESCRIPTION)]
    assert spec.rows == ()


async def test_staff_closed_keys_are_optional_with_a_fallback(op: TestEnv) -> None:
    registry = op.app.content.registry
    for key, fallback in (
        (k.STAFF_CLOSED_TITLE, k.CANCELLED_TITLE),
        (k.STAFF_CLOSED_DESCRIPTION, k.CANCELLED_DESCRIPTION),
    ):
        definition = registry.get(key)
        assert definition is not None and not definition.required and definition.fallback == fallback
