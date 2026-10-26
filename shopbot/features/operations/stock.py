"""Stock alerts in the staff channel (``operations.stock_alerts``): an order leaves an item with
``catalog.low_stock_threshold`` units or fewer ("low"), or sells it out ("out").

Each item's level is remembered in ``kv_state`` (``operations.stock_level:<product id>``), so an alert is posted once
per drop: only when an order makes the level worse (fine → low, fine → out, low → out). The level and the alert task
are written in one transaction, so an alert is never lost between the two. Orders that end unpaid (cancelled, expired,
rejected) and staff stock changes only ever store a BETTER level, never a worse one: a drop they happen to see first
(an order whose own check has not run yet) is left for that order's check to alert. A restock therefore lets the next
drop alert again. Unlimited items are never low.

Units that come back only because an unpaid order ended do not re-alert the same level for ``REPEAT_SECONDS`` (an
item that keeps selling out to orders nobody pays for would otherwise alert each time); a staff stock change ends
that quiet period. The alert is a durable ``operations.alert`` task built from the item's current numbers when it
runs, and skipped when the level no longer holds by then.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
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
# When a level was last alerted for an item: ``operations.stock_alerted:<product id>`` = "<level>:<unix time>".
ALERTED: Final = "operations.stock_alerted:"
REPEAT_SECONDS: Final = 6 * 3600
TASK_ALERT: Final = "operations.alert"  # the durable alert task (alerts.alert_task)
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


def _recently_alerted(conn: sqlite3.Connection, product_id: int, current: str, now: int) -> bool:
    raw = state.kv_get(conn, f"{ALERTED}{product_id}")
    alerted, _, when = (raw or "").partition(":")
    return alerted == current and when.isdigit() and now - int(when) < REPEAT_SECONDS


def _record(
    conn: sqlite3.Connection,
    guild_id: int,
    product_ids: list[int],
    threshold: int,
    now: int,
    alert: bool,
    attempts: int,
) -> list[tuple[int, str]]:
    """Store each product's level and, with ``alert``, schedule the alert of each one that got worse (same
    transaction). Without ``alert`` a worse level is not stored (see the module docstring). Returns the (product id,
    level) pairs that got worse."""
    worse: list[tuple[int, str]] = []
    for product_id in product_ids:
        product = catalog_repo.get_product(conn, guild_id, product_id)
        key = f"{MARKER}{product_id}"
        current = level(product, threshold) if product is not None and not product.archived else None
        before = state.kv_get(conn, key)
        if current is None:
            if before is not None:
                state.kv_delete(conn, key)
            continue
        if _RANK[current] > _RANK.get(before, 0):
            if not alert or product is None:
                continue  # left for the alerting check of the order that caused the drop
            worse.append((product_id, current))
            if not _recently_alerted(conn, product_id, current, now):
                # The version changes with every reservation and stock change: one task per drop.
                task_repo.schedule(
                    conn,
                    kind=TASK_ALERT,
                    run_at=now,
                    payload={"alert": current, "product_id": product_id},
                    max_attempts=max(1, attempts),
                    dedupe_key=f"{TASK_ALERT}:{current}:{product_id}:v{product.version}",
                    replace=False,
                    now=now,
                )
                state.kv_set(conn, f"{ALERTED}{product_id}", f"{current}:{now}", now)
        if current != before:
            state.kv_set(conn, key, current, now)
    return worse


def _forget_alerted(conn: sqlite3.Connection, product_id: int) -> None:
    state.kv_delete(conn, f"{ALERTED}{product_id}")


async def check(app: App, product_ids: list[int], *, alert: bool) -> list[tuple[int, str]]:
    """Update the levels of ``product_ids``; with ``alert`` (and ``operations.stock_alerts`` on), schedule a staff
    alert for each one that got worse."""
    if not product_ids:
        return []
    alert = alert and app.settings.operations.stock_alerts
    worse = await app.db.transaction(
        _record,
        app.env.guild_id,
        sorted(set(product_ids)),
        app.settings.catalog.low_stock_threshold,
        app.clock.now(),
        alert,
        app.scheduler.max_attempts,
    )
    for product_id, current in worse:
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


async def on_product_changed(app: App, *, before: Product | None = None, after: Product, **_: Any) -> None:
    """A staff change (restock, stock set, archive): the level follows without an alert. A stock change also ends the
    quiet period after an alert, so a new drop after a restock alerts again."""
    if before is None or before.stock_on_hand != after.stock_on_hand:
        await app.db.transaction(_forget_alerted, after.id)
    await check(app, [after.id], alert=False)
