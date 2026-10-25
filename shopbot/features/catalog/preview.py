"""Preview: every catalog UI state, built by the runtime view functions from synthetic sample data.

Sample products are obviously synthetic (``[sample product N]``) and exist only in the preview. A state whose
text is not configured yet is skipped (the content store records the missing keys for the checker).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.catalog import views
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.features.catalog.repo import Product
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App


def _sample(
    index: int,
    *,
    price_minor: int,
    stock_on_hand: int | None,
    reserved: int = 0,
    max_per_order: int | None = None,
    description: str = "",
) -> Product:
    return Product(
        id=index,
        guild_id=0,
        name=f"[sample product {index}]",
        description=description,
        price_minor=price_minor,
        image_url=None,
        stock_on_hand=stock_on_hand,
        reserved=reserved,
        max_per_order=max_per_order,
        active=True,
        archived=False,
        sort_order=index,
        version=1,
        created_at=0,
        updated_at=0,
    )


SAMPLE_PRODUCTS: tuple[Product, ...] = (
    _sample(1, price_minor=1250, stock_on_hand=12, reserved=2, max_per_order=3, description="[sample description 1]"),
    _sample(2, price_minor=500, stock_on_hand=None, description="[sample description 2]"),
    _sample(3, price_minor=9900, stock_on_hand=4, reserved=4),
)


def preview(app: App) -> list[PreviewState]:
    ui, op, settings = app.ui, app.op, app.settings
    catalog = settings.catalog
    money = views.MoneyFormat.from_settings(settings)
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=CATALOG, name=name, note=note, spec=spec))

    if catalog.listing_channel:
        try:
            messages = views.listing_messages(ui, settings, SAMPLE_PRODUCTS)
        except MissingContent:
            messages = []
        for index, spec in enumerate(messages, start=1):
            first = "header card + " if index == 1 else ""
            last = " + browse button" if index == len(messages) and catalog.browse_enabled else ""
            states.append(
                PreviewState(
                    feature=CATALOG,
                    name=f"listing_message_{index}",
                    note=f"Live listing message {index}/{len(messages)} in the listing channel: {first}product cards"
                    f"{last}. Sample 1 has limited stock, sample 2 unlimited stock, sample 3 is sold out.",
                    spec=spec,
                )
            )
        add(
            "listing_empty",
            "Live listing when no product is active: header card + the 'empty' card.",
            lambda: views.listing_messages(ui, settings, ())[0],
        )
    if catalog.browse_enabled:
        total = len(SAMPLE_PRODUCTS)
        for page, label in ((0, "first"), (1, "middle"), (total - 1, "last")):
            add(
                f"browse_{label}",
                f"Private browsing (only the clicking member sees it), {label} page: one product per page.",
                partial(views.browse_page, ui, SAMPLE_PRODUCTS[page], page, total),
            )
        add(
            "browse_empty",
            "Private browsing when no product is active.",
            lambda: views.browse_page(ui, None, 0, 0),
        )
    add(
        "product_add_form",
        "Staff-only product form opened by /product add (operator wording).",
        lambda: views.add_form(op, money, 0),
    )
    add(
        "product_edit_form",
        "Staff-only product form opened by /product edit, prefilled (operator wording).",
        lambda: views.edit_form(op, money, SAMPLE_PRODUCTS[0]),
    )
    add(
        "product_details",
        "Staff-only reply to /product view (operator wording).",
        lambda: MessageSpec(embeds=(views.product_details_embed(ui, op, money, SAMPLE_PRODUCTS[0]),)),
    )
    add(
        "product_list",
        "Staff-only reply to /product list, first of two pages (operator wording).",
        lambda: views.product_list_page(
            ui,
            op,
            money,
            SAMPLE_PRODUCTS,
            page=0,
            total=views.ADMIN_PAGE_SIZE + len(SAMPLE_PRODUCTS),
            include_archived=False,
        ),
    )
    return states
