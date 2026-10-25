"""Pure builders for every catalog message and form.

Nothing here talks to Discord or the database: the same functions build what the bot sends, what the
offline limits checker validates, and what the HTML preview renders. Customer-facing wording comes only
from content keys (through ``Ui``); missing required text raises ``MissingContent`` so nothing incomplete
is ever sent. Operator (staff-only) wording comes from ``op`` (``App.op``).

Product names are staff-entered data: they are markdown-escaped wherever they are displayed (button labels
and menu options do not render markdown, so they show names as entered). Product descriptions are business
copy written by an authorized manager and are shown exactly as written (their markdown formatting is
intentional).

Live listing layout
-------------------
The first listing message is the header card (with the private-browsing button). Every following message holds
up to ``catalog.products_per_listing_message`` product cards (1 = one message per product) and, while the checkout
feature is on, one buy button per card (5 per row, in card order). A message is split earlier when it would
exceed 10 cards, 6000 embed characters, or Discord's upload limits for the product images it attaches. Each card's
color shows its availability (success: available, warning: low stock, danger: out of stock); the buy button of an
out-of-stock product is disabled.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import EllipsisType

import discord

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as ck
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog.images import ProductImage, embed_image
from shopbot.features.catalog.repo import (
    DESCRIPTION_MAX,
    NAME_MAX,
    Product,
)
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import (
    BUTTON_LABEL,
    EMBED_FIELD_VALUE,
    EMBED_TOTAL_CHARS,
    EMBEDS_PER_MESSAGE,
    FILES_PER_MESSAGE,
    FILES_TOTAL_BYTES,
)
from shopbot.ui.spec import (
    ButtonSpec,
    Component,
    EmbedField,
    EmbedSpec,
    FileSpec,
    MessageSpec,
    ModalSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui

ROUTE = "ct"
# The buy button is handled by the checkout feature (``shopbot.features.checkout.handlers.on_buy``); the catalog only
# builds its custom_id ``sb:co:buy:<product_id>`` (it carries nothing but the product id).
BUY_ROUTE = "co"
BUY_ACTION = "buy"
OperatorText = Callable[..., str]
PageCustomId = Callable[[int], str]

ADMIN_PAGE_SIZE = 10
INPUT_NAME = "name"
INPUT_PRICE = "price"
INPUT_STOCK = "stock"
INPUT_MAX_PER_ORDER = "max_per_order"
INPUT_DESCRIPTION = "description"

# Availability states of a product card and the embed tone (color) of each.
AVAILABLE = "available"
LOW_STOCK = "low_stock"
OUT_OF_STOCK = "out_of_stock"
STATE_TONES: dict[str, str] = {AVAILABLE: "success", LOW_STOCK: "warning", OUT_OF_STOCK: "danger"}


def browse_custom_id(page: int) -> str:
    """custom_id of the catalog's own browse/paging buttons (``sb:ct:browse:<page>``)."""
    return cid(ROUTE, "browse", page)


def buy_custom_id(product_id: int) -> str:
    """custom_id of a product's buy button (``sb:co:buy:<product_id>``, handled by checkout)."""
    return cid(BUY_ROUTE, BUY_ACTION, product_id)


def admin_page_custom_id(page: int, include_archived: bool) -> str:
    return cid(ROUTE, "apage", page, int(include_archived))


def add_form_custom_id(nonce: int | str) -> str:
    return cid(ROUTE, "addform", nonce)


def edit_form_custom_id(product_id: int, version: int) -> str:
    return cid(ROUTE, "editform", product_id, version)


def remove_custom_id(product_id: int) -> str:
    return cid(ROUTE, "remove", product_id)


def remove_cancel_custom_id(product_id: int) -> str:
    return cid(ROUTE, "remove_no", product_id)


def escape(text: str) -> str:
    return discord.utils.escape_markdown(text)


# ---------------------------------------------------------------------------------------------------
# Customer-facing: product cards
# ---------------------------------------------------------------------------------------------------


def availability_state(product: Product, low_stock_threshold: int = 0) -> str:
    """``AVAILABLE`` (unlimited, or more than the low-stock threshold left), ``LOW_STOCK`` or ``OUT_OF_STOCK``."""
    available = product.available
    if available is None:
        return AVAILABLE
    if available <= 0:
        return OUT_OF_STOCK
    if low_stock_threshold > 0 and available <= low_stock_threshold:
        return LOW_STOCK
    return AVAILABLE


def can_buy(product: Product) -> bool:
    """Whether the product's buy button is enabled: listed and at least one unit available."""
    return product.listed and availability_state(product) != OUT_OF_STOCK


def price_text(ui: Ui, product: Product) -> str:
    text = ui.money(product.price_minor)
    if text is None:
        raise MissingContent(ck.MONEY_FORMAT)
    return text


