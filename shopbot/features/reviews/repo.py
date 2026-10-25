"""Review records (rows of ``reviews``) and the read-only order lookups the reviews feature needs.

Synchronous, connection-first functions. Writers run inside ``app.db.transaction``; every state change is a
conditional UPDATE (``WHERE status IN (...)``) and ``reviews.order_id`` is UNIQUE, so repeated or concurrent
calls never apply twice.

Review states::

    prompted --rate--> rated --comment--> (submitted) --publish--> published
        \\                \\        \\
         \\                \\        +--(reviews.require_approval)--> pending_approval --approve--> (submitted)
          \\                \\                                             \\
           +---decline------+---decline--> declined                     +--reject--> rejected
                               (any state --staff removal with /review remove--> declined)

A review is *submitted* when it is ``rated``, ``comment IS NOT NULL`` (an empty string means "submitted without
a comment") and its comment is approved (``approval`` 'automatic' when no staff approval is required, 'approved'
after a staff approval). ``comment IS NULL`` while ``rated`` means the comment form has not been submitted yet, so
the customer may still pick another rating. Submitted reviews are never lost: they stay ``rated`` until the
publication in the reviews channel succeeds.

Approval (``reviews.require_approval``): the customer's submission goes to ``pending_approval`` instead (same
conditional UPDATE, so it happens once). A staff decision is ``pending_approval -> rated (approval 'approved')``
or ``pending_approval -> rejected``, exactly once (conditional UPDATE), recorded with ``decided_by``/``decided_at``
(and the optional rejection reason), with the durable decision follow-up task scheduled in the same transaction.
The staff approval message is recorded in ``approval_channel_id``/``approval_message_id`` (UNIQUE) with the same
claim protocol as the publication (kv_state ``reviews.approval_posting:<review_id>``).

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
from shopbot.db.repos import tasks as task_repo

PROMPTED = "prompted"
RATED = "rated"
PENDING_APPROVAL = "pending_approval"
PUBLISHED = "published"
REJECTED = "rejected"
DECLINED = "declined"
STATUSES = (PROMPTED, RATED, PENDING_APPROVAL, PUBLISHED, REJECTED, DECLINED)

# ``approval`` values: how the comment was approved (None = not decided yet). REJECTED is shared with the status.
AUTOMATIC = "automatic"
APPROVED = "approved"
APPROVED_VALUES = (AUTOMATIC, APPROVED)

# Durable follow-up of a staff decision (approval message, publication or rejection notice, audit); scheduled in
# the decision's transaction and cancelled once the in-process follow-up completed.
DECISION_TASK = "reviews.decided"
DECISION_FOLLOWUP_DELAY_SECONDS = 120
REASON_MAX = 500
AUDIT_TARGET = "review"

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
    approval: str | None = None
    decided_by: int | None = None
    decided_at: int | None = None
    decision_reason: str | None = None
    approval_channel_id: int | None = None
    approval_message_id: int | None = None

    @property
    def submitted(self) -> bool:
        """Rated, the comment step is finished and the comment is approved, but not published yet."""
        return self.status == RATED and self.comment is not None and self.approval in APPROVED_VALUES

    @property
    def pending_approval(self) -> bool:
        """Submitted by the customer and waiting for a staff decision (reviews.require_approval)."""
        return self.status == PENDING_APPROVAL

    @property
    def finished(self) -> bool:
        """The customer's part is done (submitted, waiting for approval, published or rejected)."""
        return self.submitted or self.status in (PENDING_APPROVAL, PUBLISHED, REJECTED)

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
    NOT_PENDING = "not_pending"  # a staff decision on a review that no longer waits for approval


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
        approval=None if row["approval"] is None else str(row["approval"]),
        decided_by=_opt_int(row["decided_by"]),
        decided_at=_opt_int(row["decided_at"]),
        decision_reason=None if row["decision_reason"] is None else str(row["decision_reason"]),
        approval_channel_id=_opt_int(row["approval_channel_id"]),
        approval_message_id=_opt_int(row["approval_message_id"]),
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
    """Reviews the customer finished (and whose comment is approved) that are not published yet (oldest first)."""
    rows = conn.execute(
        "SELECT id FROM reviews WHERE guild_id = ? AND status = 'rated' AND comment IS NOT NULL "
        "AND approval IN ('automatic', 'approved') ORDER BY id",
        (guild_id,),
    )
    return [int(r["id"]) for r in rows]


def pending_approvals(conn: sqlite3.Connection, guild_id: int) -> list[tuple[int, int | None]]:
    """(review id, approval message id or None) of every review waiting for a staff decision (oldest first)."""
    rows = conn.execute(
        "SELECT id, approval_message_id FROM reviews WHERE guild_id = ? AND status = 'pending_approval' ORDER BY id",
        (guild_id,),
    )
    return [(int(r["id"]), _opt_int(r["approval_message_id"])) for r in rows]


