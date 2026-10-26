"""Stock alerts in the staff channel (``operations.stock_alerts``): an order leaves an item with
``catalog.low_stock_threshold`` units or fewer ("low"), or sells it out ("out").

Each item's level is remembered in ``kv_state`` (``operations.stock_level:<product id>``), so an alert is posted once
per drop: only when the level gets worse (fine → low, fine → out, low → out). Orders that end unpaid (cancelled,
expired, rejected) and staff stock changes update the level without an alert, so a restock lets the next drop alert
again. Unlimited items are never low. The alert is a durable ``operations.alert`` task (``alerts.alert_task``) built
from the item's current numbers when it runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from shopbot.db.repos import state
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import service as checkout_service
from shopbot.features.operations import content_keys as k
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    import sqlite3

    from shopbot.context import App
    from shopbot.features.catalog.repo import Product

log = logging.getLogger(__name__)

MARKER: Final = "operations.stock_level:"
_RANK: Final = {None: 0, k.LOW_STOCK: 1, k.SOLD_OUT: 2}
# Orders that ended without a sale give their units back: the level can only get better.
_RELEASING: Final = (checkout_models.CANCELLED, checkout_models.EXPIRED, checkout_models.REJECTED)


def level(product: Product, threshold: int) -> str | None:
    """``SOLD_OUT``, ``LOW_STOCK`` or None (enough left, or unlimited)."""
    available = product.available
    if available is None:
        return None
    if available == 0:
        return k.SOLD_OUT
    if threshold and available <= threshold:
        return k.LOW_STOCK
    return None


def _record(
    conn: sqlite3.Connection, guild_id: int, product_ids: list[int], threshold: int, now: int
) -> list[tuple[int, str, int]]:
    """Store each product's level; (product id, level, product version) of those that got worse."""
    worse: list[tuple[int, str, int]] = []
    for product_id in product_ids:
        product = catalog_repo.get_product(conn, guild_id, product_id)
        key = f"{MARKER}{product_id}"
        current = level(product, threshold) if product is not None and not product.archived else None
        before = state.kv_get(conn, key)
        if current is None:
            if before is not None:
                state.kv_delete(conn, key)
            continue
        if _RANK[current] > _RANK.get(before, 0) and product is not None:
            worse.append((product_id, current, product.version))
        if current != before:
            state.kv_set(conn, key, current, now)
    return worse


async def check(app: App, product_ids: list[int], *, alert: bool) -> list[tuple[int, str, int]]:
    """Update the levels of ``product_ids``; with ``alert``, schedule a staff alert for each one that got worse."""
    if not product_ids:
        return []
    now = app.clock.now()
    threshold = app.settings.catalog.low_stock_threshold
    worse = await app.db.transaction(_record, app.env.guild_id, sorted(set(product_ids)), threshold, now)
    if alert and app.settings.operations.stock_alerts:
        from shopbot.features.operations.alerts import schedule_alert

        for product_id, current, version in worse:
            # The version changes with every reservation and stock change: one alert per drop, never deduplicated
            # against an earlier drop.
            payload = {"alert": current, "product_id": product_id}
            await schedule_alert(app, f"operations.alert:{current}:{product_id}:v{version}", payload)
            log_event(log, logging.INFO, "stock_alert_scheduled", product_id=product_id, stock=current)
    return worse


async def on_order_submitted(app: App, *, order_id: int, **_: Any) -> None:
    view = await checkout_service.order_view(app, order_id)
    if view is not None:
        await check(app, [item.product_id for item in view.items], alert=True)


async def on_order_status_changed(app: App, *, order_id: int, status: str, **_: Any) -> None:
    if status not in _RELEASING:
        return
    view = await checkout_service.order_view(app, order_id)
    if view is not None:
        await check(app, [item.product_id for item in view.items], alert=False)


async def on_product_changed(app: App, *, after: Product, **_: Any) -> None:
    """A staff change (restock, stock set, archive): the level follows without an alert."""
    await check(app, [after.id], alert=False)
