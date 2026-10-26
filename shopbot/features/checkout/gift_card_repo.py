"""Gift card payments (``payments.provider = "gift_card"``): the transaction functions of the customer's submission
and of staff decisions. Each is ONE transaction that checks the order's state and changes it conditionally, so a
double click or two staff members acting at once never apply anything twice.

- ``submit_card``: a customer submits a card for their unpaid order. Once the cards waiting or redeemed cover the
  total, the order moves to payment_submitted ("Order placed"); the card's code is deleted after
  ``payments.gift_card.keep_days`` at the latest (a durable task scheduled in the same transaction).
- ``reveal``: staff with the payment roles read the codes waiting for a decision (logged, without the code).
- ``decide_cards``: staff redeemed cards or refused them with a reason. Once no card waits: redeemed cards that cover
  the total make the order paid; otherwise it waits for payment again (the customer is told why and submits another
  card) and its payment expiry, when on, starts again.

The state machine (``repo.transition``) handles the cards of an order that is paid or ends unpaid any other way.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import gift_card_store as store
from shopbot.features.checkout import models as m
from shopbot.features.checkout import repo

TASK_ERASE: Final = "checkout.gift_card_erase"


def erase_task_key(card_id: int) -> str:
    return f"{TASK_ERASE}:{card_id}"


class GiftCardError(Exception):
    """reason: 'missing' | 'state' (the order is not waiting for this) | 'too_many' (``max_cards`` reached) |
    'duplicate' (the card is waiting or was redeemed already) | 'decided' (no waiting card left to decide on).
    Nothing was written."""

    def __init__(self, reason: str, *, order: m.Order | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.order = order


@dataclass(frozen=True)
class Submitted:
    card_id: int
    order: m.Order
    totals: store.CardTotals
    # Set when this card completed the payment (pending_payment -> payment_submitted).
    moved: repo.Transition | None


@dataclass(frozen=True)
class Decided:
    changed: int
    order: m.Order
    totals: store.CardTotals
    # Set once no card waits: -> paid, or back to pending_payment.
    moved: repo.Transition | None


def _order(conn: sqlite3.Connection, guild_id: int, order_id: int) -> m.Order:
    order = repo.get_order(conn, order_id)
    if order is None or order.guild_id != guild_id:
        raise GiftCardError("missing")
    return order


def submit_card(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    user_id: int,
    amount_minor: int,
    fingerprint: str,
    sealed: bytes,
    last4: str,
    max_cards: int,
    erase_at: int,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Submitted:
    order = _order(conn, guild_id, order_id)
    if order.user_id != user_id:
        raise GiftCardError("missing")
    if order.status != m.PENDING:
        raise GiftCardError("state", order=order)
    if store.totals(conn, order_id).active >= max_cards:
        raise GiftCardError("too_many", order=order)
    if store.fingerprint_in_use(conn, guild_id, fingerprint):
        raise GiftCardError("duplicate", order=order)
    card_id = store.insert(
        conn,
        guild_id=guild_id,
        order_id=order_id,
        user_id=user_id,
        amount_minor=amount_minor,
        fingerprint=fingerprint,
        sealed=sealed,
        last4=last4,
        now=now,
    )
    task_repo.schedule(
        conn,
        kind=TASK_ERASE,
        run_at=erase_at,
        payload={"card_id": card_id},
        max_attempts=max(1, task_attempts),
        dedupe_key=erase_task_key(card_id),
        replace=False,
        now=now,
    )
    totals = store.totals(conn, order_id)
    moved = None
    if totals.covered_minor >= order.total_minor:
        moved = repo.transition(
            conn,
            guild_id=guild_id,
            order_id=order_id,
            allowed_from=(m.PENDING,),
            to=m.PAYMENT_SUBMITTED,
            actor_id=user_id,
            note=None,
            now=now,
            task_attempts=task_attempts,
        )
    current = repo.get_order(conn, order_id)
    assert current is not None
    return Submitted(card_id=card_id, order=current, totals=totals, moved=moved)


def reveal(
    conn: sqlite3.Connection, *, guild_id: int, order_id: int, actor_id: int, now: int
) -> tuple[m.Order, list[store.GiftCard], list[tuple[store.GiftCard, bytes]]]:
    """(order, every card of the order, the waiting cards with their sealed codes). Records who revealed them.
    Only once the cards cover the total (payment_submitted): a card is never redeemed for an order that may still be
    left unpaid."""
    order = _order(conn, guild_id, order_id)
    if order.status != m.PAYMENT_SUBMITTED:
        raise GiftCardError("state", order=order)
    sealed = store.sealed_waiting(conn, order_id)
    if sealed:
        store.mark_revealed(conn, [card.id for card, _ in sealed], actor_id, now)
    return order, store.cards(conn, order_id), sealed


def decide_cards(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    card_ids: Sequence[int] | None,
    redeemed: bool,
    reason: str | None,
    actor_id: int,
    expiry_at: int | None,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Decided:
    """Staff redeemed (``redeemed``) or refused the order's waiting cards (``card_ids`` None: all of them).
    ``expiry_at``: when an order that waits for payment again expires (None: it does not expire)."""
    order = _order(conn, guild_id, order_id)
    if order.status != m.PAYMENT_SUBMITTED:
        raise GiftCardError("state", order=order)
    status, kept_reason = (store.REDEEMED, None) if redeemed else (store.REFUSED, reason)
    changed = store.decide(
        conn, order_id, card_ids=card_ids, status=status, reason=kept_reason, actor_id=actor_id, now=now
    )
    if changed == 0:
        raise GiftCardError("decided", order=order)
    totals = store.totals(conn, order_id)
    moved = None
    if totals.waiting == 0:
        to = m.PAID if totals.redeemed_minor >= order.total_minor else m.PENDING
        moved = repo.transition(
            conn,
            guild_id=guild_id,
            order_id=order_id,
            allowed_from=(m.PAYMENT_SUBMITTED,),
            to=to,
            actor_id=actor_id,
            note=None if to == m.PAID else reason,
            now=now,
            task_attempts=task_attempts,
        )
        if moved is not None and to == m.PENDING and expiry_at is not None:
            task_repo.schedule(
                conn,
                kind=repo.TASK_PAYMENT_EXPIRE,
                run_at=expiry_at,
                payload={"order_id": order_id},
                max_attempts=max(1, task_attempts),
                dedupe_key=repo.expiry_task_key(order_id),
                replace=True,
                now=now,
            )
    current = repo.get_order(conn, order_id)
    assert current is not None
    return Decided(changed=changed, order=current, totals=store.totals(conn, order_id), moved=moved)


def erase_card(conn: sqlite3.Connection, card_id: int, now: int) -> bool:
    """Delete the code of a card kept for ``keep_days`` (staff did not decide in time)."""
    return store.erase(conn, card_id, now)
