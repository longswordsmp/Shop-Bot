"""Customer notifications of the order ticket: 'submission received' (DM with the configured fallback), the existing
status notifications, the expiry warning before an idle cart expires (durable task), and the private link to an
existing open order's ticket on the start button."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.db.repos import tasks as task_repo
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo, service, views
from shopbot.features.checkout import tasks as checkout_tasks
from tests.checkout.util import (
    add_product,
    error_text,
    money,
    place_order,
    reload_session,
    render,
    restarted_app,
    set_qty,
    start,
    task,
)
from tests.customer_panel.util import WARNING, bot_messages, env
from tests.fakes import click, http_error
from tests.helpers import TestEnv, make_settings

# -- submission received ----------------------------------------------------------------------------------------------


def _submitted_text(tenv: TestEnv, placed: m.Order, channel_id: int) -> str:
    return render(
        tenv,
        k.notify_description_key(k.NOTIFY_SUBMITTED),
        order_number=str(placed.number),
        total=money(tenv, placed.total_minor),
        channel_mention=f"<#{channel_id}>",
    )


async def test_submission_received_is_sent_once_by_dm(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, ch, _ = await place_order(cp, customer, [(p1.id, 1)])
    (dm,) = customer.dms
    assert dm.embeds[0].title == render(
        cp,
        k.notify_title_key(k.NOTIFY_SUBMITTED),
        order_number="1",
        total=money(cp, 1000),
        channel_mention=f"<#{ch.id}>",
    )
    assert dm.embeds[0].description == _submitted_text(cp, placed, ch.id)
    # The durable follow-up repeats every step; the notification is deduplicated.
    await service.run_submit_followups(cp.app, placed.id, adopt=True)
    assert len(customer.dms) == 1


async def test_submission_received_falls_back_to_the_ticket(cp: TestEnv) -> None:
    customer = cp.member()
    customer.dm_forbidden = True
    p1 = await add_product(cp, "[p1]")
    placed, ch, _ = await place_order(cp, customer, [(p1.id, 1)])
    posted = bot_messages(ch)[-1]
    assert posted.embeds[0].description == _submitted_text(cp, placed, ch.id)
    assert posted.content == customer.mention  # the fallback mentions the customer


async def test_a_crash_before_the_notification_is_repaired_by_the_follow_up_task(cp: TestEnv, monkeypatch: Any) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")

    async def crash(app: Any, order_id: int) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_notify_submitted", crash)
    placed, ch, _ = await place_order(cp, customer, [(p1.id, 1)])
    assert customer.dms == []
    followup = await task(cp, repo.after_submit_key(placed.id))
    assert followup is not None and followup.status == "pending"
    monkeypatch.undo()
    cp.clock.advance(repo.FOLLOWUP_DELAY_SECONDS)
    await cp.app.scheduler.run_due()
    assert [d.embeds[0].description for d in customer.dms] == [_submitted_text(cp, placed, ch.id)]


async def test_missing_submission_text_keeps_checkout_unready(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.notify_description_key(k.NOTIFY_SUBMITTED): ""})
    try:
        assert not tenv.app.ready("checkout")
        assert not tenv.app.ready("my_orders")  # depends on checkout
    finally:
        await tenv.close()


# -- the expiry warning ------------------------------------------------------------------------------------------------


def _warning_text(tenv: TestEnv, expires_at: int) -> str:
    return render(tenv, k.notify_description_key(k.NOTIFY_SESSION_WARNING), minutes=10, expires=f"<t:{expires_at}:R>")


def _warnings(tenv: TestEnv, ch: Any) -> list[Any]:
    """The expiry warnings posted in ``ch`` (recognized by the fixture token their text starts with)."""
    token = tenv.text(k.notify_description_key(k.NOTIFY_SESSION_WARNING)).split(" ")[0]
    return [msg for msg in bot_messages(ch) if msg.embeds and (msg.embeds[0].description or "").startswith(token)]


async def test_the_customer_is_warned_in_the_ticket_before_the_cart_expires(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        customer = tenv.member()
        _, session, ch, message = await start(tenv, customer)
        warning = await task(tenv, repo.session_warning_key(session.id))
        assert warning is not None and warning.run_at == session.updated_at + 20 * 60
        tenv.clock.advance(19 * 60)
        await tenv.app.scheduler.run_due()
        assert _warnings(tenv, ch) == []
        tenv.clock.advance(60)
        await tenv.app.scheduler.run_due()
        (posted,) = _warnings(tenv, ch)
        assert posted.embeds[0].description == _warning_text(tenv, session.updated_at + 30 * 60)
        assert posted.content == customer.mention and customer.dms == []  # in the ticket, mentioning the customer
        await tenv.app.scheduler.run_due()  # running again in the same idle period sends nothing
        assert len(_warnings(tenv, ch)) == 1
        tenv.clock.advance(10 * 60)
        await tenv.app.scheduler.run_due()
        assert (await reload_session(tenv, session.id)).status == m.SESSION_EXPIRED
        assert message.embeds[0].description == tenv.text(k.EXPIRED_DESCRIPTION)
        assert len(_warnings(tenv, ch)) == 1
        assert (await task(tenv, repo.session_warning_key(session.id))).status in ("done", "cancelled")
    finally:
        await tenv.close()


async def test_activity_moves_the_warning_and_a_new_idle_period_warns_again(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        _, session, ch, message = await start(tenv, customer)
        tenv.clock.advance(15 * 60)
        await set_qty(tenv, customer, message, session.id, p1.id, 1)  # activity
        tenv.clock.advance(6 * 60)  # 21 minutes after the start, 6 after the activity
        await tenv.app.scheduler.run_due()
        assert _warnings(tenv, ch) == []
        tenv.clock.advance(14 * 60)  # 20 minutes after the activity
        await tenv.app.scheduler.run_due()
        assert len(_warnings(tenv, ch)) == 1
        await set_qty(tenv, customer, message, session.id, p1.id, 2)  # activity after the warning
        active = await reload_session(tenv, session.id)
        tenv.clock.advance(10 * 60)  # the old expiry time: the task looks again
        await tenv.app.scheduler.run_due()
        assert (await reload_session(tenv, session.id)).status == m.ACTIVE
        tenv.clock.advance(10 * 60)  # 20 minutes after the second activity
        await tenv.app.scheduler.run_due()
        warnings = _warnings(tenv, ch)
        assert len(warnings) == 2
        assert warnings[-1].embeds[0].description == _warning_text(tenv, active.updated_at + 30 * 60)
    finally:
        await tenv.close()


async def test_the_warning_survives_a_restart(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        _, session, ch, _ = await start(tenv, tenv.member())
        app = restarted_app(tenv)
        tenv.clock.advance(20 * 60)
        await app.scheduler.run_due()
        assert len(_warnings(tenv, ch)) == 1
        del session
    finally:
        await tenv.close()


async def test_submitting_or_cancelling_ends_the_warning(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        customer = tenv.member()
        p1 = await add_product(tenv, "[p1]")
        placed, ch, _ = await place_order(tenv, customer, [(p1.id, 1)])
        assert placed.session_id is not None
        assert (await task(tenv, repo.session_warning_key(placed.session_id))).status == "cancelled"
        _, other, other_ch, message = await start(tenv, tenv.member(name="other"))
        await click(tenv.app, user=tenv.guild.get_member(other.user_id), custom_id=views.cancel_yes_id(other.id))
        assert (await task(tenv, repo.session_warning_key(other.id))).status == "cancelled"
        tenv.clock.advance(25 * 60)
        await tenv.app.scheduler.run_due()
        assert _warnings(tenv, ch) == [] and _warnings(tenv, other_ch) == []
        del message
    finally:
        await tenv.close()


async def test_a_warning_that_cannot_be_posted_never_breaks_the_task(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        _, session, ch, _ = await start(tenv, tenv.member())
        ch.fail_send = http_error(discord.HTTPException, 500, 0, "boom")
        tenv.clock.advance(20 * 60)
        await tenv.app.scheduler.run_due()
        current = await task(tenv, repo.session_warning_key(session.id))
        assert current.status == "pending" and current.run_at == session.updated_at + 30 * 60  # waits for the expiry
        rows = await tenv.app.db.run(
            lambda conn: conn.execute(
                "SELECT status FROM notifications WHERE kind = 'checkout.session_expiry_warning'"
            ).fetchall()
        )
        assert [r["status"] for r in rows] == ["failed"]
    finally:
        await tenv.close()


async def test_recovery_arms_the_warning_when_it_was_switched_on_later(tmp_path: Path) -> None:
    tenv = await env(tmp_path, {"checkout": {"session_expiry_minutes": 30}})
    try:
        _, session, ch, _ = await start(tenv, tenv.member())
        assert await task(tenv, repo.session_warning_key(session.id)) is None
        tenv.app.settings = make_settings(
            {"features": {"my_orders": True}, "bot": {"rate_limit_per_10s": 100}, **WARNING}
        )
        app = restarted_app(tenv)
        await app.resources.refresh()
        await checkout_tasks.on_ready(app)
        armed = await task(tenv, repo.session_warning_key(session.id))
        assert armed is not None and armed.status == "pending"
        tenv.clock.advance(20 * 60)
        await app.scheduler.run_due()
        assert len(_warnings(tenv, ch)) == 1
    finally:
        await tenv.close()


async def test_a_switched_off_warning_sends_nothing(tmp_path: Path) -> None:
    tenv = await env(tmp_path, WARNING)
    try:
        _, session, ch, _ = await start(tenv, tenv.member())
        tenv.app.settings = make_settings({"features": {"my_orders": True}, "checkout": {"session_expiry_minutes": 30}})
        tenv.clock.advance(20 * 60)
        await tenv.app.scheduler.run_due()
        assert _warnings(tenv, ch) == []
        assert (await task(tenv, repo.session_warning_key(session.id))).status == "done"
    finally:
        await tenv.close()


def test_warning_settings_are_checked() -> None:
    from shopbot.features.checkout import MODULE

    assert MODULE.settings_problems(make_settings(WARNING)) == []
    no_expiry = make_settings({"checkout": {"session_expiry_warning_minutes": 10}})
    assert any("session_expiry_warning_minutes" in p for p in MODULE.settings_problems(no_expiry))
    too_late = make_settings({"checkout": {"session_expiry_minutes": 10, "session_expiry_warning_minutes": 10}})
    assert any("must be less than" in p for p in MODULE.settings_problems(too_late))
    keys = {key.key: key for key in k.content_keys(make_settings(WARNING))}
    assert keys[k.notify_description_key(k.NOTIFY_SESSION_WARNING)].required
    plain = {key.key: key for key in k.content_keys(make_settings())}
    assert not plain[k.notify_description_key(k.NOTIFY_SESSION_WARNING)].required


async def test_the_warning_task_ignores_a_malformed_payload(cp: TestEnv) -> None:
    await cp.app.db.transaction(
        task_repo.schedule,
        kind=service.TASK_SESSION_WARNING,
        run_at=cp.clock.now(),
        payload={},
        max_attempts=1,
        dedupe_key="test:bad",
        replace=False,
        now=cp.clock.now(),
    )
    await cp.app.scheduler.run_due()
    assert (await task(cp, "test:bad")).status == "failed"


# -- start: link to the existing ticket --------------------------------------------------------------------------------


async def test_start_links_the_open_orders_ticket_with_the_link_button(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, ch, _ = await place_order(cp, customer, [(p1.id, 1)])
    cp.clock.advance(60)
    blocked = await click(cp.app, user=customer, custom_id=views.start_id())
    reply = blocked.last_sent
    assert reply is not None and reply.ephemeral  # type: ignore[attr-defined]
    assert reply.embeds[0].description == render(
        cp, k.ORDER_EXISTS, channel_mention=f"<#{ch.id}>", order_number=str(placed.number), limit=1
    )
    (button,) = reply.components()
    assert button.label == cp.text(k.BUTTON_OPEN_TICKET)
    assert button.url == f"https://discord.com/channels/{cp.guild.id}/{ch.id}"
    assert len([c for c in cp.guild.text_channels if c.category_id == ch.category_id]) == 1  # no second ticket


async def test_without_an_open_ticket_the_limit_text_is_used(cp: TestEnv) -> None:
    customer = cp.member()
    p1 = await add_product(cp, "[p1]")
    placed, _, _ = await place_order(cp, customer, [(p1.id, 1)])
    await cp.app.db.run(
        lambda conn: conn.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (placed.ticket_id,))
    )
    cp.clock.advance(60)
    blocked = await click(cp.app, user=customer, custom_id=views.start_id())
    assert error_text(blocked) == render(cp, k.LIMIT_REACHED, limit=1)


async def test_the_link_button_is_optional(tmp_path: Path) -> None:
    tenv = await env(tmp_path, content={k.BUTTON_OPEN_TICKET: ""})
    try:
        assert tenv.app.ready("checkout")
        customer = tenv.member()
        started = await click(tenv.app, user=customer, custom_id=views.start_id())
        assert started.last_sent is not None and started.last_sent.components() == []
        tenv.clock.advance(60)
        again = await click(tenv.app, user=customer, custom_id=views.start_id())
        assert again.last_sent is not None and again.last_sent.components() == []
    finally:
        await tenv.close()


async def test_started_and_existing_session_replies_link_the_ticket(cp: TestEnv) -> None:
    customer = cp.member()
    started, _, ch, _ = await start(cp, customer)
    url = f"https://discord.com/channels/{cp.guild.id}/{ch.id}"
    assert started.last_sent is not None and started.last_sent.components()[0].url == url
    cp.clock.advance(60)
    again = await click(cp.app, user=customer, custom_id=views.start_id())
    assert error_text(again) == render(cp, k.SESSION_EXISTS, channel_mention=f"<#{ch.id}>")
    assert again.last_sent is not None and again.last_sent.components()[0].url == url
    assert error_text(again) != cp.text(core.ERROR_GENERIC)
