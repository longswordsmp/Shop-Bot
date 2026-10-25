from __future__ import annotations

from pathlib import Path

import discord
from discord import app_commands

from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.ui.custom_ids import cid
from tests.fakes import http_error, submit_modal
from tests.helpers import TestEnv
from tests.tickets.util import (
    audit_actions,
    bot_messages,
    channel,
    make_env,
    number,
    open_ticket,
    overwrite_for,
    render,
    run_command,
    ticket_of,
)


def _reply(interaction) -> discord.Embed:  # type: ignore[no-untyped-def]
    assert interaction.last_sent is not None
    assert interaction.last_sent.ephemeral
    return interaction.last_sent.embeds[0]


def test_group_is_guild_only_and_hidden_by_default(tk: TestEnv) -> None:
    group = tk.bot.tree.get_command("ticket", guild=tk.app.guild_object)
    assert isinstance(group, app_commands.Group)
    assert group.guild_only is True
    assert group.default_permissions is not None and group.default_permissions.manage_messages
    names = sorted(c.name for c in group.commands)
    assert names == ["add", "claim", "close", "info", "list", "remove", "reopen", "transcript"]


async def test_customers_cannot_use_ticket_commands(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    interaction = await run_command(tk, "close", user=customer, channel=ch)
    assert _reply(interaction).description == tk.text(core.ERROR_PERMISSION)
    assert (await ticket_of(tk, ticket.id)).status == "open"


async def test_add_and_remove_participant(tk: TestEnv) -> None:
    customer = tk.member()
    _, _ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)
    friend = tk.member(name="friend")
    added = await run_command(tk, "add", user=staff, channel=ch, member=friend)
    assert _reply(added).title == tk.app.op("tickets.cmd.added", user=friend.mention, number=number(tk, 1))
    assert ch.permissions_for(friend).view_channel and ch.permissions_for(friend).send_messages
    notice = bot_messages(ch)[-1]
    assert notice.embeds[0].description == render(tk, k.USER_ADDED, user_mention=friend.mention)
    assert notice.content == friend.mention
    again = await run_command(tk, "add", user=staff, channel=ch, member=friend)
    assert _reply(again).title == tk.app.op("tickets.cmd.already_added", user=friend.mention, number=number(tk, 1))
    owner = await run_command(tk, "add", user=staff, channel=ch, member=customer)
    assert _reply(owner).title == tk.app.op("tickets.cmd.owner", user=customer.mention, number=number(tk, 1))

    removed = await run_command(tk, "remove", user=staff, channel=ch, member=friend)
    assert _reply(removed).title == tk.app.op("tickets.cmd.removed", user=friend.mention, number=number(tk, 1))
    assert overwrite_for(ch, friend.id) is None and not ch.permissions_for(friend).view_channel
    assert bot_messages(ch)[-1].embeds[0].description == render(tk, k.USER_REMOVED, user_mention=friend.mention)
    actions = await audit_actions(tk)
    assert actions.count("tickets.participant_added") == 1 and actions.count("tickets.participant_removed") == 1


async def test_add_reports_discord_errors(tk: TestEnv) -> None:
    customer = tk.member()
    _, _ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)

    async def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    ch.set_permissions = refuse  # type: ignore[method-assign]
    interaction = await run_command(tk, "add", user=staff, channel=ch, member=tk.member(name="friend"))
    assert _reply(interaction).title == tk.app.op("tickets.cmd.discord_error", status=403)
    assert "tickets.participant_added" not in await audit_actions(tk)


