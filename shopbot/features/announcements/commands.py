"""The ``/announce`` slash command (staff).

Visibility: ``default_permissions(manage_messages=True)`` hides the command from members without Manage Messages (a
convenience only; server admins can change it under Server Settings, Integrations). Every use is authorized by
``require_staff`` (``permissions.staff_roles``, ``announcements.roles``, or an admin). Replies are operator text and
always private.

Options: ``image`` (an uploaded picture, when ``announcements.allow_image``) and ``mention`` (one of
``announcements.mention_roles``, suggested by autocomplete). The command opens the announcement form; the
announcement is posted when the form is submitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_staff
from shopbot.features.announcements import service, views
from shopbot.features.announcements.content_keys import ANNOUNCEMENTS
from shopbot.ui.discord_render import check_modal, to_modal
from shopbot.ui.spec import ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

MentionOption = app_commands.Range[str, 1, 100]
CHOICE_NAME_MAX = 100


async def _send_modal(interaction: discord.Interaction[Any], spec: ModalSpec) -> None:
    check_modal(spec)
    modal = to_modal(spec)
    await interaction.response.send_modal(modal)
    # Submissions are routed by custom id from on_interaction (restart-safe); drop discord.py's in-memory copy.
    modal.stop()


async def run_announce(
    interaction: discord.Interaction[Any], image: service.AttachmentLike | None, mention: str | None
) -> None:
    app = app_of(interaction)
    if not app.ready(ANNOUNCEMENTS):
        disabled = views.feature_disabled_reply(app.op, feature=ANNOUNCEMENTS)
        await reply(interaction, views.reply_spec(app.ui, app.op, disabled), ephemeral=True)
        return
    result = await service.open_form(app, actor_id=interaction.user.id, image=image, mention=mention)
    if isinstance(result, ModalSpec):
        await _send_modal(interaction, result)
        return
    await reply(interaction, views.reply_spec(app.ui, app.op, result), ephemeral=True)


def _is_staff(app: App, interaction: discord.Interaction[Any]) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    return interaction.guild_id == app.env.guild_id and app.perms.is_staff(
        member, interaction.user.id, service.staff_role_keys(app)
    )


async def mention_autocomplete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[str]]:
    """Suggest the roles of ``announcements.mention_roles`` that exist (by their server name)."""
    app = app_of(interaction)
    if not _is_staff(app, interaction):
        return []
    guild = app.guild
    query = current.strip().casefold()
    choices: list[app_commands.Choice[str]] = []
    for key in app.settings.announcements.mention_roles:
        role_id = app.resources.role_id(key)
        role = guild.get_role(role_id) if guild is not None and role_id else None
        if role is None:
            continue
        name = role.name if 1 <= len(role.name) <= CHOICE_NAME_MAX else key
        if query and query not in name.casefold() and query not in key:
            continue
        choices.append(app_commands.Choice(name=name, value=key))
    return choices[:25]


def build_announce_command() -> app_commands.Command[Any, ..., None]:
    """A fresh ``/announce`` command (one per application instance)."""

    @app_commands.command(name="announce", description="Publish an announcement in the announcements channel.")
    @app_commands.describe(
        image="Optional picture shown in the announcement (png, jpg, gif or webp).",
        mention="Optional role to mention with the announcement (from announcements.mention_roles).",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    @require_staff(service.staff_role_keys)
    async def announce(
        interaction: discord.Interaction[Any],
        image: discord.Attachment | None = None,
        mention: MentionOption | None = None,
    ) -> None:
        await run_announce(interaction, image, mention)

    announce.autocomplete("mention")(mention_autocomplete)
    return announce
