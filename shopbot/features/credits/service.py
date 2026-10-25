"""Credits logic: balances, the staff history, staff adjustments and the member notice.

Public helpers for other features:
- ``balance_button(app)``: the 'my balance' button for their panels (None when credits are not ready or the label
  is not configured, so callers can pass it straight to ``rows_of``).
- ``available_at_checkout(app)``: whether checkout may offer to apply credits.
- ``get_balance(app, user_id)``.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from shopbot.config.content import MissingContent
from shopbot.core.money import MoneyError, currency_exponent, format_amount, parse_amount
from shopbot.features import core_keys
from shopbot.features.credits import repo, views
from shopbot.features.credits.content_keys import CREDITS
from shopbot.logging_setup import log_event
from shopbot.ui.spec import ButtonSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

AdjustAction = Literal["add", "remove"]
NOTIFY_KIND = "credits.adjusted"


def notice_dedupe_key(entry_id: int) -> str:
    return f"credits.adjust:{entry_id}"


# -- formatting -----------------------------------------------------------------------------------


def operator_amount(app: App, minor: int) -> str:
    """Amount for staff replies: the configured separators plus the currency code (operator text)."""
    checkout = app.settings.checkout
    exponent = currency_exponent(checkout.currency, checkout.currency_exponent)
    plain = format_amount(minor, exponent, checkout.decimal_separator, checkout.thousands_separator)
    return app.op("credits.amount", amount=plain, currency=checkout.currency)


def customer_amount(app: App, minor: int) -> str:
    """Amount for customers, written with common.money_format. Raises MissingContent when it cannot be written."""
    text = app.ui.money(minor)
    if text is None:
        raise MissingContent(core_keys.MONEY_FORMAT)
    return text


def amount_example(app: App) -> str:
    """An example amount in the configured format (12.50 for two decimals, 12 for none)."""
    checkout = app.settings.checkout
    exponent = currency_exponent(checkout.currency, checkout.currency_exponent)
    minor = 1250 * 10 ** (exponent - 2) if exponent >= 2 else 12 * 10**exponent
    return format_amount(minor, exponent, checkout.decimal_separator, checkout.thousands_separator)


# -- public helpers --------------------------------------------------------------------------------


def balance_button(app: App) -> ButtonSpec | None:
    """The 'my balance' button for other features' panels, or None when it cannot be shown."""
    if not app.ready(CREDITS):
        return None
    return views.balance_button(app.ui)


def available_at_checkout(app: App) -> bool:
    return app.ready(CREDITS) and app.settings.credits.allow_at_checkout


async def get_balance(app: App, user_id: int) -> int:
    return await app.db.run(repo.balance, app.env.guild_id, user_id)


async def balance_reply(app: App, user_id: int) -> MessageSpec:
    minor = await get_balance(app, user_id)
    return views.balance_reply(app.ui, amount=customer_amount(app, minor))


async def load_history(app: App, user_id: int, page: int) -> views.HistoryPage:
    guild_id = app.env.guild_id

    def work(conn: sqlite3.Connection) -> views.HistoryPage:
        total = repo.count_entries(conn, guild_id, user_id)
        pages = views.page_count(total)
        current = min(max(0, page), pages - 1)
        entries = repo.history(
            conn, guild_id, user_id, limit=views.HISTORY_PAGE_SIZE, offset=current * views.HISTORY_PAGE_SIZE
        )
        return views.HistoryPage(
            user_id=user_id,
            page=current,
            pages=pages,
            total=total,
            balance_minor=repo.balance(conn, guild_id, user_id),
            entries=tuple(entries),
        )

    # One unit of work on the database thread: total, page and balance form a consistent snapshot.
    return await app.db.run(work)


async def history_view(app: App, user_id: int, page: int) -> MessageSpec:
    data = await load_history(app, user_id, page)
    return views.history_view(app.ui, app.op, lambda minor: operator_amount(app, minor), data)


async def balance_view(app: App, user_id: int) -> MessageSpec:
    minor = await get_balance(app, user_id)
    line = app.op("credits.cmd.balance", member=f"<@{user_id}>", balance=operator_amount(app, minor))
    return views.operator_reply(app.ui, app.op("credits.cmd.balance_title"), [line])


# -- staff adjustment ------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjustResult:
    spec: MessageSpec
    entry: repo.Entry | None = None
    balance_minor: int | None = None


def _rejected(app: App, line: str) -> AdjustResult:
    return AdjustResult(views.operator_reply(app.ui, app.op("credits.cmd.rejected_title"), [line], tone="warning"))


