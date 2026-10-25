"""Pure builders of the ``/myorders`` reply (also used by the preview and the limits checker).

The reply is one private card: the title, optional text and page indicator, then one section per order (newest
first): its number and current stage in bold, its total and date, a link to its ticket while it is open, and the
tracking details staff recorded. Orders are split into pages of at most ``my_orders.orders_per_page`` that also fit
one embed; nothing is cut. Customer data (tracking text) is escaped; configured text is used exactly as written.
Every page (and the empty reply) shows the ``account-and-personal-records`` banner (paging edits the same message,
so the banner stays).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from discord.utils import escape_markdown

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import views as checkout_views
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders.repo import CustomerOrder
from shopbot.ui.card import Card, Section, card_message
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_DESCRIPTION
from shopbot.ui.spec import ButtonSpec, MessageSpec, rows_of
from shopbot.ui.style import Ui

ROUTE = k.ROUTE
BANNER: BannerId = "account-and-personal-records"


def page_id(user_id: int, page: int) -> str:
    """A paging button of ``user_id``'s list (only that member can use it)."""
    return cid(ROUTE, "page", user_id, page)


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _optional_name(ui: Ui, key: str) -> str | None:
    return ui.text(key)


def _button(ui: Ui, key: str, custom_id: str, *, disabled: bool) -> ButtonSpec:
    button = ui.button(key, custom_id, disabled=disabled)
    if button is None:
        raise MissingContent(key)
    return button


def order_section(ui: Ui, settings: Settings, item: CustomerOrder) -> Section:
    """One order: its heading (number and current stage), the total and date, the ticket link (while open) and the
    tracking details."""
    order = item.order
    values: dict[str, str | int] = {
        "order_number": str(order.number),
        "status": checkout_views.stage_name(ui, order.status) or "",
        "total": checkout_views.order_money(ui, settings, order.currency)(order.total_minor),
        "date": f"<t:{order.created_at}:d>",
    }
    lines = [_text(ui, k.ORDER_SUMMARY, **values)]
    ticket_label = _optional_name(ui, k.ORDER_TICKET_FIELD)
    if ticket_label and item.channel_id is not None:
        lines.append(f"{ticket_label}: {checkout_views.channel_mention(item.channel_id)}")
    tracking_label = _optional_name(ui, k.ORDER_TRACKING_FIELD)
    if tracking_label and item.tracking:
        lines.append(f"{tracking_label}: {escape_markdown(item.tracking)}")
    return Section(_text(ui, k.ORDER_TITLE, **values), tuple(lines))


def _section_length(section: Section) -> int:
    return len(section.heading or "") + 6 + sum(len(line) + 1 for line in section.lines)


@dataclass(frozen=True)
class Pages:
    """The member's orders split into pages (lists of section indexes)."""

    sections: tuple[Section, ...]
    pages: tuple[tuple[int, ...], ...]


def paginate(ui: Ui, settings: Settings, items: Sequence[CustomerOrder]) -> Pages:
    """Split the orders into pages: at most ``orders_per_page`` orders, and only as many as fit one embed's text next
    to the title and introduction (an order that does not fit with any other gets a page of its own; a single order
    longer than one embed continues in the next embed of the same message)."""
    sections = tuple(order_section(ui, settings, item) for item in items)
    per_page = settings.my_orders.orders_per_page
    budget = EMBED_DESCRIPTION - len(ui.text(k.LIST_DESCRIPTION, count=len(sections)) or "") - 2
    pages: list[tuple[int, ...]] = []
    current: list[int] = []
    used = 0
    for index, section in enumerate(sections):
        size = _section_length(section)
        if current and (len(current) >= per_page or used + size > budget):
            pages.append(tuple(current))
            current, used = [], 0
        current.append(index)
        used += size
    if current:
        pages.append(tuple(current))
    return Pages(sections=sections, pages=tuple(pages))


def _list_card(ui: Ui, count: int, sections: Sequence[Section], footer: str | None) -> Card:
    return Card(
        tone="primary",
        title=_text(ui, k.LIST_TITLE, count=count),
        intro=ui.text(k.LIST_DESCRIPTION, count=count),
        sections=tuple(sections),
        timestamp=True,
        footer_extra=footer,
    )


def empty_message(ui: Ui) -> MessageSpec:
    """The reply of a member without any order."""
    card = Card(tone="neutral", title=_text(ui, k.LIST_TITLE, count=0), intro=_text(ui, k.EMPTY))
    return card_message(ui, card, banner=BANNER)


def my_orders_message(
    ui: Ui, settings: Settings, items: Sequence[CustomerOrder], *, page: int, user_id: int
) -> MessageSpec:
    """Page ``page`` (0-based, clamped to the existing pages) of ``user_id``'s orders, with previous/next buttons
    when there is more than one page."""
    if not items:
        return empty_message(ui)
    split = paginate(ui, settings, items)
    count = len(split.pages)
    page = min(max(0, page), count - 1)
    indicator = ui.page_indicator(page + 1, count) if count > 1 else None
    card = _list_card(ui, len(items), [split.sections[i] for i in split.pages[page]], indicator)
    if count <= 1:
        return card_message(ui, card, banner=BANNER)
    rows = rows_of(
        _button(ui, core.BUTTON_PREV, page_id(user_id, max(0, page - 1)), disabled=page <= 0),
        _button(ui, core.BUTTON_NEXT, page_id(user_id, min(count - 1, page + 1)), disabled=page >= count - 1),
    )
    return card_message(ui, card, rows, banner=BANNER)
