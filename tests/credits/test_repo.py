"""Credit ledger repository: math, invariants, idempotent order helpers, concurrency."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest

from shopbot.features.credits import repo
from tests.credits.util import balance, entries, grant, make_order
from tests.helpers import GUILD_ID, TestEnv

USER = 500_000_000_000_000_001
OTHER = 500_000_000_000_000_002


async def _add(tenv: TestEnv, **kwargs: Any) -> int:
    values: dict[str, Any] = {
        "guild_id": GUILD_ID,
        "user_id": USER,
        "order_id": None,
        "actor_id": 1,
        "note": None,
        "now": tenv.clock.now(),
    }
    values.update(kwargs)
    return await tenv.app.db.transaction(repo.add_entry, **values)


async def _debit(tenv: TestEnv, order_id: int, amount: int, user_id: int = USER) -> repo.OrderCredit:
    return await tenv.app.db.transaction(
        repo.debit_for_order,
        guild_id=GUILD_ID,
        user_id=user_id,
        order_id=order_id,
        amount_minor=amount,
        now=tenv.clock.now(),
    )


async def _return(tenv: TestEnv, order_id: int) -> repo.OrderCredit | None:
    return await tenv.app.db.transaction(
        repo.return_for_order, guild_id=GUILD_ID, order_id=order_id, actor_id=None, now=tenv.clock.now()
    )


# --------------------------------------------------------------------------------------------------
# Ledger math
# --------------------------------------------------------------------------------------------------


async def test_balance_is_the_sum_of_entries(tenv: TestEnv) -> None:
    assert await balance(tenv, USER) == 0
    await grant(tenv, USER, 1_000)
    await grant(tenv, USER, 250)
    await _add(tenv, delta_minor=-400, kind=repo.KIND_ADMIN_ADJUST)
    await grant(tenv, OTHER, 99)
    assert await balance(tenv, USER) == 850
    assert await balance(tenv, OTHER) == 99
    rows = await entries(tenv, USER)
    assert [e.delta_minor for e in rows] == [-400, 250, 1_000]  # newest first
    assert sum(e.delta_minor for e in rows) == 850


async def test_balances_are_per_guild(tenv: TestEnv) -> None:
    await grant(tenv, USER, 500)
    await _add(tenv, guild_id=GUILD_ID + 1, delta_minor=700, kind=repo.KIND_ADMIN_ADJUST)
    assert await balance(tenv, USER) == 500
    assert await tenv.app.db.run(repo.balance, GUILD_ID + 1, USER) == 700


async def test_entry_fields_are_stored(tenv: TestEnv) -> None:
    tenv.clock.advance(60)
    entry_id = await _add(tenv, delta_minor=123, kind=repo.KIND_ADMIN_ADJUST, actor_id=42, note="[reason]")
    entry = await tenv.app.db.run(repo.get_entry, GUILD_ID, entry_id)
    assert entry == repo.Entry(
        id=entry_id,
        guild_id=GUILD_ID,
        user_id=USER,
        delta_minor=123,
        kind=repo.KIND_ADMIN_ADJUST,
        order_id=None,
        actor_id=42,
        note="[reason]",
        created_at=tenv.clock.now(),
        order_number=None,
    )
    assert await tenv.app.db.run(repo.get_entry, GUILD_ID + 1, entry_id) is None


async def test_history_paginates_newest_first(tenv: TestEnv) -> None:
    for amount in range(1, 8):
        await grant(tenv, USER, amount)
    first = await tenv.app.db.run(repo.history, GUILD_ID, USER, limit=3, offset=0)
    second = await tenv.app.db.run(repo.history, GUILD_ID, USER, limit=3, offset=3)
    third = await tenv.app.db.run(repo.history, GUILD_ID, USER, limit=3, offset=6)
    assert [e.delta_minor for e in first] == [7, 6, 5]
    assert [e.delta_minor for e in second] == [4, 3, 2]
    assert [e.delta_minor for e in third] == [1]
    assert await tenv.app.db.run(repo.count_entries, GUILD_ID, USER) == 7
    assert await tenv.app.db.run(repo.history, GUILD_ID, USER, limit=3, offset=9) == []


async def test_applicable_amount(tenv: TestEnv) -> None:
    assert await tenv.app.db.run(repo.applicable_amount, GUILD_ID, USER, 1_000) == 0
    await grant(tenv, USER, 600)
    assert await tenv.app.db.run(repo.applicable_amount, GUILD_ID, USER, 1_000) == 600
    assert await tenv.app.db.run(repo.applicable_amount, GUILD_ID, USER, 250) == 250
    assert await tenv.app.db.run(repo.applicable_amount, GUILD_ID, USER, 0) == 0


# --------------------------------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------------------------------


async def test_negative_balance_is_prevented(tenv: TestEnv) -> None:
    await grant(tenv, USER, 300)
    with pytest.raises(repo.InsufficientCredit) as info:
        await _add(tenv, delta_minor=-301, kind=repo.KIND_ADMIN_ADJUST)
    assert info.value.balance_minor == 300 and info.value.requested_minor == 301
    assert await balance(tenv, USER) == 300
    await _add(tenv, delta_minor=-300, kind=repo.KIND_ADMIN_ADJUST)  # exactly to zero is fine
    assert await balance(tenv, USER) == 0
    with pytest.raises(repo.InsufficientCredit):
        await _add(tenv, delta_minor=-1, kind=repo.KIND_ADMIN_ADJUST)
    assert len(await entries(tenv, USER)) == 2


@pytest.mark.parametrize(
    ("delta", "kind", "message"),
    [
        (0, repo.KIND_ADMIN_ADJUST, "must not be 0"),
        (True, repo.KIND_ADMIN_ADJUST, "integer"),
        (1.5, repo.KIND_ADMIN_ADJUST, "integer"),
        (10**12 + 1, repo.KIND_ADMIN_ADJUST, "at most"),
        (5, "gift", "unknown"),
        (5, repo.KIND_REFUND_TO_CREDIT, "require an order_id"),
        (-5, repo.KIND_ORDER_DEBIT, "require an order_id"),
    ],
)
async def test_malformed_entries_are_rejected(tenv: TestEnv, delta: Any, kind: str, message: str) -> None:
    await grant(tenv, USER, 1_000)
    with pytest.raises(ValueError, match=message):
        await _add(tenv, delta_minor=delta, kind=kind)
    assert await balance(tenv, USER) == 1_000


async def test_note_length_is_limited(tenv: TestEnv) -> None:
    await _add(tenv, delta_minor=1, kind=repo.KIND_ADMIN_ADJUST, note="x" * repo.NOTE_MAX_LENGTH)
    with pytest.raises(ValueError, match="note"):
        await _add(tenv, delta_minor=1, kind=repo.KIND_ADMIN_ADJUST, note="x" * (repo.NOTE_MAX_LENGTH + 1))
    assert await balance(tenv, USER) == 1


async def test_order_kinds_enforce_signs(tenv: TestEnv) -> None:
    order_id, _ = await make_order(tenv, USER)
    await grant(tenv, USER, 1_000)
    with pytest.raises(ValueError, match="negative"):
        await _add(tenv, delta_minor=100, kind=repo.KIND_ORDER_DEBIT, order_id=order_id)
    with pytest.raises(ValueError, match="positive"):
        await _add(tenv, delta_minor=-100, kind=repo.KIND_REFUND_TO_CREDIT, order_id=order_id)
    with pytest.raises(ValueError, match="positive"):
        await _add(tenv, delta_minor=-100, kind=repo.KIND_ORDER_RETURN, order_id=order_id)
    assert await balance(tenv, USER) == 1_000


async def test_order_reference_must_match_member_and_guild(tenv: TestEnv) -> None:
    order_id, _ = await make_order(tenv, USER)
    foreign_order, _ = await make_order(tenv, USER, guild_id=GUILD_ID + 1)
    await grant(tenv, OTHER, 1_000)
    with pytest.raises(repo.OrderCreditConflict, match="another member"):
        await _debit(tenv, order_id, 100, user_id=OTHER)
    with pytest.raises(repo.OrderCreditConflict, match="does not exist"):
        await _debit(tenv, foreign_order, 100, user_id=OTHER)
    with pytest.raises(repo.OrderCreditConflict, match="does not exist"):
        await _debit(tenv, 999_999, 100, user_id=OTHER)
    assert await balance(tenv, OTHER) == 1_000


async def test_writes_outside_a_transaction_are_refused(tenv: TestEnv) -> None:
    def outside(conn: sqlite3.Connection) -> int:
        return repo.add_entry(
            conn,
            guild_id=GUILD_ID,
            user_id=USER,
            delta_minor=5,
            kind=repo.KIND_ADMIN_ADJUST,
            order_id=None,
            actor_id=None,
            note=None,
            now=0,
        )

    with pytest.raises(RuntimeError, match="transaction"):
        await tenv.app.db.run(outside)
    assert await balance(tenv, USER) == 0


async def test_failed_check_rolls_back_the_callers_transaction(tenv: TestEnv) -> None:
    """Checkout calls the helpers inside its own transaction: an InsufficientCredit undoes the whole unit."""
    await grant(tenv, USER, 100)
    order_id, _ = await make_order(tenv, USER)

    def checkout_unit(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
        repo.debit_for_order(conn, guild_id=GUILD_ID, user_id=USER, order_id=order_id, amount_minor=500, now=1)

    with pytest.raises(repo.InsufficientCredit):
        await tenv.app.db.transaction(checkout_unit)
    status = await tenv.app.db.run(
        lambda conn: conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
    )
    assert status == "pending_payment"
    assert await balance(tenv, USER) == 100


# --------------------------------------------------------------------------------------------------
# Order debit / return (checkout API)
# --------------------------------------------------------------------------------------------------


async def test_debit_for_order_is_idempotent(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    order_id, number = await make_order(tenv, USER)
    first = await _debit(tenv, order_id, 400)
    second = await _debit(tenv, order_id, 400)
    assert first.created is True and second.created is False
    assert first.entry == second.entry
    assert first.entry.kind == repo.KIND_ORDER_DEBIT and first.entry.delta_minor == -400
    assert first.entry.order_number == number
    assert await balance(tenv, USER) == 600
    assert len(await tenv.app.db.run(repo.order_entries, GUILD_ID, order_id)) == 1


async def test_conflicting_second_debit_is_refused(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    order_id, _ = await make_order(tenv, USER)
    await _debit(tenv, order_id, 400)
    with pytest.raises(repo.OrderCreditConflict):
        await _debit(tenv, order_id, 300)
    with pytest.raises(repo.OrderCreditConflict, match="already has"):
        await _add(tenv, delta_minor=-100, kind=repo.KIND_ORDER_DEBIT, order_id=order_id)
    assert await balance(tenv, USER) == 600


async def test_debit_requires_enough_credit_and_positive_amount(tenv: TestEnv) -> None:
    await grant(tenv, USER, 300)
    order_id, _ = await make_order(tenv, USER)
    with pytest.raises(repo.InsufficientCredit):
        await _debit(tenv, order_id, 301)
    for bad in (0, -5):
        with pytest.raises(ValueError, match="positive"):
            await _debit(tenv, order_id, bad)
    assert await tenv.app.db.run(repo.order_entries, GUILD_ID, order_id) == []
    assert (await _debit(tenv, order_id, 300)).created
    assert await balance(tenv, USER) == 0


async def test_return_for_order_gives_back_the_debit_once(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    order_id, _ = await make_order(tenv, USER)
    await _debit(tenv, order_id, 650)
    first = await _return(tenv, order_id)
    second = await _return(tenv, order_id)
    assert first is not None and second is not None
    assert first.created is True and second.created is False
    assert first.entry == second.entry
    assert first.entry.kind == repo.KIND_ORDER_RETURN and first.entry.delta_minor == 650
    assert first.entry.user_id == USER
    assert await balance(tenv, USER) == 1_000
    # Credit cannot be re-applied to the order after the return (at most one debit per order).
    with pytest.raises(repo.OrderCreditConflict, match="already returned"):
        await _debit(tenv, order_id, 650)
    assert await balance(tenv, USER) == 1_000


async def test_return_without_debit_returns_none(tenv: TestEnv) -> None:
    order_id, _ = await make_order(tenv, USER)
    assert await _return(tenv, order_id) is None
    assert await tenv.app.db.run(repo.order_entries, GUILD_ID, order_id) == []


async def test_direct_return_entries_must_match_the_debit(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    order_id, _ = await make_order(tenv, USER)
    with pytest.raises(repo.OrderCreditConflict, match="no credit debit"):
        await _add(tenv, delta_minor=100, kind=repo.KIND_ORDER_RETURN, order_id=order_id)
    await _debit(tenv, order_id, 300)
    with pytest.raises(repo.OrderCreditConflict, match="exactly"):
        await _add(tenv, delta_minor=500, kind=repo.KIND_ORDER_RETURN, order_id=order_id)
    await _add(tenv, delta_minor=300, kind=repo.KIND_ORDER_RETURN, order_id=order_id)
    with pytest.raises(repo.OrderCreditConflict, match="already returned"):
        await _add(tenv, delta_minor=300, kind=repo.KIND_ORDER_RETURN, order_id=order_id)
    assert await balance(tenv, USER) == 1_000


async def test_refund_to_credit(tenv: TestEnv) -> None:
    order_id, number = await make_order(tenv, USER)
    entry = await tenv.app.db.transaction(
        repo.refund_to_credit,
        guild_id=GUILD_ID,
        user_id=USER,
        order_id=order_id,
        amount_minor=250,
        actor_id=7,
        note="[refund]",
        now=tenv.clock.now(),
    )
    assert entry.kind == repo.KIND_REFUND_TO_CREDIT and entry.delta_minor == 250 and entry.order_number == number
    await tenv.app.db.transaction(
        repo.refund_to_credit,
        guild_id=GUILD_ID,
        user_id=USER,
        order_id=order_id,
        amount_minor=100,
        actor_id=7,
        now=tenv.clock.now(),
    )
    assert await balance(tenv, USER) == 350
    with pytest.raises(ValueError, match="positive"):
        await tenv.app.db.transaction(
            repo.refund_to_credit,
            guild_id=GUILD_ID,
            user_id=USER,
            order_id=order_id,
            amount_minor=0,
            actor_id=7,
            now=tenv.clock.now(),
        )


# --------------------------------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------------------------------


async def test_concurrent_debits_never_overdraw(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    orders = [(await make_order(tenv, USER))[0] for _ in range(6)]

    async def attempt(order_id: int) -> bool:
        try:
            await _debit(tenv, order_id, 300)
        except repo.InsufficientCredit:
            return False
        return True

    results = await asyncio.gather(*(attempt(order_id) for order_id in orders))
    assert results.count(True) == 3
    assert await balance(tenv, USER) == 100
    debits = [e for e in await entries(tenv, USER) if e.kind == repo.KIND_ORDER_DEBIT]
    assert len(debits) == 3


async def test_concurrent_duplicate_debits_apply_once(tenv: TestEnv) -> None:
    await grant(tenv, USER, 1_000)
    order_id, _ = await make_order(tenv, USER)
    results = await asyncio.gather(*(_debit(tenv, order_id, 200) for _ in range(5)))
    assert [r.created for r in results].count(True) == 1
    assert await balance(tenv, USER) == 800
    returns = await asyncio.gather(*(_return(tenv, order_id) for _ in range(5)))
    assert [r.created for r in returns if r is not None].count(True) == 1
    assert await balance(tenv, USER) == 1_000
