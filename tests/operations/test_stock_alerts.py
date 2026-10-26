"""Stock alerts (operations.stock_alerts): an order that leaves an item low or sells it out alerts the staff channel
once per drop; a restock lets it alert again; orders that end unpaid and unlimited items never alert; a drop seen
first by another event still alerts; units back from unpaid orders do not re-alert for a while; an alert that is no
longer true when it runs is skipped."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.core.events import ORDER_SUBMITTED
from shopbot.features.catalog import service as catalog_service
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import content_keys as k
from shopbot.features.operations import stock
from tests.checkout.util import add_product, place_order, product
from tests.helpers import TestEnv
from tests.operations.util import make_ops, run_tasks, staff_channel

STOCK: dict[str, Any] = {"operations": {"stock_alerts": True}, "catalog": {"low_stock_threshold": 3}}


def _alerts(tenv: TestEnv, alert: str) -> list[str]:
    """Descriptions of the stock alerts of this kind in the staff channel."""
    template = tenv.text(k.alert_key(alert, "description"))
    head = template.split("{", 1)[0]
    return [
        msg.embeds[0].description or ""
        for msg in staff_channel(tenv).messages
        if msg.embeds and (msg.embeds[0].description or "").startswith(head)
    ]


async def _buy(tenv: TestEnv, product_id: int, quantity: int, name: str) -> m.Order:
    placed, _, _ = await place_order(tenv, tenv.member(name=name), [(product_id, quantity)])
    await run_tasks(tenv)
    return placed


async def test_low_then_sold_out_each_alert_once(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=6)
        await _buy(tenv, item.id, 2, "a")  # 4 left: fine
        assert _alerts(tenv, k.LOW_STOCK) == [] and _alerts(tenv, k.SOLD_OUT) == []
        await _buy(tenv, item.id, 1, "b")  # 3 left: low
        assert len(_alerts(tenv, k.LOW_STOCK)) == 1
        await _buy(tenv, item.id, 1, "c")  # 2 left: still low, no second alert
        assert len(_alerts(tenv, k.LOW_STOCK)) == 1
        await _buy(tenv, item.id, 2, "d")  # sold out
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1 and len(_alerts(tenv, k.LOW_STOCK)) == 1
    finally:
        await tenv.close()


async def test_a_restock_lets_the_next_drop_alert_again(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=4)
        await _buy(tenv, item.id, 1, "a")  # 3 left: low
        assert len(_alerts(tenv, k.LOW_STOCK)) == 1
        admin = tenv.member(name="admin", admin=True)
        reply = await catalog_service.change_stock(
            tenv.app, actor_id=admin.id, product_id=item.id, mode="add", amount=10
        )
        assert reply.tone == "success"
        await run_tasks(tenv)
        assert len(_alerts(tenv, k.LOW_STOCK)) == 1  # restocking itself does not alert
        await _buy(tenv, item.id, 10, "b")  # 3 left again: a new drop
        assert len(_alerts(tenv, k.LOW_STOCK)) == 2
    finally:
        await tenv.close()


async def test_an_order_that_ends_unpaid_only_improves_the_level(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=2)
        placed = await _buy(tenv, item.id, 2, "a")  # sold out (reserved)
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1
        outcome = await checkout_service.change_status(tenv.app, placed.id, m.CANCELLED, actor_id=None)
        assert outcome.applied
        await run_tasks(tenv)
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1 and _alerts(tenv, k.LOW_STOCK) == []  # back to 2: no alert
        await _buy(tenv, item.id, 2, "b")  # sold out again soon after: staff already know
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1
    finally:
        await tenv.close()


async def test_units_back_from_unpaid_orders_alert_again_only_after_a_while_or_a_restock(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=1)
        for name in ("a", "b", "c"):  # three customers let their order expire
            placed = await _buy(tenv, item.id, 1, name)
            assert (await checkout_service.change_status(tenv.app, placed.id, m.EXPIRED, actor_id=None)).applied
            await run_tasks(tenv)
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1
        tenv.clock.advance(stock.REPEAT_SECONDS)
        placed = await _buy(tenv, item.id, 1, "d")
        assert len(_alerts(tenv, k.SOLD_OUT)) == 2
        assert (await checkout_service.change_status(tenv.app, placed.id, m.EXPIRED, actor_id=None)).applied
        admin = tenv.member(name="admin", admin=True)
        reply = await catalog_service.change_stock(
            tenv.app, actor_id=admin.id, product_id=item.id, mode="add", amount=1
        )
        assert reply.tone == "success"
        await run_tasks(tenv)
        await _buy(tenv, item.id, 2, "e")  # restocked, then sold out: a new drop
        assert len(_alerts(tenv, k.SOLD_OUT)) == 3
    finally:
        await tenv.close()


def _hold_submitted(tenv: TestEnv) -> list[dict[str, Any]]:
    """Hold ORDER_SUBMITTED back, as if the order's follow-up steps were still running."""
    held: list[dict[str, Any]] = []
    bus = tenv.app.events
    original = bus.publish

    async def publish(name: str, /, **payload: Any) -> None:
        if name == ORDER_SUBMITTED:
            held.append(payload)
            return
        await original(name, **payload)

    bus.publish = publish  # type: ignore[method-assign]
    held_original.append(original)
    return held


