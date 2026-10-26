"""``/stats``: the shop at a glance for staff. The orders and sales of a period, the orders that need staff right now,
and the best-selling items. A private operator reply; the numbers are read from the orders each time (nothing is
stored).

- Orders placed in the period count as **paid** once their payment was confirmed (``paid_at`` is set; whatever their
  status is now, e.g. paid and later cancelled), except orders refunded in full. Orders cancelled, rejected or expired
  before any payment count as ended before payment.
- **Revenue** is the money customers paid for those orders (their totals: store credit a customer used is not money
  and is not in the total) minus the money refunded to them, minus the tips kept for the team. Refunds given as store
  credit are shown on their own line and not taken off: the shop keeps that money until the credit is spent, and the
  order it is spent on adds nothing to the revenue, so taking it off as well would count it twice.
- **Tips for the team** (``orders.tip_minor``, paid with the order) go 100% to the team, so they are shown on their
  own line and are not revenue. A paid order keeps its tip while it still holds at least that much: what was paid for
  it (money and store credit) minus every refund (money or store credit) and the store credit given back. Below that
  it keeps only what it still holds, so a refund counts against the items first and a fully refunded order keeps no
  tip. A refund of the tip alone cannot be told apart from a refund of an item, so it still shows the tip as kept.
  So revenue = paid totals - money refunds - kept tips.
- A tip paid with store credit is a tip too: the shop pays it to the team, so it is taken off the revenue like any
  other tip, and the revenue of a period can be below zero when store credit paid for tips (the money behind the
  credit was counted when it came in, or the credit was a gift). A full refund given as store credit keeps no tip, but
  like every credit refund it is not taken off the revenue until the credit is spent.
- **Best sellers** are the items of paid orders that were neither cancelled nor refunded in full.
- "Needs attention now" counts open orders of every age by what staff do next.
- Only orders in the shop's currency (``checkout.currency``) are added up; orders in another currency (placed before
  the currency changed) are counted in a note.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from shopbot.features.checkout import models as m
from shopbot.features.checkout.order_money import Money, order_money
from shopbot.ui.card import heading
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.checkout.views import OperatorText
    from shopbot.ui.style import Ui

Period = Literal["24h", "7d", "30d", "all"]
PERIOD_SECONDS: Final[dict[str, int | None]] = {"24h": 86_400, "7d": 7 * 86_400, "30d": 30 * 86_400, "all": None}
PERIOD_NAMES: Final = {
    "24h": "checkout.stats.period.day",
    "7d": "checkout.stats.period.week",
    "30d": "checkout.stats.period.month",
    "all": "checkout.stats.period.all",
}
DEFAULT_PERIOD: Final = "7d"
BEST_SELLERS: Final = 5

ENDED_UNSOLD: Final = (m.CANCELLED, m.REJECTED, m.EXPIRED)
# Paid orders left out of best sellers (the items did not stay sold).
NOT_SOLD_ITEMS: Final = (m.CANCELLED, m.REFUNDED)
# What staff do next, in that order: wait for the payment, check it, ship, and wait for the delivery.
OPEN_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("pending", (m.PENDING,)),
    ("submitted", (m.PAYMENT_SUBMITTED,)),
    ("to_ship", (m.PAID, m.PROCESSING)),
    ("shipped", (m.SHIPPED,)),
)
OPEN_LINES: Final = {
    "pending": "checkout.stats.line.pending",
    "submitted": "checkout.stats.line.submitted",
    "to_ship": "checkout.stats.line.to_ship",
    "shipped": "checkout.stats.line.shipped",
}


@dataclass(frozen=True)
class BestSeller:
    name: str
    quantity: int
    total_minor: int


@dataclass(frozen=True)
class Stats:
    currency: str
    placed: int = 0
    sold: int = 0
    ended_unsold: int = 0
    revenue_minor: int = 0
    refunded_minor: int = 0
    credit_refunded_minor: int = 0
    tips_minor: int = 0
    open_counts: dict[str, int] = field(default_factory=dict)
    best: tuple[BestSeller, ...] = ()
    other_currency: int = 0

    @property
    def average_minor(self) -> int | None:
        return self.revenue_minor // self.sold if self.sold else None


# The tip each paid order keeps: at most what it still holds (paid with money and store credit, minus every refund
# and the store credit given back; ``refunded_minor`` counts money and store-credit refunds).
_SALES_SQL = """
SELECT COUNT(*),
  COALESCE(SUM(paid_at IS NOT NULL AND status != ?), 0),
  COALESCE(SUM(paid_at IS NULL AND status IN (SELECT value FROM json_each(?))), 0),
  COALESCE(SUM(CASE WHEN paid_at IS NOT NULL THEN total_minor END), 0),
  COALESCE(SUM(CASE WHEN paid_at IS NOT NULL AND status != ? AND tip_minor > 0 THEN MIN(tip_minor, MAX(0,
    total_minor + credits_applied_minor - refunded_minor - (SELECT COALESCE(SUM(a.amount_minor), 0)
      FROM order_adjustments a WHERE a.order_id = orders.id AND a.kind = 'credit_return'))) END), 0)
FROM orders WHERE guild_id = ? AND currency = ? AND (? IS NULL OR created_at >= ?)
"""
_REFUNDS_SQL = """
SELECT COALESCE(SUM(CASE WHEN a.method != 'credits' THEN a.amount_minor END), 0),
  COALESCE(SUM(CASE WHEN a.method = 'credits' THEN a.amount_minor END), 0)
