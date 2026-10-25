"""Component handlers (route ``cr``).

- ``sb:cr:balance``: the 'my balance' button (any member of the configured server). Answers privately with the
  clicking member's own balance; the member is taken from the interaction, never from the custom_id.
- ``sb:cr:hpage:<user_id>:<page>``: staff paging through a member's credit history (staff-only route; the
  page is reloaded from the database on every click and clamped to the existing pages).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features import core_keys as ck
from shopbot.features.credits import service
from shopbot.features.credits.content_keys import CREDITS
from shopbot.features.credits.views import ROUTE
from shopbot.ui.routing import Ctx, HandlerError

if TYPE_CHECKING:
    from shopbot.context import App


def staff_role_keys(app: App) -> tuple[str, ...]:
    """Roles that count as staff for credit lookups besides permissions.staff_roles: the checkout staff."""
    return app.settings.checkout.staff_roles


async def on_balance(ctx: Ctx) -> None:
    member = ctx.member
    if member is None or member.bot:
        raise HandlerError(ck.ERROR_PERMISSION)
    spec = await service.balance_reply(ctx.app, member.id)
    await ctx.respond(spec, ephemeral=True)


async def on_history_page(ctx: Ctx) -> None:
    if not ctx.is_staff(staff_role_keys(ctx.app)):  # also enforced by the route's access level
        raise HandlerError(ck.ERROR_PERMISSION)
    user_id = ctx.arg_int(0)
    page = ctx.arg_int(1)
    spec = await service.history_view(ctx.app, user_id, page)
    await ctx.update(spec)


def register_handlers(app: App) -> None:
    router = app.router
    router.component(ROUTE, "balance", feature=CREDITS)(on_balance)
    router.component(ROUTE, "hpage", feature=CREDITS, access="staff", staff_roles=staff_role_keys)(on_history_page)
