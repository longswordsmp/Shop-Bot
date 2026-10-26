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
The first listing message is the header card with the category menu (``category_picker``: "All", then every category;
always shown, also before the first category and while nothing is listed) and the private-browsing button
(``catalog.browse_enabled``). With ``catalog.product_cards = false`` (shipped) the header is the only message: members
see the items privately through the menu. With ``true`` every following message holds up to
``catalog.products_per_listing_message`` product cards (1 = one message per product) and, while the checkout feature
is on, one buy button per card (5 per row, in card order). A message is split earlier when it would exceed 10 cards,
6000 embed characters, or Discord's upload limits for the files it attaches (product images and the message's
banner). Each card's color shows its availability (success: available, warning: low stock, danger: out of stock); the
buy button of an out-of-stock product is disabled.

Banners
-------
Only the first listing message (the header) shows the ``catalog`` banner; the product-card messages show none.
Private browsing and a category's private view show ``catalog`` on every page (paging edits the same message), above
the cards. Staff messages: product details, product list, category list -> ``staff-dashboard``; the remove
confirmation -> ``general-response`` (the second message of /product view, the customer card preview, shows none).
Banners are never shown inside a product card's picture slot.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import EllipsisType

import discord

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.config.settings import CatalogSettings, Settings
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as ck
from shopbot.features.catalog import content_keys as keys
from shopbot.features.catalog.categories import ALL, MAX_CATEGORIES, Category
from shopbot.features.catalog.images import ProductImage, embed_image
from shopbot.features.catalog.repo import (
    DESCRIPTION_MAX,
    NAME_MAX,
    Product,
)
from shopbot.ui.card import with_paragraph
from shopbot.ui.component_emoji import split_label
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import (
    BUTTON_LABEL,
    EMBED_DESCRIPTION,
    EMBED_FIELD_VALUE,
    EMBED_TOTAL_CHARS,
    EMBEDS_PER_MESSAGE,
    FILES_PER_MESSAGE,
    FILES_TOTAL_BYTES,
    message_files,
)
from shopbot.ui.spec import (
    ButtonSpec,
    Component,
    EmbedField,
    EmbedSpec,
    FileSpec,
    MessageSpec,
    ModalSpec,
    SelectOptionSpec,
    SelectSpec,
    TextInputSpec,
    rows_of,
)
from shopbot.ui.style import Ui, chunk_lines

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


def picker_custom_id() -> str:
    """custom_id of the category menu on the listing header (``sb:ct:cat``; the picked value is the category id, or
    0 for "All")."""
    return cid(ROUTE, "cat")


def category_page_custom_id(choice: int, page: int) -> str:
    """custom_id of the paging buttons of a category's private view (``sb:ct:catpage:<category id or 0>:<page>``)."""
    return cid(ROUTE, "catpage", choice, page)


def admin_page_custom_id(page: int, include_archived: bool) -> str:
    return cid(ROUTE, "apage", page, int(include_archived))


def add_form_custom_id(nonce: int | str, *, with_image: bool = False) -> str:
    """``with_image``: /product add came with a picture (saved to the product once the form is submitted)."""
    return cid(ROUTE, "addform", nonce, 1) if with_image else cid(ROUTE, "addform", nonce)


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
    branding_text: bool = True,
) -> EmbedSpec:
    """The customer card of one product: name, description (if any), image (if any), price, availability.

    ``image_url`` defaults to the product's image link; pass the result of ``images.embed_image`` /
    ``images.image_for`` to show an uploaded image (and attach its file to the message). The card's color shows the
    availability state (``STATE_TONES``); pass ``catalog.low_stock_threshold`` as ``low_stock_threshold``.
    ``branding_text=False`` leaves out the author line and footer (a card under another card that carries them).
    """
    return ui.embed(
        title=escape(product.name),
        description=product.description or None,
        image_url=product.image_url if isinstance(image_url, EllipsisType) else image_url,
        fields=product_fields(ui, product, low_stock_threshold),
        tone=STATE_TONES[availability_state(product, low_stock_threshold)],
        footer_extra=footer_extra,
        branding_text=branding_text,
    )


def _visible_label(label: str) -> int:
    """A label's length as Discord gets it: a leading icon is the button's emoji."""
    return len(split_label(label, None)[0] or "")