FROM order_adjustments a JOIN orders o ON o.id = a.order_id
WHERE a.kind = 'refund' AND o.guild_id = ? AND o.currency = ? AND o.paid_at IS NOT NULL
  AND (? IS NULL OR o.created_at >= ?)
"""
_OPEN_SQL = """
SELECT status, COUNT(*) FROM orders WHERE guild_id = ? AND status IN (SELECT value FROM json_each(?)) GROUP BY status
"""
_BEST_SQL = """
SELECT COALESCE(p.name, MAX(i.name_snapshot)), SUM(i.quantity), SUM(i.line_total_minor)
FROM order_items i JOIN orders o ON o.id = i.order_id LEFT JOIN products p ON p.id = i.product_id
WHERE o.guild_id = ? AND o.currency = ? AND o.paid_at IS NOT NULL
  AND o.status NOT IN (SELECT value FROM json_each(?)) AND (? IS NULL OR o.created_at >= ?)
GROUP BY i.product_id ORDER BY SUM(i.quantity) DESC, SUM(i.line_total_minor) DESC, i.product_id LIMIT ?
"""
_OTHER_CURRENCY_SQL = """
SELECT COUNT(*) FROM orders WHERE guild_id = ? AND currency != ? AND (? IS NULL OR created_at >= ?)
"""


def collect(conn: sqlite3.Connection, guild_id: int, currency: str, since: int | None) -> Stats:
    """The numbers of orders placed since ``since`` (None: all time), plus the open orders of every age."""
    placed, sold, ended, paid_total, kept_tips = conn.execute(
        _SALES_SQL, (m.REFUNDED, json.dumps(ENDED_UNSOLD), m.REFUNDED, guild_id, currency, since, since)
    ).fetchone()
    refunded, credit_refunded = conn.execute(_REFUNDS_SQL, (guild_id, currency, since, since)).fetchone()
    open_by_status = dict(conn.execute(_OPEN_SQL, (guild_id, json.dumps(m.OPEN_STATUSES))).fetchall())
    best = conn.execute(
        _BEST_SQL, (guild_id, currency, json.dumps(NOT_SOLD_ITEMS), since, since, BEST_SELLERS)
    ).fetchall()
    other = conn.execute(_OTHER_CURRENCY_SQL, (guild_id, currency, since, since)).fetchone()[0]
    return Stats(
        currency=currency,
        placed=int(placed),
        sold=int(sold),
        ended_unsold=int(ended),
        revenue_minor=int(paid_total) - int(refunded) - int(kept_tips),
        refunded_minor=int(refunded),
        credit_refunded_minor=int(credit_refunded),
        tips_minor=int(kept_tips),
        open_counts={
            group: sum(open_by_status.get(status, 0) for status in statuses) for group, statuses in OPEN_GROUPS
        },
        best=tuple(BestSeller(str(name), int(quantity), int(total)) for name, quantity, total in best),
        other_currency=int(other),
    )


def stats_message(ui: Ui, op: OperatorText, money: Money, stats: Stats, period: str) -> MessageSpec:
    """The private /stats reply: three sections (sales of the period, what needs staff now, best sellers)."""
    when = op(PERIOD_NAMES[period])
    average = stats.average_minor
    lines = [
        heading(op("checkout.stats.heading.sales", period=when)),
        op("checkout.stats.line.placed", count=stats.placed),
        op("checkout.stats.line.sold", count=stats.sold, ended=stats.ended_unsold),
        op(
            "checkout.stats.line.revenue",
            revenue=money(stats.revenue_minor),
            average=money(average) if average is not None else "-",
        ),
    ]
    if stats.refunded_minor:
        lines.append(op("checkout.stats.line.refunded", amount=money(stats.refunded_minor)))
    if stats.credit_refunded_minor:
        lines.append(op("checkout.stats.line.credit_refunded", amount=money(stats.credit_refunded_minor)))
    if stats.tips_minor:
        lines.append(op("checkout.stats.line.tips", amount=money(stats.tips_minor)))
    lines += ["", heading(op("checkout.stats.heading.open"))]
    lines += [op(OPEN_LINES[group], count=stats.open_counts.get(group, 0)) for group, _ in OPEN_GROUPS]
    lines += ["", heading(op("checkout.stats.heading.best", period=when))]
    if stats.best:
        lines += [
            op(
                "checkout.stats.line.best",
                rank=rank,
                name=item.name,
                quantity=item.quantity,
                total=money(item.total_minor),
            )
            for rank, item in enumerate(stats.best, start=1)
        ]
    else:
        lines.append(op("checkout.stats.line.none"))
    if stats.other_currency:
        lines += ["", op("checkout.stats.other_currency", count=stats.other_currency, currency=stats.currency)]
    embed = ui.operator_embed(op("checkout.stats.title", period=when), "\n".join(lines), tone="info")
    return MessageSpec(embeds=(embed,), banner=ui.banner("staff-dashboard"))


async def stats_reply(app: App, period: str) -> MessageSpec:
    seconds = PERIOD_SECONDS.get(period, PERIOD_SECONDS[DEFAULT_PERIOD])
    since = app.clock.now() - seconds if seconds is not None else None
    currency = app.settings.checkout.currency
    stats = await app.db.run(collect, app.env.guild_id, currency, since)
    money = order_money(app.ui, app.settings, currency)
    return stats_message(app.ui, app.op, money, stats, period if period in PERIOD_SECONDS else DEFAULT_PERIOD)
