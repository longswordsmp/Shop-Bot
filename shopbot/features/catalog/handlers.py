"""Component and form handlers (route ``ct``).

The category menu on the listing header keeps showing the option a member picked (Discord does not reset a select),
so every pick is acknowledged by re-rendering the header's components; otherwise the same category could not be
picked again. The category's products follow in a private message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.features import core_keys as ck
from shopbot.features.catalog import categories, service
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.features.catalog.views import ROUTE
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import to_view
from shopbot.ui.routing import Ctx, HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def _is_ephemeral(message: discord.Message | None) -> bool:
    return message is not None and bool(message.flags.ephemeral)


def _require_manager(ctx: Ctx) -> None:
    if not service.is_manager(ctx.app, ctx.member, ctx.user_id):
        raise HandlerError(ck.ERROR_PERMISSION)


async def on_browse(ctx: Ctx) -> None:
    """Private browsing. From the public listing: a new private message; inside it: edit in place."""
    if not ctx.app.settings.catalog.browse_enabled:
        raise HandlerError(ck.ERROR_UNAVAILABLE)
    page = ctx.arg_int(0)
    spec = await service.browse_view(ctx.app, page)
    if _is_ephemeral(ctx.interaction.message):
        await ctx.update(spec)
    else:
        await ctx.respond(spec, ephemeral=True)


async def _ack_by_resetting_menu(ctx: Ctx) -> bool:
    """Acknowledge a pick by re-rendering the header's components (no option selected); False when that is not
    possible, and the caller acknowledges differently."""
    rows = await service.header_rows(ctx.app)
    if rows is None or ctx.interaction.message is None or ctx.responded:
        return False
    try:
        await ctx.interaction.response.edit_message(view=to_view(MessageSpec(rows=rows)))
    except discord.HTTPException as exc:
        log_event(log, logging.INFO, "catalog_menu_reset_failed", status=exc.status)
        return False
    return True


async def _category_page(ctx: Ctx, choice: int, page: int) -> MessageSpec:
    try:
        return await service.category_view(ctx.app, choice, page)
    except categories.CategoryNotFound:
        raise HandlerError(ck.ERROR_NOT_FOUND) from None


async def on_category_pick(ctx: Ctx) -> None:
    """A category picked in the menu on the listing header: its first page in a private message (only the member who
    picked sees it); the menu is reset in every case."""
    if not await _ack_by_resetting_menu(ctx):
        await ctx.defer_reply(ephemeral=True)
    picked = ctx.values[0] if ctx.values else ""
    if not picked.isdigit():
        raise HandlerError(ck.ERROR_NOT_FOUND)
    await ctx.respond(await _category_page(ctx, int(picked), 0), ephemeral=True)


async def on_category_page(ctx: Ctx) -> None:
    """Previous/next in a category's private view: edits it in place (outside one, a new private message)."""
    spec = await _category_page(ctx, ctx.arg_int(0), ctx.arg_int(1))
    if _is_ephemeral(ctx.interaction.message):
        await ctx.update(spec)
    else:
        await ctx.respond(spec, ephemeral=True)


async def on_admin_page(ctx: Ctx) -> None:
    """Staff product list paging (managers only)."""
    _require_manager(ctx)
    page = ctx.arg_int(0)
    include_archived = len(ctx.args) > 1 and ctx.args[1] == "1"
    spec = await service.admin_list_view(ctx.app, page, include_archived)
    await ctx.update(spec)


async def on_add_form(ctx: Ctx) -> None:
    _require_manager(ctx)
    await ctx.defer_reply(ephemeral=True)
    nonce = ctx.args[0] if ctx.args else None
    with_image = len(ctx.args) > 1 and ctx.args[1] == "1"
    reply = await service.create_from_form(
        ctx.app, actor_id=ctx.user_id, fields=ctx.fields, nonce=nonce, with_image=with_image
    )
    await ctx.respond(service.reply_spec(ctx.app, reply), ephemeral=True)


async def on_edit_form(ctx: Ctx) -> None:
    _require_manager(ctx)
    product_id = ctx.arg_int(0)
    version = ctx.arg_int(1)
    await ctx.defer_reply(ephemeral=True)
    reply = await service.edit_from_form(
        ctx.app, actor_id=ctx.user_id, product_id=product_id, expected_version=version, fields=ctx.fields
    )
    await ctx.respond(service.reply_spec(ctx.app, reply), ephemeral=True)


async def on_remove(ctx: Ctx) -> None:
    """Staff: the confirmed /product remove (archive rules: refused while pending orders hold units; idempotent)."""
    _require_manager(ctx)
    product_id = ctx.arg_int(0)
    await ctx.defer_update()
    reply = await service.archive(ctx.app, actor_id=ctx.user_id, product_id=product_id, confirm=True, via="remove")
    await ctx.update(service.reply_spec(ctx.app, reply))


async def on_remove_cancel(ctx: Ctx) -> None:
    _require_manager(ctx)
    product_id = ctx.arg_int(0)
    reply = service.OpReply(ctx.app.op("catalog.cmd.remove_kept", product_id=product_id), banner=service.NOTED)
    await ctx.update(service.reply_spec(ctx.app, reply))


def register_handlers(app: App) -> None:
    router = app.router
    router.component(ROUTE, "browse", feature=CATALOG)(on_browse)
    # Picks are private and safe to repeat: a pick of another category right after the last one (or while it still
    # loads) is answered and resets the menu, instead of being refused as a double click.
    router.component(ROUTE, "cat", feature=CATALOG, per_value=True)(on_category_pick)
    router.component(ROUTE, "catpage", feature=CATALOG)(on_category_page)
    router.component(ROUTE, "apage", feature=CATALOG, access="staff", staff_roles=service.manager_role_keys)(
        on_admin_page
    )
    router.modal(ROUTE, "addform", feature=CATALOG, access="staff", staff_roles=service.manager_role_keys)(on_add_form)
    router.modal(ROUTE, "editform", feature=CATALOG, access="staff", staff_roles=service.manager_role_keys)(
        on_edit_form
    )
    router.component(ROUTE, "remove", feature=CATALOG, access="staff", staff_roles=service.manager_role_keys)(on_remove)
    router.component(ROUTE, "remove_no", feature=CATALOG, access="staff", staff_roles=service.manager_role_keys)(
        on_remove_cancel
    )