def buy_button(ui: Ui, product: Product, label_key: str = keys.BUTTON_BUY, *, cut_name: bool = False) -> ButtonSpec:
    """The product's buy button (disabled while it cannot be bought). ``label_key``: ``catalog.button.buy``, or
    ``catalog.category.button_buy`` under the cards of a category's private view. A label made longer than Discord
    allows by the product name is replaced by ``catalog.button.buy_short``; with ``cut_name`` (several buy buttons
    share the message, so each must keep naming its item) the name is cut to fit and ends with "…" instead."""
    label = ui.text(label_key, name=product.name)
    if label is None:
        raise MissingContent(label_key)
    over = _visible_label(label) - BUTTON_LABEL
    if over > 0 and cut_name:
        name = product.name[: max(1, len(product.name) - over - 1)].rstrip() + "…"
        label = ui.text(label_key, name=name) or label
        over = _visible_label(label) - BUTTON_LABEL
    if over > 0:
        label = ui.text(keys.BUTTON_BUY_SHORT)
        if label is None:
            raise MissingContent(keys.BUTTON_BUY_SHORT)
    return ButtonSpec(
        label=label,
        custom_id=buy_custom_id(product.id),
        style="primary",
        emoji=ui.content.render(keys.BUTTON_BUY_EMOJI),
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


def category_emoji(ui: Ui, emoji: str) -> str | None:
    """A category's own emoji as its menu option shows it: one of the bot's icons once it is uploaded (None before),
    or the unicode emoji. None without one."""
    if not emoji:
        return None
    if emoji.startswith("{icon_"):
        return ui.content.render_icons(emoji, label=True) or None
    return emoji


def all_name(ui: Ui) -> str:
    """The name of the built-in "All" choice: the menu option's label without its icon."""
    label = ui.text(keys.PICKER_ALL)
    if label is None:
        raise MissingContent(keys.PICKER_ALL)
    return split_label(label, None)[0] or label


def category_picker(ui: Ui, categories: Sequence[Category]) -> SelectSpec:
    """The category menu under the listing's header card: "All" first, then the categories in their order (each with
    its name, emoji and description, as staff entered them; menu options do not render markdown). A category without
    an emoji shows the icon the "All" option starts with, so the options line up."""
    placeholder = ui.text(keys.PICKER_PLACEHOLDER)
    if placeholder is None:
        raise MissingContent(keys.PICKER_PLACEHOLDER)
    all_label = ui.text(keys.PICKER_ALL)
    everything = ui.option(all_label, str(ALL), description=ui.text(keys.PICKER_ALL_DESCRIPTION))
    if everything is None:
        raise MissingContent(keys.PICKER_ALL)
    no_emoji = split_label(all_label, None)[1]  # the "All" option's uploaded icon, or None
    options = [everything] + [
        SelectOptionSpec(
            label=category.name,
            value=str(category.id),
            description=category.description or None,
            emoji=category_emoji(ui, category.emoji) or no_emoji,
        )
        for category in categories[:MAX_CATEGORIES]
    ]
    return SelectSpec(custom_id=picker_custom_id(), options=tuple(options), placeholder=placeholder)


def header_rows(ui: Ui, catalog: CatalogSettings, categories: Sequence[Category]) -> tuple[tuple[Component, ...], ...]:
    """The components of the listing's header card: the category menu, always (before the first category its only
    choice is "All"; while nothing is listed a pick answers that there is nothing yet), then the private-browsing
    button (``catalog.browse_enabled``)."""
    browse = browse_button(ui) if catalog.browse_enabled else None
    return rows_of(category_picker(ui, categories), browse)


@dataclass(frozen=True)
class _Card:
    embed: EmbedSpec
    button: ButtonSpec | None
    file: FileSpec | None


def _card_message(cards: Sequence[_Card], banner: FileSpec | None = None) -> MessageSpec:
    return MessageSpec(
        embeds=tuple(c.embed for c in cards),
        rows=rows_of(*(c.button for c in cards)),
        files=tuple(c.file for c in cards if c.file is not None),
        banner=banner,
    )


def _fits(cards: Sequence[_Card], card: _Card, per_message: int, banner: FileSpec | None = None) -> bool:
    """Whether ``card`` can join the message holding ``cards``. ``banner``: the banner of that message; its file
    counts toward the upload limits like the product images (``message_files``, as the limits checker counts)."""
    if len(cards) >= min(per_message, EMBEDS_PER_MESSAGE):
        return False
    if sum(c.embed.text_length() for c in cards) + card.embed.text_length() > EMBED_TOTAL_CHARS:
        return False
    if card.file is not None:
        files = message_files(_card_message([*cards, card], banner))
        if len(files) > FILES_PER_MESSAGE or sum(f.size for f in files) > FILES_TOTAL_BYTES:
            return False
    return True


def listing_messages(
    ui: Ui,
    settings: Settings,
    products: Sequence[Product],
    images: Mapping[int, ProductImage] | None = None,
    *,
    buy: bool | None = None,
    categories: Sequence[Category] = (),
) -> list[MessageSpec]:
    """Every message of the live listing, in channel order (see the module docstring).

    ``images`` are the products' uploaded images by product id; they may be references without bytes (the listing
    refresh loads the bytes only for messages it actually sends or edits). Every message manages its attachments
    (``files`` is a tuple), so an edit keeps, replaces or removes a product image. The header card ends with
    ``catalog.picker.description`` (optional) and, without products, the "empty" text; its menu is always there.
    ``buy``: show the buy buttons (the caller passes whether checkout is ready; None = whenever the checkout feature
    is switched on, as in previews). ``categories``: the menu's categories, in order. Only the first message (the
    header) carries the ``catalog`` banner; the card messages carry none.
    """
    catalog = settings.catalog
    header = listing_header_embed(ui)
    banner = ui.banner("catalog")
    products = [p for p in products if p.listed]
    notes = (ui.text(keys.PICKER_DESCRIPTION), None if products else empty_embed(ui).description)
    head = with_paragraph(header, "\n\n".join(n for n in notes if n) or None)
    specs = [MessageSpec(embeds=head, rows=header_rows(ui, catalog, categories), files=(), banner=banner)]
    if not products or not catalog.product_cards:
        return specs
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
        if current and not _fits(current, card, catalog.products_per_listing_message):  # card messages: no banner
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
    banner_id: BannerId = "catalog",
) -> MessageSpec:
    """One page of private browsing: the product card of ``product`` (page ``page`` of ``total``).

    Previous/next buttons use the shared ``common.button.previous_page``/``next_page`` labels and are
    disabled at the ends (their custom_ids are ``page_custom_id(page - 1)`` / ``page_custom_id(page + 1)``,
    so a disabled button may carry -1 or ``total``). They are omitted when there is a single page.
    With ``buy`` the product's buy button follows, then ``extra_buttons`` (e.g. checkout's "add to cart").
    ``image`` is the product's uploaded image (with its bytes): it is attached and shown. The page always manages
    its attachments, so paging from a product with an uploaded image to one without removes the image.
    With ``product`` None (nothing to browse) the "empty" card is shown and no buttons.
    Every page shows the ``banner_id`` banner (paging keeps it; another feature embedding the browser may pass its
    own).
    """
    banner = ui.banner(banner_id)
    if product is None or total <= 0:
        return MessageSpec(embeds=(empty_embed(ui),), files=(), banner=banner)
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
    # A product card: the banner stays above it, so it is never taken for the product's photo.
    return MessageSpec(embeds=(embed,), rows=rows, files=tuple(files), banner=banner, banner_in_card=False)


