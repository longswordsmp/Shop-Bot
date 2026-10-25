"""The live order record in the staff channel (#orders): its content (reference, customer, status, claim, timeline,
notes), the staff actions (claim/unclaim, note, correction request, rejection with a reason, tracking details,
details), permissions, duplicates, restart persistence and Discord failures."""

from __future__ import annotations

import asyncio
from pathlib import Path

import discord

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, staff_keys, staff_views, views
from tests.checkout.util import (
    add_product,
    audit_actions,
    error_text,
    money,
    op_text,
    order,
    place_order,
    press,
    reject,
    render,
    restarted_app,
    run_command,
    staff_message,
)
from tests.fakes import click, http_error, modal_input_ids, modal_of
from tests.helpers import TestEnv
from tests.operations.util import (
    activity,
    add_note,
    click_record,
    field_values,
    handling,
    make_ops,
    mark,
    record,
    request_correction,
    set_tracking,
    submit_form,
)


def _ids(message: object) -> list[str]:
    return message.custom_ids()  # type: ignore[attr-defined]


async def _placed(tenv: TestEnv, *, price: int = 1000) -> tuple[m.Order, discord.abc.Messageable, object]:
    customer = tenv.member()
    product = await add_product(tenv, "[p1]", price=price, stock=5)
    placed, ch, workflow = await place_order(tenv, customer, [(product.id, 1)])
    return placed, ch, (customer, workflow)


# -- content of the live record ---------------------------------------------------------------------------------


async def test_record_always_shows_reference_customer_status_and_timeline(ops: TestEnv) -> None:
    placed, ch, _ = await _placed(ops)
    message = await record(ops, placed.id)
    embed = message.embeds[0]
    fields = {f.name: f.value for f in embed.fields}
    assert fields[ops.text(staff_keys.REFERENCE_FIELD)] == "1"
    assert fields[ops.text(staff_keys.CUSTOMER_FIELD)] == f"<@{placed.user_id}>"
    assert fields[ops.text(staff_keys.CHANNEL_FIELD)] == f"<#{ch.id}>"  # type: ignore[attr-defined]
    assert fields[ops.text(staff_keys.STATUS_FIELD)] == ops.text(k.status_label_key(m.PENDING))
    assert ops.text(staff_keys.CLAIMED_FIELD) not in fields
    timeline = fields[ops.text(staff_keys.TIMELINE_FIELD)]
    assert timeline == render(
        ops,
        staff_keys.TIMELINE_LINE,
        time=f"<t:{placed.created_at}:f>",
        stage=ops.text(k.status_label_key(m.PENDING)),
        actor=f"<@{placed.user_id}>",
    )
    # Status buttons first, the management buttons on their own row.
    assert [[getattr(c, "custom_id", None) for c in row] for row in _rows(ops, placed)] == [
        [
            views.staff_action_id(m.PAID, placed.id, m.PENDING),
            views.staff_action_id(m.REJECTED, placed.id, m.PENDING),
            views.staff_action_id(m.CANCELLED, placed.id, m.PENDING),
        ],
        [
            staff_views.claim_id(placed.id),
            staff_views.correction_id(placed.id),
            staff_views.note_id(placed.id),
            staff_views.record_details_id(placed.id),
        ],
    ]


def _rows(tenv: TestEnv, placed: m.Order) -> list[list[object]]:
    message = staff_message(tenv, placed)
    view = message.view
    assert view is not None
    rows: dict[int, list[object]] = {}
    for child in view.children:
        item = getattr(child, "item", child)
        rows.setdefault(child.row or 0, []).append(item)
    return [rows[index] for index in sorted(rows)]


