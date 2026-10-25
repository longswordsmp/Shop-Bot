"""Synchronous data access for checkout: sessions, carts, orders, the order state machine, refunds.

Every function takes the connection first and runs on the database thread (``app.db.run`` for reads,
``app.db.transaction`` for writes). Writes check and change state in ONE transaction function, using conditional
UPDATEs (``WHERE status IN (...)``) and the schema's UNIQUE constraints (one order per session, one reservation
per order line, one credit debit/return per order), so duplicate clicks and concurrent requests never apply
anything twice. An exception raised inside a transaction function rolls back every write it made (including
stock reservations and credit debits).

Durable follow-ups: work that must happen after a change (Discord messages, notifications, closing the order
channel) is scheduled as a deduplicated task IN THE SAME TRANSACTION as the change (``scheduled_tasks``), so a crash
or an error after the commit can never lose it: ``checkout.after_submit:<order_id>`` (staff controls and details,
payment start), ``checkout.after_transition:<order_id>:<status>`` (order views, notification, channel close, review
request), ``checkout.ticket_check:<ticket_id>`` (closing an order channel that can no longer be used), plus the
payment reminder/expiry tasks of a new order. The process normally performs the same steps right after the commit
and then cancels the follow-up; the task only runs when that did not complete.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shopbot.config.settings import CheckoutSettings, Settings
from shopbot.core.money import MAX_AMOUNT_MINOR
from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import flow
from shopbot.features.checkout import models as m
from shopbot.features.checkout.totals import Totals, compute_totals
from shopbot.features.credits import repo as credits_repo

# ---------------------------------------------------------------------------------------------------
# Scheduled tasks (kinds and dedupe keys; the handlers are in ``tasks``)
# ---------------------------------------------------------------------------------------------------

TASK_SESSION_EXPIRE = "checkout.session_expire"
TASK_PAYMENT_REMINDER = "checkout.payment_reminder"
TASK_PAYMENT_EXPIRE = "checkout.payment_expire"
TASK_AFTER_SUBMIT = "checkout.after_submit"
TASK_AFTER_TRANSITION = "checkout.after_transition"
TASK_TICKET_CHECK = "checkout.ticket_check"
TASK_SESSION_WARNING = "checkout.session_warning"
# Follow-up tasks run this long after the change they follow, only if the process did not finish the same steps
# right after the commit (it cancels the task then). The delay keeps the two from running at the same time.
FOLLOWUP_DELAY_SECONDS = 60
# Attempts of a task scheduled inside a transaction (callers pass the scheduler's configured value).
DEFAULT_TASK_ATTEMPTS = 5


def session_task_key(session_id: int) -> str:
    return f"checkout.session:{session_id}"


def session_warning_key(session_id: int) -> str:
    return f"checkout.session_warning:{session_id}"


def reminder_task_key(order_id: int) -> str:
    return f"checkout.payment_reminder:{order_id}"


def expiry_task_key(order_id: int) -> str:
    return f"checkout.payment_expire:{order_id}"


def after_submit_key(order_id: int) -> str:
    return f"checkout.after_submit:{order_id}"


def after_transition_key(order_id: int, status: str) -> str:
    return f"checkout.after_transition:{order_id}:{status}"


def ticket_check_key(ticket_id: int) -> str:
    return f"checkout.ticket_check:{ticket_id}"


def _schedule(
    conn: sqlite3.Connection,
    kind: str,
    *,
    run_at: int,
    payload: dict[str, Any],
    dedupe_key: str,
    attempts: int,
    now: int,
    replace: bool = False,
) -> None:
    task_repo.schedule(
        conn,
        kind=kind,
        run_at=run_at,
        payload=payload,
        max_attempts=max(1, attempts),
        dedupe_key=dedupe_key,
        replace=replace,
        now=now,
    )


# ---------------------------------------------------------------------------------------------------
# Errors (each maps to a configured customer message or an operator reply)
# ---------------------------------------------------------------------------------------------------


class CheckoutError(Exception):
    """Base class: the requested change violates a checkout rule. Nothing was written."""


class SessionNotFound(CheckoutError):
    pass


class SessionNotActive(CheckoutError):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class WrongStep(CheckoutError):
    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


class ProductMissing(CheckoutError):
    """The product does not exist in this guild (a forged or stale control)."""


class ProductUnavailable(CheckoutError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class OutOfStock(CheckoutError):
    def __init__(self, name: str, available: int) -> None:
        super().__init__(f"{name}: {available} available")
        self.name = name
        self.available = available


class QuantityTooHigh(CheckoutError):
    def __init__(self, maximum: int) -> None:
        super().__init__(str(maximum))
        self.maximum = maximum


class CartFull(CheckoutError):
    def __init__(self, maximum: int) -> None:
        super().__init__(str(maximum))
        self.maximum = maximum


class CartEmpty(CheckoutError):
    pass


class MinimumNotMet(CheckoutError):
    def __init__(self, minimum: int) -> None:
        super().__init__(str(minimum))
        self.minimum = minimum


class FulfillmentRequired(CheckoutError):
    pass


class DetailsIncomplete(CheckoutError):
    pass


class AmountTooLarge(CheckoutError):
    pass


class PriceChanged(CheckoutError):
    """The totals differ from the ones the customer was shown (or the click carries no shown totals)."""

    def __init__(self, totals: Totals) -> None:
        super().__init__(str(totals.total_minor))
        self.totals = totals


class RefundNotAllowed(CheckoutError):
    """reason: 'missing' | 'state' | 'amount' (a credit return: 'state' when there is nothing left to return)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------------------------------


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _json_dict(raw: object) -> dict[str, str]:
    try:
        data = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _dump(data: Mapping[str, str]) -> str:
    return json.dumps(dict(data), ensure_ascii=False, sort_keys=True)