# ---------------------------------------------------------------------------------------------------
# Customer-facing: a category's private view (a title card over product cards, page by page)
# ---------------------------------------------------------------------------------------------------


def category_title(ui: Ui, name: str) -> str:
    title = ui.text(keys.CATEGORY_TITLE, category=escape(name))
    if title is None:
        raise MissingContent(keys.CATEGORY_TITLE)
    return title


def _category_head(ui: Ui, name: str, count: int, indicator: str | None) -> EmbedSpec:
    return ui.embed(
        title=category_title(ui, name),
        description=ui.text(keys.CATEGORY_INTRO, category=escape(name), count=count),
        footer_extra=indicator,
    )


def _page_fits(head: EmbedSpec, cards: Sequence[_Card], card: _Card, per_page: int, banner: FileSpec | None) -> bool:
    """Whether ``card`` can join the page holding ``cards`` under the title card ``head`` (the files count with the
    banner, as the limits checker counts them)."""
    if len(cards) >= min(per_page, EMBEDS_PER_MESSAGE - 1):
        return False
    if head.text_length() + sum(c.embed.text_length() for c in cards) + card.embed.text_length() > EMBED_TOTAL_CHARS:
        return False
    if card.file is not None:
        files = message_files(_card_message([*cards, card], banner))
        if len(files) > FILES_PER_MESSAGE or sum(f.size for f in files) > FILES_TOTAL_BYTES:
            return False
    return True


