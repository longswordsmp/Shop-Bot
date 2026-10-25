"""Helpers for slash commands.

All slash commands are staff/admin tools; customers interact only through panels. Commands are hidden by
``default_permissions`` (a convenience) and protected by ``require_admin``/``require_staff`` checks
(authoritative, evaluated by the bot on every invocation).

Command descriptions are technical, admin-facing text (Discord requires one for every command).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import discord
from discord import app_commands

from shopbot.core.events import FAILURE
from shopbot.features import core_keys as ck
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.spec import EmbedField, MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)
T = TypeVar("T")


class NotAuthorized(app_commands.CheckFailure):
    pass


def app_of(interaction: discord.Interaction[Any]) -> App:
    app: App | None = getattr(interaction.client, "app", None)
    if app is None:
        raise RuntimeError("client has no app attached")
    return app


def _member(interaction: discord.Interaction[Any]) -> discord.Member | None:
    return interaction.user if isinstance(interaction.user, discord.Member) else None


def require_admin() -> Callable[[T], T]:
    async def predicate(interaction: discord.Interaction[Any]) -> bool:
        app = app_of(interaction)
        if interaction.guild_id != app.env.guild_id or not app.perms.is_admin(
            _member(interaction), interaction.user.id
        ):
            raise NotAuthorized()
        return True

    return app_commands.check(predicate)


def require_staff(extra_role_keys: Callable[[App], tuple[str, ...]] | None = None) -> Callable[[T], T]:
    async def predicate(interaction: discord.Interaction[Any]) -> bool:
        app = app_of(interaction)
        extra = extra_role_keys(app) if extra_role_keys is not None else ()
        if interaction.guild_id != app.env.guild_id or not app.perms.is_staff(
            _member(interaction), interaction.user.id, extra
        ):
            raise NotAuthorized()
        return True

    return app_commands.check(predicate)


async def reply(
    interaction: discord.Interaction[Any],
    spec: MessageSpec,
    *,
    ephemeral: bool = True,
    files: Sequence[discord.File] = (),
) -> None:
    kwargs = send_kwargs(spec)
    if files:
        kwargs["files"] = list(files)
    if interaction.response.is_done():
        await interaction.followup.send(ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(ephemeral=ephemeral, **kwargs)


async def reply_operator(
    interaction: discord.Interaction[Any],
    title: str,
    description: str | None = None,
    *,
    tone: str = "info",
    fields: Sequence[EmbedField] = (),
    files: Sequence[discord.File] = (),
) -> None:
    """Admin-only reply with operator wording (always ephemeral)."""
    app = app_of(interaction)
    spec = MessageSpec(embeds=(app.ui.operator_embed(title, description, tone=tone, fields=fields),))
    await reply(interaction, spec, ephemeral=True, files=files)


async def reply_content_error(
    interaction: discord.Interaction[Any], key: str = ck.ERROR_GENERIC, **values: str | int
) -> None:
    """Customer-safe error: configured content, or a silent acknowledgement when not configured."""
    app = app_of(interaction)
    embed = app.ui.error_embed(key, **values)
    if embed is None:
        # Slash commands have no silent acknowledgement: deferring leaves a "thinking" message. Defer, then delete
        # the deferred response so nothing (and no invented wording) remains.
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.delete_original_response()
        except discord.HTTPException:
            log.debug("could not clear deferred command response")
        return
    await reply(interaction, MessageSpec(embeds=(embed,)), ephemeral=True)


async def on_tree_error(interaction: discord.Interaction[Any], error: app_commands.AppCommandError) -> None:
    app = app_of(interaction)
    try:
        if isinstance(error, (NotAuthorized, app_commands.CheckFailure)):
            log_event(
                log,
                logging.INFO,
                "command_forbidden",
                user_id=interaction.user.id,
                command=getattr(interaction.command, "qualified_name", None),
            )
            await reply_content_error(interaction, ck.ERROR_PERMISSION)
            return
        error_id = uuid.uuid4().hex[:12]
        original = getattr(error, "original", error)
        log_event(
            log,
            logging.ERROR,
            "command_failed",
            error_id=error_id,
            error=type(original).__name__,
            command=getattr(interaction.command, "qualified_name", None),
        )
        log.error("command failure %s", error_id, exc_info=original)
        try:
            if app.perms.is_staff(_member(interaction), interaction.user.id):
                await reply_operator(interaction, app.op("command.failed", error_id=error_id), tone="danger")
            else:
                await reply_content_error(interaction, ck.ERROR_GENERIC, error_id=error_id)
        finally:
            # Operations records it in the audit log and alerts staff (no private data: names and the reference).
            await app.events.publish(
                FAILURE,
                kind="command",
                source=getattr(interaction.command, "qualified_name", None) or "-",
                error_id=error_id,
                error=type(original).__name__,
            )
    except (discord.HTTPException, RenderError):
        log.exception("failed to report command error")


def operator_fields(pairs: Sequence[tuple[str, str]], *, inline: bool = False) -> list[EmbedField]:
    return [EmbedField(name, value or "-", inline=inline) for name, value in pairs]
