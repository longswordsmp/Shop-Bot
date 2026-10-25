"""Product catalog: products and inventory, the live listing, private browsing, and ``/product`` management.

Public API for other features (see the module docstrings for details):
- ``shopbot.features.catalog.repo``: ``Product``, ``get_product``, ``list_products``, ``count_products``,
  ``available_quantity``, ``create_product``, ``update_product``, ``set_stock``, ``add_stock``, and the
  inventory primitives ``reserve`` / ``release`` / ``commit_sale`` (synchronous, conn-first, for use INSIDE
  the caller's ``app.db.transaction``), plus their exceptions.
- ``shopbot.features.catalog.service``: ``browse_view`` (embeddable product browser with extra buttons),
  ``schedule_listing_refresh`` (call after stock changes), ``request_listing_refresh``, ``is_manager``.
- ``shopbot.features.catalog.views``: pure builders, e.g. ``product_embed`` (a product card) and
  ``browse_page``.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.catalog import service, tasks
from shopbot.features.catalog.commands import build_product_group
from shopbot.features.catalog.handlers import register_handlers
from shopbot.features.catalog.preview import preview
from shopbot.features.catalog.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    bot = app.bot
    if bot is None:
        raise RuntimeError("register() requires app.bot")
    register_handlers(app)
    app.scheduler.register(service.REFRESH_TASK, tasks.run_refresh_task)
    bot.tree.add_command(build_product_group(), guild=app.guild_object)
    bot.add_listener("raw_message_delete", partial(tasks.on_raw_message_delete, app))


async def publish(app: App) -> list[str]:
    report = await service.request_listing_refresh(app)
    return list(report.lines)


MODULE = FeatureModule(
    name=catalog_keys.CATALOG,
    is_enabled=lambda settings: settings.features.catalog,
    content_keys=catalog_keys.content_keys,
    settings_problems=catalog_keys.settings_problems,
    shared_keys=catalog_keys.shared_keys,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=tasks.on_ready,
    publish=publish,
    preview=preview,
)
