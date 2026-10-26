"""/reset and its controls (route ``rs``; the steps are described in ``reset``).

- ``/reset [wipe_shop_data]``      the private preview with Continue and Cancel
- ``sb:rs:go:<wipe>:<plan>``      Continue: opens the confirmation form
- ``sb:rs:no``                    Cancel: nothing changes
- ``sb:rs:ok:<wipe>:<plan>``      the confirmation form: checks the phrase and the plan, then runs the reset

The router admits administrators only; every handler checks again that the user may reset (the bot owner or the
server owner). ``<plan>`` is the preview's fingerprint: a confirmation for a server that changed since is refused.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from shopbot.commands import app_of, reply
from shopbot.config.banner_ids import BannerId
from shopbot.features.setup import reset
from shopbot.features.setup import reset_views as views
from shopbot.features.setup.service import setup_lock
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_spec
from shopbot.ui.routing import Ctx

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.ui.spec import MessageSpec

log = logging.getLogger(__name__)


def _notice(app: App, key: str, *, tone: str, banner: BannerId, **values: object) -> MessageSpec:
    return views.notice(app.ui, app.op(key, **values), tone=tone, banner=banner)


async def handle_reset(interaction: discord.Interaction[Any], *, wipe: bool) -> None:
    """``/reset``: show privately what would be deleted and kept."""
    app = app_of(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = app.guild
    if guild is None or guild.me is None:
        await reply(interaction, _notice(app, "server.reset.no_guild", tone="danger", banner="error"))
        return
    actor = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
    if actor is None or not reset.can_reset(app, guild, actor.id):
        await reply(interaction, _notice(app, "server.reset.owner_only", tone="danger", banner="error"))
        return
    plan = reset.plan_reset(guild, reset.keep_ids(app, guild, actor))
    if plan.total == 0:
        await reply(interaction, _notice(app, "server.reset.nothing", tone="info", banner="general-response"))
        return
    view = views.plan_view(guild.name, plan, await reset.open_work(app), wipe=wipe)
    log_event(log, logging.INFO, "reset_previewed", actor_id=interaction.user.id, targets=plan.total, wipe=wipe)
    await reply(interaction, views.preview_message(app.ui, app.op, view))


def _owner_guild(ctx: Ctx) -> discord.Guild | None:
    guild = ctx.app.guild
    if guild is None or guild.me is None or not reset.can_reset(ctx.app, guild, ctx.user_id):
        return None
    return guild


def _flags(ctx: Ctx) -> tuple[bool, str]:
    wipe = ctx.arg_int(0) == 1
    fingerprint = ctx.args[1] if len(ctx.args) > 1 else ""
    return wipe, fingerprint


async def continue_clicked(ctx: Ctx) -> None:
    app = ctx.app
    guild = _owner_guild(ctx)
    if guild is None:
        await ctx.respond(_notice(app, "server.reset.owner_only", tone="danger", banner="error"))
        return
    wipe, fingerprint = _flags(ctx)
    await ctx.send_modal(views.confirm_modal(app.op, guild.name, wipe=wipe, fingerprint=fingerprint))


async def cancel_clicked(ctx: Ctx) -> None:
    await ctx.update(_notice(ctx.app, "server.reset.cancelled", tone="info", banner="cancellation"))


async def confirm_submitted(ctx: Ctx) -> None:
    app = ctx.app
    guild = _owner_guild(ctx)
    if guild is None:
        await ctx.respond(_notice(app, "server.reset.owner_only", tone="danger", banner="error"))
        return
    wipe, fingerprint = _flags(ctx)
    typed = ctx.fields.get(views.PHRASE_INPUT_ID, "")
    if not reset.phrase_matches(typed, guild.name):
        phrase = reset.confirmation_phrase(guild.name)  # shown in a code span: as it must be typed
        await ctx.respond(_notice(app, "server.reset.mismatch", tone="warning", banner="cancellation", phrase=phrase))
        return
    lock = setup_lock(app)
    if lock.locked():
        await ctx.respond(_notice(app, "server.reset.busy", tone="warning", banner="submission-and-processing"))
        return
    await ctx.defer_reply()
    async with lock:
        await _confirmed(ctx, guild, wipe=wipe, fingerprint=fingerprint)


async def _confirmed(ctx: Ctx, guild: discord.Guild, *, wipe: bool, fingerprint: str) -> None:
    app = ctx.app
    actor = ctx.member or guild.get_member(ctx.user_id)
    plan = reset.plan_reset(guild, reset.keep_ids(app, guild, actor)) if actor is not None else None
    if actor is None or plan is None or plan.fingerprint != fingerprint:
        await ctx.respond(_notice(app, "server.reset.changed", tone="warning", banner="cancellation"))
        return
    try:
        progress_dm = await send_spec(actor, views.progress_message(app.ui, app.op, 0, plan.total, 0))
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "reset_dm_failed", actor_id=actor.id, error=type(exc).__name__)
        await ctx.respond(_notice(app, "server.reset.dm_closed", tone="danger", banner="error"))
        return
    try:
        backup = await reset.backup_first(app, actor.id)
    except reset.BackupFailed as exc:
        failed = _notice(app, "server.reset.backup_failed", tone="danger", banner="error", error=str(exc))
        await ctx.respond(failed)
        await _send_quietly(actor, failed)
        return
    try:
        setup_channel = await reset.create_setup_channel(app, guild, actor)
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "reset_setup_channel_failed", status=exc.status, code=exc.code)
        failed = _notice(app, "server.reset.setup_channel_failed", tone="danger", banner="error", status=exc.status)
        await ctx.respond(failed)
        await _send_quietly(actor, failed)
        return
    # The last answer in this channel, which is about to be deleted.
    await ctx.respond(_notice(app, "server.reset.started", tone="warning", banner="submission-and-processing"))

    def progress(done: int, total: int, failed: int) -> MessageSpec:
        return views.progress_message(app.ui, app.op, done, total, failed)

    try:
        result = await reset.run_reset(
            app,
            guild,
            actor=actor,
            plan=plan,
            wipe=wipe,
            backup=backup,
            setup_channel_id=setup_channel.id,
            progress_message=progress_dm,
            progress=progress,
        )
    except Exception:
        # The records and the audit entry are already taken care of; tell the owner what to do next.
        log.exception("reset failed")
        await _send_quietly(actor, interrupted_notice(app))
        await reset.finished(app)
        return
    await _send_quietly(actor, views.result_message(app.ui, app.op, result))
    await reset.finished(app)


def interrupted_notice(app: App) -> MessageSpec:
    title, text = app.op("server.reset.interrupted_title"), app.op("server.reset.interrupted")
    return views.notice(app.ui, title, text, tone="danger", banner="error")


async def report_interrupted(app: App) -> None:
    """Startup: tell the owner about a reset that a restart cut off."""
    await reset.report_interrupted(app, lambda: interrupted_notice(app))


async def _send_quietly(user: discord.abc.Messageable, spec: MessageSpec) -> None:
    try:
        await send_spec(user, spec)
    except (discord.HTTPException, RenderError) as exc:
        log_event(log, logging.WARNING, "reset_result_dm_failed", error=type(exc).__name__)


def register_handlers(app: App) -> None:
    router = app.router
    # No readiness gate (feature=None): a reset needs no configured text.
    router.component(views.ROUTE, views.CONTINUE_ACTION, feature=None, access="admin")(continue_clicked)
    router.component(views.ROUTE, views.CANCEL_ACTION, feature=None, access="admin")(cancel_clicked)
    router.modal(views.ROUTE, views.CONFIRM_ACTION, feature=None, access="admin")(confirm_submitted)