def availability_text(ui: Ui, product: Product, low_stock_threshold: int = 0) -> str:
    """The configured availability text. ``low_stock_threshold`` must be ``catalog.low_stock_threshold``."""
    available = product.available
    state = availability_state(product, low_stock_threshold)
    values: dict[str, str | int] = {}
    if available is None:
        key = keys.AVAILABILITY_UNLIMITED
    elif state == OUT_OF_STOCK:
        key = keys.AVAILABILITY_OUT_OF_STOCK
    elif state == LOW_STOCK:
        key, values = keys.AVAILABILITY_LOW_STOCK, {"count": available}
    else:
        key, values = keys.AVAILABILITY_IN_STOCK, {"count": available}
    text = ui.text(key, **values)
    if text is None:
        raise MissingContent(key)
    return text


def product_fields(ui: Ui, product: Product, low_stock_threshold: int = 0) -> tuple[EmbedField, ...]:
    """Price and availability fields (required) plus the optional per-order limit field."""
    price = ui.field(keys.FIELD_PRICE, price_text(ui, product), inline=True)
    if price is None:
        raise MissingContent(keys.FIELD_PRICE)
    availability = ui.field(keys.FIELD_AVAILABILITY, availability_text(ui, product, low_stock_threshold), inline=True)
    if availability is None:
        raise MissingContent(keys.FIELD_AVAILABILITY)
    fields = [price, availability]
    if product.max_per_order is not None:
        limit = ui.field(keys.FIELD_MAX_PER_ORDER, str(product.max_per_order), inline=True)
        if limit is not None:
            fields.append(limit)
    return tuple(fields)


def product_embed(
    ui: Ui,
    product: Product,
    *,
    footer_extra: str | None = None,
    image_url: str | EllipsisType | None = ...,
    low_stock_threshold: int = 0,
) -> EmbedSpec:
    """The customer card of one product: name, description (if any), image (if any), price, availability.

    ``image_url`` defaults to the product's image link; pass the result of ``images.embed_image`` /
    ``images.image_for`` to show an uploaded image (and attach its file to the message). The card's color shows the
    availability state (``STATE_TONES``); pass ``catalog.low_stock_threshold`` as ``low_stock_threshold``.
    """
    return ui.embed(
        title=escape(product.name),
        description=product.description or None,
        image_url=product.image_url if isinstance(image_url, EllipsisType) else image_url,
        fields=product_fields(ui, product, low_stock_threshold),
        tone=STATE_TONES[availability_state(product, low_stock_threshold)],
        footer_extra=footer_extra,
    )


