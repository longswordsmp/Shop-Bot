"""Helpers for ticket tests: environments with provisioned channels and common flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import service
from shopbot.features.tickets.models import Ticket
from shopbot.ui.custom_ids import cid
from tests.fakes import FakeCategory, FakeInteraction, FakeMember, FakeMessage, FakeTextChannel, click
from tests.helpers import TestEnv, deep_merge, make_settings, make_test_env, provision_channels

FORM_CATEGORIES: dict[str, Any] = {
    "server": {
        "roles": [{"key": "staff"}, {"key": "billing_staff"}],
        "categories": [
            {"key": "support", "access": "public_readonly"},
            {"key": "tickets", "access": "staff"},
            {"key": "closed", "access": "staff"},
            {"key": "orders", "access": "staff"},
            {"key": "staff", "access": "staff"},
            {"key": "admin", "access": "admin"},
        ],
    },
    "tickets": {
        "categories": [
            {"id": "general", "discord_category": "tickets"},
            {
                "id": "billing",
                "discord_category": "tickets",
                "staff_roles": ["billing_staff"],
                "max_open_per_user": 2,
                "button_style": "success",
                "ping_staff": True,
                "form": [
                    {"id": "details", "style": "paragraph", "required": True, "min_length": 3, "max_length": 4000},
                    {"id": "reference", "style": "short", "required": False, "max_length": 100},
                ],
            },
        ]
    },
}


async def make_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    # Many clicks by the same fake user happen within milliseconds; keep the anti-spam limiter out of the way.
    settings = make_settings(deep_merge({"bot": {"rate_limit_per_10s": 100}}, overrides or {}))
    tenv = await make_test_env(tmp_path, settings=settings, **kwargs)
    await provision_channels(tenv)
    if settings.server.role("billing_staff") is not None:
        role = tenv.guild.add_role(name="billing", position=5)
        await tenv.app.resources.remember("role", "billing_staff", role.id)
    return tenv


def form_overrides(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return deep_merge(FORM_CATEGORIES, extra or {})


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return found


def category(tenv: TestEnv, key: str) -> FakeCategory:
    found = tenv.app.resources.category(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeCategory)
    return found


def render(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.app.content.render(key, **values)
    assert text is not None, key
    return text


def number(tenv: TestEnv, value: int) -> str:
    return service.format_number(tenv.app, value)


async def ticket_of(tenv: TestEnv, ticket_id: int) -> Ticket:
    ticket = await service.get_ticket(tenv.app, ticket_id)
    assert ticket is not None
    return ticket


async def tickets(tenv: TestEnv) -> list[Ticket]:
    from shopbot.features.tickets import repo

    return list(reversed(await tenv.app.db.run(repo.list_tickets, tenv.app.env.guild_id, limit=1000)))


async def open_ticket(
    tenv: TestEnv, user: FakeMember, category_id: str = "general"
) -> tuple[FakeInteraction, Ticket, FakeTextChannel]:
    """Click the panel button and return (interaction, ticket, channel)."""
    before = {t.id for t in await tickets(tenv)}
    interaction = await click(tenv.app, user=user, custom_id=cid(k.ROUTE, "open", category_id))
    created = [t for t in await tickets(tenv) if t.id not in before]
    assert len(created) == 1, interaction.sent and interaction.sent[-1].text()
    ticket = created[0]
    assert ticket.channel_id is not None
    ticket_channel = tenv.guild.get_channel(ticket.channel_id)
    assert isinstance(ticket_channel, FakeTextChannel)
    return interaction, ticket, ticket_channel


def control_message(ticket_channel: FakeTextChannel, ticket: Ticket) -> FakeMessage:
    for message in ticket_channel.messages:
        if message.id == ticket.control_message_id:
            return message
    raise AssertionError("control message not found")


def bot_messages(ticket_channel: FakeTextChannel) -> list[FakeMessage]:
    return [m for m in ticket_channel.messages if m.author is ticket_channel.guild.me]


def overwrite_for(ticket_channel: FakeTextChannel, target_id: int) -> discord.PermissionOverwrite | None:
    for target, overwrite in ticket_channel.overwrites.items():
        if target.id == target_id:
            return overwrite
    return None


def advance_past_cooldown(tenv: TestEnv) -> None:
    tenv.clock.advance(tenv.app.settings.tickets.creation_cooldown_seconds + 1)


async def run_command(
    tenv: TestEnv, name: str, *, user: FakeMember, channel: FakeTextChannel | None = None, **kwargs: Any
) -> FakeInteraction:
    """Invoke a /ticket subcommand like discord.py does: checks first (errors go to the tree error
    handler), then the callback with already-converted options."""
    from discord import app_commands

    from shopbot.commands import on_tree_error

    group = tenv.bot.tree.get_command("ticket", guild=tenv.app.guild_object)
    assert isinstance(group, app_commands.Group)
    command = group.get_command(name)
    assert isinstance(command, app_commands.Command)
    interaction = FakeInteraction(
        client=tenv.bot,
        user=user,
        guild=tenv.guild,
        channel=channel,
        type=discord.InteractionType.application_command,
    )
    try:
        if not await command._check_can_run(interaction):  # type: ignore[arg-type]
            raise app_commands.CheckFailure("check failed")
        await command.callback(interaction, **kwargs)  # type: ignore[arg-type]
    except app_commands.CheckFailure as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
    return interaction


async def audit_actions(tenv: TestEnv) -> list[str]:
    from shopbot.db.repos import audit as audit_repo

    events = await tenv.app.db.run(audit_repo.recent, tenv.app.env.guild_id, limit=100)
    return [e.action for e in reversed(events)]


async def task(tenv: TestEnv, dedupe_key: str) -> Any:
    from shopbot.db.repos import tasks as task_repo

    return await tenv.app.db.run(task_repo.get_by_dedupe, dedupe_key)
