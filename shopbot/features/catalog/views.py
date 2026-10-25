"""Pure builders for every catalog message and form.

Nothing here talks to Discord or the database: the same functions build what the bot sends, what the
offline limits checker validates, and what the HTML preview renders. Customer-facing wording comes only
from content keys (through ``Ui``); missing required text raises ``MissingContent`` so nothing incomplete
is ever sent. Operator (staff-only) wording comes from ``op`` (``App.op``).

Product names are staff-entered data: they are markdown-escaped wherever they are displayed. Product
descriptions are business copy written by an authorized manager and are shown exactly as written (their
markdown formatting is intentional).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as ck
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog.repo import (
    DESCRIPTION_MAX,
    NAME_MAX,
    Product,
)
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import EMBED_FIELD_VALUE, EMBED_TOTAL_CHARS, EMBEDS_PER_MESSAGE
from shopbot.ui.spec import (
    ButtonSpec,
    Component,
    EmbedField,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui

ROUTE = "ct"
OperatorText = Callable[..., str]
PageCustomId = Callable[[int], str]

ADMIN_PAGE_SIZE = 10
INPUT_NAME = "name"
INPUT_PRICE = "price"
INPUT_STOCK = "stock"
INPUT_MAX_PER_ORDER = "max_per_order"
INPUT_DESCRIPTION = "description"


def browse_custom_id(page: int) -> str:
    """custom_id of the catalog's own browse/paging buttons (``sb:ct:browse:<page>``)."""
    return cid(ROUTE, "browse", page)


def admin_page_custom_id(page: int, include_archived: bool) -> str:
    return cid(ROUTE, "apage", page, int(include_archived))


def add_form_custom_id(nonce: int | str) -> str:
    return cid(ROUTE, "addform", nonce)


def edit_form_custom_id(product_id: int, version: int) -> str:
    return cid(ROUTE, "editform", product_id, version)


def escape(text: str) -> str:
    return discord.utils.escape_markdown(text)


# ---------------------------------------------------------------------------------------------------
# Customer-facing: product cards
# ---------------------------------------------------------------------------------------------------


def price_text(ui: Ui, product: Product) -> str:
    text = ui.money(product.price_minor)
    if text is None:
        raise MissingContent(ck.MONEY_FORMAT)
    return text


def availability_text(ui: Ui, product: Product) -> str:
    available = product.available
    if available is None:
        key, values = keys.AVAILABILITY_UNLIMITED, {}
    elif available > 0:
        key, values = keys.AVAILABILITY_IN_STOCK, {"count": available}
    else:
        key, values = keys.AVAILABILITY_OUT_OF_STOCK, {}
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def product_fields(ui: Ui, product: Product) -> tuple[EmbedField, ...]:
    """Price and availability fields (required) plus the optional per-order limit field."""
    price = ui.field(keys.FIELD_PRICE, price_text(ui, product), inline=True)
    if price is None:
        raise MissingContent(keys.FIELD_PRICE)
    availability = ui.field(keys.FIELD_AVAILABILITY, availability_text(ui, product), inline=True)
    if availability is None:
        raise MissingContent(keys.FIELD_AVAILABILITY)
    fields = [price, availability]
    if product.max_per_order is not None:
        limit = ui.field(keys.FIELD_MAX_PER_ORDER, str(product.max_per_order), inline=True)
        if limit is not None:
            fields.append(limit)
    return tuple(fields)


def product_embed(ui: Ui, product: Product, *, footer_extra: str | None = None) -> EmbedSpec:
    """The customer card of one product: name, description (if any), image (if any), price, availability."""
    return ui.embed(
        title=escape(product.name),
        description=product.description or None,
        image_url=product.image_url,
        fields=product_fields(ui, product),
        tone="primary",
        footer_extra=footer_extra,
    )


# ---------------------------------------------------------------------------------------------------
# Customer-facing: live listing
# ---------------------------------------------------------------------------------------------------


def listing_header_embed(ui: Ui) -> EmbedSpec:
    embed = ui.content_embed(keys.LISTING_TITLE, keys.LISTING_DESCRIPTION, panel=True)
    if embed is None:
        missing = next(k for k in (keys.LISTING_TITLE, keys.LISTING_DESCRIPTION) if not ui.content.has(k))
        raise MissingContent(missing)
    return embed


def empty_embed(ui: Ui) -> EmbedSpec:
    embed = ui.content_embed(None, keys.LISTING_EMPTY, tone="neutral")
    if embed is None:
        raise MissingContent(keys.LISTING_EMPTY)
    return embed


def browse_button(ui: Ui) -> ButtonSpec:
    button = ui.button(keys.BUTTON_BROWSE, browse_custom_id(0), style="primary")
    if button is None:
        raise MissingContent(keys.BUTTON_BROWSE)
    return button


def _total(embeds: Sequence[EmbedSpec]) -> int:
    return sum(e.text_length() for e in embeds)


