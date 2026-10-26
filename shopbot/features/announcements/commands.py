"""The ``/announce`` slash command (staff).

Visibility: ``default_permissions(manage_messages=True)`` hides the command from members without Manage Messages (a
convenience only; server admins can change it under Server Settings, Integrations). Every use is authorized by
``require_staff`` (``permissions.staff_roles``, ``announcements.roles``, or an admin). Replies are operator text and
always private.

Options: ``image`` (an uploaded picture, when ``announcements.allow_image``), ``mention`` (one of
``announcements.mention_roles``, suggested by autocomplete) and ``preset`` (one of ``announcements.presets``,
suggested by autocomplete by name). Without a preset the command opens the announcement form; the announcement is
posted when the form is submitted. With a preset there is no form: staff get a private preview (``presets``) and the
announcement is posted by its Post announcement button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_staff
from shopbot.features.announcements import presets, service, views
from shopbot.features.announcements.content_keys import ANNOUNCEMENTS
from shopbot.ui.discord_render import check_modal, to_modal
from shopbot.ui.spec import ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

MentionOption = app_commands.Range[str, 1, 100]
PresetOption = app_commands.Range[str, 1, 100]
CHOICE_NAME_MAX = 100


async def _send_modal(interaction: discord.Interaction[Any], spec: ModalSpec) -> None:
    check_modal(spec)
    modal = to_modal(spec)
    await interaction.response.send_modal(modal)
    # Submissions are routed by custom id from on_interaction (restart-safe); drop discord.py's in-memory copy.
    modal.stop()


async def run_announce(
    interaction: discord.Interaction[Any],
    image: service.AttachmentLike | None,
    mention: str | None,
    preset: str | None = None,
) -> None:
    app = app_of(interaction)
    if not app.ready(ANNOUNCEMENTS):
        disabled = views.feature_disabled_reply(app.op, feature=ANNOUNCEMENTS)
        await reply(interaction, views.reply_spec(app.ui, app.op, disabled), ephemeral=True)
        return
    if preset is not None:
        await run_preset(interaction, preset, image, mention)
        return
    result = await service.open_form(app, actor_id=interaction.user.id, image=image, mention=mention)
    if isinstance(result, ModalSpec):
        await _send_modal(interaction, result)
        return
    await reply(interaction, views.reply_spec(app.ui, app.op, result), ephemeral=True)


async def run_preset(
    interaction: discord.Interaction[Any], preset: str, image: service.AttachmentLike | None, mention: str | None
) -> None:
    """The private preview of a preset: the announcement as it will be posted, then the card with Post announcement
    (two private messages; checking the coupon and an earlier post may take longer than 3 seconds)."""
    app = app_of(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    preview = await presets.open_preview(app, actor_id=interaction.user.id, preset=preset, image=image, mention=mention)
    if preview.announcement is not None:
        await reply(interaction, preview.announcement, ephemeral=True)
    await reply(interaction, views.reply_spec(app.ui, app.op, preview.card), ephemeral=True)


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


async def preset_autocomplete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[str]]:
    """Suggest the configured presets by their name (``announcements.presets``); staff only."""
    app = app_of(interaction)
    if not _is_staff(app, interaction):
        return []
    return [app_commands.Choice(name=name, value=preset_id) for name, preset_id in presets.choices(app, current)]


def build_announce_command() -> app_commands.Command[Any, ..., None]:
    """A fresh ``/announce`` command (one per application instance)."""

    @app_commands.command(name="announce", description="Publish an announcement in the announcements channel.")
    @app_commands.describe(
        image="Optional picture shown in the announcement (png, jpg, gif or webp).",
        mention="Optional role to mention with the announcement (from announcements.mention_roles).",
        preset="Optional ready-made announcement (from announcements.presets): preview it, then post it.",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    @require_staff(service.staff_role_keys)
    async def announce(
        interaction: discord.Interaction[Any],
        image: discord.Attachment | None = None,
        mention: MentionOption | None = None,
        preset: PresetOption | None = None,
    ) -> None:
        await run_announce(interaction, image, mention, preset)

    announce.autocomplete("mention")(mention_autocomplete)
    announce.autocomplete("preset")(preset_autocomplete)
    return announce