def audited(conn: sqlite3.Connection, action: str, review_id: int) -> bool:
    """Whether the audit event ``action`` was recorded for the review (a re-run follow-up must not repeat it)."""
    row = conn.execute(
        "SELECT 1 FROM audit_events WHERE action = ? AND target_type = ? AND target_id = ? LIMIT 1",
        (action, AUDIT_TARGET, str(review_id)),
    ).fetchone()
    return row is not None


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
    if review.finished:
        return review, Result(Outcome.ALREADY, review)
    if review.status == DECLINED:
        return review, Result(Outcome.CLOSED, review)
    return review, None


def _order_expired(conn: sqlite3.Connection, review: Review) -> bool:
    order = get_order(conn, review.order_id)
    return order is None or not order.fulfilled


def _submission(approval_required: bool) -> tuple[str, str | None]:
    """(status, approval) of a review the customer just finished."""
    return (PENDING_APPROVAL, None) if approval_required else (RATED, AUTOMATIC)


def rate(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    user_id: int,
    rating: int,
    submit: bool,
    now: int,
    approval_required: bool = False,
) -> Result:
    """Record (or change) the rating of an open review. ``submit`` finishes the review without a comment (it then
    waits for a staff decision when ``approval_required``, else it is approved automatically)."""
    review, refusal = _check(conn, review_id, user_id)
    if refusal is not None:
        return refusal
    assert review is not None
    if _order_expired(conn, review):
        return Result(Outcome.EXPIRED, review)
    status, approval = _submission(approval_required) if submit else (RATED, None)
    updated = conn.execute(
        "UPDATE reviews SET status = ?, rating = ?, comment = ?, approval = ?, updated_at = ? "
        "WHERE id = ? AND status IN ('prompted', 'rated') AND comment IS NULL",
        (status, rating, "" if submit else None, approval, now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - guarded by _check inside the same transaction
        return Result(Outcome.ALREADY, review)
    return Result(Outcome.OK, get_review(conn, review_id))


def submit_comment(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    user_id: int,
    comment: str,
    now: int,
    approval_required: bool = False,
) -> Result:
    """Finish a rated review with its comment ('' = no comment). Only the first submission counts. With
    ``approval_required`` the review then waits for a staff decision, else it is approved automatically."""
    review, refusal = _check(conn, review_id, user_id)
    if refusal is not None:
        return refusal
    assert review is not None
    if review.status != RATED or review.rating is None:
        return Result(Outcome.NOT_RATED, review)
    if _order_expired(conn, review):
        return Result(Outcome.EXPIRED, review)
    status, approval = _submission(approval_required)
    updated = conn.execute(
        "UPDATE reviews SET status = ?, comment = ?, approval = ?, updated_at = ? "
        "WHERE id = ? AND status = 'rated' AND comment IS NULL",
        (status, comment, approval, now, review_id),
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


def _claim(conn: sqlite3.Connection, key: str, channel_id: int, now: int) -> bool:
    if state.kv_get(conn, key) is not None:
        return True
    state.kv_set(conn, key, str(channel_id), now)
    return False


def claim_publication(conn: sqlite3.Connection, review_id: int, *, channel_id: int, now: int) -> bool | None:
    """Before posting a submitted review: store its publication claim (the value is the channel it is posted in).

    Returns None when the review is no longer waiting for publication (nothing stored), True when the claim of an
    earlier attempt is still there (that post may exist), and False when this is a first attempt.
    """
    review = get_review(conn, review_id)
    if review is None or not review.submitted:
        return None
    return _claim(conn, publish_claim_key(review_id), channel_id, now)


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
            "WHERE id = ? AND status = 'rated' AND comment IS NOT NULL AND approval IN ('automatic', 'approved')",
            (message_id, now, review_id),
        ).rowcount
        > 0
    )


# -- staff approval --------------------------------------------------------------------------------------------


def decision_key(review_id: int) -> str:
    """Dedupe key of the durable decision follow-up task."""
    return f"{DECISION_TASK}:{review_id}"


def approval_claim_key(review_id: int) -> str:
    return f"reviews.approval_posting:{review_id}"


def _schedule_followup(conn: sqlite3.Connection, review_id: int, *, now: int, task_attempts: int) -> None:
    task_repo.schedule(
        conn,
        kind=DECISION_TASK,
        run_at=now + DECISION_FOLLOWUP_DELAY_SECONDS,
        payload={"review_id": review_id},
        max_attempts=task_attempts,
        dedupe_key=decision_key(review_id),
        replace=True,
        now=now,
    )


def approve(conn: sqlite3.Connection, review_id: int, *, actor_id: int, now: int, task_attempts: int) -> Result:
    """Staff approval: ``pending_approval -> rated`` (submitted, approval 'approved'), exactly once.

    Refused (nothing written) when the review no longer waits for approval (NOT_PENDING) or its order is no longer
    fulfilled (EXPIRED: it could not be shown as a verified review). Schedules the decision follow-up task.
    """
    review = get_review(conn, review_id)
    if review is None:
        return Result(Outcome.NOT_FOUND)
    if not review.pending_approval:
        return Result(Outcome.NOT_PENDING, review)
    if _order_expired(conn, review):
        return Result(Outcome.EXPIRED, review)
    updated = conn.execute(
        "UPDATE reviews SET status = 'rated', approval = 'approved', decided_by = ?, decided_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'pending_approval'",
        (actor_id, now, now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - checked above inside the same transaction
        return Result(Outcome.NOT_PENDING, review)
    _schedule_followup(conn, review_id, now=now, task_attempts=task_attempts)
    return Result(Outcome.OK, get_review(conn, review_id))


def reject(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    actor_id: int,
    reason: str | None,
    now: int,
    task_attempts: int,
) -> Result:
    """Staff rejection: ``pending_approval -> rejected`` (never published), exactly once. Schedules the decision
    follow-up task. ``reason`` (optional, at most REASON_MAX characters) is stored with the decision."""
    if reason is not None and not 1 <= len(reason) <= REASON_MAX:
        raise ValueError("reason must be 1-500 characters")
    review = get_review(conn, review_id)
    if review is None:
        return Result(Outcome.NOT_FOUND)
    if not review.pending_approval:
        return Result(Outcome.NOT_PENDING, review)
    updated = conn.execute(
        "UPDATE reviews SET status = 'rejected', approval = 'rejected', decided_by = ?, decided_at = ?, "
        "decision_reason = ?, updated_at = ? WHERE id = ? AND status = 'pending_approval'",
        (actor_id, now, reason, now, review_id),
    ).rowcount
    if not updated:  # pragma: no cover - checked above inside the same transaction
        return Result(Outcome.NOT_PENDING, review)
    _schedule_followup(conn, review_id, now=now, task_attempts=task_attempts)
    return Result(Outcome.OK, get_review(conn, review_id))


def cancel_followup(conn: sqlite3.Connection, review_id: int, *, now: int) -> int:
    return task_repo.cancel(conn, dedupe_key=decision_key(review_id), now=now)


def claim_approval_post(conn: sqlite3.Connection, review_id: int, *, channel_id: int, now: int) -> bool | None:
    """Before posting the approval message of a pending review: store its claim (see ``claim_publication``).

    Returns None when the review no longer waits for approval or its approval message is already recorded.
    """
    review = get_review(conn, review_id)
    if review is None or not review.pending_approval or review.approval_message_id is not None:
        return None
    return _claim(conn, approval_claim_key(review_id), channel_id, now)


def recorded_approval_ids(conn: sqlite3.Connection, message_ids: Sequence[int]) -> set[int]:
    """Those of ``message_ids`` that are the recorded approval message of some review."""
    return {
        message_id
        for message_id in message_ids
        if conn.execute("SELECT 1 FROM reviews WHERE approval_message_id = ? LIMIT 1", (message_id,)).fetchone()
    }


def record_approval_message(
    conn: sqlite3.Connection, review_id: int, *, channel_id: int, message_id: int, now: int
) -> bool:
    """Record the posted approval message and clear the claim. False when the review no longer waits for approval
    or already has one (nothing stored)."""
    state.kv_delete(conn, approval_claim_key(review_id))
    return (
        conn.execute(
            "UPDATE reviews SET approval_channel_id = ?, approval_message_id = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending_approval' AND approval_message_id IS NULL",
            (channel_id, message_id, now, review_id),
        ).rowcount
        > 0
    )


def adopt_approval_message(
    conn: sqlite3.Connection, review_id: int, *, channel_id: int, message_ids: Sequence[int], now: int
) -> int | None:
    """Record the first of ``message_ids`` (posts showing exactly this review's approval request, newest first) that
    is not already some review's approval message. Returns its id, or None when none can be adopted."""
    taken = recorded_approval_ids(conn, message_ids)
    for message_id in message_ids:
        if message_id in taken:
            continue
        if record_approval_message(conn, review_id, channel_id=channel_id, message_id=message_id, now=now):
            return message_id
        return None  # pragma: no cover - the review changed meanwhile (decisions also take the review lock)
    return None


def forget_approval_message(conn: sqlite3.Connection, review_id: int, *, message_id: int, now: int) -> bool:
    """The recorded approval message was deleted in Discord: clear it so a new one can be posted."""
    return (
        conn.execute(
            "UPDATE reviews SET approval_channel_id = NULL, approval_message_id = NULL, updated_at = ? "
            "WHERE id = ? AND approval_message_id = ?",
            (now, review_id, message_id),
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
    state.kv_delete(conn, approval_claim_key(review_id))
    return review