def buy_button(ui: Ui, product: Product) -> ButtonSpec:
    """The product's buy button (disabled while it cannot be bought). Never cut: a label made too long by the product
    name is replaced by ``catalog.button.buy_short``."""
    label = ui.text(keys.BUTTON_BUY, name=product.name)
    if label is None:
        raise MissingContent(keys.BUTTON_BUY)
    if len(label) > BUTTON_LABEL:
        label = ui.text(keys.BUTTON_BUY_SHORT)
        if label is None:
            raise MissingContent(keys.BUTTON_BUY_SHORT)
    return ButtonSpec(
        label=label,
        custom_id=buy_custom_id(product.id),
        style="primary",
        emoji=ui.content.get(keys.BUTTON_BUY_EMOJI),
        disabled=not can_buy(product),
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


@dataclass(frozen=True)
class _Card:
    embed: EmbedSpec
    button: ButtonSpec | None
    file: FileSpec | None


def _card_message(cards: Sequence[_Card]) -> MessageSpec:
    return MessageSpec(
        embeds=tuple(c.embed for c in cards),
        rows=rows_of(*(c.button for c in cards)),
        files=tuple(c.file for c in cards if c.file is not None),
    )


def _fits(cards: Sequence[_Card], card: _Card, per_message: int) -> bool:
    if len(cards) >= min(per_message, EMBEDS_PER_MESSAGE):
        return False
    if sum(c.embed.text_length() for c in cards) + card.embed.text_length() > EMBED_TOTAL_CHARS:
        return False
    if card.file is not None:
        files = [c.file for c in cards if c.file is not None]
        if len(files) + 1 > FILES_PER_MESSAGE or sum(f.size for f in files) + card.file.size > FILES_TOTAL_BYTES:
            return False
    return True


def listing_messages(
    ui: Ui,
    settings: Settings,
    products: Sequence[Product],
    images: Mapping[int, ProductImage] | None = None,
    *,
    buy: bool | None = None,
) -> list[MessageSpec]:
    """Every message of the live listing, in channel order (see the module docstring).

    ``images`` are the products' uploaded images by product id; they may be references without bytes (the listing
    refresh loads the bytes only for messages it actually sends or edits). Every message manages its attachments
    (``files`` is a tuple), so an edit keeps, replaces or removes a product image. Without products: the header and
    the "empty" card. ``buy``: show the buy buttons (the caller passes whether checkout is ready; None = whenever the
    checkout feature is switched on, as in previews).
    """
    catalog = settings.catalog
    header = listing_header_embed(ui)
    products = [p for p in products if p.listed]
    if not products:
        return [MessageSpec(embeds=(header, empty_embed(ui)), files=())]
    head_rows = rows_of(browse_button(ui)) if catalog.browse_enabled else ()
    specs = [MessageSpec(embeds=(header,), rows=head_rows, files=())]
    if buy is None:
        buy = settings.features.checkout
    current: list[_Card] = []
    for product in products:
        image = (images or {}).get(product.id)
        image_url, files = embed_image(product, image)
        card = _Card(
            embed=product_embed(ui, product, image_url=image_url, low_stock_threshold=catalog.low_stock_threshold),
            button=buy_button(ui, product) if buy else None,
            file=files[0] if files else None,
        )
        if current and not _fits(current, card, catalog.products_per_listing_message):
            specs.append(_card_message(current))
            current = []
        current.append(card)
    specs.append(_card_message(current))
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
    image: ProductImage | None = None,
    buy: bool = False,
    low_stock_threshold: int = 0,
) -> MessageSpec:
    """One page of private browsing: the product card of ``product`` (page ``page`` of ``total``).

    Previous/next buttons use the shared ``common.button.previous_page``/``next_page`` labels and are
    disabled at the ends (their custom_ids are ``page_custom_id(page - 1)`` / ``page_custom_id(page + 1)``,
    so a disabled button may carry -1 or ``total``). They are omitted when there is a single page.
    With ``buy`` the product's buy button follows, then ``extra_buttons`` (e.g. checkout's "add to cart").
    ``image`` is the product's uploaded image (with its bytes): it is attached and shown. The page always manages
    its attachments, so paging from a product with an uploaded image to one without removes the image.
    With ``product`` None (nothing to browse) the "empty" card is shown and no buttons.
    """
    if product is None or total <= 0:
        return MessageSpec(embeds=(empty_embed(ui),), files=())
    indicator = ui.page_indicator(page + 1, total) if total > 1 else None
    image_url, files = embed_image(product, image)
    embed = product_embed(
        ui, product, footer_extra=indicator, image_url=image_url, low_stock_threshold=low_stock_threshold
    )
    nav: list[Component] = []
    if total > 1:
        previous = ui.button(ck.BUTTON_PREV, page_custom_id(page - 1), disabled=page <= 0)
        if previous is None:
            raise MissingContent(ck.BUTTON_PREV)
        following = ui.button(ck.BUTTON_NEXT, page_custom_id(page + 1), disabled=page >= total - 1)
        if following is None:
            raise MissingContent(ck.BUTTON_NEXT)
        nav = [previous, following]
    actions = ([buy_button(ui, product)] if buy else []) + list(extra_buttons)
    rows = rows_of(*nav) + rows_of(*actions)
    return MessageSpec(embeds=(embed,), rows=rows, files=tuple(files))


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


def product_details_embed(
    ui: Ui, op: OperatorText, money: MoneyFormat, product: Product, image: ProductImage | None = None
) -> EmbedSpec:
    """Staff details of a product. With an uploaded ``image`` the thumbnail is ``attachment://``: send the embed in
    a message carrying ``image.file_spec()`` (see ``product_details_message``)."""
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
        EmbedField(op("catalog.view.image"), _image_value(op, product.image_url, image) or none),
    ]
    embed = ui.operator_embed(
        op("catalog.view.title", product_id=product.id),
        product.description or None,
        fields=fields,
    )
    thumbnail, _ = embed_image(product, image)
    return replace(embed, thumbnail_url=thumbnail) if thumbnail else embed


def product_details_message(
    ui: Ui, op: OperatorText, money: MoneyFormat, product: Product, image: ProductImage | None = None
) -> MessageSpec:
    _, files = embed_image(product, image)
    return MessageSpec(embeds=(product_details_embed(ui, op, money, product, image),), files=tuple(files))


def _image_value(op: OperatorText, url: str | None, image: ProductImage | None) -> str | None:
    if image is not None:
        return op(
            "catalog.view.image_uploaded",
            filename=escape(image.filename),
            kilobytes=max(1, -(-image.size // 1024)),
            type=image.content_type,
        )
    if not url:
        return None
    return url if len(url) <= EMBED_FIELD_VALUE else op("catalog.view.image_long")


def remove_confirm(ui: Ui, op: OperatorText, product: Product) -> MessageSpec:
    """Staff-only confirmation of /product remove (operator wording); the buttons carry only the product id."""
    embed = ui.operator_embed(
        op("catalog.remove.title", product_id=product.id),
        op("catalog.remove.confirm", product_id=product.id, name=escape(product.name)),
        tone="warning",
    )
    rows = rows_of(
        ButtonSpec(label=op("catalog.remove.button_remove"), custom_id=remove_custom_id(product.id), style="danger"),
        ButtonSpec(label=op("catalog.remove.button_keep"), custom_id=remove_cancel_custom_id(product.id)),
    )
    return MessageSpec(embeds=(embed,), rows=rows)


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
