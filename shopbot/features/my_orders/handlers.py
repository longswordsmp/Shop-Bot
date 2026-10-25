"""Paging buttons of the private ``/myorders`` reply (route ``mo``, any member).

The button carries the id of the member the list belongs to and the page number only: the handler checks that the
clicking member is that member, reloads their orders from the database and edits the reply, so the buttons keep
working after a restart and never show another member's orders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features import core_keys as core
from shopbot.features.my_orders import content_keys as k
from shopbot.features.my_orders import service
from shopbot.ui.routing import Ctx, HandlerError

if TYPE_CHECKING:
    from shopbot.context import App


async def on_page(ctx: Ctx) -> None:
    owner = ctx.arg_int(0)
    if owner != ctx.user_id:
        raise HandlerError(core.ERROR_PERMISSION)
    await ctx.update(await service.page_spec(ctx.app, ctx.user_id, ctx.arg_int(1)))


def register(app: App) -> None:
    app.router.component(k.ROUTE, "page", feature=k.FEATURE)(on_page)
