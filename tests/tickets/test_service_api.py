"""The private-channel API the checkout feature reuses for ORDER channels (kind='order')."""

from __future__ import annotations

import asyncio

import pytest

from shopbot.config.content import MissingContent
from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import service
from shopbot.ui.custom_ids import cid
from tests.fakes import click
from tests.helpers import TestEnv
from tests.tickets.util import category, channel, overwrite_for, ticket_of, tickets

PREFIX_KEY = k.category_key("general", "channel_prefix")


async def _create_order(tenv: TestEnv, member, *, limit: int = 1) -> service.CreatedChannel:  # type: ignore[no-untyped-def]
    return await service.create_private_channel(
        tenv.app,
        member=member,
        kind="order",
        category_id="checkout",
        discord_category_key="orders",
        channel_prefix_key=PREFIX_KEY,
        extra_staff_role_keys=(),
        topic=None,
        limit=limit,
        form_answers={"note": "[sample]"},
    )


async def test_create_private_order_channel(tkf: TestEnv) -> None:
    customer = tkf.member()
    created = await _create_order(tkf, customer)
    ticket, ch = created.ticket, created.channel
    assert ticket.kind == "order" and ticket.status == "creating" and ticket.channel_id == ch.id
    assert ticket.form_answers == {"note": "[sample]"}
    assert ch.category_id == category(tkf, "orders").id and ch.messages == []
    everyone = overwrite_for(ch, tkf.guild.default_role.id)
    assert everyone is not None and everyone.view_channel is False
    assert ch.permissions_for(customer).view_channel and ch.permissions_for(tkf.member(staff=True)).view_channel

    with pytest.raises(service.TicketLimitReached) as exc:
        await _create_order(tkf, customer)
    assert exc.value.limit == 1

    opened = await service.activate_ticket(tkf.app, ticket.id, control_message_id=None)
    assert opened.status == "open"
    with pytest.raises(service.TicketStateError):
        await service.activate_ticket(tkf.app, ticket.id, control_message_id=None)

    # Support-ticket controls never act on order channels.
    denied = await click(tkf.app, user=customer, custom_id=cid("tk", "close", ticket.id))
    assert denied.last_sent is not None and denied.last_sent.embeds[0].description == tkf.text(core.ERROR_NOT_FOUND)


async def test_concurrent_order_creation_is_limited(tkf: TestEnv) -> None:
    customer = tkf.member()
    results = await asyncio.gather(_create_order(tkf, customer), _create_order(tkf, customer), return_exceptions=True)
    assert sum(isinstance(r, service.CreatedChannel) for r in results) == 1
    assert sum(isinstance(r, service.TicketLimitReached) for r in results) == 1


async def test_close_order_uses_checkout_defaults_and_no_support_ui(tkf: TestEnv) -> None:
    customer = tkf.member()
    created = await _create_order(tkf, customer)
    await service.activate_ticket(tkf.app, created.ticket.id, control_message_id=None)
    closed = await service.close_ticket(tkf.app, created.ticket.id, actor_id=None, reason="fulfilled")
    assert closed is not None and closed.status == "closed" and closed.transcript_status == "skipped"
    assert created.channel.messages == []  # order UI belongs to checkout
    owner = overwrite_for(created.channel, customer.id)
    assert owner is not None and owner.send_messages is False
    assert await service.close_ticket(tkf.app, created.ticket.id, actor_id=None) is None
    await service.schedule_channel_deletion(tkf.app, created.ticket.id, 0)
    await tkf.app.scheduler.run_due()
    assert (await ticket_of(tkf, created.ticket.id)).status == "deleted" and created.channel.deleted


async def test_order_close_with_transcript_override(tkf: TestEnv) -> None:
    customer = tkf.member()
    created = await _create_order(tkf, customer)
    await service.activate_ticket(tkf.app, created.ticket.id, control_message_id=None)
    created.channel.add_user_message(customer, "order chat")
    await service.close_ticket(tkf.app, created.ticket.id, actor_id=customer.id, transcript=True)
    await tkf.app.scheduler.run_due()
    assert (await ticket_of(tkf, created.ticket.id)).transcript_status == "done"
    assert len(channel(tkf, "transcripts").messages) == 1


async def test_staff_roles_and_abort(tkf: TestEnv) -> None:
    roles = await service.staff_role_ids(tkf.app, ("billing_staff",))
    assert {r.id for r in roles} == {tkf.staff_role.id, tkf.app.resources.role_id("billing_staff")}
    created = await _create_order(tkf, tkf.member())
    await service.abort_ticket(tkf.app, created.ticket.id, reason="test", channel=created.channel)
    assert (await ticket_of(tkf, created.ticket.id)).status == "failed" and created.channel.deleted


async def test_missing_prefix_content_fails_cleanly(tkf: TestEnv) -> None:
    values = dict(tkf.content)
    del values[PREFIX_KEY]
    tkf.app.content.replace(file_values=values)
    with pytest.raises(MissingContent):
        await _create_order(tkf, tkf.member())
    assert [t.status for t in await tickets(tkf)] == ["failed"]
    assert [c for kind, c in tkf.guild.audit if kind == "create_text_channel"] == []


async def test_lookup_helpers(tkf: TestEnv) -> None:
    created = await _create_order(tkf, tkf.member())
    by_channel = await service.get_ticket_by_channel(tkf.app, created.channel.id)
    assert by_channel is not None and by_channel.id == created.ticket.id
    assert await service.get_ticket(tkf.app, 999) is None
    assert service.format_number(tkf.app, 7) == "0007"
    assert await service.participants(tkf.app, created.ticket.id) == []
