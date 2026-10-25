"""Preview: every catalog UI state, built by the runtime view functions from synthetic sample data.

Sample products are obviously synthetic (``[sample product N]``) and exist only in the preview; the sample uploaded
image is a small generated grey PNG. A state whose text is not configured yet is skipped (the content store records
the missing keys for the checker).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.catalog import images, service, views
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.features.catalog.repo import Product
from shopbot.ui.spec import EmbedField, MessageSpec, ModalSpec, PreviewState

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
    _sample(4, price_minor=750, stock_on_hand=1),
)


def sample_png(size: int = 48) -> bytes:
    """A small solid grey PNG (synthetic sample image; generated, never a real product photo)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8-bit greyscale
    rows = b"".join(b"\x00" + bytes([0x99]) * size for _ in range(size))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def sample_image(product: Product) -> images.ProductImage:
    data = sample_png()
    return images.ProductImage(
        product_id=product.id,
        guild_id=0,
        filename="[sample image].png",
        content_type="image/png",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        uploaded_by=None,
        created_at=0,
        data=data,
    )


def preview(app: App) -> list[PreviewState]:
    ui, op, settings = app.ui, app.op, app.settings
    catalog = settings.catalog
    money = views.MoneyFormat.from_settings(settings)
    threshold = catalog.low_stock_threshold
    buy = settings.features.checkout
    states: list[PreviewState] = []
    uploaded = {SAMPLE_PRODUCTS[0].id: sample_image(SAMPLE_PRODUCTS[0])}

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=CATALOG, name=name, note=note, spec=spec))

    def availability(product: Product) -> str:
        state = views.availability_state(product, threshold)
        return {
            views.AVAILABLE: "available (success color" + (", buy button enabled)" if buy else ")"),
            views.LOW_STOCK: "low stock (warning color" + (", buy button enabled)" if buy else ")"),
            views.OUT_OF_STOCK: "out of stock (danger color" + (", buy button disabled)" if buy else ")"),
        }[state]

    if catalog.listing_channel:
        try:
            messages = views.listing_messages(ui, settings, SAMPLE_PRODUCTS, uploaded)
        except MissingContent:
            messages = []
        by_title = {views.escape(p.name): p for p in SAMPLE_PRODUCTS}
        for index, spec in enumerate(messages):
            if index == 0:
                browse = " with the private-browsing button" if catalog.browse_enabled else ""
                note = f"Live listing, first message in the listing channel: the header card{browse}."
            else:
                shown = [by_title[e.title] for e in spec.embeds if e.title in by_title]
                parts = [
                    f"{p.name}: {availability(p)}" + (", uploaded image attached" if p.id in uploaded else "")
                    for p in shown
                ]
                note = f"Live listing message {index + 1}/{len(messages)}: " + "; ".join(parts) + "."
            states.append(PreviewState(feature=CATALOG, name=f"listing_message_{index + 1}", note=note, spec=spec))
        add(
            "listing_empty",
            "Live listing when no product is active: header card + the 'empty' card.",
            lambda: views.listing_messages(ui, settings, ())[0],
        )
    if catalog.browse_enabled:
        total = len(SAMPLE_PRODUCTS)
        for page, label in ((0, "first"), (1, "middle"), (2, "sold_out"), (total - 1, "last")):
            product = SAMPLE_PRODUCTS[page]
            extra = ", with its uploaded image" if product.id in uploaded else ""
            add(
                f"browse_{label}",
                f"Private browsing (only the clicking member sees it), page {page + 1}/{total}: "
                f"{availability(product)}{extra}.",
                partial(
                    views.browse_page,
                    ui,
                    product,
                    page,
                    total,
                    image=uploaded.get(product.id),
                    buy=buy,
                    low_stock_threshold=threshold,
                ),
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
        "Staff-only reply to /product view of a product with an uploaded image (operator wording).",
        lambda: views.product_details_message(ui, op, money, SAMPLE_PRODUCTS[0], uploaded[SAMPLE_PRODUCTS[0].id]),
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
    add(
        "product_remove_confirm",
        "Staff-only confirmation of /product remove (operator wording): Remove archives the product, Keep cancels.",
        lambda: views.remove_confirm(ui, op, SAMPLE_PRODUCTS[1]),
    )
    sample_id = SAMPLE_PRODUCTS[0].id
    listing_field = (EmbedField(op("catalog.refresh.field"), op("catalog.refresh.not_ready")),)
    replies = (
        (
            "reply_image_uploaded",
            "Staff-only reply to /product image with an uploaded file, with the listing refresh report.",
            service.OpReply(
                op("catalog.cmd.image_set", product_id=sample_id),
                tone="success",
                fields=listing_field,
                banner=service.SAVED,
            ),
        ),
        (
            "reply_image_rejected",
            "Staff-only reply to /product image when the uploaded file is not an accepted image.",
            service.OpReply(
                op("catalog.cmd.invalid"), op("catalog.cmd.image_type"), tone="warning", banner=service.REFUSED
            ),
        ),
        (
            "reply_removed",
            "Staff-only result of the confirmed /product remove.",
            service.OpReply(
                op("catalog.cmd.removed", product_id=sample_id),
                tone="success",
                fields=listing_field,
                banner=service.SAVED,
            ),
        ),
        (
            "reply_remove_reserved",
            "Staff-only reply when a product to remove still has units held by pending orders.",
            service.OpReply(
                op("catalog.cmd.remove_reserved", product_id=sample_id, reserved=2),
                tone="warning",
                banner=service.REFUSED,
            ),
        ),
    )
    for name, note, reply in replies:
        add(name, note, partial(service.reply_spec, app, reply))
    return states
