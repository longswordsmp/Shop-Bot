"""Review records (rows of ``reviews``) and the read-only order lookups the reviews feature needs.

Synchronous, connection-first functions. Writers run inside ``app.db.transaction``; every state change is a
conditional UPDATE (``WHERE status IN (...)``) and ``reviews.order_id`` is UNIQUE, so repeated or concurrent
calls never apply twice.

Review states::

    prompted --rate--> rated --comment--> (submitted) --publish--> published
        \\                \\
         +---decline------+---decline / staff remove--> declined   (published --staff remove--> declined)

A review is *submitted* when it is ``rated`` and ``comment IS NOT NULL`` (an empty string means "submitted
without a comment"). ``comment IS NULL`` while ``rated`` means the comment form has not been submitted yet, so
the customer may still pick another rating. Submitted reviews are never lost: they stay ``rated`` until the
publication in the reviews channel succeeds.

Orders are read directly from the ``orders`` table (owned by checkout); this module never writes them.

Publication claim: before a review is posted in the reviews channel, ``claim_publication`` stores the kv_state key
``reviews.publishing:<review_id>``; recording the posted message (``record_publication``/``adopt_publication``) or
removing the review clears it. A claim that is still there on the next attempt means the earlier post may exist
although its message was never recorded (the process stopped, or Discord created the message but the answer was
lost).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shopbot.db.repos import state

PROMPTED = "prompted"
RATED = "rated"
PUBLISHED = "published"
DECLINED = "declined"
STATUSES = (PROMPTED, RATED, PUBLISHED, DECLINED)

ORDER_FULFILLED = "fulfilled"
TICKET_DELETED = "deleted"


@dataclass(frozen=True)
class Order:
    """The order fields reviews rely on (plus its order-channel, when the ticket still has one)."""

    id: int
    guild_id: int
    number: int
    user_id: int
    status: str
    ticket_id: int | None
    channel_id: int | None

    @property
    def fulfilled(self) -> bool:
        return self.status == ORDER_FULFILLED


@dataclass(frozen=True)
class Review:
    id: int
    guild_id: int
    order_id: int
    order_number: int
    user_id: int
    status: str
    rating: int | None
    comment: str | None
    prompt_message_id: int | None
    prompt_channel_id: int | None
    published_message_id: int | None
    created_at: int
    updated_at: int

    @property
    def submitted(self) -> bool:
        """Rated and the comment step is finished, but not published yet."""
        return self.status == RATED and self.comment is not None

    @property
    def open(self) -> bool:
        """Still waiting for the customer (a rating or the comment)."""
        return self.status == PROMPTED or (self.status == RATED and self.comment is None)


class Outcome(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    ALREADY = "already"  # submitted or published
    CLOSED = "closed"  # declined (by the customer or removed by staff)
    EXPIRED = "expired"  # the order is no longer fulfilled
    NOT_RATED = "not_rated"  # comment without a rating


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    review: Review | None = None


# -- reads ---------------------------------------------------------------------------------------------

_ORDER_SELECT = (
    "SELECT o.id, o.guild_id, o.number, o.user_id, o.status, o.ticket_id, "
    "CASE WHEN t.status = 'deleted' THEN NULL ELSE t.channel_id END AS channel_id "
    "FROM orders o LEFT JOIN tickets t ON t.id = o.ticket_id"
)
_REVIEW_SELECT = "SELECT r.*, o.number AS order_number FROM reviews r JOIN orders o ON o.id = r.order_id"


def _order(row: sqlite3.Row) -> Order:
    return Order(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        number=int(row["number"]),
        user_id=int(row["user_id"]),
        status=str(row["status"]),
        ticket_id=None if row["ticket_id"] is None else int(row["ticket_id"]),
        channel_id=None if row["channel_id"] is None else int(row["channel_id"]),
    )


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _review(row: sqlite3.Row) -> Review:
    return Review(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        order_id=int(row["order_id"]),
        order_number=int(row["order_number"]),
        user_id=int(row["user_id"]),
        status=str(row["status"]),
        rating=_opt_int(row["rating"]),
        comment=None if row["comment"] is None else str(row["comment"]),
        prompt_message_id=_opt_int(row["prompt_message_id"]),
        prompt_channel_id=_opt_int(row["prompt_channel_id"]),
        published_message_id=_opt_int(row["published_message_id"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def get_order(conn: sqlite3.Connection, order_id: int) -> Order | None:
    row = conn.execute(f"{_ORDER_SELECT} WHERE o.id = ?", (order_id,)).fetchone()
    return None if row is None else _order(row)


def get_order_by_number(conn: sqlite3.Connection, guild_id: int, number: int) -> Order | None:
    row = conn.execute(f"{_ORDER_SELECT} WHERE o.guild_id = ? AND o.number = ?", (guild_id, number)).fetchone()
    return None if row is None else _order(row)


def get_review(conn: sqlite3.Connection, review_id: int) -> Review | None:
    row = conn.execute(f"{_REVIEW_SELECT} WHERE r.id = ?", (review_id,)).fetchone()
    return None if row is None else _review(row)


def get_review_by_order(conn: sqlite3.Connection, order_id: int) -> Review | None:
    row = conn.execute(f"{_REVIEW_SELECT} WHERE r.order_id = ?", (order_id,)).fetchone()
    return None if row is None else _review(row)


def list_reviews(
    conn: sqlite3.Connection, guild_id: int, *, status: str | None = None, limit: int = 25
) -> list[Review]:
    """Most recently updated first."""
    if status is None:
        rows = conn.execute(
            f"{_REVIEW_SELECT} WHERE r.guild_id = ? ORDER BY r.updated_at DESC, r.id DESC LIMIT ?", (guild_id, limit)
        )
    else:
        rows = conn.execute(
            f"{_REVIEW_SELECT} WHERE r.guild_id = ? AND r.status = ? ORDER BY r.updated_at DESC, r.id DESC LIMIT ?",
            (guild_id, status, limit),
        )
    return [_review(r) for r in rows]


def submitted_review_ids(conn: sqlite3.Connection, guild_id: int) -> list[int]:
    """Reviews the customer finished that are not published yet (oldest first)."""
    rows = conn.execute(
        "SELECT id FROM reviews WHERE guild_id = ? AND status = 'rated' AND comment IS NOT NULL ORDER BY id",
        (guild_id,),
    )
    return [int(r["id"]) for r in rows]


# -- writes --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Started:
    review: Review
    order: Order
    created: bool


def start_review(conn: sqlite3.Connection, *, order_id: int, guild_id: int, now: int) -> Started | None:
    """Create the review request of a fulfilled order (idempotent: the existing row is returned).

    Returns None when the order does not exist, belongs to another server, or is not fulfilled.
    """
    order = get_order(conn, order_id)
    if order is None or order.guild_id != guild_id or not order.fulfilled:
        return None
    created = (
        conn.execute(
            "INSERT INTO reviews(guild_id, order_id, user_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'prompted', ?, ?) ON CONFLICT(order_id) DO NOTHING",
            (guild_id, order_id, order.user_id, now, now),
        ).rowcount
        > 0
    )
    review = get_review_by_order(conn, order_id)
    if review is None:  # pragma: no cover - the row was inserted or already existed
        raise RuntimeError(f"review of order {order_id} vanished")
    return Started(review=review, order=order, created=created)


def set_prompt_message(conn: sqlite3.Connection, review_id: int, *, channel_id: int, message_id: int, now: int) -> None:
    conn.execute(
        "UPDATE reviews SET prompt_channel_id = ?, prompt_message_id = ?, updated_at = ? WHERE id = ?",
        (channel_id, message_id, now, review_id),
    )


def _check(conn: sqlite3.Connection, review_id: int, user_id: int) -> tuple[Review | None, Result | None]:
    review = get_review(conn, review_id)
    if review is None:
        return None, Result(Outcome.NOT_FOUND)
    if review.user_id != user_id:
        return review, Result(Outcome.FORBIDDEN, review)
    if review.status == PUBLISHED or review.submitted:
        return review, Result(Outcome.ALREADY, review)
    if review.status == DECLINED:
        return review, Result(Outcome.CLOSED, review)
    return review, None


def _order_expired(conn: sqlite3.Connection, review: Review) -> bool:
    order = get_order(conn, review.order_id)
    return order is None or not order.fulfilled


def rate(conn: sqlite3.Connection, review_id: int, *, user_id: int, rating: int, submit: bool, now: int) -> Result:
    """Record (or change) the rating of an open review. ``submit`` finishes the review without a comment."""
    review, refusal = _check(conn, review_id, user_id)
    if refusal is not None:
        return refusal
    assert review is not None
    if _order_expired(conn, review):
        return Result(Outcome.EXPIRED, review)
    updated = conn.execute(
        "UPDATE reviews SET status = 'rated', rating = ?, comment = ?, updated_at = ? "
        "WHERE id = ? AND status IN ('prompted', 'rated') AND comment IS NULL",
        (rating, "" if submit else None, now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - guarded by _check inside the same transaction
        return Result(Outcome.ALREADY, review)
    return Result(Outcome.OK, get_review(conn, review_id))


def submit_comment(conn: sqlite3.Connection, review_id: int, *, user_id: int, comment: str, now: int) -> Result:
    """Finish a rated review with its comment ('' = no comment). Only the first submission counts."""
    review, refusal = _check(conn, review_id, user_id)
    if refusal is not None:
        return refusal
    assert review is not None
    if review.status != RATED or review.rating is None:
        return Result(Outcome.NOT_RATED, review)
    if _order_expired(conn, review):
        return Result(Outcome.EXPIRED, review)
    updated = conn.execute(
        "UPDATE reviews SET comment = ?, updated_at = ? WHERE id = ? AND status = 'rated' AND comment IS NULL",
        (comment, now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - guarded by _check inside the same transaction
        return Result(Outcome.ALREADY, review)
    return Result(Outcome.OK, get_review(conn, review_id))


def decline(conn: sqlite3.Connection, review_id: int, *, user_id: int, now: int) -> Result:
    """The customer declines an open review. Declining twice is reported as CLOSED."""
    review, refusal = _check(conn, review_id, user_id)
    if refusal is not None:
        return refusal
    updated = conn.execute(
        "UPDATE reviews SET status = 'declined', updated_at = ? "
        "WHERE id = ? AND (status = 'prompted' OR (status = 'rated' AND comment IS NULL))",
        (now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - guarded by _check inside the same transaction
        return Result(Outcome.ALREADY, review)
    return Result(Outcome.OK, get_review(conn, review_id))


def publish_claim_key(review_id: int) -> str:
    return f"reviews.publishing:{review_id}"


def claim_publication(conn: sqlite3.Connection, review_id: int, *, channel_id: int, now: int) -> bool | None:
    """Before posting a submitted review: store its publication claim (the value is the channel it is posted in).

    Returns None when the review is no longer waiting for publication (nothing stored), True when the claim of an
    earlier attempt is still there (that post may exist), and False when this is a first attempt.
    """
    review = get_review(conn, review_id)
    if review is None or not review.submitted:
        return None
    key = publish_claim_key(review_id)
    if state.kv_get(conn, key) is not None:
        return True
    state.kv_set(conn, key, str(channel_id), now)
    return False


def recorded_message_ids(conn: sqlite3.Connection, message_ids: Sequence[int]) -> set[int]:
    """Those of ``message_ids`` that are the recorded publication of some review."""
    return {
        message_id
        for message_id in message_ids
        if conn.execute("SELECT 1 FROM reviews WHERE published_message_id = ? LIMIT 1", (message_id,)).fetchone()
    }


def record_publication(conn: sqlite3.Connection, review_id: int, *, message_id: int, now: int) -> bool:
    """``mark_published`` and clear the publication claim (in the caller's transaction)."""
    stored = mark_published(conn, review_id, message_id=message_id, now=now)
    state.kv_delete(conn, publish_claim_key(review_id))
    return stored


def adopt_publication(conn: sqlite3.Connection, review_id: int, *, message_ids: Sequence[int], now: int) -> int | None:
    """Record the first of ``message_ids`` (posts showing exactly this review, newest first) that is not already the
    publication of a review as this review's message. Returns its id, or None when none can be adopted."""
    taken = recorded_message_ids(conn, message_ids)
    for message_id in message_ids:
        if message_id in taken:
            continue
        if record_publication(conn, review_id, message_id=message_id, now=now):
            return message_id
        return None  # pragma: no cover - the review changed meanwhile (removal also takes the review lock)
    return None


def mark_published(conn: sqlite3.Connection, review_id: int, *, message_id: int, now: int) -> bool:
    """Submitted -> published. False when the review changed meanwhile (e.g. staff removed it)."""
    return (
        conn.execute(
            "UPDATE reviews SET status = 'published', published_message_id = ?, updated_at = ? "
            "WHERE id = ? AND status = 'rated' AND comment IS NOT NULL",
            (message_id, now, review_id),
        ).rowcount
        > 0
    )


def remove(conn: sqlite3.Connection, review_id: int, *, now: int) -> Review | None:
    """Staff removal: any state -> declined. Returns the review as it was before, or None if already declined."""
    review = get_review(conn, review_id)
    if review is None or review.status == DECLINED:
        return None
    updated = conn.execute(
        "UPDATE reviews SET status = 'declined', updated_at = ? WHERE id = ? AND status = ?",
        (now, review_id, review.status),
    ).rowcount
    if not updated:
        return None
    state.kv_delete(conn, publish_claim_key(review_id))  # a declined review is never published
    return review