async def adjust(
    app: App,
    *,
    actor_id: int,
    user_id: int,
    is_bot: bool,
    action: AdjustAction,
    amount_text: str,
    note: str,
) -> AdjustResult:
    """Add or remove credits (admins only; the caller checks authorization). Returns the operator reply."""
    checkout = app.settings.checkout
    if not checkout.currency:
        return _rejected(app, app.op("credits.cmd.no_currency"))
    if is_bot:
        return _rejected(app, app.op("credits.cmd.bot_member"))
    exponent = currency_exponent(checkout.currency, checkout.currency_exponent)
    try:
        amount = parse_amount(amount_text, exponent, checkout.decimal_separator, checkout.thousands_separator)
    except MoneyError as exc:
        return _rejected(app, app.op("credits.cmd.invalid_amount", reason=str(exc), example=amount_example(app)))
    if amount <= 0:
        return _rejected(app, app.op("credits.cmd.zero_amount"))
    clean_note = note.strip()
    if not clean_note or len(clean_note) > repo.NOTE_MAX_LENGTH:
        return _rejected(app, app.op("credits.cmd.invalid_note", max=repo.NOTE_MAX_LENGTH))
    delta = amount if action == "add" else -amount
    try:
        result = await app.db.transaction(
            repo.adjust,
            guild_id=app.env.guild_id,
            user_id=user_id,
            delta_minor=delta,
            actor_id=actor_id,
            note=clean_note,
            now=app.clock.now(),
        )
    except repo.InsufficientCredit as exc:
        line = app.op(
            "credits.cmd.insufficient",
            amount=operator_amount(app, amount),
            member=f"<@{user_id}>",
            balance=operator_amount(app, exc.balance_minor),
        )
        return _rejected(app, line)
    entry = result.entry
    log_event(
        log,
        logging.INFO,
        "credits_adjusted",
        entry_id=entry.id,
        user_id=user_id,
        actor_id=actor_id,
        delta_minor=delta,
    )
    await app.audit.record(
        "credits.adjust",
        actor_id=actor_id,
        target_type="member",
        target_id=user_id,
        details={
            "entry_id": entry.id,
            "delta_minor": delta,
            "balance_minor": result.balance_minor,
            "currency": checkout.currency,
            "note": clean_note,
        },
    )
    notified = await notify_adjustment(app, entry=entry, balance_minor=result.balance_minor)
    title = app.op("credits.cmd.added_title" if delta > 0 else "credits.cmd.removed_title")
    line = app.op(
        "credits.cmd.adjusted",
        member=f"<@{user_id}>",
        delta=views.signed_amount(app.op, lambda minor: operator_amount(app, minor), delta),
        balance=operator_amount(app, result.balance_minor),
        entry_id=entry.id,
    )
    spec = views.operator_reply(app.ui, title, [line, notified], tone="success")
    return AdjustResult(spec, entry=entry, balance_minor=result.balance_minor)


async def notify_adjustment(app: App, *, entry: repo.Entry, balance_minor: int) -> str:
    """Send the optional adjustment notice to the member (at most once per entry). Returns an operator line."""
    if not views.notice_configured(app.content):
        return app.op("credits.notify.not_configured")
    if not app.settings.features.dm_notifications:
        return app.op("credits.notify.dm_disabled")
    try:
        spec = views.adjusted_notice(
            app.ui, amount=customer_amount(app, entry.delta_minor), balance=customer_amount(app, balance_minor)
        )
    except MissingContent as exc:
        return app.op("credits.notify.missing_content", content_key=exc.key)
    if spec is None:
        return app.op("credits.notify.not_configured")
    try:
        delivery = await app.notifier.notify_user(
            user_id=entry.user_id, spec=spec, kind=NOTIFY_KIND, dedupe_key=notice_dedupe_key(entry.id)
        )
    except Exception as exc:  # the adjustment is committed; report the notice failure, never undo the change
        error_id = uuid.uuid4().hex[:12]
        log_event(
            log,
            logging.ERROR,
            "credits_notice_failed",
            entry_id=entry.id,
            user_id=entry.user_id,
            error_id=error_id,
            error=type(exc).__name__,
        )
        log.exception("credits notice failure %s", error_id)
        return app.op("credits.notify.error", error_id=error_id)
    if delivery == "duplicate":
        return app.op("credits.notify.duplicate")
    if delivery in ("dm", "channel"):
        return app.op("credits.notify.sent")
    return app.op("credits.notify.failed")
