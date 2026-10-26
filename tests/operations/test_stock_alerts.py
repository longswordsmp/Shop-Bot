"""Stock alerts (operations.stock_alerts): an order that leaves an item low or sells it out alerts the staff channel
once per drop; a restock lets it alert again; orders that end unpaid and unlimited items never alert."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.features.catalog import service as catalog_service
from shopbot.features.checkout import models as m
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import content_keys as k
from tests.checkout.util import add_product, place_order
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
        await _buy(tenv, item.id, 2, "b")  # sold out again: a new drop
        assert len(_alerts(tenv, k.SOLD_OUT)) == 2
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
