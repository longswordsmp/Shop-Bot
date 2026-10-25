"""``/myorders`` logic: load the asking member's own orders and build the requested page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.my_orders import repo, views
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App


async def orders_of(app: App, user_id: int) -> list[repo.CustomerOrder]:
    """The member's own orders in the configured guild, newest first."""
    return await app.db.run(repo.customer_orders, app.env.guild_id, user_id)


async def page_spec(app: App, user_id: int, page: int) -> MessageSpec:
    """Page ``page`` of ``user_id``'s orders (raises ``MissingContent`` when required text is missing)."""
    items = await orders_of(app, user_id)
    return views.my_orders_message(app.ui, app.settings, items, page=page, user_id=user_id)
