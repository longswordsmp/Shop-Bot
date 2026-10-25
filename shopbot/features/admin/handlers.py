"""Admin-only component and form handlers (route ``ad``).

- ``sb:ad:status:<feature>:<page>``  page through a feature's problems (from ``/status feature:``)
- ``sb:ad:miss:<feature|->:<page>``  page through missing/invalid content (``/content missing``)
- ``sb:ad:keys:<feature|->:<page>``  page through content keys (``/content keys``)
- ``sb:ad:cset:<token>``             the ``/content set`` form submission (then posted panels are refreshed)
- ``sb:ad:etext``                    the text picked in the "Edit text" message command's list (opens that form)

The router enforces guild and administrator access before these run; each handler re-checks
administrator rights, re-reads the current state (readiness, registry), and only then acts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shopbot.commands import reply
from shopbot.features import core_keys as ck
from shopbot.features.admin import service, views
from shopbot.features.admin.commands import open_content_form, send_view_reply
from shopbot.features.admin.state import state_for
from shopbot.logging_setup import log_event
from shopbot.ui.routing import Ctx, HandlerError

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def _require_admin(ctx: Ctx) -> None:
    if not ctx.is_admin():
        raise HandlerError(ck.ERROR_PERMISSION)


def _scope(ctx: Ctx) -> str | None:
    """Feature named by the first argument (None for all features); unknown names are not found."""
    if not ctx.args:
        raise HandlerError(ck.ERROR_NOT_FOUND)
    scope = ctx.args[0]
    if scope == views.ALL_SCOPE:
        return None
    if scope not in service.feature_names(ctx.app):
        raise HandlerError(ck.ERROR_NOT_FOUND)
    return scope


async def status_page(ctx: Ctx) -> None:
    _require_admin(ctx)
    feature = _scope(ctx)
    page = ctx.arg_int(1)
    status = service.feature_status(ctx.app, feature) if feature is not None else None
    if feature is None or status is None:
        raise HandlerError(ck.ERROR_NOT_FOUND)
    await ctx.update(views.status_feature(ctx.app.ui, ctx.app.op, feature, status, page))


async def missing_page(ctx: Ctx) -> None:
    _require_admin(ctx)
    feature = _scope(ctx)
    page = ctx.arg_int(1)
    grouped = service.missing_content(ctx.app, feature)
    await ctx.update(views.content_missing(ctx.app.ui, ctx.app.op, feature, grouped, page))


async def keys_page(ctx: Ctx) -> None:
    _require_admin(ctx)
    feature = _scope(ctx)
    page = ctx.arg_int(1)
    grouped = service.key_rows(ctx.app, feature)
    await ctx.update(views.content_keys(ctx.app.ui, ctx.app.op, feature, grouped, page))


async def content_set_submit(ctx: Ctx) -> None:
    _require_admin(ctx)
    app = ctx.app
    token = ctx.args[0] if ctx.args else ""
    pending = state_for(app).pending.take(token, ctx.user_id, app.clock.now())
    if pending is None:
        log_event(log, logging.INFO, "admin_content_form_expired", user_id=ctx.user_id)
        expired = views.simple(app.ui, app.op, app.op("admin.content.expired"), tone="warning", banner="cancellation")
        await reply(ctx.interaction, expired)
        return
    key = pending.key
    if key not in app.content.registry:  # the configuration was reloaded and the key no longer exists
        await reply(ctx.interaction, views.unknown_key(app.ui, app.op, key, service.close_keys(app, key)))
        return
    value = ctx.fields.get(views.VALUE_INPUT_ID, "")  # exactly as submitted: never stripped
    await ctx.defer_reply()  # saving can withdraw panels over Discord (3-second reply deadline)
    result = await service.save_override(app, key, value, actor_id=ctx.user_id)
    if not result.saved:
        log_event(log, logging.INFO, "admin_content_rejected", key=key, problems=len(result.problems))
        outcome = views.content_invalid(app.ui, app.op, key, value, result.problems)
    else:
        log_event(log, logging.INFO, "admin_content_saved", key=key, length=len(value))
        # Show the new text on the posted panels right away (no /setup needed).
        panels = views.panel_refresh_lines(app.op, await service.refresh_panels(app))
        outcome = views.content_saved(app.ui, app.op, key, value, result.changes, panels)
    await send_view_reply(ctx.interaction, outcome)


async def edit_text_choose(ctx: Ctx) -> None:
    """A text picked in the "Edit text" list: open the /content set form for it."""
    _require_admin(ctx)
    key = ctx.values[0] if ctx.values else ""
    if key not in ctx.app.content.registry:  # the configuration was reloaded and the key no longer exists
        raise HandlerError(ck.ERROR_NOT_FOUND)
    await open_content_form(ctx.interaction, key)


def register_handlers(app: App) -> None:
    router = app.router
    router.component(views.ROUTE, "status", feature=service.FEATURE, access="admin")(status_page)
    router.component(views.ROUTE, "miss", feature=service.FEATURE, access="admin")(missing_page)
    router.component(views.ROUTE, "keys", feature=service.FEATURE, access="admin")(keys_page)
    router.modal(views.ROUTE, "cset", feature=service.FEATURE, access="admin")(content_set_submit)
    router.component(views.ROUTE, views.EDIT_ACTION, feature=service.FEATURE, access="admin")(edit_text_choose)
