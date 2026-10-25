from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features import core_keys as core
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import service
from shopbot.ui.custom_ids import cid
from tests.fakes import FakeMessage, FakeTextChannel, click, http_error, submit_modal
from tests.helpers import TestEnv
from tests.tickets.util import (
    audit_actions,
    bot_messages,
    category,
    channel,
    control_message,
    make_env,
    number,
    open_ticket,
    overwrite_for,
    render,
    task,
    ticket_of,
    tickets,
)


def _closed_message(ch: FakeTextChannel, ticket_id: int) -> FakeMessage:
    for message in ch.messages:
        if cid("tk", "delete", ticket_id) in message.custom_ids():
            return message
    raise AssertionError("closed message not found")


async def _close_with_confirmation(tenv: TestEnv, user, ticket_id: int):  # type: ignore[no-untyped-def]
    prompt_click = await click(tenv.app, user=user, custom_id=cid("tk", "close", ticket_id))
    prompt = prompt_click.last_sent
    assert prompt is not None
    confirm = await click(tenv.app, user=user, custom_id=cid("tk", "closeok", ticket_id), message=prompt)
    return prompt, confirm


async def test_owner_closes_with_confirmation(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)

    prompt_click = await click(tk.app, user=customer, custom_id=cid("tk", "close", ticket.id))
    prompt = prompt_click.last_sent
    assert prompt is not None and prompt.ephemeral  # type: ignore[attr-defined]
    assert prompt.embeds[0].title == tk.text(k.CLOSE_CONFIRM_TITLE)
    assert prompt.embeds[0].description == tk.text(k.CLOSE_CONFIRM_DESCRIPTION)
    assert prompt.custom_ids() == [cid("tk", "closeok", ticket.id), cid("tk", "closeno", ticket.id)]
    assert (await ticket_of(tk, ticket.id)).status == "open"

    await click(tk.app, user=customer, custom_id=cid("tk", "closeok", ticket.id), message=prompt)
    closed = await ticket_of(tk, ticket.id)
    assert closed.status == "closed" and closed.closed_by == customer.id and closed.closed_at is not None
    assert closed.transcript_status == "pending"
    assert prompt.view is None  # the answered prompt lost its buttons
    # Locked: the owner can read but no longer post.
    owner = overwrite_for(ch, customer.id)
    assert owner is not None and owner.view_channel is True and owner.send_messages is False
    # Closed message with reopen / transcript / delete; the welcome controls are removed.
    message = _closed_message(ch, ticket.id)
    assert message.embeds[0].title == render(tk, k.CLOSED_TITLE, number=number(tk, 1))
    assert message.embeds[0].description == render(
        tk, k.CLOSED_DESCRIPTION, closer_mention=customer.mention, number=number(tk, 1)
    )
    assert message.custom_ids() == [
        cid("tk", "reopen", ticket.id),
        cid("tk", "transcript", ticket.id),
        cid("tk", "delete", ticket.id),
    ]
    assert control_message(ch, ticket).view is None
    transcript_task = await task(tk, service.dedupe_transcript(ticket.id))
    assert transcript_task is not None and transcript_task.status == "pending"
    assert (await task(tk, service.dedupe_delete(ticket.id))) is None  # delete_after_close_minutes = 0
    assert "tickets.closed" in await audit_actions(tk)


