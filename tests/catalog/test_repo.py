"""Inventory math, concurrency, optimistic locking, and validation of the product repository."""

from __future__ import annotations

import asyncio
import random
import sqlite3
import threading

import pytest

from shopbot.db.database import connect, run_in_transaction
from shopbot.features.catalog import repo
from tests.catalog.util import add_product, get
from tests.helpers import GUILD_ID, TestEnv


async def test_create_defaults_and_ordering(tenv: TestEnv) -> None:
    first = await add_product(tenv, "  [a]  ", price=100, description="  [d]  ")
    second = await add_product(tenv, "[b]", price=0, stock=3)
    assert first.name == "[a]" and first.description == "[d]"
    assert first.stock_on_hand is None and first.available is None and first.unlimited
    assert first.active and not first.archived and first.version == 1 and first.reserved == 0
    assert second.sort_order == first.sort_order + 1  # new products go last
    assert second.available == 3
    listed = await tenv.app.db.run(repo.list_products, GUILD_ID)
    assert [p.id for p in listed] == [first.id, second.id]
    assert await tenv.app.db.run(repo.count_products, GUILD_ID) == 2


async def test_listing_filters_inactive_and_archived(tenv: TestEnv) -> None:
    shown = await add_product(tenv, "[shown]")
    inactive = await add_product(tenv, "[inactive]", active=False)
    archived = await add_product(tenv, "[archived]")
    await tenv.app.db.transaction(repo.update_product, archived.id, archived.version, now=1, archived=True)
    run = tenv.app.db.run
    assert [p.id for p in await run(repo.list_products, GUILD_ID)] == [shown.id]
    assert [p.id for p in await run(repo.list_products, GUILD_ID, include_inactive=True)] == [shown.id, inactive.id]
    everything = await run(repo.list_products, GUILD_ID, include_inactive=True, include_archived=True)
    assert [p.id for p in everything] == [shown.id, inactive.id, archived.id]
    assert await run(repo.count_products, GUILD_ID, include_inactive=True, include_archived=True) == 3
    page = await run(repo.list_products, GUILD_ID, include_inactive=True, include_archived=True, offset=1, limit=1)
    assert [p.id for p in page] == [inactive.id]
    assert await run(repo.get_product, GUILD_ID + 1, shown.id) is None  # other guild
    found = await run(repo.search_products, GUILD_ID, "INACT")
    assert [p.id for p in found] == [inactive.id]
    assert [p.id for p in await run(repo.search_products, GUILD_ID, f"#{shown.id}")] == [shown.id]


async def test_reserve_release_commit_math_finite_stock(tenv: TestEnv) -> None:
    product = await add_product(tenv, stock=10)
    tx = tenv.app.db.transaction
    assert await tx(repo.reserve, product.id, 4, 5)
    p = await get(tenv, product.id)
    assert (p.stock_on_hand, p.reserved, p.available, p.version) == (10, 4, 6, 2)
    assert not await tx(repo.reserve, product.id, 7, 6)  # only 6 available: refused, nothing changes
    assert (await get(tenv, product.id)).reserved == 4
    assert await tx(repo.reserve, product.id, 6, 7)
    assert (await get(tenv, product.id)).available == 0
    assert not await tx(repo.reserve, product.id, 1, 8)

    assert await tx(repo.release, product.id, 3, 9) == 3
    p = await get(tenv, product.id)
    assert (p.stock_on_hand, p.reserved, p.available) == (10, 7, 3)

    await tx(repo.commit_sale, product.id, 5, 10)
    p = await get(tenv, product.id)
    assert (p.stock_on_hand, p.reserved, p.available) == (5, 2, 3)
    assert p.updated_at == 10

    # release never goes below zero and reports what was actually released
    assert await tx(repo.release, product.id, 5, 11) == 2
    assert (await get(tenv, product.id)).reserved == 0

    with pytest.raises(repo.InsufficientReservation):
        await tx(repo.commit_sale, product.id, 1, 12)
    assert (await get(tenv, product.id)).stock_on_hand == 5


async def test_reserve_release_commit_unlimited_stock(tenv: TestEnv) -> None:
    product = await add_product(tenv, stock=None)
    tx = tenv.app.db.transaction
    assert await tx(repo.reserve, product.id, 1000, 1)
    await tx(repo.commit_sale, product.id, 600, 2)
    p = await get(tenv, product.id)
    assert (p.stock_on_hand, p.reserved, p.available) == (None, 400, None)
    assert await tx(repo.release, product.id, 400, 3) == 400
    assert (await get(tenv, product.id)).reserved == 0


async def test_reserve_refuses_inactive_archived_and_missing(tenv: TestEnv) -> None:
    tx = tenv.app.db.transaction
    inactive = await add_product(tenv, active=False)
    archived = await add_product(tenv)
    await tx(repo.update_product, archived.id, archived.version, now=1, archived=True)
    assert not await tx(repo.reserve, inactive.id, 1, 2)
    assert not await tx(repo.reserve, archived.id, 1, 2)
    assert not await tx(repo.reserve, 999_999, 1, 2)
    with pytest.raises(repo.InvalidProduct):
        await tx(repo.reserve, inactive.id, 0, 2)
    with pytest.raises(repo.ProductNotFound):
        await tx(repo.release, 999_999, 1, 2)
    with pytest.raises(repo.ProductNotFound):
        await tx(repo.commit_sale, 999_999, 1, 2)