def category_page(
    ui: Ui,
    settings: Settings,
    products: Sequence[Product],
    images: Mapping[int, ProductImage] | None = None,
    *,
    choice: int,
    name: str | None = None,
    page: int = 0,
    buy: bool | None = None,
) -> MessageSpec:
    """Page ``page`` (0-based, clamped to the existing pages) of the private view a pick in the category menu opens.

    A title card (``catalog.category.title`` with the category's name, ``catalog.category.intro`` and the page
    indicator) over up to ``catalog.products_per_category_page`` product cards, then one buy button per card, named
    after its product (``catalog.category.button_buy``, a long name cut to fit; 5 per row, in card order; only with
    ``buy``), and the
    previous/next buttons when there is more than one page. A page also ends early when the next card would pass
    Discord's limits (10 embeds, 6000 embed characters, 10 files or 10 MiB with the banner). ``choice``: the category id
    (``categories.ALL``: every listed product; ``name`` None then shows the "All" option's name). ``images``: uploaded
    images by product id, as references without bytes (the caller loads the files of the page). Without products the
    card says so: ``catalog.category.empty`` for a category, the shop's own "empty" text (``catalog.listing.empty``)
    for "All", since then nothing at all is listed. The ``catalog`` banner shows above the cards, never in a card's
    picture slot, where it would read as a product photo.
    """
    catalog = settings.catalog
    shown = all_name(ui) if name is None else name
    banner = ui.banner("catalog")
    products = [p for p in products if p.listed]
    if not products:
        if choice == ALL:
            text = empty_embed(ui).description
        else:
            text = ui.text(keys.CATEGORY_EMPTY, category=escape(shown))
            if text is None:
                raise MissingContent(keys.CATEGORY_EMPTY)
        embed = ui.embed(title=category_title(ui, shown), description=text, tone="neutral")
        return MessageSpec(embeds=(embed,), files=(), banner=banner)
    if buy is None:
        buy = settings.features.checkout
    # Pages are packed with the longest page indicator the view can show, so every page fits with its own.
    widest = _category_head(ui, shown, len(products), ui.page_indicator(len(products), len(products)))
    pages: list[list[_Card]] = []
    current: list[_Card] = []
    for product in products:
        image_url, files = embed_image(product, (images or {}).get(product.id))
        card = _Card(
            embed=product_embed(
                ui,
                product,
                image_url=image_url,
                low_stock_threshold=catalog.low_stock_threshold,
                branding_text=False,  # the title card above carries the author line and footer
            ),
            button=buy_button(ui, product, keys.CATEGORY_BUTTON_BUY, cut_name=True) if buy else None,
            file=files[0] if files else None,
        )
        if current and not _page_fits(widest, current, card, catalog.products_per_category_page, banner):
            pages.append(current)
            current = []
        current.append(card)
    pages.append(current)
    page = min(max(0, page), len(pages) - 1)
    cards = pages[page]
    nav: list[Component] = []
    if len(pages) > 1:
        previous = ui.button(ck.BUTTON_PREV, category_page_custom_id(choice, page - 1), disabled=page <= 0)
        if previous is None:
            raise MissingContent(ck.BUTTON_PREV)
        following = ui.button(
            ck.BUTTON_NEXT, category_page_custom_id(choice, page + 1), disabled=page >= len(pages) - 1
        )
        if following is None:
            raise MissingContent(ck.BUTTON_NEXT)
        nav = [previous, following]
    indicator = ui.page_indicator(page + 1, len(pages)) if len(pages) > 1 else None
    return MessageSpec(
        embeds=(_category_head(ui, shown, len(products), indicator), *(c.embed for c in cards)),
        rows=rows_of(*(c.button for c in cards)) + rows_of(*nav),
        files=tuple(c.file for c in cards if c.file is not None),
        banner=banner,
        banner_in_card=False,
    )


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