async def test_close_with_reason_reopen_claim_info_list(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)

    claimed = await run_command(tk, "claim", user=staff, channel=ch)
    assert _reply(claimed).title == tk.app.op("tickets.cmd.claimed", number=number(tk, 1))
    assert (await ticket_of(tk, ticket.id)).assigned_staff_id == staff.id
    twice = await run_command(tk, "claim", user=tk.member(name="other staff", staff=True), channel=ch)
    assert _reply(twice).title == tk.app.op("tickets.cmd.already_claimed", number=number(tk, 1), staff=staff.mention)

    closed = await run_command(tk, "close", user=staff, channel=ch, reason="duplicate of #2")
    assert _reply(closed).title == tk.app.op("tickets.cmd.closed", number=number(tk, 1))
    row = await ticket_of(tk, ticket.id)
    assert row.status == "closed" and row.close_reason == "duplicate of #2" and row.closed_by == staff.id
    closed_message = bot_messages(ch)[-1]
    assert closed_message.embeds[0].description == render(
        tk, k.CLOSED_DESCRIPTION, closer_mention=staff.mention, number=number(tk, 1)
    )
    assert closed_message.embeds[0].fields[0].value == "duplicate of #2"
    not_open = await run_command(tk, "close", user=staff, channel=ch)
    assert _reply(not_open).title == tk.app.op("tickets.cmd.wrong_state", number=number(tk, 1), status="closed")

    info = await run_command(tk, "info", user=staff, channel=ch)
    embed = _reply(info)
    assert embed.title == tk.app.op("tickets.info.title", number=number(tk, 1))
    fields = {f.name: f.value for f in embed.fields}
    assert fields[tk.app.op("tickets.info.status")] == "closed"
    assert fields[tk.app.op("tickets.info.owner")] == customer.mention
    assert fields[tk.app.op("tickets.info.close_reason")] == "duplicate of #2"

    reopened = await run_command(tk, "reopen", user=staff, channel=ch)
    assert _reply(reopened).title == tk.app.op("tickets.cmd.reopened", number=number(tk, 1))
    assert (await ticket_of(tk, ticket.id)).status == "open"

    listed = await run_command(tk, "list", user=staff, channel=channel(tk, "staff_alerts"), status="open")
    listing = _reply(listed)
    assert listing.title == tk.app.op("tickets.list.title", count=1)
    assert ch.mention in (listing.description or "")
    empty = await run_command(tk, "list", user=staff, status="failed")
    assert _reply(empty).description == tk.app.op("tickets.list.empty")


async def test_commands_outside_ticket_and_wrong_category_staff(tkf: TestEnv) -> None:
    staff = tkf.member(name="staff", staff=True)
    outside = await run_command(tkf, "info", user=staff, channel=channel(tkf, "staff_alerts"))
    assert _reply(outside).title == tkf.app.op("tickets.cmd.not_ticket")
    billing_role = tkf.guild.get_role(tkf.app.resources.role_id("billing_staff") or 0)
    agent = tkf.guild.add_member(name="agent", roles=[billing_role])
    _, ticket, ch = await open_ticket(tkf, tkf.member())
    denied = await run_command(tkf, "close", user=agent, channel=ch)
    assert _reply(denied).title == tkf.app.op("tickets.cmd.not_staff_here")
    assert (await ticket_of(tkf, ticket.id)).status == "open"


async def test_transcript_command_with_and_without_channel(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"transcript_channel": ""}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        staff = tenv.member(name="staff", staff=True)
        interaction = await run_command(tenv, "transcript", user=staff, channel=ch)
        message = interaction.last_sent
        assert message is not None and message.ephemeral  # type: ignore[attr-defined]
        assert message.embeds[0].title == tenv.app.op("tickets.cmd.transcript_attached", number=number(tenv, 1))
        assert message.files[0].filename == f"transcript-{number(tenv, 1)}.html"
        assert (await ticket_of(tenv, ticket.id)).transcript_status == "none"
    finally:
        await tenv.close()


async def test_transcript_command_posts_to_channel(tk: TestEnv) -> None:
    _, _ticket, ch = await open_ticket(tk, tk.member())
    staff = tk.member(name="staff", staff=True)
    interaction = await run_command(tk, "transcript", user=staff, channel=ch)
    transcripts = channel(tk, "transcripts")
    assert len(transcripts.messages) == 1
    assert _reply(interaction).title == tk.app.op(
        "tickets.cmd.transcript_posted", number=number(tk, 1), channel=transcripts.mention
    )


async def test_commands_refuse_when_feature_not_ready(tk: TestEnv) -> None:
    _, _, ch = await open_ticket(tk, tk.member())
    tk.app.readiness.statuses["tickets"].ready = False
    staff = tk.member(name="staff", staff=True)
    interaction = await run_command(tk, "close", user=staff, channel=ch)
    assert _reply(interaction).title == tk.app.op("command.feature_disabled", feature="tickets")


async def test_list_only_shows_categories_the_caller_is_staff_for(tkf: TestEnv) -> None:
    _, _, general_channel = await open_ticket(tkf, tkf.member(name="c1"))
    await submit_modal(
        tkf.app, user=tkf.member(name="c2"), custom_id=cid("tk", "form", "billing"), fields={"details": "need help"}
    )
    billing_role = tkf.guild.get_role(tkf.app.resources.role_id("billing_staff") or 0)
    agent = tkf.guild.add_member(name="agent", roles=[billing_role])
    scoped = _reply(await run_command(tkf, "list", user=agent))
    assert scoped.title == tkf.app.op("tickets.list.title", count=1)
    assert general_channel.mention not in (scoped.description or "")
    full = _reply(await run_command(tkf, "list", user=tkf.member(name="staff", staff=True)))
    assert full.title == tkf.app.op("tickets.list.title", count=2)
