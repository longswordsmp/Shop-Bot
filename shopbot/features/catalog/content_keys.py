"""Customer-facing content keys of the catalog, its shared keys, and its settings checks."""

from __future__ import annotations

import re

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features import core_keys
from shopbot.features.server_keys import reference_problems

CATALOG = "catalog"

LISTING_TITLE = "catalog.listing.title"
LISTING_DESCRIPTION = "catalog.listing.description"
LISTING_EMPTY = "catalog.listing.empty"
FIELD_PRICE = "catalog.field.price"
FIELD_AVAILABILITY = "catalog.field.availability"
FIELD_MAX_PER_ORDER = "catalog.field.max_per_order"
AVAILABILITY_IN_STOCK = "catalog.availability.in_stock"
AVAILABILITY_UNLIMITED = "catalog.availability.unlimited"
AVAILABILITY_OUT_OF_STOCK = "catalog.availability.out_of_stock"
AVAILABILITY_LOW_STOCK = "catalog.availability.low_stock"
BUTTON_BROWSE = "catalog.button.browse"
BUTTON_BUY = "catalog.button.buy"
BUTTON_BUY_SHORT = "catalog.button.buy_short"
BUTTON_BUY_EMOJI = "catalog.button.buy_emoji"

_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def content_keys(settings: Settings) -> list[ContentKey]:
    catalog = settings.catalog
    listing = bool(catalog.listing_channel)
    # The buy button is shown on product cards whenever the checkout feature is switched on.
    buy = settings.features.checkout and (listing or catalog.browse_enabled)
    g_listing = "Catalog: live product listing"
    g_product = "Catalog: product cards (listing and private browsing)"
    g_browse = "Catalog: private browsing"
    g_buy = "Catalog: buy button on product cards"
    keys = [
        ck(
            LISTING_TITLE,
            Kind.TITLE,
            CATALOG,
            g_listing,
            "Title of the header card: the first message of the product listing channel (each product follows "
            "in its own message).",
            required=listing,
        ),
        ck(
            LISTING_DESCRIPTION,
            Kind.DESCRIPTION,
            CATALOG,
            g_listing,
            "Text of the header card at the top of the product listing channel.",
            required=listing,
        ),
        ck(
            LISTING_EMPTY,
            Kind.DESCRIPTION,
            CATALOG,
            g_listing,
            "Shown below the listing header, and in private browsing, when no product is currently offered.",
            required=listing or catalog.browse_enabled,
        ),
        ck(
            FIELD_PRICE,
            Kind.FIELD_NAME,
            CATALOG,
            g_product,
            "Name of the price field on every product card. The value is the price written with common.money_format.",
        ),
        ck(
            FIELD_AVAILABILITY,
            Kind.FIELD_NAME,
            CATALOG,
            g_product,
            "Name of the availability field on every product card. The value is one of the "
            "catalog.availability.* texts.",
        ),
        ck(
            FIELD_MAX_PER_ORDER,
            Kind.FIELD_NAME,
            CATALOG,
            g_product,
            "Optional: name of a field showing the maximum quantity per order (the value is the number). "
            "Shown only on products that have a limit. If empty, the limit is not shown.",
            required=False,
        ),
        ck(
            AVAILABILITY_IN_STOCK,
            Kind.FIELD_VALUE,
            CATALOG,
            g_product,
            "Availability of a product with limited stock that can still be ordered (its card uses the success "
            "color). {count} is the number of units that can still be ordered (stock minus units held by pending "
            "orders).",
            placeholders=("count",),
        ),
        ck(
            AVAILABILITY_UNLIMITED,
            Kind.FIELD_VALUE,
            CATALOG,
            g_product,
            "Availability of a product without a stock limit (its card uses the success color).",
        ),
        ck(
            AVAILABILITY_OUT_OF_STOCK,
            Kind.FIELD_VALUE,
            CATALOG,
            g_product,
            "Availability of a product whose stock is used up (still listed, cannot be ordered; its buy button is "
            "disabled and its card uses the danger color).",
        ),
        ck(
            BUTTON_BROWSE,
            Kind.BUTTON_LABEL,
            CATALOG,
            g_browse,
            "Button under the listing's header card that opens a private, page-by-page view of the products "
            "(only when catalog.browse_enabled is true).",
            required=catalog.browse_enabled and listing,
        ),
        ck(
            BUTTON_BUY,
            Kind.BUTTON_LABEL,
            CATALOG,
            g_buy,
            "Button under each product card (listing and private browsing) that opens the customer's private "
            "order channel, or continues the one they already have, with this product in the cart. {name} is the "
            "product name (use it when catalog.products_per_listing_message is more than 1, so each button names "
            "its product). Disabled while the product is out of stock.",
            required=buy,
            placeholders=("name",),
        ),
        ck(
            BUTTON_BUY_SHORT,
            Kind.BUTTON_LABEL,
            CATALOG,
            g_buy,
            "Optional: label used instead of catalog.button.buy for a product whose name makes that label longer "
            "than Discord's 80 characters. Required only when such a product exists (the listing reports it).",
            required=False,
        ),
        ck(
            BUTTON_BUY_EMOJI,
            Kind.EMOJI,
            CATALOG,
            g_buy,
            "Optional emoji shown on the buy button.",
            required=False,
        ),
    ]
    if catalog.low_stock_threshold > 0:
        keys.append(
            ck(
                AVAILABILITY_LOW_STOCK,
                Kind.FIELD_VALUE,
                CATALOG,
                g_product,
                "Optional: availability of a product with only 1 to catalog.low_stock_threshold units left (its card "
                "uses the warning color). {count} is the number of units that can still be ordered. If empty, "
                "catalog.availability.in_stock is shown.",
                placeholders=("count",),
                fallback=AVAILABILITY_IN_STOCK,
            )
        )
    return keys


def shared_keys(settings: Settings) -> list[str]:
    keys = [core_keys.MONEY_FORMAT]
    if settings.catalog.browse_enabled:
        keys += [core_keys.BUTTON_PREV, core_keys.BUTTON_NEXT]
    return keys


def settings_problems(settings: Settings) -> list[str]:
    catalog = settings.catalog
    problems: list[str] = []
    if not settings.checkout.currency:
        problems.append("settings.checkout.currency is required to show prices")
    problems.extend(
        reference_problems(
            settings,
            roles=catalog.manager_roles,
            channels=(("catalog.listing_channel", catalog.listing_channel),),
        )
    )
    for host in catalog.image_hosts:
        if not _HOST_RE.match(host):
            problems.append(
                f"catalog.image_hosts: '{host}' is not a lowercase host name such as 'cdn.example.com' "
                "(no scheme, path or port)"
            )
    return problems