def add_form(op: OperatorText, money: MoneyFormat, nonce: int | str, *, with_image: bool = False) -> ModalSpec:
    """Staff-only form that creates a product. ``nonce`` makes a repeated submission idempotent; ``with_image``: a
    picture came with the command."""
    return ModalSpec(
        custom_id=add_form_custom_id(nonce, with_image=with_image),
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
    ui: Ui,
    op: OperatorText,
    money: MoneyFormat,
    product: Product,
    image: ProductImage | None = None,
    categories: Sequence[Category] = (),
) -> EmbedSpec:
    """Staff details of a product (with the ``categories`` it is in). With an uploaded ``image`` the thumbnail is
    ``attachment://``: send the embed in a message carrying ``image.file_spec()`` (see ``product_details_message``)."""
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
        EmbedField(op("catalog.view.categories"), ", ".join(escape(c.name) for c in categories) or none),
    ]
    embed = ui.operator_embed(
        op("catalog.view.title", product_id=product.id),
        product.description or None,
        fields=fields,
    )
    thumbnail, _ = embed_image(product, image)
    return replace(embed, thumbnail_url=thumbnail) if thumbnail else embed


def product_details_message(
    ui: Ui,
    op: OperatorText,
    money: MoneyFormat,
    product: Product,
    image: ProductImage | None = None,
    categories: Sequence[Category] = (),
) -> MessageSpec:
    """Staff details of a product (the first message of /product view: it shows the ``staff-dashboard`` banner)."""
    _, files = embed_image(product, image)
    return MessageSpec(
        embeds=(product_details_embed(ui, op, money, product, image, categories),),
        files=tuple(files),
        banner=ui.banner("staff-dashboard"),
    )


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
    return MessageSpec(embeds=(embed,), rows=rows, banner=ui.banner("general-response"))


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
    """Staff product list (``ADMIN_PAGE_SIZE`` per page) with previous/next buttons for managers (every page shows
    the ``staff-dashboard`` banner)."""
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
        return MessageSpec(embeds=(embed,), rows=buttons, banner=ui.banner("staff-dashboard"))
    return MessageSpec(embeds=(embed,), banner=ui.banner("staff-dashboard"))


# ---------------------------------------------------------------------------------------------------
# Operator (staff-only): categories
# ---------------------------------------------------------------------------------------------------


def category_line(ui: Ui, op: OperatorText, category: Category, counts: tuple[int, int]) -> str:
    """One category in staff replies: number, emoji, name, its items, position and description."""
    listed, hidden = counts
    line = op(
        "catalog.category.list.line",
        category_id=category.id,
        emoji=ui.with_icons(category.emoji) if category.emoji else op("catalog.category.list.no_emoji"),
        name=escape(category.name),
        items=listed,
        position=category.position,
    )
    if hidden:
        line += op("catalog.category.list.hidden", hidden=hidden)
    if category.description:
        line += "\n" + op("catalog.category.list.description", description=escape(category.description))
    return line


def category_list_messages(
    ui: Ui,
    op: OperatorText,
    categories: Sequence[Category],
    counts: Mapping[int, tuple[int, int]],
    all_count: int,
) -> list[MessageSpec]:
    """Staff reply to /category list: "All" and every category in menu order with its items. One message with one
    card; when the descriptions make the list longer than one card holds, it continues in a further message with one
    card each (only the first shows the ``staff-dashboard`` banner)."""
    lines = [op("catalog.category.list.all", items=all_count)]
    lines += [category_line(ui, op, c, counts.get(c.id, (0, 0))) for c in categories]
    if not categories:
        lines.append(op("catalog.category.list.empty"))
    title = op("catalog.category.list.title", count=len(categories), max=MAX_CATEGORIES)
    blocks = chunk_lines(lines, EMBED_DESCRIPTION)
    embeds = [ui.operator_embed(title, block) for block in blocks]
    embeds[1:] = [replace(e, title=None) for e in embeds[1:]]  # a continuation of the same list
    messages = [MessageSpec(embeds=(embed,)) for embed in embeds]
    messages[0] = replace(messages[0], banner=ui.banner("staff-dashboard"))
    return messages