async def test_duplicate_close_does_not_double_apply(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    prompt, _ = await _close_with_confirmation(tk, customer, ticket.id)
    again = await click(tk.app, user=customer, custom_id=cid("tk", "closeok", ticket.id), message=prompt)
    assert again.last_sent is not None and again.last_sent.embeds[0].description == tk.text(k.NOT_OPEN)
    stale = await click(tk.app, user=customer, custom_id=cid("tk", "close", ticket.id))
    assert stale.last_sent is not None and stale.last_sent.embeds[0].description == tk.text(k.NOT_OPEN)
    closed_messages = [m for m in ch.messages if cid("tk", "delete", ticket.id) in m.custom_ids()]
    assert len(closed_messages) == 1
    assert (await audit_actions(tk)).count("tickets.closed") == 1


async def test_cancel_keeps_ticket_open(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, _ = await open_ticket(tk, customer)
    prompt_click = await click(tk.app, user=customer, custom_id=cid("tk", "close", ticket.id))
    prompt = prompt_click.last_sent
    await click(tk.app, user=customer, custom_id=cid("tk", "closeno", ticket.id), message=prompt)
    assert prompt is not None and prompt.view is None
    assert (await ticket_of(tk, ticket.id)).status == "open"


async def test_stale_prompt_still_closes_when_its_text_was_removed(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, _ = await open_ticket(tk, customer)
    prompt = (await click(tk.app, user=customer, custom_id=cid("tk", "close", ticket.id))).last_sent
    values = dict(tk.content)
    del values[k.CLOSE_CONFIRM_DESCRIPTION]  # e.g. the operator switched confirmations off meanwhile
    tk.app.content.replace(file_values=values)
    confirm = await click(tk.app, user=customer, custom_id=cid("tk", "closeok", ticket.id), message=prompt)
    assert (await ticket_of(tk, ticket.id)).status == "closed"
    assert confirm.sent == [] and confirm.response_kinds() == ["defer"]


async def test_other_customer_cannot_close_and_customer_cannot_claim(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, _ = await open_ticket(tk, customer)
    stranger = tk.member(name="stranger")
    for action in ("close", "closeok"):
        denied = await click(tk.app, user=stranger, custom_id=cid("tk", action, ticket.id))
        assert denied.last_sent is not None
        assert denied.last_sent.embeds[0].description == tk.text(core.ERROR_PERMISSION)
    for action in ("claim", "delete", "transcript"):
        denied = await click(tk.app, user=customer, custom_id=cid("tk", action, ticket.id))
        assert denied.last_sent is not None
        assert denied.last_sent.embeds[0].description == tk.text(core.ERROR_PERMISSION)
    current = await ticket_of(tk, ticket.id)
    assert current.status == "open" and current.assigned_staff_id is None


async def test_close_without_confirmation_and_user_close_disabled(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"close_confirmation": False, "allow_user_close": False}})
    try:
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        denied = await click(tenv.app, user=customer, custom_id=cid("tk", "close", ticket.id))
        assert denied.last_sent is not None
        assert denied.last_sent.embeds[0].description == tenv.text(core.ERROR_PERMISSION)
        staff = tenv.member(name="staff", staff=True)
        done = await click(tenv.app, user=staff, custom_id=cid("tk", "close", ticket.id))
        assert done.response_kinds() == ["defer"] and done.sent == []
        assert (await ticket_of(tenv, ticket.id)).status == "closed"
    finally:
        await tenv.close()


async def test_reopen_by_staff_restores_everything(tkf: TestEnv) -> None:
    tkf.app.settings = _with(tkf, closed_category="closed")
    customer = tkf.member()
    _, ticket, ch = await open_ticket(tkf, customer)
    await _close_with_confirmation(tkf, customer, ticket.id)
    assert ch.category_id == category(tkf, "closed").id
    closed_message = _closed_message(ch, ticket.id)

    denied = await click(tkf.app, user=customer, custom_id=cid("tk", "reopen", ticket.id))
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tkf.text(core.ERROR_PERMISSION)

    staff = tkf.member(name="staff", staff=True)
    await click(tkf.app, user=staff, custom_id=cid("tk", "reopen", ticket.id))
    reopened = await ticket_of(tkf, ticket.id)
    assert reopened.status == "open" and reopened.closed_at is None and reopened.transcript_status == "none"
    assert ch.category_id == category(tkf, "tickets").id
    owner = overwrite_for(ch, customer.id)
    assert owner is not None and owner.send_messages is True
    assert closed_message.view is None
    assert control_message(ch, ticket).custom_ids() == [cid("tk", "close", ticket.id), cid("tk", "claim", ticket.id)]
    assert bot_messages(ch)[-1].embeds[0].description == render(tkf, k.REOPENED, actor_mention=staff.mention)
    transcript_task = await task(tkf, service.dedupe_transcript(ticket.id))
    assert transcript_task.status == "cancelled"
    again = await click(tkf.app, user=staff, custom_id=cid("tk", "reopen", ticket.id))
    assert again.last_sent is not None and again.last_sent.embeds[0].description == tkf.text(k.NOT_OPEN)
    assert (await audit_actions(tkf)).count("tickets.reopened") == 1


async def test_owner_can_reopen_when_allowed(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"user_can_reopen": True}})
    try:
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        await _close_with_confirmation(tenv, customer, ticket.id)
        await click(tenv.app, user=customer, custom_id=cid("tk", "reopen", ticket.id))
        assert (await ticket_of(tenv, ticket.id)).status == "open"
    finally:
        await tenv.close()


