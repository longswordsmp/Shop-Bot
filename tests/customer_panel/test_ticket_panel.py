"""The private order ticket: introduction first, then ONE live panel that always shows the ticket reference, the
current status, the step and progress, what was selected/entered so far, and the actions of the step; after
submission the current status and a timeline of the stages, updated on every status change."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.db.repos import notifications as notif_repo
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.checkout import tasks as checkout_tasks
from shopbot.features.tickets import service as tickets_service
from tests.checkout.util import (
    add_product,
    error_text,
    go,
    money,
    order,
    place_order,
    press,
    reload_session,
    render,
    restarted_app,
    session_of,
    set_qty,
    staff_message,
    start,
    ticket_channel_of,
    ticket_intro_message,
    ticket_status,
    workflow_message,
)
from tests.customer_panel.util import bot_messages, env, field_map, fields_of, stage, timeline_entry
from tests.fakes import click, http_error, submit_modal
from tests.helpers import TestEnv

# -- the ticket introduction ---------------------------------------------------------------------------------------


async def test_ticket_starts_with_an_introduction_then_the_live_panel(cp: TestEnv) -> None:
    customer = cp.member()
    _, session, ch, panel = await start(cp, customer)
    ticket = await tickets_service.get_ticket(cp.app, session.ticket_id)
    assert ticket is not None and ticket.channel_id == ch.id and ticket.number == 1  # the unique identifier is stored
    intro, workflow = bot_messages(ch)
    assert workflow is panel and intro is ticket_intro_message(ch)
    embed = intro.embeds[0]
    assert embed.title == render(cp, k.TICKET_INTRO_TITLE, ticket_number="0001")
    assert embed.description == render(
        cp, k.TICKET_INTRO_DESCRIPTION, ticket_number="0001", user_mention=customer.mention
    )
    assert fields_of(intro) == [fields_of(intro)[0]]
    assert (embed.fields[0].name, embed.fields[0].value) == (cp.text(k.TICKET_REFERENCE_FIELD), "0001")
    assert intro.custom_ids() == []  # information only; the controls are on the live panel
    assert await cp.app.db.run(notif_repo.already_sent, service.ticket_intro_key(ticket.id))
    # Only the customer, staff and the bot can see the ticket (existing private overwrites).
    everyone = ch.overwrites_for(cp.guild.default_role)
    assert everyone.view_channel is False or everyone.read_messages is False


async def test_the_introduction_needs_its_text(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.TICKET_INTRO_DESCRIPTION: ""})
    try:
        assert not tenv.app.ready("checkout")
        problems = {p.key for p in tenv.app.readiness.statuses["checkout"].problems}
        assert k.TICKET_INTRO_DESCRIPTION in problems
        clicked = await click(tenv.app, user=tenv.member(), custom_id=views.start_id())
        assert error_text(clicked) == tenv.text(core.ERROR_UNAVAILABLE)
        assert [c for c in tenv.guild.text_channels if c.name.startswith(tenv.text(k.CHANNEL_PREFIX))] == []
    finally:
        await tenv.close()


async def test_a_failed_introduction_leaves_no_half_made_ticket(cp: TestEnv, monkeypatch: Any) -> None:
    original = service.send_spec
    calls: list[int] = []

    async def first_send_fails(channel: Any, spec: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise http_error(discord.HTTPException, 500, 0, "boom")
        return await original(channel, spec)

    monkeypatch.setattr(service, "send_spec", first_send_fails)
    customer = cp.member()
    failed = await click(cp.app, user=customer, custom_id=views.start_id())
    assert error_text(failed) == cp.text(core.ERROR_GENERIC)
    session = await session_of(cp, customer)
    assert session.status == m.SESSION_CANCELLED
    assert await ticket_status(cp, session.ticket_id) == "failed"
    assert [c for c in cp.guild.text_channels if c.name.startswith(cp.text(k.CHANNEL_PREFIX))] == []


async def test_a_resent_panel_never_repeats_the_introduction(cp: TestEnv) -> None:
    customer = cp.member()
    _, session, ch, panel = await start(cp, customer)
    await panel.delete()
    await cp.app.db.run(lambda conn: conn.execute("UPDATE checkout_sessions SET message_id = NULL"))
    app = restarted_app(cp)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    resent = await reload_session(cp, session.id)
    intro, workflow = bot_messages(ch)
    assert workflow.id == resent.message_id
    assert intro.embeds[0].description == render(
        cp, k.TICKET_INTRO_DESCRIPTION, ticket_number="0001", user_mention=customer.mention
    )


async def test_an_introduction_lost_before_the_panel_is_posted_by_the_recovery(cp: TestEnv) -> None:
    customer = cp.member()
    _, session, ch, _ = await start(cp, customer)
    for message in bot_messages(ch):
        await message.delete()
    await cp.app.db.run(lambda conn: conn.execute("DELETE FROM notifications WHERE kind = 'checkout.ticket_intro'"))
    await cp.app.db.run(lambda conn: conn.execute("UPDATE checkout_sessions SET message_id = NULL"))
    app = restarted_app(cp)
    await app.resources.refresh()
    await checkout_tasks.on_ready(app)
    intro, workflow = bot_messages(ch)
    assert intro.embeds[0].fields[0].value == "0001"
    assert workflow.id == (await reload_session(cp, session.id)).message_id


# -- the live panel before submission --------------------------------------------------------------------------------


def _header(tenv: TestEnv, message: Any, reference: str = "0001") -> None:
    first, second = fields_of(message)[:2]
    assert (first.name, first.value, first.inline) == (tenv.text(k.TICKET_REFERENCE_FIELD), reference, True)
    assert (second.name, second.value) == (tenv.text(k.TICKET_STATUS_FIELD), tenv.text(k.TICKET_STATUS_IN_PROGRESS))


def _progress(tenv: TestEnv, message: Any, current: int, total: int) -> None:
    progress = render(tenv, core.PROGRESS_FORMAT, current=current, total=total, bar="")
    assert progress in (message.embeds[0].footer.text or "")


async def test_every_step_shows_reference_status_progress_selection_and_actions(cpf: TestEnv) -> None:
    tenv = cpf
    customer = tenv.member()
    p1 = await add_product(tenv, "[p1]", price=1000)
    _, session, _, message = await start(tenv, customer)
    sid = session.id
    # browse: reference, status, progress 1/5, the (empty) cart, cancel but no back
    _header(tenv, message)
    _progress(tenv, message, 1, 5)
    assert field_map(message)[tenv.text(k.CART_FIELD)] == tenv.text(k.CART_EMPTY)
    assert views.cancel_id(sid) in message.custom_ids()
    assert not any(cid.startswith("sb:co:go:") and cid.endswith(":browse") for cid in message.custom_ids())

    await set_qty(tenv, customer, message, sid, p1.id, 2)
    await go(tenv, customer, message, sid, m.CART)
    _header(tenv, message)
    _progress(tenv, message, 2, 5)
    line = render(
        tenv, k.CART_LINE, name="[p1]", quantity=2, unit_price=money(tenv, 1000), line_total=money(tenv, 2000)
    )
    assert line in field_map(message)[tenv.text(k.CART_FIELD)]
    assert {views.go_id(sid, m.BROWSE), views.go_id(sid, m.FULFILLMENT), views.cancel_id(sid)} <= set(
        message.custom_ids()
    )

    await go(tenv, customer, message, sid, m.FULFILLMENT)
    await press(tenv, customer, message, views.fulfillment_id(sid), values=["delivery"])
    _header(tenv, message)
    _progress(tenv, message, 3, 5)
    selected = field_map(message)
    assert line in selected[tenv.text(k.CART_FIELD)]  # the cart is still visible
    assert selected[tenv.text(k.FULFILLMENT_CHOSEN_FIELD)] == tenv.text(k.option_key("delivery", "label"))
    assert {views.go_id(sid, m.CART), views.cancel_id(sid)} <= set(message.custom_ids())

    await go(tenv, customer, message, sid, m.DETAILS)
    await submit_modal(
        tenv.app,
        user=customer,
        custom_id=views.details_form_id(sid, 0),
        fields={"d.name": "[sample name]", "o.delivery.address": "[sample address]", "o.delivery.phone": ""},
        message=message,
    )
    _header(tenv, message)
    _progress(tenv, message, 4, 5)
    entered = field_map(message)
    assert entered[tenv.text(k.form_field_key("name", "label"))] == "[sample name]"
    assert entered[tenv.text(k.FULFILLMENT_CHOSEN_FIELD)] == tenv.text(k.option_key("delivery", "label"))
    assert line in entered[tenv.text(k.CART_FIELD)]
    assert {views.go_id(sid, m.FULFILLMENT), views.go_id(sid, m.REVIEW), views.cancel_id(sid)} <= set(
        message.custom_ids()
    )

    await go(tenv, customer, message, sid, m.REVIEW)
    _header(tenv, message)
    _progress(tenv, message, 5, 5)
    review = field_map(message)
    # The summary: selections, the details the customer entered, totals, and the status.
    assert review[tenv.text(k.form_field_key("name", "label"))] == "[sample name]"
    assert review[tenv.text(k.option_form_key("delivery", "address", "label"))] == "[sample address]"
    assert review[tenv.text(k.REVIEW_TOTAL_FIELD)] == money(tenv, 2000 + 500 + 150 + 200)
    assert {views.go_id(sid, m.DETAILS), views.cancel_id(sid)} <= set(message.custom_ids())


async def test_the_ended_panel_keeps_its_reference(cp: TestEnv) -> None:
    customer = cp.member()
    _, session, _, message = await start(cp, customer)
    await press(cp, customer, message, views.cancel_id(session.id))
    await press(cp, customer, message, views.cancel_yes_id(session.id))
    assert message.embeds[0].description == cp.text(k.CANCELLED_DESCRIPTION)
    assert [(f.name, f.value) for f in message.embeds[0].fields] == [(cp.text(k.TICKET_REFERENCE_FIELD), "0001")]
    assert message.custom_ids() == []


async def test_reference_follows_the_ticket_number_padding(tmp_path: Path) -> None:
    tenv = await env(tmp_path, {"tickets": {"number_padding": 6}})
    try:
        _, _, ch, message = await start(tenv, tenv.member())
        _header(tenv, message, "000001")
        assert ticket_intro_message(ch).embeds[0].fields[0].value == "000001"
    finally:
        await tenv.close()


# -- after submission: status and timeline ----------------------------------------------------------------------------


def _timeline(tenv: TestEnv, message: Any) -> str:
    return field_map(message)[tenv.text(k.TICKET_TIMELINE_FIELD)]


async def test_submitted_panel_shows_status_and_timeline_updated_on_every_change(cp: TestEnv) -> None:
    staffer = cp.member(name="staffer", staff=True)
    p1 = await add_product(cp, "[p1]", price=1000)
    submitted_at = cp.clock.now()
    placed, _, message = await place_order(cp, cp.member(), [(p1.id, 1)])
    fields = fields_of(message)
    assert [(f.name, f.value) for f in fields[:3]] == [
        (cp.text(k.TICKET_REFERENCE_FIELD), "0001"),
        (cp.text(k.TICKET_ORDER_NUMBER_FIELD), str(placed.number)),
        (cp.text(k.TICKET_STATUS_FIELD), stage(cp, m.PENDING)),
    ]
    assert _timeline(cp, message) == timeline_entry(cp, m.PENDING, submitted_at)

    cp.clock.advance(3600)
    paid_at = cp.clock.now()
    await press(cp, staffer, staff_message(cp, placed), views.staff_action_id(m.PAID, placed.id))
    assert field_map(message)[cp.text(k.TICKET_STATUS_FIELD)] == stage(cp, m.PAID)
    assert _timeline(cp, message) == "\n".join(
        [timeline_entry(cp, m.PENDING, submitted_at), timeline_entry(cp, m.PAID, paid_at)]
    )

    cp.clock.advance(60)
    current = await order(cp, placed.id)
    await press(cp, staffer, staff_message(cp, current), views.staff_action_id(m.PROCESSING, placed.id))
    cp.clock.advance(60)
    current = await order(cp, placed.id)
    await press(cp, staffer, staff_message(cp, current), views.staff_action_id(m.FULFILLED, placed.id, m.PROCESSING))
    assert field_map(message)[cp.text(k.TICKET_STATUS_FIELD)] == stage(cp, m.FULFILLED)
    lines = _timeline(cp, message).split("\n")
    assert len(lines) == 4 and lines[-1] == timeline_entry(cp, m.FULFILLED, paid_at + 120)


async def test_staff_notes_never_reach_the_customer_timeline(cp: TestEnv) -> None:
    staffer = cp.member(name="staffer", staff=True)
    p1 = await add_product(cp, "[p1]")
    placed, _, message = await place_order(cp, cp.member(), [(p1.id, 1)])
    await service.add_note(cp.app, placed.id, actor_id=staffer.id, note="[secret staff note]")
    await press(cp, staffer, staff_message(cp, placed), views.staff_action_id(m.PAID, placed.id))
    assert "[secret staff note]" not in message.text()
    assert len(_timeline(cp, message).split("\n")) == 2
    view = await service.order_view(cp.app, placed.id)
    assert view is not None and [e.to_status for e in view.timeline] == [m.PENDING, m.PAID]
    assert all(e.note is None for e in view.timeline)


async def test_a_cancelled_order_shows_its_final_stage(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, _, message = await place_order(cp, customer, [(p1.id, 1)])
    await press(cp, customer, message, views.order_cancel_yes_id(placed.id))
    assert field_map(message)[cp.text(k.TICKET_STATUS_FIELD)] == stage(cp, m.CANCELLED)
    assert _timeline(cp, message).split("\n")[-1] == timeline_entry(cp, m.CANCELLED, cp.clock.now())
    assert message.custom_ids() == []  # nothing left to do for the customer


async def test_unknown_statuses_are_left_out_rather_than_invented(cp: TestEnv) -> None:
    p1 = await add_product(cp, "[p1]")
    placed, _, _ = await place_order(cp, cp.member(), [(p1.id, 1)])
    view = await service.order_view(cp.app, placed.id)
    assert view is not None
    foreign = m.OrderEvent(
        id=99, order_id=placed.id, from_status=m.PENDING, to_status="awaiting_x", actor_id=None, note=None, created_at=1
    )
    lines = views.timeline_lines(cp.app.ui, (*view.timeline, foreign))
    assert len(lines) == 1  # the stage without a configured name is skipped


async def test_restart_keeps_the_timeline_and_panel_controls(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, ch, message = await place_order(cp, customer, [(p1.id, 1)])
    app = restarted_app(cp)
    confirm = await click(app, user=customer, custom_id=views.order_cancel_id(placed.id), message=message)
    assert error_text(confirm) is None
    back = await click(app, user=customer, custom_id=views.order_cancel_no_id(placed.id), message=message)
    assert error_text(back) is None
    assert field_map(message)[cp.text(k.TICKET_STATUS_FIELD)] == stage(cp, m.PENDING)
    assert workflow_message(ch) is message
    assert await ticket_channel_of(cp, (await reload_session(cp, placed.session_id or 0)).ticket_id) is ch
    assert (await cp.app.db.run(repo.get_order, placed.id)) is not None
