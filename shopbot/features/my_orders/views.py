"""Pure builders of the ``/myorders`` reply (also used by the preview and the limits checker).

The reply is one private message: a list card (title, optional text, page indicator) and one card per order (newest
first) with its number, current stage, total and timeline, plus a link to its ticket while it is open and the
tracking details staff recorded. Orders are split into pages of at most ``my_orders.orders_per_page`` cards that
also fit Discord's limits (6000 characters and 10 cards per message); nothing is cut. Customer data (tracking text)
is escaped; configured text is used exactly as written.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features import core_keys as core
from shopbot.features.checkout import views as checkout_views
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders.repo import CustomerOrder
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELD_VALUE, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE
from shopbot.ui.spec import ButtonSpec, EmbedField, EmbedSpec, MessageSpec, rows_of
from shopbot.ui.style import Ui, chunk_lines

ROUTE = k.ROUTE


def page_id(user_id: int, page: int) -> str:
    """A paging button of ``user_id``'s list (only that member can use it)."""
    return cid(ROUTE, "page", user_id, page)


def _text(ui: Ui, key: str, /, **values: str | int) -> str:
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def _field(ui: Ui, key: str, value: str, *, inline: bool = False) -> EmbedField:
    return EmbedField(name=_text(ui, key), value=value, inline=inline)


def _optional_name(ui: Ui, key: str) -> str | None:
    return ui.text(key)


def _button(ui: Ui, key: str, custom_id: str, *, disabled: bool) -> ButtonSpec:
    button = ui.button(key, custom_id, disabled=disabled)
    if button is None:
        raise MissingContent(key)
    return button


def order_card(ui: Ui, settings: Settings, item: CustomerOrder) -> EmbedSpec:
    """One order: number, current stage, total, timeline, the ticket link (while open) and tracking details."""
    order = item.order
    total = checkout_views.order_money(ui, settings, order.currency)(order.total_minor)
    stage = checkout_views.stage_name(ui, order.status)
    title = _text(
        ui,
        k.ORDER_TITLE,
        order_number=str(order.number),
        status=stage or "",
        total=total,
        date=f"<t:{order.created_at}:d>",
    )
    fields = [_field(ui, k.ORDER_NUMBER_FIELD, str(order.number), inline=True)]
    if stage is not None:
        fields.append(_field(ui, k.ORDER_STATUS_FIELD, stage, inline=True))
    fields.append(_field(ui, k.ORDER_TOTAL_FIELD, total, inline=True))
    lines = checkout_views.timeline_lines(ui, item.timeline)
    if lines:
        name = _text(ui, k.ORDER_TIMELINE_FIELD)
        fields += [EmbedField(name=name, value=block) for block in chunk_lines(lines, EMBED_FIELD_VALUE)]
    ticket_name = _optional_name(ui, k.ORDER_TICKET_FIELD)
    if ticket_name and item.channel_id is not None:
        fields.append(EmbedField(name=ticket_name, value=checkout_views.channel_mention(item.channel_id)))
    tracking_name = _optional_name(ui, k.ORDER_TRACKING_FIELD)
    if tracking_name and item.tracking:
        fields += checkout_views.answer_fields(tracking_name, item.tracking)
    return ui.embed(title=title, fields=fields, tone=checkout_views.status_tone(order.status), branding_text=False)


def _list_card(ui: Ui, count: int, footer: str | None) -> EmbedSpec:
    return ui.embed(
        title=_text(ui, k.LIST_TITLE, count=count),
        description=ui.text(k.LIST_DESCRIPTION, count=count),
        tone="primary",
        footer_extra=footer,
        timestamp=True,
    )


@dataclass(frozen=True)
class Pages:
    """The member's order cards split into pages (lists of card indexes)."""

    cards: tuple[EmbedSpec, ...]
    pages: tuple[tuple[int, ...], ...]


def paginate(ui: Ui, settings: Settings, items: Sequence[CustomerOrder]) -> Pages:
    """Split the order cards into pages: at most ``orders_per_page`` cards, and only as many as fit one message next
    to the list card (a card that does not fit with any other gets a page of its own)."""
    cards = tuple(order_card(ui, settings, item) for item in items)
    per_page = settings.my_orders.orders_per_page
    # Budget with the widest page indicator this list can show (the indicator is the only part that varies).
    widest = ui.page_indicator(max(1, len(cards)), max(1, len(cards)))
    base = _list_card(ui, len(cards), widest).text_length()
    pages: list[tuple[int, ...]] = []
    current: list[int] = []
    used = base
    for index, card in enumerate(cards):
        size = card.text_length()
        full = len(current) >= per_page or len(current) + 1 >= EMBEDS_PER_MESSAGE or used + size > EMBED_TOTAL_CHARS
        if current and full:
            pages.append(tuple(current))
            current, used = [], base
        current.append(index)
        used += size
    if current:
        pages.append(tuple(current))
    return Pages(cards=cards, pages=tuple(pages))


def empty_message(ui: Ui) -> MessageSpec:
    """The reply of a member without any order."""
    card = ui.embed(title=_text(ui, k.LIST_TITLE, count=0), description=_text(ui, k.EMPTY), tone="neutral")
    return MessageSpec(embeds=(card,))


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
    embeds = [_list_card(ui, len(items), indicator), *(split.cards[i] for i in split.pages[page])]
    if count <= 1:
        return MessageSpec(embeds=tuple(embeds))
    rows = rows_of(
        _button(ui, core.BUTTON_PREV, page_id(user_id, max(0, page - 1)), disabled=page <= 0),
        _button(ui, core.BUTTON_NEXT, page_id(user_id, min(count - 1, page + 1)), disabled=page >= count - 1),
    )
    return MessageSpec(embeds=tuple(embeds), rows=rows)