def listing_messages(ui: Ui, settings: Settings, products: Sequence[Product]) -> list[MessageSpec]:
    """Every message of the live listing, in channel order.

    The first message starts with the header card. Each message holds at most
    ``catalog.products_per_listing_message`` product cards and is split further so it never exceeds 10
    embeds or 6000 embed characters. The last message carries the browse button (when browsing is enabled
    and there is something to browse). Without products: header + the "empty" card.
    """
    header = listing_header_embed(ui)
    products = [p for p in products if p.listed]
    if not products:
        return [MessageSpec(embeds=(header, empty_embed(ui)))]
    per_message = settings.catalog.products_per_listing_message
    groups: list[list[EmbedSpec]] = []
    current: list[EmbedSpec] = [header]
    product_count = 0
    for product in products:
        embed = product_embed(ui, product)
        full = (
            product_count >= per_message
            or len(current) >= EMBEDS_PER_MESSAGE
            or _total(current) + embed.text_length() > EMBED_TOTAL_CHARS
        )
        if full and current:
            groups.append(current)
            current, product_count = [], 0
        current.append(embed)
        product_count += 1
    groups.append(current)
    specs = [MessageSpec(embeds=tuple(group)) for group in groups]
    if settings.catalog.browse_enabled:
        last = specs[-1]
        specs[-1] = MessageSpec(embeds=last.embeds, rows=rows_of(browse_button(ui)))
    return specs


# ---------------------------------------------------------------------------------------------------
# Customer-facing: private browsing (one product per page)
# ---------------------------------------------------------------------------------------------------


def browse_page(
    ui: Ui,
    product: Product | None,
    page: int,
    total: int,
    *,
    page_custom_id: PageCustomId = browse_custom_id,
    extra_buttons: Sequence[ButtonSpec] = (),
) -> MessageSpec:
    """One page of private browsing: the product card of ``product`` (page ``page`` of ``total``).

    Previous/next buttons use the shared ``common.button.previous_page``/``next_page`` labels and are
    disabled at the ends (their custom_ids are ``page_custom_id(page - 1)`` / ``page_custom_id(page + 1)``,
    so a disabled button may carry -1 or ``total``). They are omitted when there is a single page.
    ``extra_buttons`` (e.g. checkout's "add to cart") are placed in the rows after the navigation.
    With ``product`` None (nothing to browse) the "empty" card is shown and no buttons.
    """
    if product is None or total <= 0:
        return MessageSpec(embeds=(empty_embed(ui),))
    indicator = ui.page_indicator(page + 1, total) if total > 1 else None
    embed = product_embed(ui, product, footer_extra=indicator)
    nav: list[Component] = []
    if total > 1:
        previous = ui.button(ck.BUTTON_PREV, page_custom_id(page - 1), disabled=page <= 0)
        if previous is None:
            raise MissingContent(ck.BUTTON_PREV)
        following = ui.button(ck.BUTTON_NEXT, page_custom_id(page + 1), disabled=page >= total - 1)
        if following is None:
            raise MissingContent(ck.BUTTON_NEXT)
        nav = [previous, following]
    rows = rows_of(*nav) + rows_of(*extra_buttons)
    return MessageSpec(embeds=(embed,), rows=rows)


# ---------------------------------------------------------------------------------------------------
# Operator (staff-only): product editor form, details, list
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MoneyFormat:
    currency: str
    exponent: int
    decimal_separator: str
    thousands_separator: str

    @classmethod
    def from_settings(cls, settings: Settings) -> MoneyFormat:
        checkout = settings.checkout
        return cls(
            currency=checkout.currency,
            exponent=currency_exponent(checkout.currency, checkout.currency_exponent) if checkout.currency else 2,
            decimal_separator=checkout.decimal_separator,
            thousands_separator=checkout.thousands_separator,
        )

    def plain(self, minor: int) -> str:
        """The amount as typed in the editor (no currency), e.g. '1,250.00'."""
        return format_amount(minor, self.exponent, self.decimal_separator, self.thousands_separator)


def _product_inputs(op: OperatorText, money: MoneyFormat, product: Product | None) -> tuple[TextInputSpec, ...]:
    example = money.plain(1250 * 10 ** (money.exponent - 2) if money.exponent >= 2 else 1250)
    return (
        TextInputSpec(
            custom_id=INPUT_NAME,
            label=op("catalog.form.name"),
            description=op("catalog.form.name_hint"),
            default=product.name if product else None,
            required=True,
            min_length=1,
            max_length=NAME_MAX,
        ),
        TextInputSpec(
            custom_id=INPUT_PRICE,
            label=op("catalog.form.price", currency=money.currency),
            description=op("catalog.form.price_hint", example=example),
            default=money.plain(product.price_minor) if product else None,
            required=True,
            min_length=1,
            max_length=24,
        ),
        TextInputSpec(
            custom_id=INPUT_STOCK,
            label=op("catalog.form.stock"),
            description=op("catalog.form.stock_hint"),
            default=(str(product.stock_on_hand) if product.stock_on_hand is not None else None) if product else None,
            required=False,
            max_length=10,
        ),
        TextInputSpec(
            custom_id=INPUT_MAX_PER_ORDER,
            label=op("catalog.form.max_per_order"),
            description=op("catalog.form.max_per_order_hint"),
            default=(str(product.max_per_order) if product.max_per_order is not None else None) if product else None,
            required=False,
            max_length=7,
        ),
        TextInputSpec(
            custom_id=INPUT_DESCRIPTION,
            label=op("catalog.form.description"),
            style="paragraph",
            description=op("catalog.form.description_hint"),
            default=(product.description or None) if product else None,
            required=False,
            max_length=DESCRIPTION_MAX,
        ),
    )


