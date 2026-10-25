"""Preview: every new-arrivals UI state, built by the runtime view functions from synthetic sample data.

Sample products, the sample role and the sample channel are obviously synthetic (``[sample product N]``, fixed sample
ids) and exist only in the preview. A state whose text is not configured yet is skipped. States the settings make
impossible are left out (no new-product updates with ``announce_new_products = false``, no restock updates with
``announce_restocks = false``, no mentions with ``mention_role = false``).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.catalog.repo import Product
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals import views
from shopbot.features.new_arrivals.content_keys import NEW_ARRIVALS
from shopbot.features.server_keys import channel_mentions, channel_placeholder, channel_url
from shopbot.ui.spec import MessageSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_ROLE_ID = 100_000_000_000_000_201
SAMPLE_CHANNEL_ID = 100_000_000_000_000_202
SAMPLE_GUILD_ID = 100_000_000_000_000_203


def _sample(index: int, *, price_minor: int, stock_on_hand: int | None, description: str) -> Product:
    return Product(
        id=index,
        guild_id=0,
        name=f"[sample product {index}]",
        description=description,
        price_minor=price_minor,
        image_url=None,
        stock_on_hand=stock_on_hand,
        reserved=0,
        max_per_order=None,
        active=True,
        archived=False,
        sort_order=index,
        version=2,
        created_at=0,
        updated_at=0,
    )


SAMPLE_NEW = _sample(1, price_minor=1250, stock_on_hand=12, description="[sample description 1]")
SAMPLE_RESTOCK = _sample(2, price_minor=500, stock_on_hand=6, description="[sample description 2]")


def sample_values(app: App) -> dict[str, str | int]:
    """Placeholder values for the preview: the sample role and a sample link for every configured channel."""
    values: dict[str, str | int] = {keys.ROLE_PLACEHOLDER: f"<@&{SAMPLE_ROLE_ID}>"}
    for channel in app.settings.server.channels:
        values[channel_placeholder(channel.key)] = f"<#{SAMPLE_CHANNEL_ID}>"
    values.update(channel_mentions(app))
    return values


def preview(app: App) -> list[PreviewState]:
    ui, settings = app.ui, app.settings
    na = settings.new_arrivals
    values = sample_values(app)
    listing = settings.catalog.listing_channel
    catalog_link = (
        (channel_url(app, listing) or f"https://discord.com/channels/{SAMPLE_GUILD_ID}/{SAMPLE_CHANNEL_ID}")
        if listing
        else None
    )
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=NEW_ARRIVALS, name=name, note=note, spec=spec))

    add(
        "panel",
        "Persistent panel posted by /setup in the new-arrivals channel: one button turns notifications on or off.",
        lambda: views.panel_message(ui, values),
    )
    add(
        "subscribed",
        "Private confirmation after a member turned notifications on (they received the notification role).",
        lambda: views.confirmation(ui, subscribed=True, values=values),
    )
    add(
        "unsubscribed",
        "Private confirmation after a member turned notifications off (the notification role was removed).",
        lambda: views.confirmation(ui, subscribed=False, values=values),
    )
    variants: list[tuple[views.Event, Product, str]] = []
    if na.announce_new_products:
        variants.append((views.NEW, SAMPLE_NEW, "a product was added or switched back on"))
    if na.announce_restocks:
        variants.append((views.RESTOCK, SAMPLE_RESTOCK, "a sold-out product can be ordered again"))
    mentions = (False, True) if na.mention_role else (False,)
    for event, product, why in variants:
        for mention in mentions:
            role_id = SAMPLE_ROLE_ID if mention else None
            suffix = "_mention" if mention else ""
            how = "mentioning the notification role" if mention else "without a role mention"
            add(
                f"update_{event}{suffix}",
                f"Automatic update in the new-arrivals channel when {why}, {how}.",
                partial(
                    views.announcement,
                    ui,
                    event,
                    product,
                    role_id=role_id,
                    catalog_url=catalog_link,
                    low_stock_threshold=settings.catalog.low_stock_threshold,
                ),
            )
    return states
