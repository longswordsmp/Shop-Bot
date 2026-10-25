"""Banners on the staff side of orders: the live order record in #orders shows the banner of the order's status (kept
without a new upload on claims, notes and tracking details, swapped once on every status change), the separate
order-line and answer messages show none, staff replies show the general or the error banner, and the full record
and the order list show the staff banner (the list keeps it while paging)."""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.checkout import MODULE as CHECKOUT
from shopbot.features.checkout import handling_repo as h
from shopbot.features.checkout import models as m
from shopbot.features.checkout import preview as checkout_preview
from shopbot.features.checkout import service, staff, staff_preview, staff_views, views
from shopbot.ui.spec import EmbedField, MessageSpec
from tests.checkout.util import (
    ANSWERS_IN_CHANNEL,
    FULL_SETTINGS,
    add_product,
    order,
    place_order,
    press,
    reject,
    run_command,
)
from tests.fakes import FakeInteraction, FakeMessage, submit_modal
from tests.helpers import TestEnv, banner_names, deep_merge
from tests.operations.util import add_note, click_record, make_banner_ops, mark, record, set_tracking


@pytest.fixture
async def bops(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Checkout and operations ready with banners on (a tiny picture per banner id); no Owner alerts."""
    env = await make_banner_ops(tmp_path, {"operations": {"owner_dm_alerts": False}})
    try:
        yield env
    finally:
        await env.close()


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def reply_banners(interaction: FakeInteraction) -> list[str]:
    assert interaction.last_sent is not None
    return banner_names(interaction.last_sent)


async def _placed(tenv: TestEnv, name: str = "customer") -> m.Order:
    product = await add_product(tenv, f"[{name}]", price=1000, stock=5)
    placed, _, _ = await place_order(tenv, tenv.member(name=name), [(product.id, 1)])
    return placed


def uploads_since(message: FakeMessage, edits_before: int) -> int:
    """How many of the record's edits since ``edits_before`` changed its attachments (uploaded a banner)."""
    return len([edit for edit in message.edits[edits_before:] if "attachments" in edit])


# -- the live order record -------------------------------------------------------------------------------------------


async def test_the_record_banner_follows_the_status_and_stays_on_handling_changes(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    placed = await _placed(bops)
    message = await record(bops, placed.id)
    assert banner_names(message) == [banner_file(bops, "pending-review")]

    # Claim and note: the record is edited, its banner is kept without uploading it again.
    before = len(message.edits)
    claimed = await click_record(bops, staffer, placed.id, staff_views.claim_id(placed.id))
    await add_note(bops, staffer, placed.id, "[sample note]")
    assert len(message.edits) >= before + 2 and uploads_since(message, before) == 0
    assert banner_names(message) == [banner_file(bops, "pending-review")]
    assert reply_banners(claimed) == [banner_file(bops, "general-response")]

    # Every status change swaps the banner once (the old picture is not kept next to the new one).
    for status, banner_id in (
        (m.PAID, "successful-confirmation"),
        (m.PROCESSING, "submission-and-processing"),
        (m.FULFILLED, "completed-or-dispatched"),
    ):
        before = len(message.edits)
        changed = await mark(bops, staffer, placed.id, status)
        assert uploads_since(message, before) == 1, status
        assert banner_names(message) == [banner_file(bops, banner_id)] and len(message.attachments) == 1
        assert reply_banners(changed) == [banner_file(bops, "general-response")]

    # Tracking details on a completed order: edited, banner kept.
    before = len(message.edits)
    tracked = await set_tracking(bops, staffer, placed.id, "[sample tracking]")
    assert len(message.edits) > before and uploads_since(message, before) == 0
    assert banner_names(message) == [banner_file(bops, "completed-or-dispatched")]
    assert tracked.last_sent is not None and len(banner_names(tracked.last_sent)) == 1

    # A full refund closes it out.
    before = len(message.edits)
    refunded = await submit_modal(
        bops.app,
        user=staffer,
        custom_id=views.refund_form_id(placed.id, views.MODE_MONEY),
        fields={views.REFUND_AMOUNT_INPUT: "10.00"},
    )
    assert (await order(bops, placed.id)).status == m.REFUNDED
    assert uploads_since(message, before) == 1
    assert banner_names(message) == [banner_file(bops, "resolution")]
    assert reply_banners(refunded) == [banner_file(bops, "general-response")]


async def test_closed_orders_show_the_banner_of_how_they_ended(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    cancelled = await _placed(bops, "c1")
    rejected = await _placed(bops, "c2")
    expired = await _placed(bops, "c3")
    paid_cancelled = await _placed(bops, "c4")
    await mark(bops, staffer, cancelled.id, m.CANCELLED)
    rejection = await reject(bops, staffer, await record(bops, rejected.id), rejected.id)
    await service.change_status(bops.app, expired.id, m.EXPIRED, actor_id=None)  # as the payment expiry does
    await mark(bops, staffer, paid_cancelled.id, m.PAID)
    await mark(bops, staffer, paid_cancelled.id, m.CANCELLED)
    for placed, banner_id in (
        (cancelled, "cancellation"),
        (rejected, "rejected-or-unsuccessful-submission"),
        (expired, "cancellation"),
        (paid_cancelled, "cancellation"),
    ):
        message = await record(bops, placed.id)
        assert banner_names(message) == [banner_file(bops, banner_id)], placed.id
        assert len(message.attachments) == 1
    assert reply_banners(rejection) == [banner_file(bops, "general-response")]


async def test_an_adopted_or_posted_again_record_has_the_banner_of_the_current_status(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    placed = await _placed(bops)
    message = await record(bops, placed.id)
    # Posted but never recorded: the record is found again by its buttons (adopted) and edited in place (a full
    # message, so its attachments are known); its banner is not uploaded again.
    message.components = [  # type: ignore[method-assign]
        types.SimpleNamespace(children=[types.SimpleNamespace(custom_id=c) for c in message.custom_ids()])
    ]
    await bops.app.db.run(
        lambda conn: conn.execute("UPDATE orders SET staff_message_id = NULL WHERE id = ?", (placed.id,))
    )
    before = len(message.edits)
    assert await service.refresh_staff_message(bops.app, placed.id)
    assert (await order(bops, placed.id)).staff_message_id == message.id
    assert len(message.edits) == before + 1 and uploads_since(message, before) == 0
    assert banner_names(message) == [banner_file(bops, "pending-review")]
    # Deleted by someone: the next change posts a new record with the banner of the current status.
    await message.delete()
    await run_command(bops, "status", user=staffer, number=placed.number, new_status=m.PAID)
    reposted = await record(bops, placed.id)
    assert reposted is not message and banner_names(reposted) == [banner_file(bops, "successful-confirmation")]


async def test_order_lines_that_do_not_fit_follow_without_a_banner(bops: TestEnv) -> None:
    """The largest order's lines move to separate messages: only the record (the first message) has the banner."""
    settings, ui = bops.app.settings, bops.app.ui
    view = checkout_preview._order(settings, m.PAID, checkout_preview._largest_cart(settings)[:4])
    data = staff_preview.sample_data(view.order, claimed=True, claims=15)
    notes = tuple(
        h.Activity(100 + i, view.order.id, h.NOTE, 1, f"<{i}>" + "*" * 475, 1_700_000_000 + i) for i in range(5)
    )
    data = h.StaffData(handling=data.handling, events=data.events, activity=(*data.activity, *notes))
    [first, *extra] = staff_views.staff_messages(
        ui, settings, view, data, staff_confirms_payment=True, credit_refunds=True, tracking=True
    )
    assert first.banner is not None and first.banner.filename == banner_file(bops, "successful-confirmation")
    assert extra and all(part.banner is None for part in extra)


async def test_the_customers_answers_under_the_record_show_no_banner(tmp_path: Path) -> None:
    """With ``operations.answers_in_orders_channel`` the answers follow the record as their own messages (none)."""
    tenv = await make_banner_ops(tmp_path, deep_merge(FULL_SETTINGS, ANSWERS_IN_CHANNEL))
    try:
        settings = tenv.app.settings
        view = checkout_preview._order(settings, m.PENDING, checkout_preview._largest_cart(settings)[:1])
        parts = views.staff_details_messages(tenv.app.ui, settings, view.order)
        assert parts and all(part.banner is None for part in parts)
    finally:
        await tenv.close()


# -- staff replies, the full record, the order list ---------------------------------------------------------------


async def test_refused_staff_actions_show_the_error_banner(bops: TestEnv) -> None:
    first, second = bops.member(name="first", staff=True), bops.member(name="second", staff=True)
    placed = await _placed(bops)
    message = await record(bops, placed.id)
    await click_record(bops, first, placed.id, staff_views.claim_id(placed.id))
    before = len(message.edits)
    taken = await click_record(bops, second, placed.id, staff_views.claim_id(placed.id))
    assert reply_banners(taken) == [banner_file(bops, "error")]
    # The stale record was brought up to date: same status, so the banner was not uploaded again.
    assert uploads_since(message, before) == 0 and banner_names(message) == [banner_file(bops, "pending-review")]
    too_long = await add_note(bops, first, placed.id, "x" * (h.NOTE_MAX + 1))
    assert reply_banners(too_long) == [banner_file(bops, "error")]
    invalid = await run_command(bops, "status", user=first, number=placed.number, new_status=m.FULFILLED)
    assert reply_banners(invalid) == [banner_file(bops, "error")]
    done = await run_command(bops, "note", user=first, number=placed.number, text="[sample note]")
    assert reply_banners(done) == [banner_file(bops, "general-response")]


async def test_reply_spec_maps_every_tone(bops: TestEnv) -> None:
    for tone, banner_id in (
        ("success", "general-response"),
        ("info", "general-response"),
        ("warning", "error"),
        ("danger", "error"),
    ):
        spec = staff.reply_spec(bops.app, staff.OpReply("[text]", tone))
        assert spec.banner is not None and spec.banner.filename == banner_file(bops, banner_id), tone


async def test_command_refusals_show_the_error_banner(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    missing = await run_command(bops, "lookup", user=staffer, number=42)
    assert reply_banners(missing) == [banner_file(bops, "error")]


async def test_command_feature_disabled_shows_the_error_banner(tmp_path: Path) -> None:
    tenv = await make_banner_ops(tmp_path, FULL_SETTINGS, content={"checkout.review.title": ""})
    try:
        assert not tenv.app.ready("checkout")
        reply = await run_command(tenv, "list", user=tenv.member(name="staffer", staff=True))
        assert reply.last_sent is not None
        assert reply.last_sent.embeds[0].title == tenv.app.op("command.feature_disabled", feature="checkout")
        assert banner_names(reply.last_sent) == [banner_file(tenv, "error")]
    finally:
        await tenv.close()


async def test_the_full_record_shows_the_staff_banner_on_its_first_message_only(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    placed = await _placed(bops)
    details = await click_record(bops, staffer, placed.id, staff_views.record_details_id(placed.id))
    assert details.sent and banner_names(details.sent[0]) == [banner_file(bops, "staff-dashboard")]
    assert all(banner_names(part) == [] for part in details.sent[1:])
    viewed = await run_command(bops, "view", user=staffer, number=placed.number)
    assert banner_names(viewed.sent[0]) == [banner_file(bops, "staff-dashboard")]
    # A record too long for one message: the later messages continue it without a banner.
    fields = [EmbedField(f"[f{i}]", "v" * 1000) for i in range(30)]
    [first, *rest] = staff._pack(bops.app, "[title]", fields)
    assert first.banner is not None and first.banner.filename == banner_file(bops, "staff-dashboard")
    assert rest and all(part.banner is None for part in rest)


async def test_the_order_list_keeps_its_banner_while_paging_and_filtering(bops: TestEnv) -> None:
    staffer = bops.member(name="staffer", staff=True)
    product = await add_product(bops, "[p1]", price=1000, stock=50)
    for index in range(12):
        await place_order(bops, bops.member(name=f"b{index}"), [(product.id, 1)])
    listed = await run_command(bops, "list", user=staffer)
    message = listed.sent[-1]
    assert banner_names(message) == [banner_file(bops, "staff-dashboard")]
    paged = await press(bops, staffer, message, views.list_page_id(1, "", 0))
    assert paged.response_kinds() == ["edit_message"]
    assert "attachments" not in message.edits[-1] and banner_names(message) == [banner_file(bops, "staff-dashboard")]
    filtered = await press(bops, staffer, message, views.list_filter_id(0), values=["paid"])
    assert filtered.response_kinds() == ["edit_message"]
    assert "attachments" not in message.edits[-1] and banner_names(message) == [banner_file(bops, "staff-dashboard")]
    assert message.embeds[0].title == bops.app.op("checkout.list.title", total=0)


# -- the preview shows what is sent ------------------------------------------------------------------------------


async def test_staff_preview_states_carry_their_banners(bops: TestEnv) -> None:
    assert CHECKOUT.preview is not None
    states = {state.name: state.spec for state in CHECKOUT.preview(bops.app)}

    def shown(name: str) -> str | None:
        spec = states[name]
        assert isinstance(spec, MessageSpec), name
        return spec.banner.filename if spec.banner is not None else None

    for status, banner_id in staff_views.RECORD_BANNERS.items():
        assert shown(f"staff_{status}") == banner_file(bops, banner_id), status
    assert shown("staff_paid_tracking") == banner_file(bops, "successful-confirmation")
    assert shown("staff_order_list") == banner_file(bops, "staff-dashboard")
    assert shown("staff_record_details_1") == banner_file(bops, "staff-dashboard")
    for name in states:
        if name.startswith("staff_pending_largest_part_") or (
            name.startswith("staff_record_details_") and name != "staff_record_details_1"
        ):
            assert shown(name) is None, name


def test_record_banners_cover_every_status() -> None:
    assert set(staff_views.RECORD_BANNERS) == set(m.ORDER_STATUSES)