def _session(r: sqlite3.Row) -> m.Session:
    return m.Session(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        user_id=int(r["user_id"]),
        ticket_id=int(r["ticket_id"]),
        status=str(r["status"]),
        step=str(r["step"]),
        page=int(r["page"]),
        fulfillment_option=None if r["fulfillment_option"] is None else str(r["fulfillment_option"]),
        details=_json_dict(r["details"]),
        apply_credits=bool(r["apply_credits"]),
        message_id=_opt_int(r["message_id"]),
        order_id=_opt_int(r["order_id"]),
        version=int(r["version"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        expires_at=_opt_int(r["expires_at"]),
    )


def _order(r: sqlite3.Row) -> m.Order:
    returned = r["credit_returned_minor"] if "credit_returned_minor" in r.keys() else 0  # noqa: SIM118
    return m.Order(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        number=int(r["number"]),
        user_id=int(r["user_id"]),
        session_id=_opt_int(r["session_id"]),
        ticket_id=_opt_int(r["ticket_id"]),
        status=str(r["status"]),
        currency=str(r["currency"]),
        subtotal_minor=int(r["subtotal_minor"]),
        fulfillment_fee_minor=int(r["fulfillment_fee_minor"]),
        fees_minor=int(r["fees_minor"]),
        credits_applied_minor=int(r["credits_applied_minor"]),
        total_minor=int(r["total_minor"]),
        refunded_minor=int(r["refunded_minor"]),
        fulfillment_option=None if r["fulfillment_option"] is None else str(r["fulfillment_option"]),
        details=_json_dict(r["details"]),
        payment_provider=str(r["payment_provider"]),
        customer_message_id=_opt_int(r["customer_message_id"]),
        staff_channel_id=_opt_int(r["staff_channel_id"]),
        staff_message_id=_opt_int(r["staff_message_id"]),
        version=int(r["version"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        paid_at=_opt_int(r["paid_at"]),
        fulfilled_at=_opt_int(r["fulfilled_at"]),
        closed_at=_opt_int(r["closed_at"]),
        credit_returned_minor=int(returned or 0),
    )


# Every order read includes the store credit already given back for it (its ledger ``order_return`` row).
_ORDER_SELECT = (
    "SELECT o.*, (SELECT COALESCE(SUM(l.delta_minor), 0) FROM credit_ledger l WHERE l.order_id = o.id "
    "AND l.kind = 'order_return') AS credit_returned_minor FROM orders o"
)


# ---------------------------------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------------------------------


def get_session(conn: sqlite3.Connection, session_id: int) -> m.Session | None:
    row = conn.execute("SELECT * FROM checkout_sessions WHERE id = ?", (session_id,)).fetchone()
    return None if row is None else _session(row)


def active_session_for_user(conn: sqlite3.Connection, guild_id: int, user_id: int) -> m.Session | None:
    row = conn.execute(
        "SELECT * FROM checkout_sessions WHERE guild_id = ? AND user_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
        (guild_id, user_id, m.ACTIVE),
    ).fetchone()
    return None if row is None else _session(row)


def active_session_for_ticket(conn: sqlite3.Connection, ticket_id: int) -> m.Session | None:
    row = conn.execute(
        "SELECT * FROM checkout_sessions WHERE ticket_id = ? AND status = ?", (ticket_id, m.ACTIVE)
    ).fetchone()
    return None if row is None else _session(row)


def active_sessions(conn: sqlite3.Connection, guild_id: int) -> list[m.Session]:
    rows = conn.execute(
        "SELECT * FROM checkout_sessions WHERE guild_id = ? AND status = ? ORDER BY id", (guild_id, m.ACTIVE)
    )
    return [_session(r) for r in rows]


def _expires(now: int, expiry_seconds: int) -> int | None:
    return now + expiry_seconds if expiry_seconds > 0 else None


def create_session(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    user_id: int,
    ticket_id: int,
    now: int,
    expiry_seconds: int,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
    warning_seconds: int = 0,
) -> m.Session:
    """A new ACTIVE session; its idle-expiry task (when expiry is on) and its expiry-warning task (when the warning
    is on: ``warning_seconds`` before the expiry) are scheduled in the same transaction."""
    cursor = conn.execute(
        "INSERT INTO checkout_sessions(guild_id, user_id, ticket_id, status, step, page, details, apply_credits, "
        "created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, 0, '{}', 0, ?, ?, ?)",
        (guild_id, user_id, ticket_id, m.ACTIVE, m.BROWSE, now, now, _expires(now, expiry_seconds)),
    )
    session = get_session(conn, int(cursor.lastrowid or 0))
    assert session is not None
    if expiry_seconds > 0:
        _schedule(
            conn,
            TASK_SESSION_EXPIRE,
            run_at=now + expiry_seconds,
            payload={"session_id": session.id},
            dedupe_key=session_task_key(session.id),
            attempts=task_attempts,
            now=now,
            replace=True,
        )
    if 0 < warning_seconds < expiry_seconds:
        _schedule(
            conn,
            TASK_SESSION_WARNING,
            run_at=now + expiry_seconds - warning_seconds,
            payload={"session_id": session.id},
            dedupe_key=session_warning_key(session.id),
            attempts=task_attempts,
            now=now,
            replace=True,
        )
    return session


def set_session_message(conn: sqlite3.Connection, session_id: int, message_id: int, now: int) -> None:
    conn.execute(
        "UPDATE checkout_sessions SET message_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
        (message_id, now, session_id),
    )


def _active_owned(conn: sqlite3.Connection, guild_id: int, session_id: int, user_id: int) -> m.Session:
    session = get_session(conn, session_id)
    if session is None or session.guild_id != guild_id or session.user_id != user_id:
        raise SessionNotFound(str(session_id))
    if session.status != m.ACTIVE:
        raise SessionNotActive(session.status)
    return session


_UNSET: Any = object()


def update_session(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    session_id: int,
    user_id: int,
    now: int,
    expiry_seconds: int,
    step: str = _UNSET,
    page: int = _UNSET,
    fulfillment_option: str | None = _UNSET,
    details: Mapping[str, str] = _UNSET,
    apply_credits: bool = _UNSET,
) -> m.Session:
    """Change fields of the caller's ACTIVE session (activity also pushes its expiry back)."""
    current = _active_owned(conn, guild_id, session_id, user_id)
    values = {
        "step": current.step if step is _UNSET else step,
        "page": current.page if page is _UNSET else max(0, page),
        "fulfillment_option": current.fulfillment_option if fulfillment_option is _UNSET else fulfillment_option,
        "details": _dump(current.details if details is _UNSET else details),
        "apply_credits": int(current.apply_credits if apply_credits is _UNSET else apply_credits),
    }
    cur = conn.execute(
        "UPDATE checkout_sessions SET step = ?, page = ?, fulfillment_option = ?, details = ?, apply_credits = ?, "
        "updated_at = ?, expires_at = ?, version = version + 1 WHERE id = ? AND status = ?",
        (
            values["step"],
            values["page"],
            values["fulfillment_option"],
            values["details"],
            values["apply_credits"],
            now,
            _expires(now, expiry_seconds),
            session_id,
            m.ACTIVE,
        ),
    )
    if cur.rowcount == 0:  # unreachable inside the transaction; kept as a guard
        raise SessionNotActive("changed")
    session = get_session(conn, session_id)
    assert session is not None
    return session


def merge_details(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    session_id: int,
    user_id: int,
    updates: Mapping[str, str | None],
    now: int,
    expiry_seconds: int,
) -> m.Session:
    """Store form answers (None removes one) of the caller's ACTIVE session, keeping its other answers."""
    current = _active_owned(conn, guild_id, session_id, user_id)
    details = dict(current.details)
    for key, value in updates.items():
        if value is None:
            details.pop(key, None)
        else:
            details[key] = value
    return update_session(
        conn,
        guild_id=guild_id,
        session_id=session_id,
        user_id=user_id,
        now=now,
        expiry_seconds=expiry_seconds,
        details=details,
    )


def end_session(
    conn: sqlite3.Connection,
    session_id: int,
    to_status: str,
    now: int,
    *,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
) -> m.Session | None:
    """active -> cancelled/expired. None when the session was not active (already ended or submitted).

    In the same transaction its expiry task is cancelled and a ``checkout.ticket_check`` task is scheduled, so the
    order channel of an ended session is closed even if the process stops (or fails) before closing it."""
    if to_status not in (m.SESSION_CANCELLED, m.SESSION_EXPIRED):
        raise ValueError(f"cannot end a session as {to_status!r}")
    cur = conn.execute(
        "UPDATE checkout_sessions SET status = ?, step = ?, updated_at = ?, expires_at = NULL, "
        "version = version + 1 WHERE id = ? AND status = ?",
        (to_status, m.DONE, now, session_id, m.ACTIVE),
    )
    if not cur.rowcount:
        return None
    ended = get_session(conn, session_id)
    assert ended is not None
    task_repo.cancel(conn, dedupe_key=session_task_key(session_id), now=now)
    task_repo.cancel(conn, dedupe_key=session_warning_key(session_id), now=now)
    _schedule(
        conn,
        TASK_TICKET_CHECK,
        run_at=now + FOLLOWUP_DELAY_SECONDS,
        payload={"ticket_id": ended.ticket_id},
        dedupe_key=ticket_check_key(ended.ticket_id),
        attempts=task_attempts,
        now=now,
        replace=True,
    )
    return ended


def orphan_order_tickets(conn: sqlite3.Connection, guild_id: int) -> list[tuple[int, int]]:
    """(ticket id, created_at) of active order-channel tickets without any checkout session (the process stopped
    between creating the channel and recording the session)."""
    rows = conn.execute(
        "SELECT t.id, t.created_at FROM tickets t WHERE t.guild_id = ? AND t.kind = 'order' "
        "AND t.status IN ('creating', 'open') "
        "AND NOT EXISTS (SELECT 1 FROM checkout_sessions s WHERE s.ticket_id = t.id) ORDER BY t.id",
        (guild_id,),
    )
    return [(int(r["id"]), int(r["created_at"])) for r in rows]


def unused_order_tickets(conn: sqlite3.Connection, guild_id: int) -> list[int]:
    """Active order-channel tickets that can no longer be used: they had a session, but none is active and no order
    of the channel is open (the session was cancelled/expired without an order, or the order is final)."""
    rows = conn.execute(
        "SELECT t.id FROM tickets t WHERE t.guild_id = ? AND t.kind = 'order' AND t.status IN ('creating', 'open') "
        "AND EXISTS (SELECT 1 FROM checkout_sessions s WHERE s.ticket_id = t.id) "
        "AND NOT EXISTS (SELECT 1 FROM checkout_sessions s WHERE s.ticket_id = t.id AND s.status = ?) "
        "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.ticket_id = t.id "
        "AND o.status IN (SELECT value FROM json_each(?))) ORDER BY t.id",
        (guild_id, m.ACTIVE, json.dumps(list(m.OPEN_STATUSES))),
    )
    return [int(r["id"]) for r in rows]


# Close reason of an order channel that never got a session.
ORPHAN = "checkout_orphan"


def ticket_close_reason(conn: sqlite3.Connection, ticket_id: int) -> str | None:
    """Why an order channel can be closed: None while it is in use (an active session or an open order), else the
    latest order's status, the latest session's status, or ``ORPHAN`` (it never got a session)."""
    sessions = [
        str(r["status"])
        for r in conn.execute("SELECT status FROM checkout_sessions WHERE ticket_id = ? ORDER BY id DESC", (ticket_id,))
    ]
    orders = [
        str(r["status"])
        for r in conn.execute("SELECT status FROM orders WHERE ticket_id = ? ORDER BY id DESC", (ticket_id,))
    ]
    if m.ACTIVE in sessions or any(status in m.OPEN_STATUSES for status in orders):
        return None
    if orders:
        return orders[0]
    if sessions:
        return sessions[0]
    return ORPHAN


# ---------------------------------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------------------------------


def cart_quantities(conn: sqlite3.Connection, session_id: int) -> dict[int, int]:
    rows = conn.execute(
        "SELECT product_id, quantity FROM cart_items WHERE session_id = ? ORDER BY added_at, product_id",
        (session_id,),
    )
    return {int(r["product_id"]): int(r["quantity"]) for r in rows}


def load_cart(conn: sqlite3.Connection, guild_id: int, session_id: int) -> list[m.CartLine]:
    """Cart lines in the order they were added, with each product as it is now."""
    lines: list[m.CartLine] = []
    for product_id, quantity in cart_quantities(conn, session_id).items():
        product = catalog_repo.get_product(conn, guild_id, product_id)
        if product is not None:
            lines.append(m.CartLine(product=product, quantity=quantity))
    return lines


def line_limit(settings: CheckoutSettings, product: catalog_repo.Product) -> int | None:
    """The highest quantity allowed for one line of ``product`` (None: only limited by stock)."""
    limits = [n for n in (settings.max_line_quantity, product.max_per_order or 0) if n > 0]
    return min(limits) if limits else None


def set_quantity(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    session_id: int,
    user_id: int,
    product_id: int,
    quantity: int,
    settings: CheckoutSettings,
    now: int,
) -> m.Session:
    """Set a cart line (0 removes it). Stock is only checked softly here; it is reserved at submission."""
    session = _active_owned(conn, guild_id, session_id, user_id)
    if quantity < 0:
        raise QuantityTooHigh(0)
    product = catalog_repo.get_product(conn, guild_id, product_id)
    if product is None:
        raise ProductMissing(str(product_id))
    current = cart_quantities(conn, session_id)
    if quantity == 0:
        conn.execute("DELETE FROM cart_items WHERE session_id = ? AND product_id = ?", (session_id, product_id))
    else:
        if not product.listed:
            raise ProductUnavailable(product.name)
        limit = line_limit(settings, product)
        if limit is not None and quantity > limit:
            raise QuantityTooHigh(limit)
        available = product.available
        if available is not None and quantity > available:
            raise OutOfStock(product.name, available)
        if product_id not in current and len(current) >= settings.max_cart_lines:
            raise CartFull(settings.max_cart_lines)
        if product.price_minor * quantity > MAX_AMOUNT_MINOR:
            raise AmountTooLarge()
        conn.execute(
            "INSERT INTO cart_items(session_id, product_id, quantity, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity",
            (session_id, product_id, quantity, now),
        )
    # Changing the cart after the cart step sends the customer back to the cart (totals must be re-checked).
    step = session.step if session.step in (m.BROWSE, m.CART) else m.CART
    return update_session(
        conn,
        guild_id=guild_id,
        session_id=session_id,
        user_id=user_id,
        now=now,
        expiry_seconds=settings.session_expiry_minutes * 60,
        step=step,
    )


def add_to_cart(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    session_id: int,
    user_id: int,
    product_id: int,
    settings: CheckoutSettings,
    now: int,
) -> tuple[m.Session, bool]:
    """A catalog buy button: put one unit of ``product_id`` in the caller's ACTIVE session's cart (every rule of
    ``set_quantity`` applies) and show the cart step. A product that is already in the cart keeps its quantity (a
    repeated click never adds a second unit). Returns (session, whether a line was added)."""
    session = _active_owned(conn, guild_id, session_id, user_id)
    added = product_id not in cart_quantities(conn, session_id)
    if added:
        set_quantity(
            conn,
            guild_id=guild_id,
            session_id=session_id,
            user_id=user_id,
            product_id=product_id,
            quantity=1,
            settings=settings,
            now=now,
        )
    elif catalog_repo.get_product(conn, guild_id, product_id) is None:
        raise ProductMissing(str(product_id))
    session = update_session(
        conn,
        guild_id=guild_id,
        session_id=session.id,
        user_id=user_id,
        now=now,
        expiry_seconds=settings.session_expiry_minutes * 60,
        step=m.CART,
    )
    return session, added


# ---------------------------------------------------------------------------------------------------
# Workflow snapshot and totals
# ---------------------------------------------------------------------------------------------------


def session_totals(
    conn: sqlite3.Connection,
    guild_id: int,
    session: m.Session,
    lines: Sequence[m.CartLine],
    settings: CheckoutSettings,
    credits_enabled: bool,
) -> tuple[Totals, int]:
    """(totals with current prices and settings, credit that could be applied)."""
    option = settings.fulfillment(session.fulfillment_option)
    fulfillment_fee = option.fee_minor if option is not None else 0
    pairs = [(line.product.price_minor, line.quantity) for line in lines]
    fees = flow.session_fees(settings, session.details)
    base = compute_totals(pairs, fulfillment_fee_minor=fulfillment_fee, fees=fees)
    available = (
        credits_repo.applicable_amount(conn, guild_id, session.user_id, base.gross_minor) if credits_enabled else 0
    )
    totals = compute_totals(
        pairs,
        fulfillment_fee_minor=fulfillment_fee,
        fees=fees,
        credits_available_minor=available,
        apply_credits=session.apply_credits and credits_enabled,
    )
    return totals, available


def product_page(conn: sqlite3.Connection, guild_id: int, page: int, per_page: int) -> m.ProductPage:
    total = catalog_repo.count_products(conn, guild_id)
    pages = max(1, -(-total // per_page))
    current = min(max(0, page), pages - 1)
    products = catalog_repo.list_products(conn, guild_id, offset=current * per_page, limit=per_page)
    return m.ProductPage(products=tuple(products), page=current, pages=pages, total=total)


def load_workflow(
    conn: sqlite3.Connection, guild_id: int, session_id: int, settings: Settings, credits_enabled: bool
) -> m.WorkflowData | None:
    session = get_session(conn, session_id)
    if session is None or session.guild_id != guild_id:
        return None
    lines = load_cart(conn, guild_id, session_id)
    browse = product_page(conn, guild_id, session.page, settings.checkout.products_per_page)
    totals, available = session_totals(conn, guild_id, session, lines, settings.checkout, credits_enabled)
    return m.WorkflowData(
        session=session,
        lines=tuple(lines),
        browse=browse,
        totals=totals,
        credits_enabled=credits_enabled,
        credits_available_minor=available,
        ticket_number=ticket_number(conn, session.ticket_id),
    )


# ---------------------------------------------------------------------------------------------------
# Orders: reads
# ---------------------------------------------------------------------------------------------------


def get_order(conn: sqlite3.Connection, order_id: int) -> m.Order | None:
    row = conn.execute(f"{_ORDER_SELECT} WHERE o.id = ?", (order_id,)).fetchone()
    return None if row is None else _order(row)


def get_order_by_number(conn: sqlite3.Connection, guild_id: int, number: int) -> m.Order | None:
    row = conn.execute(f"{_ORDER_SELECT} WHERE o.guild_id = ? AND o.number = ?", (guild_id, number)).fetchone()
    return None if row is None else _order(row)


def count_open_orders(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE guild_id = ? AND user_id = ? AND status IN (?, ?, ?)",
        (guild_id, user_id, *m.OPEN_STATUSES),
    ).fetchone()
    return int(row["n"])


def open_orders_for_user(conn: sqlite3.Connection, guild_id: int, user_id: int) -> list[m.Order]:
    """The member's open orders (waiting for payment, paid, in progress), newest first."""
    rows = conn.execute(
        f"{_ORDER_SELECT} WHERE o.guild_id = ? AND o.user_id = ? "  # noqa: S608 (constant SQL, bound values)
        "AND o.status IN (SELECT value FROM json_each(?)) ORDER BY o.id DESC",
        (guild_id, user_id, json.dumps(list(m.OPEN_STATUSES))),
    )
    return [_order(r) for r in rows]


def open_order_for_ticket(conn: sqlite3.Connection, ticket_id: int) -> m.Order | None:
    """The newest open order (waiting for payment, paid, in progress) placed in an order ticket, else None."""
    row = conn.execute(
        f"{_ORDER_SELECT} WHERE o.ticket_id = ? "  # noqa: S608 (constant SQL, bound values)
        "AND o.status IN (SELECT value FROM json_each(?)) ORDER BY o.id DESC LIMIT 1",
        (ticket_id, json.dumps(list(m.OPEN_STATUSES))),
    ).fetchone()
    return None if row is None else _order(row)


def orders_with_status(conn: sqlite3.Connection, guild_id: int, statuses: Sequence[str]) -> list[m.Order]:
    # Only constant SQL is concatenated; every value is a bound parameter.
    rows = conn.execute(
        f"{_ORDER_SELECT} WHERE o.guild_id = ? AND o.status IN (SELECT value FROM json_each(?)) ORDER BY o.id",  # noqa: S608
        (guild_id, json.dumps(list(statuses))),
    )
    return [_order(r) for r in rows]


_LIST_SQL = (
    f"{_ORDER_SELECT} WHERE o.guild_id = ? AND (? = '' OR o.status = ?) AND (? = 0 OR o.user_id = ?) "
    "ORDER BY o.id DESC LIMIT ? OFFSET ?"
)
_COUNT_SQL = "SELECT COUNT(*) AS n FROM orders WHERE guild_id = ? AND (? = '' OR status = ?) AND (? = 0 OR user_id = ?)"


def list_orders(
    conn: sqlite3.Connection, guild_id: int, *, status: str = "", user_id: int = 0, limit: int, offset: int
) -> list[m.Order]:
    params = (guild_id, status, status, user_id, user_id, max(1, limit), max(0, offset))
    return [_order(r) for r in conn.execute(_LIST_SQL, params)]


def count_orders(conn: sqlite3.Connection, guild_id: int, *, status: str = "", user_id: int = 0) -> int:
    row = conn.execute(_COUNT_SQL, (guild_id, status, status, user_id, user_id)).fetchone()
    return int(row["n"])


def order_items(conn: sqlite3.Connection, order_id: int) -> list[m.OrderItem]:
    rows = conn.execute(
        "SELECT product_id, name_snapshot, unit_price_minor, quantity, line_total_minor FROM order_items "
        "WHERE order_id = ? ORDER BY rowid",
        (order_id,),
    )
    return [
        m.OrderItem(
            product_id=int(r["product_id"]),
            name=str(r["name_snapshot"]),
            unit_price_minor=int(r["unit_price_minor"]),
            quantity=int(r["quantity"]),
            line_total_minor=int(r["line_total_minor"]),
        )
        for r in rows
    ]


def order_fees(conn: sqlite3.Connection, order_id: int) -> list[m.OrderFee]:
    rows = conn.execute("SELECT fee_id, amount_minor FROM order_fees WHERE order_id = ? ORDER BY rowid", (order_id,))
    return [m.OrderFee(fee_id=str(r["fee_id"]), amount_minor=int(r["amount_minor"])) for r in rows]


def payment_for(conn: sqlite3.Connection, order_id: int) -> m.Payment | None:
    row = conn.execute("SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
    if row is None:
        return None
    return m.Payment(
        id=int(row["id"]),
        order_id=int(row["order_id"]),
        provider=str(row["provider"]),
        status=str(row["status"]),
        amount_minor=int(row["amount_minor"]),
        currency=str(row["currency"]),
        external_ref=None if row["external_ref"] is None else str(row["external_ref"]),
    )


def adjustments(conn: sqlite3.Connection, order_id: int) -> list[m.Adjustment]:
    rows = conn.execute("SELECT * FROM order_adjustments WHERE order_id = ? ORDER BY id", (order_id,))
    return [
        m.Adjustment(
            id=int(r["id"]),
            order_id=int(r["order_id"]),
            kind=str(r["kind"]),
            method=str(r["method"]),
            amount_minor=int(r["amount_minor"]),
            reason=r["reason"],
            actor_id=_opt_int(r["actor_id"]),
            external_ref=r["external_ref"],
            created_at=int(r["created_at"]),
        )
        for r in rows
    ]


def events(conn: sqlite3.Connection, order_id: int) -> list[m.OrderEvent]:
    rows = conn.execute("SELECT * FROM order_events WHERE order_id = ? ORDER BY id", (order_id,))
    return [
        m.OrderEvent(
            id=int(r["id"]),
            order_id=int(r["order_id"]),
            from_status=r["from_status"],
            to_status=str(r["to_status"]),
            actor_id=_opt_int(r["actor_id"]),
            note=r["note"],
            created_at=int(r["created_at"]),
        )
        for r in rows
    ]


def reservations(conn: sqlite3.Connection, order_id: int) -> list[tuple[int, int, str]]:
    """(product_id, quantity, status) of an order's stock reservations."""
    rows = conn.execute(
        "SELECT product_id, quantity, status FROM inventory_reservations WHERE order_id = ? ORDER BY id", (order_id,)
    )
    return [(int(r["product_id"]), int(r["quantity"]), str(r["status"])) for r in rows]


def status_events(conn: sqlite3.Connection, order_id: int) -> list[m.OrderEvent]:
    """The order's status changes in order, the submission first (the customer's timeline). Events that change no
    status (staff notes and other internal entries) are left out: customers never see them."""
    return [e for e in events(conn, order_id) if e.from_status is None or e.from_status != e.to_status]


def ticket_channel_id(conn: sqlite3.Connection, ticket_id: int | None) -> int | None:
    if ticket_id is None:
        return None
    row = conn.execute("SELECT channel_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return None if row is None else _opt_int(row["channel_id"])


def ticket_number(conn: sqlite3.Connection, ticket_id: int | None) -> int | None:
    """The number (unique reference) of an order ticket."""
    if ticket_id is None:
        return None
    row = conn.execute("SELECT number FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return None if row is None else int(row["number"])


def order_view(conn: sqlite3.Connection, order_id: int) -> m.OrderView | None:
    order = get_order(conn, order_id)
    if order is None:
        return None
    return m.OrderView(
        order=order,
        items=tuple(order_items(conn, order_id)),
        fees=tuple(order_fees(conn, order_id)),
        channel_id=ticket_channel_id(conn, order.ticket_id),
        ticket_number=ticket_number(conn, order.ticket_id),
        timeline=tuple(status_events(conn, order_id)),
    )


# ---------------------------------------------------------------------------------------------------
# Orders: submission
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmitResult:
    order: m.Order
    created: bool


def submit_order(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    session_id: int,
    user_id: int,
    expected_total_minor: int,
    expected_gross_minor: int | None,
    settings: Settings,
    credits_enabled: bool,
    provider_id: str,
    now: int,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
) -> SubmitResult:
    """Turn the caller's session into an order, all-or-nothing (see the module docstring).

    A session that was already submitted returns its order (``created=False``): a duplicate click never creates
    a second order (``orders.session_id`` is UNIQUE as well). Raises a ``CheckoutError`` when something is not
    acceptable; ``PriceChanged`` carries the recomputed totals when the totals differ from the ones shown: both the
    total to pay AND the gross amount (before credits) must match, because store credit can absorb a price change
    in the total alone. A missing gross amount (a control rendered before it was recorded) counts as changed.

    In the same transaction: the session's expiry task is cancelled, the payment reminder/expiry tasks are scheduled
    (when configured) and the ``checkout.after_submit`` follow-up is scheduled.
    """
    session = get_session(conn, session_id)
    if session is None or session.guild_id != guild_id or session.user_id != user_id:
        raise SessionNotFound(str(session_id))
    if session.status == m.SUBMITTED and session.order_id is not None:
        existing = get_order(conn, session.order_id)
        if existing is not None:
            return SubmitResult(existing, created=False)
    if session.status != m.ACTIVE:
        raise SessionNotActive(session.status)
    if session.step != m.REVIEW:
        raise WrongStep(session.step)
    c = settings.checkout
    lines = load_cart(conn, guild_id, session_id)
    if not lines:
        raise CartEmpty()
    if len(lines) > c.max_cart_lines:
        raise CartFull(c.max_cart_lines)
    for line in lines:
        if not line.product.listed:
            raise ProductUnavailable(line.product.name)
        limit = line_limit(c, line.product)
        if limit is not None and line.quantity > limit:
            raise QuantityTooHigh(limit)
    if not flow.chosen_option_valid(c, session.fulfillment_option):
        raise FulfillmentRequired()
    option_id = session.fulfillment_option if c.fulfillment_options else None
    # The details form(s), then the submission form (answered in the form that opens on Submit).
    inputs = flow.form_inputs(c, option_id) + flow.submission_inputs(c)
    if not flow.details_complete(inputs, session.details):
        raise DetailsIncomplete()
    if c.ask_address and flow.address_problem(settings.shipping, session.details) is not None:
        raise DetailsIncomplete()  # e.g. we stopped shipping to the country after the address was entered
    totals, _ = session_totals(conn, guild_id, session, lines, c, credits_enabled)
    if totals.subtotal_minor < c.min_order_total_minor:
        raise MinimumNotMet(c.min_order_total_minor)
    if totals.gross_minor > MAX_AMOUNT_MINOR:
        raise AmountTooLarge()
    if totals.total_minor != expected_total_minor or totals.gross_minor != expected_gross_minor:
        raise PriceChanged(totals)

    for line in lines:  # all-or-nothing: a failure raises and rolls back the earlier reservations
        if not catalog_repo.reserve(conn, line.product.id, line.quantity, now):
            current = catalog_repo.get_product(conn, guild_id, line.product.id)
            if current is None or not current.listed:
                raise ProductUnavailable(line.product.name)
            raise OutOfStock(current.name, current.available or 0)

    number = state.next_counter(conn, guild_id, m.ORDER_COUNTER)
    answers = flow.relevant_answers(inputs, session.details)
    if c.ask_address:
        answers.update(flow.address_answers(session.details))
    cursor = conn.execute(
        "INSERT INTO orders(guild_id, number, user_id, session_id, ticket_id, status, currency, subtotal_minor, "
        "fulfillment_fee_minor, fees_minor, credits_applied_minor, total_minor, fulfillment_option, details, "
        "payment_provider, customer_message_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            guild_id,
            number,
            user_id,
            session_id,
            session.ticket_id,
            m.PENDING,
            c.currency,
            totals.subtotal_minor,
            totals.fulfillment_fee_minor,
            totals.fees_minor,
            totals.credits_applied_minor,
            totals.total_minor,
            option_id,
            _dump(answers),
            provider_id,
            session.message_id,
            now,
            now,
        ),
    )
    order_id = int(cursor.lastrowid or 0)
    for line in lines:
        conn.execute(
            "INSERT INTO order_items(order_id, product_id, name_snapshot, unit_price_minor, quantity, "
            "line_total_minor) VALUES (?, ?, ?, ?, ?, ?)",
            (
                order_id,
                line.product.id,
                line.product.name,
                line.product.price_minor,
                line.quantity,
                line.line_total_minor,
            ),
        )
        conn.execute(
            "INSERT INTO inventory_reservations(order_id, product_id, quantity, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, line.product.id, line.quantity, m.RESERVATION_HELD, now, now),
        )
    for fee in totals.fees:
        conn.execute(
            "INSERT INTO order_fees(order_id, fee_id, amount_minor) VALUES (?, ?, ?)",
            (order_id, fee.fee_id, fee.amount_minor),
        )
    if totals.credits_applied_minor > 0:
        credits_repo.debit_for_order(
            conn,
            guild_id=guild_id,
            user_id=user_id,
            order_id=order_id,
            amount_minor=totals.credits_applied_minor,
            actor_id=user_id,
            now=now,
        )
    conn.execute(
        "INSERT INTO payments(order_id, provider, status, amount_minor, currency, created_at, updated_at) "
        "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
        (order_id, provider_id, totals.total_minor, c.currency, now, now),
    )
    _event(conn, order_id, None, m.PENDING, user_id, None, now)
    cur = conn.execute(
        "UPDATE checkout_sessions SET status = ?, step = ?, order_id = ?, updated_at = ?, expires_at = NULL, "
        "version = version + 1 WHERE id = ? AND status = ?",
        (m.SUBMITTED, m.DONE, order_id, now, session_id, m.ACTIVE),
    )
    if cur.rowcount != 1:  # unreachable inside the transaction; kept as a guard
        raise SessionNotActive("changed")
    task_repo.cancel(conn, dedupe_key=session_task_key(session_id), now=now)
    task_repo.cancel(conn, dedupe_key=session_warning_key(session_id), now=now)
    payload = {"order_id": order_id}
    if c.payment_reminder_minutes > 0:
        _schedule(
            conn,
            TASK_PAYMENT_REMINDER,
            run_at=now + c.payment_reminder_minutes * 60,
            payload=payload,
            dedupe_key=reminder_task_key(order_id),
            attempts=task_attempts,
            now=now,
        )
    if c.payment_expiry_minutes > 0:
        _schedule(
            conn,
            TASK_PAYMENT_EXPIRE,
            run_at=now + c.payment_expiry_minutes * 60,
            payload=payload,
            dedupe_key=expiry_task_key(order_id),
            attempts=task_attempts,
            now=now,
        )
    _schedule(
        conn,
        TASK_AFTER_SUBMIT,
        run_at=now + FOLLOWUP_DELAY_SECONDS,
        payload=payload,
        dedupe_key=after_submit_key(order_id),
        attempts=task_attempts,
        now=now,
    )
    order = get_order(conn, order_id)
    assert order is not None
    return SubmitResult(order, created=True)


def set_payment_external_ref(conn: sqlite3.Connection, order_id: int, external_ref: str, now: int) -> None:
    conn.execute(
        "UPDATE payments SET external_ref = ?, updated_at = ? WHERE order_id = ? AND external_ref IS NULL",
        (external_ref, now, order_id),
    )


def set_staff_message(conn: sqlite3.Connection, order_id: int, channel_id: int, message_id: int, now: int) -> None:
    conn.execute(
        "UPDATE orders SET staff_channel_id = ?, staff_message_id = ?, updated_at = ? WHERE id = ?",
        (channel_id, message_id, now, order_id),
    )


# ---------------------------------------------------------------------------------------------------
# Orders: state machine
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    before: m.Order
    after: m.Order
    # Held stock went back to availability (an unpaid order ended).
    released: bool


def _event(
    conn: sqlite3.Connection,
    order_id: int,
    from_status: str | None,
    to_status: str,
    actor_id: int | None,
    note: str | None,
    now: int,
) -> None:
    conn.execute(
        "INSERT INTO order_events(order_id, from_status, to_status, actor_id, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, from_status, to_status, actor_id, note, now),
    )


def _held(conn: sqlite3.Connection, order_id: int) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT product_id, quantity FROM inventory_reservations WHERE order_id = ? AND status = ? ORDER BY id",
        (order_id, m.RESERVATION_HELD),
    )
    return [(int(r["product_id"]), int(r["quantity"])) for r in rows]


def _settle_reservations(conn: sqlite3.Connection, order_id: int, to: str, now: int) -> None:
    for product_id, quantity in _held(conn, order_id):
        if to == m.RESERVATION_COMMITTED:
            catalog_repo.commit_sale(conn, product_id, quantity, now)
        else:
            catalog_repo.release(conn, product_id, quantity, now)
        conn.execute(
            "UPDATE inventory_reservations SET status = ?, updated_at = ? WHERE order_id = ? AND product_id = ? "
            "AND status = ?",
            (to, now, order_id, product_id, m.RESERVATION_HELD),
        )


def transition(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    allowed_from: Sequence[str],
    to: str,
    actor_id: int | None,
    note: str | None,
    now: int,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
) -> Transition | None:
    """Move an order to ``to`` if it is in one of ``allowed_from`` (conditional UPDATE + ``order_events`` row).

    Returns None (and changes nothing) when the order is missing or no longer in an allowed state. Effects, in the
    same transaction:
    - -> paid: held stock is sold (``commit_sale``), the payment row succeeds;
    - unpaid -> rejected/cancelled/expired: held stock is released, applied credit is returned, the payment row is
      cancelled;
    - paid/processing -> cancelled: nothing is restocked, refunded or returned automatically - neither money nor the
      applied store credit; staff decide with the refund controls (``record_refund``, ``return_order_credit``);
    - leaving pending_payment cancels the payment reminder/expiry tasks;
    - the follow-up task ``checkout.after_transition:<order_id>:<to>`` is scheduled (see the module docstring).
    """
    if to not in m.ORDER_STATUSES:
        raise ValueError(f"unknown order status {to!r}")
    before = get_order(conn, order_id)
    if before is None or before.guild_id != guild_id or before.status not in allowed_from:
        return None
    terminal = to in m.TERMINAL_STATUSES
    cur = conn.execute(
        "UPDATE orders SET status = ?, version = version + 1, updated_at = ?, "
        "paid_at = COALESCE(paid_at, ?), fulfilled_at = COALESCE(fulfilled_at, ?), closed_at = COALESCE(closed_at, ?) "
        "WHERE id = ? AND guild_id = ? AND status IN (SELECT value FROM json_each(?))",
        (
            to,
            now,
            now if to == m.PAID else None,
            now if to == m.FULFILLED else None,
            now if terminal else None,
            order_id,
            guild_id,
            json.dumps(list(allowed_from)),
        ),
    )
    if cur.rowcount != 1:
        return None
    _event(conn, order_id, before.status, to, actor_id, note, now)
    released = False
    if to == m.PAID:
        _settle_reservations(conn, order_id, m.RESERVATION_COMMITTED, now)
        conn.execute(
            "UPDATE payments SET status = 'succeeded', updated_at = ? WHERE order_id = ? AND status = 'pending'",
            (now, order_id),
        )
    elif before.status == m.PENDING and to in (m.REJECTED, m.CANCELLED, m.EXPIRED):
        _settle_reservations(conn, order_id, m.RESERVATION_RELEASED, now)
        credits_repo.return_for_order(conn, guild_id=guild_id, order_id=order_id, actor_id=actor_id, now=now)
        conn.execute(
            "UPDATE payments SET status = 'cancelled', updated_at = ? WHERE order_id = ? AND status = 'pending'",
            (now, order_id),
        )
        released = True
    if before.status == m.PENDING:
        task_repo.cancel(conn, dedupe_key=reminder_task_key(order_id), now=now)
        task_repo.cancel(conn, dedupe_key=expiry_task_key(order_id), now=now)
    _schedule(
        conn,
        TASK_AFTER_TRANSITION,
        run_at=now + FOLLOWUP_DELAY_SECONDS,
        payload={"order_id": order_id, "status": to, "actor_id": actor_id, "released": released},
        dedupe_key=after_transition_key(order_id, to),
        attempts=task_attempts,
        now=now,
    )
    after = get_order(conn, order_id)
    assert after is not None
    return Transition(before=before, after=after, released=released)


@dataclass(frozen=True)
class RefundRecord:
    adjustment: m.Adjustment
    order: m.Order
    # Set when this refund completed the full refund (the order moved to 'refunded').
    transition: Transition | None


def record_refund(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    amount_minor: int,
    method: str,
    to_credit: bool,
    reason: str | None,
    actor_id: int | None,
    external_ref: str | None,
    now: int,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
) -> RefundRecord:
    """Record a money refund (never above what is still refundable) and, with ``to_credit``, credit it to the
    customer's store credit in the same transaction. The order moves to 'refunded' once it is fully refunded
    (money refunds cover the total AND any applied store credit was returned, see ``return_order_credit``)."""
    order = get_order(conn, order_id)
    if order is None or order.guild_id != guild_id:
        raise RefundNotAllowed("missing")
    if not order.can_refund:
        raise RefundNotAllowed("state")
    if amount_minor <= 0 or amount_minor > order.refundable_minor:
        raise RefundNotAllowed("amount")
    cur = conn.execute(
        "UPDATE orders SET refunded_minor = refunded_minor + ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND refunded_minor + ? <= total_minor AND status IN (SELECT value FROM json_each(?))",
        (amount_minor, now, order_id, amount_minor, json.dumps(list(m.TRANSITIONS[m.REFUNDED]))),
    )
    if cur.rowcount != 1:
        raise RefundNotAllowed("amount")
    cursor = conn.execute(
        "INSERT INTO order_adjustments(order_id, kind, method, amount_minor, reason, actor_id, external_ref, "
        "created_at) VALUES (?, 'refund', ?, ?, ?, ?, ?, ?)",
        (order_id, method, amount_minor, reason, actor_id, external_ref, now),
    )
    adjustment_id = int(cursor.lastrowid or 0)
    if to_credit:
        credits_repo.refund_to_credit(
            conn,
            guild_id=guild_id,
            user_id=order.user_id,
            order_id=order_id,
            amount_minor=amount_minor,
            actor_id=actor_id,
            note=reason,
            now=now,
        )
    return _finish_refund(
        conn,
        guild_id=guild_id,
        order_id=order_id,
        adjustment_id=adjustment_id,
        actor_id=actor_id,
        reason=reason,
        now=now,
        task_attempts=task_attempts,
    )


def _finish_refund(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    adjustment_id: int,
    actor_id: int | None,
    reason: str | None,
    now: int,
    task_attempts: int,
) -> RefundRecord:
    """After a refund or credit return: move a fully refunded order to 'refunded' (same transaction)."""
    refreshed = get_order(conn, order_id)
    assert refreshed is not None
    moved: Transition | None = None
    if refreshed.fully_refunded:
        moved = transition(
            conn,
            guild_id=guild_id,
            order_id=order_id,
            allowed_from=m.TRANSITIONS[m.REFUNDED],
            to=m.REFUNDED,
            actor_id=actor_id,
            note=reason,
            now=now,
            task_attempts=task_attempts,
        )
        refreshed = get_order(conn, order_id) or refreshed
    adjustment = next(a for a in adjustments(conn, order_id) if a.id == adjustment_id)
    return RefundRecord(adjustment=adjustment, order=refreshed, transition=moved)


def return_order_credit(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    order_id: int,
    reason: str | None,
    actor_id: int | None,
    now: int,
    task_attempts: int = DEFAULT_TASK_ATTEMPTS,
) -> RefundRecord:
    """Give the store credit applied to a paid order back to the customer, all at once, in ONE transaction: the
    ledger ``order_return`` (``credits_repo.return_for_order``, at most one per order and exactly the debited
    amount) and an ``order_adjustments`` row (kind 'credit_return', method 'credits'). Raises ``RefundNotAllowed``
    ('missing', or 'state' when the order was never paid, is in a state without refunds, or has no applied credit
    left to return - a repeated request changes nothing). A fully refunded order moves to 'refunded'."""
    order = get_order(conn, order_id)
    if order is None or order.guild_id != guild_id:
        raise RefundNotAllowed("missing")
    if not order.can_return_credit:
        raise RefundNotAllowed("state")
    cur = conn.execute(
        "UPDATE orders SET version = version + 1, updated_at = ? WHERE id = ? AND paid_at IS NOT NULL "
        "AND credits_applied_minor > 0 AND status IN (SELECT value FROM json_each(?))",
        (now, order_id, json.dumps(list(m.CREDIT_RETURN_STATUSES))),
    )
    if cur.rowcount != 1:
        raise RefundNotAllowed("state")
    returned = credits_repo.return_for_order(
        conn, guild_id=guild_id, order_id=order_id, actor_id=actor_id, note=reason, now=now
    )
    if returned is None or not returned.created:  # no debit, or it was already given back: roll back, change nothing
        raise RefundNotAllowed("state")
    cursor = conn.execute(
        "INSERT INTO order_adjustments(order_id, kind, method, amount_minor, reason, actor_id, external_ref, "
        "created_at) VALUES (?, 'credit_return', 'credits', ?, ?, ?, NULL, ?)",
        (order_id, returned.entry.delta_minor, reason, actor_id, now),
    )
    return _finish_refund(
        conn,
        guild_id=guild_id,
        order_id=order_id,
        adjustment_id=int(cursor.lastrowid or 0),
        actor_id=actor_id,
        reason=reason,
        now=now,
        task_attempts=task_attempts,
    )


def add_note(
    conn: sqlite3.Connection, *, guild_id: int, order_id: int, actor_id: int, note: str, now: int
) -> m.Order | None:
    """Legacy: a staff note as an ``order_events`` row without a status change. Staff notes are staff activity now
    (``service.add_note`` / ``handling_repo.add_note``: shown on the live order record, never in status timelines);
    migration 0003 moved older notes there. Kept only for callers written before that change."""
    order = get_order(conn, order_id)
    if order is None or order.guild_id != guild_id:
        return None
    _event(conn, order_id, order.status, order.status, actor_id, note, now)
    return order