async def test_concurrent_reserves_through_transactions_never_oversell(tenv: TestEnv) -> None:
    product = await add_product(tenv, stock=25)
    rng = random.Random(7)
    quantities = [rng.randint(1, 3) for _ in range(60)]
    results = await asyncio.gather(*(tenv.app.db.transaction(repo.reserve, product.id, q, 1) for q in quantities))
    reserved = sum(q for q, ok in zip(quantities, results, strict=True) if ok)
    p = await get(tenv, product.id)
    assert p.reserved == reserved <= 25
    assert p.available == 25 - reserved
    # Every refused request was refused because it did not fit at that moment, never because of a race.
    assert reserved >= 25 - 2


def test_reserve_from_many_connections_never_oversells(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Independent connections (as separate processes would use) racing on the same row."""
    from shopbot.db.migrate import apply_migrations

    path = tmp_path / "race.db"
    setup = connect(path)
    apply_migrations(setup, db_path=path)
    product = run_in_transaction(
        setup, repo.create_product, GUILD_ID, name="[race]", price_minor=1, stock_on_hand=50, now=1
    )
    setup.close()

    successes: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = connect(path, busy_timeout_ms=10_000)
        try:
            for _ in range(20):
                if run_in_transaction(conn, repo.reserve, product.id, 1, 2):
                    with lock:
                        successes.append(1)
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(successes) == 50
    check = sqlite3.connect(str(path))
    assert check.execute("SELECT reserved, stock_on_hand FROM products WHERE id = ?", (product.id,)).fetchone() == (
        50,
        50,
    )
    check.close()


async def test_optimistic_concurrency_on_update(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[old]", price=100)
    tx = tenv.app.db.transaction
    updated = await tx(repo.update_product, product.id, product.version, now=5, name="[new]", price_minor=200)
    assert (updated.name, updated.price_minor, updated.version, updated.updated_at) == ("[new]", 200, 2, 5)
    with pytest.raises(repo.ConcurrentUpdate) as info:
        await tx(repo.update_product, product.id, product.version, now=6, name="[stale]")
    assert info.value.actual_version == 2
    assert (await get(tenv, product.id)).name == "[new]"
    # Inventory movements bump the version too, so an editor opened before a reservation is stale.
    assert await tx(repo.reserve, product.id, 1, 7)
    with pytest.raises(repo.ConcurrentUpdate):
        await tx(repo.update_product, product.id, 2, now=8, price_minor=300)
    # Writing identical values is a no-op (no version bump).
    current = await get(tenv, product.id)
    same = await tx(repo.update_product, product.id, current.version, now=9, name="[new]")
    assert same.version == current.version
    with pytest.raises(repo.ProductNotFound):
        await tx(repo.update_product, 999_999, 1, now=9, name="[x]")


async def test_stock_changes_never_go_below_reserved(tenv: TestEnv) -> None:
    product = await add_product(tenv, stock=10, reserved=6)
    tx = tenv.app.db.transaction
    with pytest.raises(repo.StockBelowReserved) as info:
        await tx(repo.set_stock, product.id, 5, 1)
    assert (info.value.requested, info.value.reserved) == (5, 6)
    with pytest.raises(repo.StockBelowReserved):
        await tx(repo.add_stock, product.id, -5, 1)
    with pytest.raises(repo.StockBelowReserved):
        await tx(repo.update_product, product.id, product.version, now=1, stock_on_hand=2)
    assert (await get(tenv, product.id)).stock_on_hand == 10
    assert (await tx(repo.set_stock, product.id, 6, 2)).available == 0
    assert (await tx(repo.add_stock, product.id, 4, 3)).stock_on_hand == 10
    assert (await tx(repo.add_stock, product.id, -4, 4)).stock_on_hand == 6
    unlimited = await tx(repo.set_stock, product.id, None, 5)
    assert unlimited.stock_on_hand is None and unlimited.reserved == 6
    with pytest.raises(repo.UnlimitedStock):
        await tx(repo.add_stock, product.id, 3, 6)
    with pytest.raises(repo.InvalidProduct):
        await tx(repo.add_stock, product.id, 0, 6)
    with pytest.raises(repo.InvalidProduct):
        await tx(repo.set_stock, product.id, -1, 6)


@pytest.mark.parametrize(
    "fields",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"description": "x" * 4001},
        {"price_minor": -1},
        {"price_minor": 1.5},
        {"image_url": "http://insecure.example/x.png"},
        {"image_url": "https://example.com/a b.png"},
        {"max_per_order": 0},
        {"stock_on_hand": -1},
        {"sort_order": 10**7},
        {"active": 1},
        {"version": 3},
    ],
)
async def test_update_validation(tenv: TestEnv, fields: dict[str, object]) -> None:
    product = await add_product(tenv)
    with pytest.raises(repo.InvalidProduct):
        await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=1, **fields)
    assert (await get(tenv, product.id)).version == product.version