async def test_timeline_follows_every_stage_with_its_time(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, _ = await _placed(ops)
    ops.clock.advance(60)
    await click_record(ops, staffer, placed.id, staff_views.claim_id(placed.id))
    ops.clock.advance(60)
    await mark(ops, staffer, placed.id, m.PAID)
    ops.clock.advance(60)
    await mark(ops, staffer, placed.id, m.PROCESSING)
    message = await record(ops, placed.id)
    lines = field_values(message, ops.text(staff_keys.TIMELINE_FIELD))[0].split("\n")
    stages = [
        ops.text(k.status_label_key(m.PENDING)),
        ops.text(staff_keys.STAGE_CLAIMED),
        ops.text(k.status_label_key(m.PAID)),
        ops.text(k.status_label_key(m.PROCESSING)),
    ]
    assert len(lines) == 4
    for line, stage in zip(lines, stages, strict=True):
        assert stage in line
    assert f"<@{staffer.id}>" in lines[1] and f"<t:{placed.created_at + 60}:f>" in lines[1]
    assert message.embeds[0].description == render(
        ops,
        k.STAFF_DESCRIPTION,
        order_number="1",
        user_mention=f"<@{placed.user_id}>",
        channel_mention=f"<#{ch.id}>",  # type: ignore[attr-defined]
        total=money(ops, 1000),
        status=ops.text(k.status_label_key(m.PROCESSING)),
    )


async def test_long_history_keeps_the_latest_entries_within_limits(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    for index in range(15):  # 45 clicks: within the per-user rate limit of the test settings
        await add_note(ops, staffer, placed.id, f"[note {index}] " + "x" * 400)
        await click_record(ops, staffer, placed.id, staff_views.claim_id(placed.id))
        await click_record(ops, staffer, placed.id, staff_views.unclaim_id(placed.id))
    message = await record(ops, placed.id)
    total = sum(
        len(e.title or "") + len(e.description or "") + sum(len(f.name) + len(f.value) for f in e.fields)
        for e in message.embeds
    )
    assert total <= 6000
    notes = "\n".join(field_values(message, ops.text(staff_keys.NOTES_FIELD)))
    assert "[note 14]" in notes and "[note 0]" not in notes
    timeline = "\n".join(field_values(message, ops.text(staff_keys.TIMELINE_FIELD)))
    assert ops.text(staff_keys.STAGE_UNCLAIMED) in timeline.split("\n")[-1]
    assert len(await activity(ops, placed.id, h.NOTE)) == 15


# -- claim / unclaim ---------------------------------------------------------------------------------------------


async def test_claim_records_who_and_when_and_a_second_claim_is_refused(ops: TestEnv) -> None:
    s1, s2 = ops.member(name="s1", staff=True), ops.member(name="s2", staff=True)
    placed, _, _ = await _placed(ops)
    claimed = await click_record(ops, s1, placed.id, staff_views.claim_id(placed.id))
    assert op_text(claimed) == ops.app.op("checkout.op.claimed", number=1)
    state = await handling(ops, placed.id)
    assert (state.claimed_by, state.claimed_at) == (s1.id, ops.clock.now())
    message = await record(ops, placed.id)
    assert field_values(message, ops.text(staff_keys.CLAIMED_FIELD)) == [f"<@{s1.id}> <t:{ops.clock.now()}:R>"]
    assert staff_views.unclaim_id(placed.id) in _ids(message)
    assert staff_views.claim_id(placed.id) not in _ids(message)
    # A stale claim button (e.g. another staff member's screen) changes nothing.
    again = await press(ops, s2, message, staff_views.claim_id(placed.id))
    assert op_text(again) == ops.app.op("checkout.op.claim_taken", number=1, staff=f"<@{s1.id}>")
    same = await press(ops, s1, message, staff_views.claim_id(placed.id))
    assert op_text(same) == ops.app.op("checkout.op.claim_taken", number=1, staff=f"<@{s1.id}>")
    assert (await handling(ops, placed.id)).claimed_by == s1.id
    assert [a.kind for a in await activity(ops, placed.id)] == [h.CLAIM]
    assert (await audit_actions(ops)).count("checkout.order_claimed") == 1


async def test_concurrent_claims_apply_once(ops: TestEnv) -> None:
    s1, s2 = ops.member(name="s1", staff=True), ops.member(name="s2", staff=True)
    placed, _, _ = await _placed(ops)
    message = await record(ops, placed.id)
    results = await asyncio.gather(
        press(ops, s1, message, staff_views.claim_id(placed.id)),
        press(ops, s2, message, staff_views.claim_id(placed.id)),
    )
    replies = sorted(op_text(r) or "" for r in results)
    winner = (await handling(ops, placed.id)).claimed_by
    assert winner in (s1.id, s2.id)
    assert ops.app.op("checkout.op.claimed", number=1) in replies
    assert ops.app.op("checkout.op.claim_taken", number=1, staff=f"<@{winner}>") in replies
    assert len(await activity(ops, placed.id, h.CLAIM)) == 1


async def test_only_the_claimer_or_an_admin_releases_a_claim(ops: TestEnv) -> None:
    s1, s2 = ops.member(name="s1", staff=True), ops.member(name="s2", staff=True)
    admin = ops.member(name="admin", admin=True)
    placed, _, _ = await _placed(ops)
    await click_record(ops, s1, placed.id, staff_views.claim_id(placed.id))
    refused = await click_record(ops, s2, placed.id, staff_views.unclaim_id(placed.id))
    assert op_text(refused) == ops.app.op("checkout.op.not_claimer", number=1, staff=f"<@{s1.id}>")
    assert (await handling(ops, placed.id)).claimed_by == s1.id
    released = await click_record(ops, admin, placed.id, staff_views.unclaim_id(placed.id))
    assert op_text(released) == ops.app.op("checkout.op.unclaimed", number=1)
    state = await handling(ops, placed.id)
    assert state.claimed_by is None and state.unclaimed_at == ops.clock.now()
    twice = await click_record(ops, s1, placed.id, staff_views.unclaim_id(placed.id))
    assert op_text(twice) == ops.app.op("checkout.op.not_claimed", number=1)
    assert staff_views.claim_id(placed.id) in _ids(await record(ops, placed.id))
    assert [a.kind for a in await activity(ops, placed.id)] == [h.CLAIM, h.UNCLAIM]


async def test_final_orders_cannot_be_claimed(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    await mark(ops, staffer, placed.id, m.CANCELLED)
    message = await record(ops, placed.id)
    assert staff_views.claim_id(placed.id) not in _ids(message)
    stale = await press(ops, staffer, message, staff_views.claim_id(placed.id))
    assert op_text(stale) == ops.app.op("checkout.op.claim_state", number=1, status=m.CANCELLED)
    assert (await handling(ops, placed.id)).claimed_by is None


async def test_members_cannot_use_any_record_control(ops: TestEnv) -> None:
    placed, _, (customer, _) = await _placed(ops)
    message = await record(ops, placed.id)
    for user in (customer, ops.member(name="other")):
        for custom_id in (
            staff_views.claim_id(placed.id),
            staff_views.unclaim_id(placed.id),
            staff_views.note_id(placed.id),
            staff_views.correction_id(placed.id),
            staff_views.tracking_id(placed.id),
            staff_views.record_details_id(placed.id),
        ):
            denied = await press(ops, user, message, custom_id)
            assert error_text(denied) == ops.text(core.ERROR_PERMISSION), custom_id
            assert modal_of(denied) is None
        for form_id, input_id in (
            (staff_views.note_form_id(placed.id), staff_views.NOTE_INPUT),
            (staff_views.correction_form_id(placed.id), staff_views.MESSAGE_INPUT),
            (staff_views.tracking_form_id(placed.id), staff_views.TRACKING_INPUT),
            (staff_views.reject_form_id(placed.id, m.PENDING), staff_views.REASON_INPUT),
        ):
            denied = await submit_form(ops, user, form_id, input_id, "[forged]")
            assert error_text(denied) == ops.text(core.ERROR_PERMISSION), form_id
    assert await activity(ops, placed.id) == []
    assert (await order(ops, placed.id)).status == m.PENDING
    assert customer.dms == [] or all("[forged]" not in d.text() for d in customer.dms)


# -- internal notes ----------------------------------------------------------------------------------------------


async def test_note_form_stores_an_internal_note_and_updates_the_record(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, (customer, _) = await _placed(ops)
    opened = await click_record(ops, staffer, placed.id, staff_views.note_id(placed.id))
    modal = modal_of(opened)
    assert modal is not None and modal.custom_id == staff_views.note_form_id(placed.id)
    assert modal_input_ids(modal) == [staff_views.NOTE_INPUT]
    dms = len(customer.dms)
    saved = await add_note(ops, staffer, placed.id, "  [internal *note*]  ")
    assert op_text(saved) == ops.app.op("checkout.op.note_added", number=1)
    notes = await activity(ops, placed.id, h.NOTE)
    assert [(n.text, n.actor_id) for n in notes] == [("[internal *note*]", staffer.id)]
    message = await record(ops, placed.id)
    assert field_values(message, ops.text(staff_keys.NOTES_FIELD)) == [
        render(
            ops,
            staff_keys.NOTE_LINE,
            time=f"<t:{ops.clock.now()}:f>",
            actor=f"<@{staffer.id}>",
            note=views.escape("[internal *note*]"),
        )
    ]
    # Internal: the customer is not told, and the note is not in the status history.
    assert len(customer.dms) == dms
    assert all("[internal" not in msg.text() for msg in ch.messages)  # type: ignore[attr-defined]
    assert [e.note for e in await ops.app.db.run(repo.events, placed.id)] == [None]
    events = [e for e in await _audit(ops) if e.action == "checkout.order_note"]
    assert len(events) == 1 and "internal" not in str(events[0].details)


async def _audit(tenv: TestEnv) -> list:  # type: ignore[type-arg]
    from shopbot.db.repos import audit as audit_repo
    from tests.helpers import GUILD_ID

    return await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=100)


async def test_invalid_notes_are_refused(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    for text in ("   ", "x" * (h.NOTE_MAX + 1)):
        refused = await add_note(ops, staffer, placed.id, text)
        assert op_text(refused) == ops.app.op("checkout.op.text_invalid", max=h.NOTE_MAX)
    missing = await add_note(ops, staffer, 999, "[note]")
    assert op_text(missing) == ops.app.op("checkout.op.order_missing")
    assert await activity(ops, placed.id) == []


# -- correction requests -------------------------------------------------------------------------------------------


async def test_correction_request_notifies_the_customer_and_keeps_the_order_pending(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, (customer, _) = await _placed(ops)
    opened = await click_record(ops, staffer, placed.id, staff_views.correction_id(placed.id))
    assert modal_of(opened) is not None and modal_of(opened).custom_id == staff_views.correction_form_id(placed.id)  # type: ignore[union-attr]
    sent = await request_correction(ops, staffer, placed.id, "[please fix *your* address]")
    assert op_text(sent) == ops.app.op(
        "checkout.op.correction_sent", number=1, delivery=ops.app.op("checkout.op.delivery.dm")
    )
    dm = customer.dms[-1]
    assert dm.embeds[0].description == render(
        ops,
        staff_keys.CORRECTION_DESCRIPTION,
        order_number="1",
        total=money(ops, 1000),
        channel_mention=f"<#{ch.id}>",  # type: ignore[attr-defined]
    )
    assert [(f.name, f.value) for f in dm.embeds[0].fields] == [
        (ops.text(staff_keys.CORRECTION_MESSAGE_FIELD), views.escape("[please fix *your* address]"))
    ]
    assert (await order(ops, placed.id)).status == m.PENDING
    corrections = await activity(ops, placed.id, h.CORRECTION)
    assert [c.text for c in corrections] == ["[please fix *your* address]"]
    timeline = field_values(await record(ops, placed.id), ops.text(staff_keys.TIMELINE_FIELD))[0]
    assert ops.text(staff_keys.STAGE_CORRECTION) in timeline
    assert "checkout.order_correction_requested" in await audit_actions(ops)
    # The durable follow-up was completed right away (cancelled), so nothing is sent twice.
    from tests.operations.util import run_tasks

    await run_tasks(ops)
    assert len([d for d in customer.dms if d.embeds[0].description == dm.embeds[0].description]) == 1


async def test_correction_only_while_waiting_for_payment(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, (customer, _) = await _placed(ops)
    await mark(ops, staffer, placed.id, m.PAID)
    message = await record(ops, placed.id)
    assert staff_views.correction_id(placed.id) not in _ids(message)
    stale = await press(ops, staffer, message, staff_views.correction_id(placed.id))
    assert op_text(stale) == ops.app.op("checkout.op.correction_state", number=1, status=m.PAID)
    dms = len(customer.dms)
    late = await request_correction(ops, staffer, placed.id, "[too late]")
    assert op_text(late) == ops.app.op("checkout.op.correction_state", number=1, status=m.PAID)
    assert len(customer.dms) == dms and await activity(ops, placed.id, h.CORRECTION) == []


async def test_correction_falls_back_to_the_order_channel_and_reports_undelivered(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, (customer, _) = await _placed(ops)
    customer.dm_forbidden = True
    sent = await request_correction(ops, staffer, placed.id, "[fix it]")
    assert op_text(sent) == ops.app.op(
        "checkout.op.correction_sent", number=1, delivery=ops.app.op("checkout.op.delivery.channel")
    )
    assert ch.messages[-1].content == f"<@{customer.id}>"  # type: ignore[attr-defined]
    ch.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")  # type: ignore[attr-defined]
    failed = await request_correction(ops, staffer, placed.id, "[fix it again]")
    assert op_text(failed) == ops.app.op(
        "checkout.op.correction_sent", number=1, delivery=ops.app.op("checkout.op.delivery.none")
    )
    assert len(await activity(ops, placed.id, h.CORRECTION)) == 2


# -- rejection with a reason --------------------------------------------------------------------------------------


async def test_reject_opens_a_reason_form_and_the_reason_reaches_the_customer(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, (customer, _) = await _placed(ops)
    message = await record(ops, placed.id)
    opened = await press(ops, staffer, message, views.staff_action_id(m.REJECTED, placed.id, m.PENDING))
    modal = modal_of(opened)
    assert modal is not None and modal.custom_id == staff_views.reject_form_id(placed.id, m.PENDING)
    assert (await order(ops, placed.id)).status == m.PENDING
    rejected = await reject(ops, staffer, message, placed.id, "[out of *stock*]")
    assert op_text(rejected) == ops.app.op("checkout.op.status_changed", number=1, status=m.REJECTED)
    current = await order(ops, placed.id)
    assert current.status == m.REJECTED
    dm = customer.dms[-1]
    assert dm.embeds[0].description == render(
        ops,
        k.notify_description_key(m.REJECTED),
        order_number="1",
        total=money(ops, 1000),
        channel_mention=f"<#{ch.id}>",  # type: ignore[attr-defined]
    )
    assert [(f.name, f.value) for f in dm.embeds[0].fields] == [
        (ops.text(staff_keys.REJECTION_REASON_FIELD), views.escape("[out of *stock*]"))
    ]
    assert [a.text for a in await activity(ops, placed.id, h.REJECTION)] == ["[out of *stock*]"]
    # A second submission of the same (now stale) form changes nothing and notifies nobody.
    dms = len(customer.dms)
    again = await reject(ops, staffer, message, placed.id, "[again]")
    assert op_text(again) == ops.app.op("checkout.op.already_handled", number=1, status=m.REJECTED)
    assert len(customer.dms) == dms and len(await activity(ops, placed.id, h.REJECTION)) == 1


async def test_reject_button_of_a_paid_order_does_not_open_the_form(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    stale = await record(ops, placed.id)
    await mark(ops, staffer, placed.id, m.PAID)
    clicked = await press(ops, staffer, stale, views.staff_action_id(m.REJECTED, placed.id, m.PENDING))
    assert modal_of(clicked) is None
    assert op_text(clicked) == ops.app.op("checkout.op.already_handled", number=1, status=m.PAID)
    empty = await reject(ops, staffer, stale, placed.id, "   ")
    assert op_text(empty) == ops.app.op("checkout.op.text_invalid", max=h.TEXT_MAX)


# -- tracking details ----------------------------------------------------------------------------------------------


async def test_tracking_is_offered_only_when_configured(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, content={staff_keys.BUTTON_TRACKING: ""})
    try:
        assert tenv.app.ready("checkout")
        staffer = tenv.member(name="staffer", staff=True)
        placed, _, _ = await _placed(tenv)
        await mark(tenv, staffer, placed.id, m.PAID)
        message = await record(tenv, placed.id)
        assert staff_views.tracking_id(placed.id) not in _ids(message)
        refused = await press(tenv, staffer, message, staff_views.tracking_id(placed.id))
        assert op_text(refused) == tenv.app.op("checkout.op.tracking_unavailable")
    finally:
        await tenv.close()


async def test_tracking_needs_its_texts_once_the_button_is_configured(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, content={staff_keys.TRACKING_DESCRIPTION: ""})
    try:
        problems = tenv.app.readiness.statuses["checkout"].problems
        assert not tenv.app.ready("checkout")
        assert any(staff_keys.TRACKING_DESCRIPTION in p.message for p in problems)
    finally:
        await tenv.close()


async def test_tracking_before_completion_goes_into_the_completion_notification(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, (customer, _) = await _placed(ops)
    pending_click = await click_record(ops, staffer, placed.id, staff_views.tracking_id(placed.id))
    assert op_text(pending_click) == ops.app.op("checkout.op.tracking_state", number=1, status=m.PENDING)
    await mark(ops, staffer, placed.id, m.PAID)
    opened = await click_record(ops, staffer, placed.id, staff_views.tracking_id(placed.id))
    assert modal_of(opened) is not None
    saved = await set_tracking(ops, staffer, placed.id, "[track 123]")
    assert op_text(saved) == ops.app.op("checkout.op.tracking_saved", number=1)
    message = await record(ops, placed.id)
    assert field_values(message, ops.text(staff_keys.TRACKING_FIELD)) == ["[track 123]"]
    # The form opens prefilled with the current details.
    reopened = await click_record(ops, staffer, placed.id, staff_views.tracking_id(placed.id))
    text_input = next(i for i in modal_of(reopened).walk_children() if isinstance(i, discord.ui.TextInput))  # type: ignore[union-attr]
    assert text_input.default == "[track 123]"
    dms = len(customer.dms)
    await mark(ops, staffer, placed.id, m.FULFILLED)
    assert len(customer.dms) == dms + 1
    fields = [(f.name, f.value) for f in customer.dms[-1].embeds[0].fields]
    assert fields == [(ops.text(staff_keys.TRACKING_DETAILS_FIELD), "[track 123]")]


async def test_tracking_after_completion_is_sent_to_the_customer(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, ch, (customer, _) = await _placed(ops)
    await mark(ops, staffer, placed.id, m.PAID)
    await mark(ops, staffer, placed.id, m.FULFILLED)
    assert customer.dms[-1].embeds[0].fields == []
    ops.clock.advance(5)
    sent = await set_tracking(ops, staffer, placed.id, "[late tracking]")
    assert op_text(sent) == ops.app.op(
        "checkout.op.tracking_sent", number=1, delivery=ops.app.op("checkout.op.delivery.dm")
    )
    dm = customer.dms[-1]
    assert dm.embeds[0].description == render(
        ops,
        staff_keys.TRACKING_DESCRIPTION,
        order_number="1",
        total=money(ops, 1000),
        channel_mention=f"<#{ch.id}>",  # type: ignore[attr-defined]
    )
    assert [(f.name, f.value) for f in dm.embeds[0].fields] == [
        (ops.text(staff_keys.TRACKING_DETAILS_FIELD), "[late tracking]")
    ]
    assert "checkout.order_tracking_set" in await audit_actions(ops)
    refused = await set_tracking(ops, staffer, placed.id, "x" * (h.TEXT_MAX + 1))
    assert op_text(refused) == ops.app.op("checkout.op.text_invalid", max=h.TEXT_MAX)


async def test_tracking_after_shipping_is_sent_and_earlier_tracking_is_replaced_by_the_shipment(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, (customer, _) = await _placed(ops)
    await mark(ops, staffer, placed.id, m.PAID)
    await set_tracking(ops, staffer, placed.id, "[early tracking]")
    await run_command(ops, "ship", user=staffer, number=placed.number, carrier="UPS", tracking_number="1Z999")
    assert (await order(ops, placed.id)).status == m.SHIPPED
    assert (await handling(ops, placed.id)).tracking == "UPS · 1Z999"
    assert "[early tracking]" not in customer.dms[-1].text()  # the shipped notification carries the shipment
    dms = len(customer.dms)
    ops.clock.advance(5)
    sent = await set_tracking(ops, staffer, placed.id, "[corrected tracking]")
    assert op_text(sent) == ops.app.op(
        "checkout.op.tracking_sent", number=1, delivery=ops.app.op("checkout.op.delivery.dm")
    )
    assert len(customer.dms) == dms + 1
    assert [(f.name, f.value) for f in customer.dms[-1].embeds[0].fields] == [
        (ops.text(staff_keys.TRACKING_DETAILS_FIELD), "[corrected tracking]")
    ]


# -- details (the full record, privately) --------------------------------------------------------------------------


async def test_details_button_shows_the_full_record_privately(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    await click_record(ops, staffer, placed.id, staff_views.claim_id(placed.id))
    await add_note(ops, staffer, placed.id, "[a note for staff]")
    shown = await click_record(ops, staffer, placed.id, staff_views.record_details_id(placed.id))
    assert shown.sent and all(getattr(msg, "ephemeral", False) for msg in shown.sent)
    text = "\n".join(msg.text() for msg in shown.sent)
    assert shown.sent[0].embeds[0].title == ops.app.op("checkout.lookup.title", number=1)
    assert "[a note for staff]" in text and ops.app.op("checkout.lookup.claimed") in text
    assert f"<@{staffer.id}>" in text


# -- restart persistence and Discord failures ----------------------------------------------------------------------


async def test_record_controls_keep_working_after_a_restart(ops: TestEnv) -> None:
    staffer = ops.member(name="staffer", staff=True)
    placed, _, (customer, _) = await _placed(ops)
    message = await record(ops, placed.id)
    app = restarted_app(ops)
    await app.resources.refresh()  # as ShopBot.on_ready does
    claimed = await click(app, user=staffer, custom_id=staff_views.claim_id(placed.id), message=message)
    assert op_text(claimed) == app.op("checkout.op.claimed", number=1)
    noted = await submit_form(
        ops, staffer, staff_views.note_form_id(placed.id), staff_views.NOTE_INPUT, "[after restart]", app=app
    )
    assert op_text(noted) == app.op("checkout.op.note_added", number=1)
    corrected = await submit_form(
        ops, staffer, staff_views.correction_form_id(placed.id), staff_views.MESSAGE_INPUT, "[fix]", app=app
    )
    assert "[fix]" in customer.dms[-1].text() and op_text(corrected) is not None
    assert [a.kind for a in await activity(ops, placed.id)] == [h.CLAIM, h.NOTE, h.CORRECTION]


async def test_a_failed_record_edit_is_retried_by_the_durable_follow_up(ops: TestEnv) -> None:
    from tests.operations.util import run_tasks

    staffer = ops.member(name="staffer", staff=True)
    placed, _, _ = await _placed(ops)
    message = await record(ops, placed.id)
    original_edit = message.edit
    calls = {"n": 0}

    async def flaky_edit(**kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise http_error(discord.HTTPException, 500, 0, "Internal Server Error")
        return await original_edit(**kwargs)

    message.edit = flaky_edit  # type: ignore[method-assign]
    await add_note(ops, staffer, placed.id, "[noted while Discord failed]")
    notes = "\n".join(field_values(message, ops.text(staff_keys.NOTES_FIELD)))
    assert "[noted while Discord failed]" not in notes
    [activity_row] = await activity(ops, placed.id, h.NOTE)
    task_key = h.activity_followup_key(activity_row.id)
    ops.clock.advance(repo.FOLLOWUP_DELAY_SECONDS + 1)
    await run_tasks(ops)
    notes = "\n".join(field_values(message, ops.text(staff_keys.NOTES_FIELD)))
    assert "[noted while Discord failed]" in notes
    from shopbot.db.repos import tasks as task_repo

    task = await ops.app.db.run(task_repo.get_by_dedupe, task_key)
    assert task is not None and task.status == "done"
