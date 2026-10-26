"""Component and form handlers (route ``ct``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from shopbot.features import core_keys as ck
from shopbot.features.catalog import service
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.features.catalog.views import ROUTE
from shopbot.ui.routing import Ctx, HandlerError

if TYPE_CHECKING:
    from shopbot.context import App


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