async def test_claim_and_unclaim_rules(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    staff_a = tk.member(name="a", staff=True)
    staff_b = tk.member(name="b", staff=True)
    admin = tk.member(name="admin", admin=True)

    await click(tk.app, user=staff_a, custom_id=cid("tk", "claim", ticket.id))
    assert (await ticket_of(tk, ticket.id)).assigned_staff_id == staff_a.id
    assert bot_messages(ch)[-1].embeds[0].description == render(tk, k.CLAIMED, staff_mention=staff_a.mention)
    assert control_message(ch, ticket).custom_ids() == [
        cid("tk", "close", ticket.id),
        cid("tk", "unclaim", ticket.id),
    ]
    stale = await click(tk.app, user=staff_b, custom_id=cid("tk", "claim", ticket.id))
    assert stale.last_sent is not None and stale.last_sent.embeds[0].description == tk.text(k.NOT_OPEN)
    assert (await ticket_of(tk, ticket.id)).assigned_staff_id == staff_a.id

    denied = await click(tk.app, user=staff_b, custom_id=cid("tk", "unclaim", ticket.id))
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tk.text(core.ERROR_PERMISSION)
    await click(tk.app, user=admin, custom_id=cid("tk", "unclaim", ticket.id))
    assert (await ticket_of(tk, ticket.id)).assigned_staff_id is None
    assert bot_messages(ch)[-1].embeds[0].description == tk.text(k.UNCLAIMED)
    assert control_message(ch, ticket).custom_ids()[1] == cid("tk", "claim", ticket.id)
    actions = await audit_actions(tk)
    assert actions.count("tickets.claimed") == 1 and actions.count("tickets.unclaimed") == 1


async def test_category_staff_role_is_scoped_to_its_category(tkf: TestEnv) -> None:
    billing_role = tkf.guild.get_role(tkf.app.resources.role_id("billing_staff") or 0)
    agent = tkf.guild.add_member(name="billing agent", roles=[billing_role])
    _, general, _ = await open_ticket(tkf, tkf.member(name="c1"))
    await submit_modal(
        tkf.app, user=tkf.member(name="c2"), custom_id=cid("tk", "form", "billing"), fields={"details": "help me"}
    )
    billing = (await tickets(tkf))[-1]
    assert billing.category_id == "billing"
    billing_channel = tkf.guild.get_channel(billing.channel_id)
    assert overwrite_for(billing_channel, billing_role.id) is not None
    denied = await click(tkf.app, user=agent, custom_id=cid("tk", "claim", general.id))
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tkf.text(core.ERROR_PERMISSION)
    await click(tkf.app, user=agent, custom_id=cid("tk", "claim", billing.id))
    assert (await ticket_of(tkf, billing.id)).assigned_staff_id == agent.id


async def test_delete_generates_pending_transcript_first(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    ch.add_user_message(customer, "hello")
    staff = tk.member(name="staff", staff=True)

    early = await click(tk.app, user=staff, custom_id=cid("tk", "delete", ticket.id))
    assert early.last_sent is not None and early.last_sent.embeds[0].description == tk.text(k.NOT_OPEN)

    await _close_with_confirmation(tk, customer, ticket.id)
    prompt_click = await click(tk.app, user=staff, custom_id=cid("tk", "delete", ticket.id))
    prompt = prompt_click.last_sent
    assert prompt is not None and prompt.embeds[0].description == tk.text(k.DELETE_CONFIRM_DESCRIPTION)
    assert prompt.custom_ids() == [cid("tk", "delok", ticket.id), cid("tk", "delno", ticket.id)]

    await click(tk.app, user=staff, custom_id=cid("tk", "delok", ticket.id), message=prompt)
    deleted = await ticket_of(tk, ticket.id)
    assert deleted.status == "deleted" and deleted.deleted_at is not None
    assert deleted.transcript_status == "done"
    assert ch.deleted
    transcripts = channel(tk, "transcripts").messages
    assert len(transcripts) == 1 and transcripts[0].files[0].filename == f"{number(tk, 1)}.html"
    assert "tickets.deleted" in await audit_actions(tk)
    # The pending transcript task now has nothing to do.
    assert (await task(tk, service.dedupe_transcript(ticket.id))).status == "cancelled"


async def test_delete_is_refused_when_transcript_fails(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    await _close_with_confirmation(tk, customer, ticket.id)
    channel(tk, "transcripts").fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    staff = tk.member(name="staff", staff=True)
    refused = await click(tk.app, user=staff, custom_id=cid("tk", "delok", ticket.id))
    assert refused.last_sent is not None
    assert refused.last_sent.embeds[0].description == tk.text(core.ERROR_GENERIC)
    current = await ticket_of(tk, ticket.id)
    assert current.status == "closed" and current.transcript_status == "pending"
    assert not ch.deleted


async def test_delete_cancel_and_customer_cannot_delete(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    await _close_with_confirmation(tk, customer, ticket.id)
    denied = await click(tk.app, user=customer, custom_id=cid("tk", "delok", ticket.id))
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tk.text(core.ERROR_PERMISSION)
    staff = tk.member(name="staff", staff=True)
    prompt = (await click(tk.app, user=staff, custom_id=cid("tk", "delete", ticket.id))).last_sent
    await click(tk.app, user=staff, custom_id=cid("tk", "delno", ticket.id), message=prompt)
    assert prompt is not None and prompt.view is None
    assert not ch.deleted and (await ticket_of(tk, ticket.id)).status == "closed"


async def test_transcript_button_posts_now(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, _ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)
    reply = await click(tk.app, user=staff, custom_id=cid("tk", "transcript", ticket.id))
    posted = channel(tk, "transcripts").messages
    assert len(posted) == 1
    assert posted[0].embeds[0].description == render(
        tk,
        k.TRANSCRIPT_DESCRIPTION,
        number=number(tk, 1),
        user_mention=customer.mention,
        closer_mention=staff.mention,
    )
    assert reply.last_sent is not None and reply.last_sent.ephemeral  # type: ignore[attr-defined]
    assert (await ticket_of(tk, ticket.id)).transcript_message_id == posted[0].id


async def test_close_posts_deletion_notice_and_schedules_deletion(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"delete_after_close_minutes": 10}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        await _close_with_confirmation(tenv, customer, ticket.id)
        assert bot_messages(ch)[-1].embeds[0].description == render(tenv, k.DELETION_SCHEDULED, minutes=10)
        delete_task = await task(tenv, service.dedupe_delete(ticket.id))
        assert delete_task.status == "pending" and delete_task.run_at == tenv.clock.now() + 600
    finally:
        await tenv.close()


async def test_reason_is_shown_only_with_configured_label(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)
    await service.close_ticket(tk.app, ticket.id, actor_id=staff.id, reason="spam *here*")
    message = _closed_message(ch, ticket.id)
    assert [(f.name, f.value) for f in message.embeds[0].fields] == [(tk.text(k.CLOSED_REASON_LABEL), r"spam \*here\*")]


def _with(tenv: TestEnv, **ticket_settings: object):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(tenv.app.settings, tickets=replace(tenv.app.settings.tickets, **ticket_settings))


async def test_controls_are_kept_when_the_closed_message_cannot_be_posted(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    staff = tk.member(name="staff", staff=True)
    ch.fail_send = http_error(discord.HTTPException, 400, 50035, "Invalid Form Body")
    assert await service.close_ticket(tk.app, ticket.id, actor_id=staff.id, reason="spam") is not None
    assert (await ticket_of(tk, ticket.id)).status == "closed"
    assert [m for m in ch.messages if cid("tk", "delete", ticket.id) in m.custom_ids()] == []
    # Staff are not left without controls: the welcome keeps its buttons.
    assert control_message(ch, ticket).custom_ids() == [cid("tk", "close", ticket.id), cid("tk", "claim", ticket.id)]


async def test_controls_are_kept_when_the_closed_message_text_is_missing(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    values = dict(tk.content)
    del values[k.CLOSED_TITLE]
    tk.app.content.replace(file_values=values)  # removed at runtime (readiness not recomputed yet)
    await service.close_ticket(tk.app, ticket.id, actor_id=customer.id)
    assert control_message(ch, ticket).view is not None


def _thread_perms(overwrite: discord.PermissionOverwrite | None) -> tuple[object, object, object]:
    assert overwrite is not None
    return overwrite.create_public_threads, overwrite.create_private_threads, overwrite.send_messages_in_threads


async def test_owner_and_participants_cannot_use_threads_to_bypass_the_lock(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, ch = await open_ticket(tk, customer)
    # Open: thread creation is denied (thread messages are not in the transcript).
    assert _thread_perms(overwrite_for(ch, customer.id)) == (False, False, None)
    helper = tk.member(name="helper")
    assert await service.add_participant(tk.app, ticket, helper, actor_id=customer.id)
    assert _thread_perms(overwrite_for(ch, helper.id)) == (False, False, None)

    await service.close_ticket(tk.app, ticket.id, actor_id=customer.id)  # lock_on_close = true
    for user_id in (customer.id, helper.id):
        locked = overwrite_for(ch, user_id)
        assert _thread_perms(locked) == (False, False, False)
        assert locked is not None and locked.send_messages is False and locked.view_channel is True
    late = tk.member(name="late")
    closed = await ticket_of(tk, ticket.id)
    assert await service.add_participant(tk.app, closed, late, actor_id=customer.id)
    assert _thread_perms(overwrite_for(ch, late.id)) == (False, False, False)

    await service.reopen_ticket(tk.app, ticket.id, actor_id=customer.id)
    for user_id in (customer.id, helper.id, late.id):
        unlocked = overwrite_for(ch, user_id)
        assert unlocked is not None and unlocked.send_messages is True
        assert _thread_perms(unlocked) == (False, False, None)  # posting restored, threads still denied
