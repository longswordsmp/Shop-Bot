"""Form and button handlers of ``/announce`` (route ``an``).

- ``sb:an:submit:<token>`` (form): publish the announcement.
- ``sb:an:retry:<token>`` (button on an error reply): reopen the form prefilled with the text already typed.

Both routes are staff-only (``permissions.staff_roles``, ``announcements.roles`` or an admin), checked by the router
on every use; the handlers also check that the form belongs to the staff member using it. The token only identifies
the stored form, so both keep working after a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.announcements import service, views
from shopbot.features.announcements.content_keys import ANNOUNCEMENTS
from shopbot.ui.routing import Ctx
from shopbot.ui.spec import ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App


def _token(ctx: Ctx) -> str | None:
    if len(ctx.args) != 1 or not service.TOKEN_RE.fullmatch(ctx.args[0]):
        return None
    return ctx.args[0]


async def _unknown(ctx: Ctx) -> None:
    await ctx.respond(views.reply_spec(ctx.app.ui, ctx.app.op, views.unknown_form_reply(ctx.app.op)), ephemeral=True)


async def on_submit(ctx: Ctx) -> None:
    token = _token(ctx)
    if token is None:
        await _unknown(ctx)
        return
    # Downloading the image and posting may take longer than Discord's 3-second answer window.
    await ctx.defer_reply(ephemeral=True)
    reply = await service.submit(ctx.app, token=token, actor_id=ctx.user_id, fields=ctx.fields)
    await ctx.respond(views.reply_spec(ctx.app.ui, ctx.app.op, reply), ephemeral=True)


async def on_retry(ctx: Ctx) -> None:
    token = _token(ctx)
    if token is None:
        await _unknown(ctx)
        return
    result = await service.reopen_form(ctx.app, token=token, actor_id=ctx.user_id)
    if isinstance(result, ModalSpec):
        await ctx.send_modal(result)
        return
    await ctx.respond(views.reply_spec(ctx.app.ui, ctx.app.op, result), ephemeral=True)


def register_handlers(app: App) -> None:
    router = app.router
    router.modal(
        views.ROUTE, views.SUBMIT_ACTION, feature=ANNOUNCEMENTS, access="staff", staff_roles=service.staff_role_keys
    )(on_submit)
    router.component(
        views.ROUTE, views.RETRY_ACTION, feature=ANNOUNCEMENTS, access="staff", staff_roles=service.staff_role_keys
    )(on_retry)