held_original: list[Any] = []


async def _release(tenv: TestEnv, held: list[dict[str, Any]]) -> None:
    tenv.app.events.publish = held_original.pop()  # type: ignore[method-assign]
    for payload in held:
        await tenv.app.events.publish(ORDER_SUBMITTED, **payload)
    await run_tasks(tenv)


async def test_a_drop_seen_first_by_another_order_ending_still_alerts(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=6)
        other = await _buy(tenv, item.id, 1, "b")  # 5 left
        held = _hold_submitted(tenv)
        await place_order(tenv, tenv.member(name="a"), [(item.id, 4)])  # 1 left; its check has not run yet
        assert (await checkout_service.change_status(tenv.app, other.id, m.CANCELLED, actor_id=None)).applied
        await _release(tenv, held)
        assert (await product(tenv, item.id)).available == 2
        assert len(_alerts(tenv, k.LOW_STOCK)) == 1
    finally:
        await tenv.close()


async def test_a_drop_seen_first_by_a_staff_edit_still_alerts(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=2)
        held = _hold_submitted(tenv)
        await place_order(tenv, tenv.member(name="a"), [(item.id, 2)])  # sold out; its check has not run yet
        admin = tenv.member(name="admin", admin=True)
        reply = await catalog_service.set_price(tenv.app, actor_id=admin.id, product_id=item.id, amount="12.00")
        assert reply.tone == "success"
        await _release(tenv, held)
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1
    finally:
        await tenv.close()


async def test_the_level_and_its_alert_are_stored_together(tmp_path: Path, monkeypatch: Any) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=2)
        original = stock.task_repo.schedule
        calls: list[str] = []

        def flaky(conn: Any, **kw: Any) -> Any:
            if not calls and str(kw.get("dedupe_key")).startswith(f"{stock.TASK_ALERT}:{k.SOLD_OUT}:"):
                calls.append(kw["dedupe_key"])
                raise RuntimeError("database is locked")
            return original(conn, **kw)

        monkeypatch.setattr(stock.task_repo, "schedule", flaky)
        placed, _, _ = await place_order(tenv, tenv.member(name="a"), [(item.id, 2)])
        await run_tasks(tenv)
        assert calls and _alerts(tenv, k.SOLD_OUT) == []  # the level was not stored either
        # The durable follow-up runs again (as after a failed step or a restart): the alert is sent.
        await checkout_service.run_submit_followups(tenv.app, placed.id, adopt=True)
        await run_tasks(tenv)
        assert len(_alerts(tenv, k.SOLD_OUT)) == 1
    finally:
        await tenv.close()


async def test_an_alert_that_is_no_longer_true_when_it_runs_is_skipped(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        item = await add_product(tenv, "[item]", stock=2)
        placed, _, _ = await place_order(tenv, tenv.member(name="a"), [(item.id, 2)])  # alert scheduled, not run
        assert (await checkout_service.change_status(tenv.app, placed.id, m.CANCELLED, actor_id=None)).applied
        await run_tasks(tenv)
        assert _alerts(tenv, k.SOLD_OUT) == []
    finally:
        await tenv.close()


async def test_unlimited_items_and_switched_off_alerts_stay_quiet(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, STOCK)
    try:
        unlimited = await add_product(tenv, "[unlimited]", stock=None)
        await _buy(tenv, unlimited.id, 5, "a")
        assert _alerts(tenv, k.LOW_STOCK) == [] and _alerts(tenv, k.SOLD_OUT) == []
    finally:
        await tenv.close()
    off = await make_ops(tmp_path / "off", {"operations": {"stock_alerts": False}})
    try:
        assert k.alert_key(k.LOW_STOCK, "description") not in off.app.content.registry
        item = await add_product(off, "[item]", stock=1)
        await _buy(off, item.id, 1, "a")
        assert not any(
            "[item]" in (msg.embeds[0].description or "") for msg in staff_channel(off).messages if msg.embeds
        )
    finally:
        await off.close()