def add_form(op: OperatorText, money: MoneyFormat, nonce: int | str) -> ModalSpec:
    """Staff-only form that creates a product. ``nonce`` makes a repeated submission idempotent."""
    return ModalSpec(
        custom_id=add_form_custom_id(nonce),
        title=op("catalog.form.add_title"),
        inputs=_product_inputs(op, money, None),
    )


def edit_form(op: OperatorText, money: MoneyFormat, product: Product) -> ModalSpec:
    """Staff-only form prefilled with ``product``; it carries the product version so stale edits are rejected."""
    return ModalSpec(
        custom_id=edit_form_custom_id(product.id, product.version),
        title=op("catalog.form.edit_title", product_id=product.id),
        inputs=_product_inputs(op, money, product),
    )


def operator_price(ui: Ui, op: OperatorText, money: MoneyFormat, minor: int) -> str:
    """Price for staff views: the configured money format if available, else amount + currency code."""
    return ui.money(minor) or op("catalog.view.amount", amount=money.plain(minor), currency=money.currency)


def status_text(op: OperatorText, product: Product) -> str:
    if product.archived:
        return op("catalog.view.archived")
    return op("catalog.view.active") if product.active else op("catalog.view.inactive")


def product_details_embed(ui: Ui, op: OperatorText, money: MoneyFormat, product: Product) -> EmbedSpec:
    unlimited = op("catalog.view.unlimited")
    none = op("catalog.view.none")
    available = product.available
    fields = [
        EmbedField(op("catalog.view.name"), escape(product.name)),
        EmbedField(op("catalog.view.price"), operator_price(ui, op, money, product.price_minor), inline=True),
        EmbedField(op("catalog.view.status"), status_text(op, product), inline=True),
        EmbedField(op("catalog.view.position"), str(product.sort_order), inline=True),
        EmbedField(
            op("catalog.view.stock"),
            unlimited if product.stock_on_hand is None else str(product.stock_on_hand),
            inline=True,
        ),
        EmbedField(op("catalog.view.reserved"), str(product.reserved), inline=True),
        EmbedField(op("catalog.view.available"), unlimited if available is None else str(available), inline=True),
        EmbedField(
            op("catalog.view.max_per_order"),
            none if product.max_per_order is None else str(product.max_per_order),
            inline=True,
        ),
        EmbedField(op("catalog.view.version"), str(product.version), inline=True),
        EmbedField(op("catalog.view.image"), _image_value(op, product.image_url) or none),
    ]
    embed = ui.operator_embed(
        op("catalog.view.title", product_id=product.id),
        product.description or None,
        fields=fields,
    )
    return replace(embed, thumbnail_url=product.image_url) if product.image_url else embed


def _image_value(op: OperatorText, url: str | None) -> str | None:
    if not url:
        return None
    return url if len(url) <= EMBED_FIELD_VALUE else op("catalog.view.image_long")


def product_list_page(
    ui: Ui,
    op: OperatorText,
    money: MoneyFormat,
    products: Sequence[Product],
    *,
    page: int,
    total: int,
    include_archived: bool,
) -> MessageSpec:
    """Staff product list (``ADMIN_PAGE_SIZE`` per page) with previous/next buttons for managers."""
    pages = max(1, -(-total // ADMIN_PAGE_SIZE))
    lines = []
    for product in products:
        available = product.available
        stock = (
            op("catalog.list.stock_unlimited")
            if product.stock_on_hand is None
            else op("catalog.list.stock", available=available, stock=product.stock_on_hand)
        )
        lines.append(
            op(
                "catalog.list.line",
                product_id=product.id,
                name=escape(product.name),
                price=operator_price(ui, op, money, product.price_minor),
                stock=stock,
                status=status_text(op, product),
            )
        )
    title_key = "catalog.list.title_archived" if include_archived else "catalog.list.title"
    embed = ui.operator_embed(
        op(title_key, total=total),
        "\n".join(lines) if lines else op("catalog.list.empty"),
    )
    if pages > 1:
        embed = replace(embed, footer_text=op("catalog.list.page", page=page + 1, pages=pages))
        buttons = rows_of(
            ButtonSpec(
                label=op("catalog.list.previous"),
                custom_id=admin_page_custom_id(max(0, page - 1), include_archived),
                disabled=page <= 0,
            ),
            ButtonSpec(
                label=op("catalog.list.next"),
                custom_id=admin_page_custom_id(min(pages - 1, page + 1), include_archived),
                disabled=page >= pages - 1,
            ),
        )
        return MessageSpec(embeds=(embed,), rows=buttons)
    return MessageSpec(embeds=(embed,))
