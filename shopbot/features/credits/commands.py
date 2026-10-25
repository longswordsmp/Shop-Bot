"""The ``/credits`` slash command group.

Visibility: ``default_permissions(manage_guild=True)`` hides the group from members without Manage Server (a
convenience only). Authorization is enforced on every invocation: ``balance`` and ``history`` require staff
(``permissions.staff_roles`` or ``checkout.staff_roles``, or an admin), ``adjust`` requires an admin. Replies are
operator text and always ephemeral.

The ``member`` options accept any user, so staff can also look up (and admins adjust) the credit of someone who
has left the server; the ledger is keyed by user ID.
"""

from __future__ import annotations

from typing import Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, reply_operator, require_admin, require_staff
from shopbot.features.credits import repo, service
from shopbot.features.credits.content_keys import CREDITS
from shopbot.features.credits.handlers import staff_role_keys

NoteText = app_commands.Range[str, 1, repo.NOTE_MAX_LENGTH]
AmountText = app_commands.Range[str, 1, 32]
PageNumber = app_commands.Range[int, 1, 1_000_000]


async def _ready(interaction: discord.Interaction[Any]) -> bool:
    app = app_of(interaction)
    if app.ready(CREDITS):
        return True
    await reply_operator(interaction, app.op("command.feature_disabled", feature=CREDITS), tone="warning")
    return False


def build_credits_group() -> app_commands.Group:
    group = app_commands.Group(name="credits", description="View and adjust member credit balances (staff/admins).")
    group = app_commands.default_permissions(manage_guild=True)(group)
    group = app_commands.guild_only(group)

    @group.command(name="balance", description="Show a member's credit balance.")
    @app_commands.describe(member="The member whose balance to show.")
    @require_staff(staff_role_keys)
    async def balance(interaction: discord.Interaction[Any], member: discord.Member | discord.User) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await service.balance_view(app, member.id), ephemeral=True)

    @group.command(name="history", description="Show a member's credit history (newest first, paginated).")
    @app_commands.describe(member="The member whose history to show.", page="Page number (default 1).")
    @require_staff(staff_role_keys)
    async def history(
        interaction: discord.Interaction[Any], member: discord.Member | discord.User, page: PageNumber = 1
    ) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await service.history_view(app, member.id, page - 1), ephemeral=True)

    @group.command(name="adjust", description="Add credits to or remove credits from a member (admins only).")
    @app_commands.describe(
        member="The member whose balance to change.",
        action="add: give credits; remove: take credits away (never below 0).",
        amount="Amount in the shop currency, e.g. 12.50.",
        note="Internal reason, shown only in the staff history and the audit log.",
    )
    @require_admin()
    async def adjust(
        interaction: discord.Interaction[Any],
        member: discord.Member | discord.User,
        action: Literal["add", "remove"],
        amount: AmountText,
        note: NoteText,
    ) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await service.adjust(
            app,
            actor_id=interaction.user.id,
            user_id=member.id,
            is_bot=member.bot,
            action=action,
            amount_text=amount,
            note=note,
        )
        await reply(interaction, result.spec, ephemeral=True)

    return group
