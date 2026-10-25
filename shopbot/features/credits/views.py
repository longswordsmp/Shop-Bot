"""Pure builders for every credits message. No Discord calls: the runtime, the offline preview and the limits
checker all render through these functions.

Customer messages (balance reply, adjustment notice, the balance button) use content keys only. The staff
history pages and command replies use operator text (``op``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import discord

from shopbot.config.content import ContentStore, MissingContent
from shopbot.features.credits import repo
from shopbot.features.credits.content_keys import (
    BALANCE_DESCRIPTION,
    BALANCE_TITLE,
    BUTTON_BALANCE,
    NOTICE_DESCRIPTION,
    NOTICE_TITLE,
)
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, EmbedField, MessageSpec, rows_of
from shopbot.ui.style import Ui

ROUTE = "cr"
HISTORY_PAGE_SIZE = 8

OpText = Callable[..., str]
AmountFormat = Callable[[int], str]


# -- customer-facing -------------------------------------------------------------------------------


def balance_button_id() -> str:
    return cid(ROUTE, "balance")


def history_page_id(user_id: int, page: int) -> str:
    return cid(ROUTE, "hpage", user_id, page)


def balance_button(ui: Ui) -> ButtonSpec | None:
    """The 'my balance' button (None when its label is not configured)."""
    return ui.button(BUTTON_BALANCE, balance_button_id(), style="secondary")


def balance_reply(ui: Ui, *, amount: str) -> MessageSpec:
    """Private reply to the balance button. ``amount`` is already written with common.money_format."""
    embed = ui.content_embed(
        BALANCE_TITLE, BALANCE_DESCRIPTION, values={"amount": amount}, require=(BALANCE_DESCRIPTION,), tone="info"
    )
    if embed is None:
        raise MissingContent(BALANCE_DESCRIPTION)
    return MessageSpec(embeds=(embed,))


def notice_configured(content: ContentStore) -> bool:
    return content.has(NOTICE_TITLE) or content.has(NOTICE_DESCRIPTION)


def adjusted_notice(ui: Ui, *, amount: str, balance: str) -> MessageSpec | None:
    """Notice to a member after a staff adjustment; None when neither notice key is configured.

    Each part is shown only when configured (an empty optional title is simply omitted).
    """
    title = ui.text(NOTICE_TITLE, amount=amount, balance=balance)
    description = ui.text(NOTICE_DESCRIPTION, amount=amount, balance=balance)
    if not title and not description:
        return None
    return MessageSpec(embeds=(ui.embed(title=title, description=description, tone="info", timestamp=True),))


# -- staff (operator text) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryPage:
    user_id: int
    page: int  # 0-based, already clamped to [0, pages - 1]
    pages: int  # >= 1
    total: int
    balance_minor: int
    entries: tuple[repo.Entry, ...]


def page_count(total: int) -> int:
    return max(1, -(-total // HISTORY_PAGE_SIZE))


def signed_amount(op: OpText, amount: AmountFormat, delta_minor: int) -> str:
    text = amount(delta_minor)
    return op("credits.amount_positive", amount=text) if delta_minor > 0 else text


def kind_label(op: OpText, kind: str) -> str:
    return op(f"credits.kind.{kind}") if kind in repo.KINDS else kind


def _entry_field(op: OpText, amount: AmountFormat, entry: repo.Entry) -> EmbedField:
    lines = [op("credits.history.details", kind=kind_label(op, entry.kind), date=f"<t:{entry.created_at}:f>")]
    if entry.actor_id is not None:
        lines.append(op("credits.history.by", actor=f"<@{entry.actor_id}>"))
    else:
        lines.append(op("credits.history.by_system"))
    if entry.order_number is not None:
        lines.append(op("credits.history.order", order_number=entry.order_number))
    if entry.note:
        lines.append(op("credits.history.note", note=discord.utils.escape_markdown(entry.note)))
    name = op("credits.history.entry", entry_id=entry.id, delta=signed_amount(op, amount, entry.delta_minor))
    return EmbedField(name=name, value="\n".join(lines))


def history_view(ui: Ui, op: OpText, amount: AmountFormat, page: HistoryPage) -> MessageSpec:
    """One page of a member's ledger for staff, newest first, with previous/next buttons when needed."""
    summary = op(
        "credits.history.summary",
        member=f"<@{page.user_id}>",
        balance=amount(page.balance_minor),
        count=page.total,
    )
    description = summary if page.entries else f"{summary}\n{op('credits.history.empty')}"
    fields = [_entry_field(op, amount, entry) for entry in page.entries]
    embed = ui.operator_embed(op("credits.history.title"), description, tone="info", fields=fields)
    if page.pages <= 1:
        return MessageSpec(embeds=(embed,))
    embed = replace(embed, footer_text=op("credits.history.page", page=page.page + 1, pages=page.pages))
    last = page.pages - 1
    # With at least two pages the two targets always differ (custom_ids must be unique in a message).
    previous = ButtonSpec(
        label=op("credits.history.previous"),
        custom_id=history_page_id(page.user_id, max(0, page.page - 1)),
        disabled=page.page == 0,
    )
    following = ButtonSpec(
        label=op("credits.history.next"),
        custom_id=history_page_id(page.user_id, min(last, page.page + 1)),
        disabled=page.page >= last,
    )
    return MessageSpec(embeds=(embed,), rows=rows_of(previous, following))


def operator_reply(ui: Ui, title: str, lines: list[str], *, tone: str = "info") -> MessageSpec:
    return MessageSpec(embeds=(ui.operator_embed(title, "\n".join(lines) or None, tone=tone),))
