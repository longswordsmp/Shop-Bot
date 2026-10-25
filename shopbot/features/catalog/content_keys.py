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
BUTTON_BROWSE = "catalog.button.browse"

_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def content_keys(settings: Settings) -> list[ContentKey]:
    catalog = settings.catalog
    listing = bool(catalog.listing_channel)
    g_listing = "Catalog: live product listing"
    g_product = "Catalog: product cards (listing and private browsing)"
    g_browse = "Catalog: private browsing"
    return [
        ck(
            LISTING_TITLE,
            Kind.TITLE,
            CATALOG,
            g_listing,
            "Title of the header card at the top of the product listing channel.",
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
            "Availability of a product with limited stock that can still be ordered. {count} is the number of "
            "units that can still be ordered (stock minus units held by pending orders).",
            placeholders=("count",),
        ),
        ck(
            AVAILABILITY_UNLIMITED,
            Kind.FIELD_VALUE,
            CATALOG,
            g_product,
            "Availability of a product without a stock limit.",
        ),
        ck(
            AVAILABILITY_OUT_OF_STOCK,
            Kind.FIELD_VALUE,
            CATALOG,
            g_product,
            "Availability of a product whose stock is used up (still listed, cannot be ordered).",
        ),
        ck(
            BUTTON_BROWSE,
            Kind.BUTTON_LABEL,
            CATALOG,
            g_browse,
            "Button under the last listing message that opens a private, page-by-page view of the products "
            "(only when catalog.browse_enabled is true).",
            required=catalog.browse_enabled and listing,
        ),
    ]


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
