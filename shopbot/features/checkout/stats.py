"""``/stats``: the shop at a glance for staff. The orders and sales of a period, the orders that need staff right now,
and the best-selling items. A private operator reply; the numbers are read from the orders each time (nothing is
stored).

- Sales count orders placed in the period whose payment was confirmed (paid, being prepared, shipped, delivered, or
  refunded): revenue is their totals minus what was refunded. Cancelled, rejected and expired orders are counted
  separately and never as sales.
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

SOLD_STATUSES: Final = (m.PAID, m.PROCESSING, m.SHIPPED, m.FULFILLED, m.REFUNDED)
ENDED_UNSOLD: Final = (m.CANCELLED, m.REJECTED, m.EXPIRED)
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
    open_counts: dict[str, int] = field(default_factory=dict)
    best: tuple[BestSeller, ...] = ()
    other_currency: int = 0

    @property
    def average_minor(self) -> int | None:
        return self.revenue_minor // self.sold if self.sold else None


_BY_STATUS_SQL = """
SELECT status, COUNT(*), COALESCE(SUM(total_minor), 0), COALESCE(SUM(refunded_minor), 0) FROM orders
WHERE guild_id = ? AND currency = ? AND (? IS NULL OR created_at >= ?) GROUP BY status
"""
_OPEN_SQL = """
SELECT status, COUNT(*) FROM orders WHERE guild_id = ? AND status IN (SELECT value FROM json_each(?)) GROUP BY status
"""
_BEST_SQL = """
SELECT COALESCE(p.name, MAX(i.name_snapshot)), SUM(i.quantity), SUM(i.line_total_minor)
FROM order_items i JOIN orders o ON o.id = i.order_id LEFT JOIN products p ON p.id = i.product_id
WHERE o.guild_id = ? AND o.currency = ? AND o.status IN (SELECT value FROM json_each(?))
  AND (? IS NULL OR o.created_at >= ?)
GROUP BY i.product_id ORDER BY SUM(i.quantity) DESC, SUM(i.line_total_minor) DESC, i.product_id LIMIT ?
"""
_OTHER_CURRENCY_SQL = """
SELECT COUNT(*) FROM orders WHERE guild_id = ? AND currency != ? AND (? IS NULL OR created_at >= ?)
"""


def collect(conn: sqlite3.Connection, guild_id: int, currency: str, since: int | None) -> Stats:
    """The numbers of orders placed since ``since`` (None: all time), plus the open orders of every age."""
    rows = conn.execute(_BY_STATUS_SQL, (guild_id, currency, since, since)).fetchall()
    by_status = {status: (count, total, refunded) for status, count, total, refunded in rows}
    sold = [by_status.get(s, (0, 0, 0)) for s in SOLD_STATUSES]
    open_by_status = dict(conn.execute(_OPEN_SQL, (guild_id, json.dumps(m.OPEN_STATUSES))).fetchall())
    best = conn.execute(
        _BEST_SQL, (guild_id, currency, json.dumps(SOLD_STATUSES), since, since, BEST_SELLERS)
    ).fetchall()
    other = conn.execute(_OTHER_CURRENCY_SQL, (guild_id, currency, since, since)).fetchone()[0]
    return Stats(
        currency=currency,
        placed=sum(count for count, _, _ in by_status.values()),
        sold=sum(count for count, _, _ in sold),
        ended_unsold=sum(by_status.get(s, (0, 0, 0))[0] for s in ENDED_UNSOLD),
        revenue_minor=sum(total - refunded for _, total, refunded in sold),
        refunded_minor=sum(refunded for _, _, refunded in sold),
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
