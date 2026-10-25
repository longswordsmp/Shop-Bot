"""Pure builders for every new-arrivals message (no Discord or database calls).

The same functions build what the bot sends, what the offline limits checker validates, and what the HTML preview
renders. Wording comes only from content keys through ``Ui``; missing required text raises ``MissingContent`` so
nothing incomplete is ever sent. Product names are staff-entered data and are markdown-escaped; product descriptions
are business copy and are shown exactly as written (like the catalog cards). Price and availability fields are the
catalog's own ``product_fields``, so a product reads the same in #catalog and in #new-arrivals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from shopbot.config.content import MissingContent
from shopbot.features.catalog import views as catalog_views
from shopbot.features.catalog.repo import Product
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, FileSpec, MessageSpec, rows_of
from shopbot.ui.style import Ui

ROUTE = "na"
TOGGLE_ACTION = "toggle"

Event = Literal["new", "restock"]
NEW: Event = "new"
RESTOCK: Event = "restock"
EVENTS: tuple[Event, ...] = (NEW, RESTOCK)

Values = Mapping[str, str | int]


def toggle_custom_id() -> str:
    """The panel button (``sb:na:toggle``). It carries no state: the handler reads the member's current roles."""
    return cid(ROUTE, TOGGLE_ACTION)


def _require(ui: Ui, key: str, values: Values | None = None) -> str:
    text = ui.text(key, **dict(values or {}))
    if text is None:
        raise MissingContent(key)
    return text


# ---------------------------------------------------------------------------------------------------
# Opt-in panel and private confirmations
# ---------------------------------------------------------------------------------------------------


def panel_message(ui: Ui, values: Values) -> MessageSpec:
    """The persistent panel: title, description and the single on/off button.

    No timestamp: the panel is compared with its published copy by content hash, so /setup edits it only when its
    text actually changed.
    """
    title = _require(ui, keys.PANEL_TITLE)
    description = _require(ui, keys.PANEL_DESCRIPTION, values)
    if not ui.content.has(keys.BUTTON_TOGGLE):
        raise MissingContent(keys.BUTTON_TOGGLE)
    button = ui.button(keys.BUTTON_TOGGLE, toggle_custom_id(), style="primary", emoji_key=keys.BUTTON_TOGGLE_EMOJI)
    if button is None:  # pragma: no cover - the label is checked above
        raise MissingContent(keys.BUTTON_TOGGLE)
    embed = ui.embed(title=title, description=description, tone="primary", panel=True)
    return MessageSpec(embeds=(embed,), rows=rows_of(button))


def confirmation(ui: Ui, *, subscribed: bool, values: Values) -> MessageSpec:
    """Private answer to a click: notifications are now on (``subscribed``) or off."""
    if subscribed:
        title_key, description_key, tone = keys.SUBSCRIBED_TITLE, keys.SUBSCRIBED_DESCRIPTION, "success"
    else:
        title_key, description_key, tone = keys.UNSUBSCRIBED_TITLE, keys.UNSUBSCRIBED_DESCRIPTION, "neutral"
    description = _require(ui, description_key, values)
    embed = ui.embed(title=ui.text(title_key), description=description, tone=tone, timestamp=True)
    return MessageSpec(embeds=(embed,))


# ---------------------------------------------------------------------------------------------------
# Automatic product updates
# ---------------------------------------------------------------------------------------------------


def product_image_url(product: Product) -> str | None:
    """The linked image of ``product`` (used when no uploaded image was looked up)."""
    return product.image_url or None


def announcement(
    ui: Ui,
    event: Event,
    product: Product,
    *,
    role_id: int | None,
    catalog_url: str | None,
    image: tuple[str | None, Sequence[FileSpec]] | None = None,
    low_stock_threshold: int = 0,
) -> MessageSpec:
    """The update posted in #new-arrivals for ``event`` ("new" or "restock").

    ``role_id``: the notification role to mention (only that role may be pinged), or None for no mention.
    ``catalog_url``: link of the catalog listing channel for the optional link button (None: no button).
    ``image``: what the product's catalog card shows (``catalog.images.image_for``: embed image URL and the uploaded
    file to attach); None falls back to the product's linked image.
    """
    image_url, files = image if image is not None else (product_image_url(product), ())
    if event == NEW:
        title_key, footer_key, tone = keys.NEW_PRODUCT_TITLE, keys.NEW_PRODUCT_FOOTER, "primary"
    else:
        title_key, footer_key, tone = keys.RESTOCK_TITLE, keys.RESTOCK_FOOTER, "success"
    title = _require(ui, title_key, {keys.NAME_PLACEHOLDER: catalog_views.escape(product.name)})
    embed = ui.embed(
        title=title,
        description=product.description or None,
        image_url=image_url,
        fields=catalog_views.product_fields(ui, product, low_stock_threshold),
        tone=tone,
        timestamp=True,
        footer_extra=ui.text(footer_key),
    )
    label = ui.text(keys.BUTTON_VIEW_CATALOG)
    link = ButtonSpec(label=label, style="link", url=catalog_url) if label and catalog_url else None
    return MessageSpec(
        embeds=(embed,),
        rows=rows_of(link),
        mention_role_ids=(role_id,) if role_id else (),
        files=tuple(files) or None,
    )
