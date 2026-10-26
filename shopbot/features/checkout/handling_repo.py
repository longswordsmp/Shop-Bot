"""Synchronous data access for staff handling of orders: claims, internal notes, correction requests, tracking details
and rejection reasons (tables ``order_handling`` and ``order_activity``, migration 0003).

Every write is ONE transaction function with a conditional statement, so a double click or two staff members acting
at the same time never apply anything twice (a second claim finds the order claimed and changes nothing). Each write
also schedules its durable follow-up (``checkout.activity_followup:<activity_id>``: refresh the staff controls, send
the customer notification of a correction request or of tracking details) in the same transaction.

``order_activity`` is internal (shown to staff only). By design, the text of a 'correction' row (the staff message
asking for a correction) and of a 'rejection' row (the reason) is also sent to the customer, and tracking details
are part of the customer's completion notification. Public reads for other features: ``tracking_text`` (customer
views such as /myorders may show tracking details), ``latest_activity``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from shopbot.db.repos import tasks as task_repo
from shopbot.features.checkout import gift_card_store, repo
from shopbot.features.checkout import models as m

CLAIM: Final = "claim"
UNCLAIM: Final = "unclaim"
NOTE: Final = "note"
CORRECTION: Final = "correction"
TRACKING: Final = "tracking"
REJECTION: Final = "rejection"
ACTIVITY_KINDS: Final = (CLAIM, UNCLAIM, NOTE, CORRECTION, TRACKING, REJECTION)

# Longest staff text kept (internal notes, correction messages, tracking details, rejection reasons).
TEXT_MAX: Final = 1000
# Internal notes are shorter: they are listed on the order controls.
NOTE_MAX: Final = 500
# Order statuses in which staff may claim an order, add tracking details, request a correction.
CLAIMABLE_STATUSES: Final = m.OPEN_STATUSES
TRACKING_STATUSES: Final = (m.PAID, m.PROCESSING, m.SHIPPED, m.FULFILLED)
CARRIER_MAX: Final = 100
TRACKING_NUMBER_MAX: Final = 100
TRACKING_URL_MAX: Final = 500
CORRECTION_STATUSES: Final = (m.PENDING,)

TASK_ACTIVITY_FOLLOWUP: Final = "checkout.activity_followup"


def activity_followup_key(activity_id: int) -> str:
    return f"{TASK_ACTIVITY_FOLLOWUP}:{activity_id}"


@dataclass(frozen=True)
class Handling:
    order_id: int
    claimed_by: int | None = None
    claimed_at: int | None = None
    unclaimed_at: int | None = None
    tracking: str | None = None
    tracking_by: int | None = None
    tracking_at: int | None = None
    version: int = 0
    # The shipment (Mark shipped): carrier, tracking number, optional https:// tracking link, when it shipped.
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    shipped_at: int | None = None

    @property
    def shipment(self) -> Shipment | None:
        if self.carrier is None or self.tracking_number is None:
            return None
        return Shipment(self.carrier, self.tracking_number, self.tracking_url)


@dataclass(frozen=True)
class Shipment:
    carrier: str
    tracking_number: str
    tracking_url: str | None = None


@dataclass(frozen=True)
class Activity:
    id: int
    order_id: int
    kind: str
    actor_id: int | None
    text: str | None
    created_at: int


@dataclass(frozen=True)
class StaffData:
    """What the staff views of an order show besides the order itself (one consistent snapshot)."""

    handling: Handling
    events: tuple[m.OrderEvent, ...]
    activity: tuple[Activity, ...]
    # The gift cards submitted for the order (payments.provider = "gift_card"), without their codes.
    gift_cards: tuple[gift_card_store.GiftCard, ...] = ()

    @property
    def notes(self) -> tuple[Activity, ...]:
        return tuple(a for a in self.activity if a.kind == NOTE)


class HandlingError(Exception):
    """reason: 'missing' | 'state' (the order's status does not allow it) | 'claimed' (someone holds the claim) |
    'not_claimed' | 'not_claimer' (someone else holds the claim) | 'text' (empty or too long) | 'url' (a tracking
    link that is not an https:// address). Nothing was written."""

    def __init__(self, reason: str, *, order: m.Order | None = None, claimed_by: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.order = order
        self.claimed_by = claimed_by


@dataclass(frozen=True)
class Change:
    """A committed staff change: the order as it is now, the handling row, and the recorded activity."""

    order: m.Order
    handling: Handling
    activity: Activity


# -- reads ----------------------------------------------------------------------------------------------------------


def _opt(value: Any) -> int | None:
    return None if value is None else int(value)


def _handling(r: sqlite3.Row) -> Handling:
    return Handling(
        order_id=int(r["order_id"]),
        claimed_by=_opt(r["claimed_by"]),
        claimed_at=_opt(r["claimed_at"]),
        unclaimed_at=_opt(r["unclaimed_at"]),
        tracking=None if r["tracking"] is None else str(r["tracking"]),
        tracking_by=_opt(r["tracking_by"]),
        tracking_at=_opt(r["tracking_at"]),
        version=int(r["version"]),
        carrier=None if r["carrier"] is None else str(r["carrier"]),
        tracking_number=None if r["tracking_number"] is None else str(r["tracking_number"]),
        tracking_url=None if r["tracking_url"] is None else str(r["tracking_url"]),
        shipped_at=_opt(r["shipped_at"]),
    )


def _activity(r: sqlite3.Row) -> Activity:
    return Activity(
        id=int(r["id"]),
        order_id=int(r["order_id"]),
        kind=str(r["kind"]),
        actor_id=_opt(r["actor_id"]),
        text=None if r["text"] is None else str(r["text"]),
        created_at=int(r["created_at"]),
    )


def get_handling(conn: sqlite3.Connection, order_id: int) -> Handling:
    row = conn.execute("SELECT * FROM order_handling WHERE order_id = ?", (order_id,)).fetchone()
    return Handling(order_id=order_id) if row is None else _handling(row)


def activity(conn: sqlite3.Connection, order_id: int, kinds: Sequence[str] | None = None) -> list[Activity]:
    rows = conn.execute("SELECT * FROM order_activity WHERE order_id = ? ORDER BY id", (order_id,))
    found = [_activity(r) for r in rows]
    return found if kinds is None else [a for a in found if a.kind in kinds]


def get_activity(conn: sqlite3.Connection, activity_id: int) -> Activity | None:
    row = conn.execute("SELECT * FROM order_activity WHERE id = ?", (activity_id,)).fetchone()
    return None if row is None else _activity(row)


def latest_activity(conn: sqlite3.Connection, order_id: int, kind: str) -> Activity | None:
    row = conn.execute(
        "SELECT * FROM order_activity WHERE order_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (order_id, kind)
    ).fetchone()
    return None if row is None else _activity(row)


def tracking_text(conn: sqlite3.Connection, order_id: int) -> str | None:
    """The order's current tracking details (staff-entered data), or None."""
    return get_handling(conn, order_id).tracking


def staff_data(conn: sqlite3.Connection, order_id: int) -> StaffData:
    return StaffData(
        handling=get_handling(conn, order_id),
        events=tuple(repo.events(conn, order_id)),
        activity=tuple(activity(conn, order_id)),
        gift_cards=tuple(gift_card_store.cards(conn, order_id)),
    )


# -- writes ---------------------------------------------------------------------------------------------------------


def _order(conn: sqlite3.Connection, guild_id: int, order_id: int) -> m.Order:
    order = repo.get_order(conn, order_id)
    if order is None or order.guild_id != guild_id:
        raise HandlingError("missing")
    return order


def _text(value: str | None, maximum: int) -> str:
    text = (value or "").strip()
    if not text or len(text) > maximum:
        raise HandlingError("text")
    return text


def _record(
    conn: sqlite3.Connection,
    order_id: int,
    kind: str,
    actor_id: int | None,
    text: str | None,
    now: int,
    task_attempts: int,
) -> Activity:
    cursor = conn.execute(
        "INSERT INTO order_activity(order_id, kind, actor_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, kind, actor_id, text, now),
    )
    activity_id = int(cursor.lastrowid or 0)
    task_repo.schedule(
        conn,
        kind=TASK_ACTIVITY_FOLLOWUP,
        run_at=now + repo.FOLLOWUP_DELAY_SECONDS,
        payload={"order_id": order_id, "activity_id": activity_id},
        max_attempts=max(1, task_attempts),
        dedupe_key=activity_followup_key(activity_id),
        replace=False,
        now=now,
    )
    found = get_activity(conn, activity_id)
    assert found is not None
    return found


def _change(conn: sqlite3.Connection, order_id: int, recorded: Activity) -> Change:
    order = repo.get_order(conn, order_id)
    assert order is not None
    return Change(order=order, handling=get_handling(conn, order_id), activity=recorded)


def claim(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Change:
    """Claim an open order for ``actor_id``. Raises ``HandlingError('claimed')`` when anyone (also the actor) already
    holds the claim: the conditional upsert changes nothing then."""
    order = _order(conn, guild_id, order_id)
    if order.status not in CLAIMABLE_STATUSES:
        raise HandlingError("state", order=order)
    cur = conn.execute(
        "INSERT INTO order_handling(order_id, claimed_by, claimed_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(order_id) DO UPDATE SET claimed_by = excluded.claimed_by, claimed_at = excluded.claimed_at, "
        "version = version + 1, updated_at = excluded.updated_at WHERE order_handling.claimed_by IS NULL",
        (order_id, actor_id, now, now),
    )
    if cur.rowcount != 1:
        raise HandlingError("claimed", order=order, claimed_by=get_handling(conn, order_id).claimed_by)
    return _change(conn, order_id, _record(conn, order_id, CLAIM, actor_id, None, now, task_attempts))


def unclaim(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int,
    override: bool,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Change:
    """Release the claim. Only the member holding it may, unless ``override`` (an admin)."""
    order = _order(conn, guild_id, order_id)
    current = get_handling(conn, order_id)
    if current.claimed_by is None:
        raise HandlingError("not_claimed", order=order)
    if current.claimed_by != actor_id and not override:
        raise HandlingError("not_claimer", order=order, claimed_by=current.claimed_by)
    cur = conn.execute(
        "UPDATE order_handling SET claimed_by = NULL, claimed_at = NULL, unclaimed_at = ?, version = version + 1, "
        "updated_at = ? WHERE order_id = ? AND claimed_by = ?",
        (now, now, order_id, current.claimed_by),
    )
    if cur.rowcount != 1:  # unreachable inside the transaction; kept as a guard
        raise HandlingError("not_claimed", order=order)
    return _change(conn, order_id, _record(conn, order_id, UNCLAIM, actor_id, None, now, task_attempts))


def add_note(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int,
    text: str,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Change:
    """An internal note (any status). Never shown to the customer."""
    _order(conn, guild_id, order_id)
    note = _text(text, NOTE_MAX)
    return _change(conn, order_id, _record(conn, order_id, NOTE, actor_id, note, now, task_attempts))


def request_correction(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int,
    text: str,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Change:
    """Record a correction request for an order waiting for payment (it stays waiting). Its follow-up sends the
    customer the configured wording with the staff message."""
    order = _order(conn, guild_id, order_id)
    if order.status not in CORRECTION_STATUSES:
        raise HandlingError("state", order=order)
    message = _text(text, TEXT_MAX)
    conn.execute(
        "UPDATE orders SET version = version + 1, updated_at = ? WHERE id = ? AND status = ?",
        (now, order_id, m.PENDING),
    )
    return _change(conn, order_id, _record(conn, order_id, CORRECTION, actor_id, message, now, task_attempts))


def set_tracking(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    actor_id: int,
    text: str,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> Change:
    """Store (or replace) the tracking details of a paid, in-progress, shipped or delivered order. Its follow-up tells
    the customer once the order has shipped (``service.notify_activity``)."""
    order = _order(conn, guild_id, order_id)
    if order.status not in TRACKING_STATUSES:
        raise HandlingError("state", order=order)
    tracking = _text(text, TEXT_MAX)
    conn.execute(
        "INSERT INTO order_handling(order_id, tracking, tracking_by, tracking_at, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(order_id) DO UPDATE SET tracking = excluded.tracking, tracking_by = excluded.tracking_by, "
        "tracking_at = excluded.tracking_at, version = version + 1, updated_at = excluded.updated_at",
        (order_id, tracking, actor_id, now, now),
    )
    return _change(conn, order_id, _record(conn, order_id, TRACKING, actor_id, tracking, now, task_attempts))


def ship_order(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    allowed_from: Sequence[str],
    actor_id: int,
    carrier: str,
    tracking_number: str,
    tracking_url: str | None,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> repo.Transition | None:
    """Mark the order shipped with its shipment, in ONE transaction: the transition to 'shipped' and the carrier,
    tracking number and link the shipped notification shows (also kept as the order's tracking details, which
    /myorders and the order record show). None (nothing written) when the order is no longer in ``allowed_from``."""
    carrier_text = _text(carrier, CARRIER_MAX)
    number_text = _text(tracking_number, TRACKING_NUMBER_MAX)
    url = (tracking_url or "").strip() or None
    if url is not None and (
        len(url) > TRACKING_URL_MAX or not url.startswith("https://") or any(c.isspace() for c in url)
    ):
        raise HandlingError("url")
    moved = repo.transition(
        conn,
        guild_id=guild_id,
        order_id=order_id,
        allowed_from=tuple(s for s in m.TRANSITIONS[m.SHIPPED] if s in allowed_from),
        to=m.SHIPPED,
        actor_id=actor_id,
        note=None,
        now=now,
        task_attempts=task_attempts,
    )
    if moved is None:
        return None
    tracking = f"{carrier_text} · {number_text}"
    conn.execute(
        "INSERT INTO order_handling(order_id, tracking, tracking_by, tracking_at, carrier, tracking_number, "
        "tracking_url, shipped_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(order_id) DO UPDATE SET tracking = excluded.tracking, tracking_by = excluded.tracking_by, "
        "tracking_at = excluded.tracking_at, carrier = excluded.carrier, tracking_number = excluded.tracking_number, "
        "tracking_url = excluded.tracking_url, shipped_at = excluded.shipped_at, version = version + 1, "
        "updated_at = excluded.updated_at",
        (order_id, tracking, actor_id, now, carrier_text, number_text, url, now, now),
    )
    conn.execute(
        "INSERT INTO order_activity(order_id, kind, actor_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, TRACKING, actor_id, tracking, now),
    )
    return moved


def reject_with_reason(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    allowed_from: Sequence[str],
    actor_id: int,
    reason: str,
    now: int,
    task_attempts: int = repo.DEFAULT_TASK_ATTEMPTS,
) -> tuple[repo.Transition, Activity] | None:
    """Reject an unpaid order with a reason for the customer, in ONE transaction: the state machine's transition
    (``repo.transition``: stock released, credit returned, follow-up scheduled) and the 'rejection' activity the
    notification reads. None (nothing written) when the order is no longer in ``allowed_from``."""
    text = _text(reason, TEXT_MAX)
    moved = repo.transition(
        conn,
        guild_id=guild_id,
        order_id=order_id,
        allowed_from=tuple(s for s in m.TRANSITIONS[m.REJECTED] if s in allowed_from),
        to=m.REJECTED,
        actor_id=actor_id,
        note=text,
        now=now,
        task_attempts=task_attempts,
    )
    if moved is None:
        return None
    cursor = conn.execute(
        "INSERT INTO order_activity(order_id, kind, actor_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, REJECTION, actor_id, text, now),
    )
    recorded = get_activity(conn, int(cursor.lastrowid or 0))
    assert recorded is not None
    return moved, recorded


@dataclass(frozen=True)
class NotificationData:
    """Staff-entered data a status notification carries: the rejection reason, the tracking details, the shipment."""

    reason: str | None = None
    tracking: str | None = None
    shipment: Shipment | None = None


def notification_data(conn: sqlite3.Connection, order_id: int, status: str) -> NotificationData:
    if status == m.REJECTED:
        rejection = latest_activity(conn, order_id, REJECTION)
        return NotificationData(reason=rejection.text if rejection is not None else None)
    if status == m.SHIPPED:
        handling = get_handling(conn, order_id)
        return NotificationData(tracking=handling.tracking, shipment=handling.shipment)
    if status == m.FULFILLED:
        return NotificationData(tracking=tracking_text(conn, order_id))
    return NotificationData()
