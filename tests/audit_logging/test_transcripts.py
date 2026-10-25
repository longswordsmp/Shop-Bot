"""#transcripts: the transcript DM goes through the notifier (failures recorded, audited and alerted once), and the
transcript channel must be staff/admin-only like the audit channel (settings problem + the post refuses it)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import discord

from shopbot.db.repos import notifications as notif_repo
from shopbot.features.tickets import MODULE as TICKETS
from shopbot.features.tickets import service
from shopbot.features.tickets.models import TRANSCRIPT_PENDING
from shopbot.ui.custom_ids import cid
from tests.audit_logging.util import FailureRecorder, audit_events
from tests.fakes import click
from tests.helpers import make_settings
from tests.setup.util import restarted_app
from tests.tickets.util import channel, make_env, open_ticket, ticket_of


async def test_an_undeliverable_transcript_dm_is_recorded_and_reported_once(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"transcript_dm_user": True}})
    try:
        recorder = FailureRecorder(tenv.app)
        customer = tenv.member()
        customer.dm_forbidden = True
        _, ticket, ch = await open_ticket(tenv, customer)
        ch.add_user_message(customer, "hello")
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        await tenv.app.scheduler.run_due()
        assert (await ticket_of(tenv, ticket.id)).transcript_status == "done"
        assert len(channel(tenv, "transcripts").messages) == 1  # staff still have the transcript
        assert recorder.of(service.TRANSCRIPT_DM_KIND) == [
            {"kind": "notification", "source": service.TRANSCRIPT_DM_KIND, "user_id": customer.id, "channel_id": None}
        ]
        [failed] = await audit_events(tenv.app, "notifications.failed")
        assert failed.target_id == str(customer.id) and failed.details == {"kind": service.TRANSCRIPT_DM_KIND}
        assert not await tenv.app.db.run(notif_repo.already_sent, service.dedupe_transcript_dm(ticket.id))
    finally:
        await tenv.close()


async def test_the_transcript_dm_is_sent_once_even_with_other_dms_off_and_after_a_restart(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"transcript_dm_user": True}, "features": {"dm_notifications": False}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        ch.add_user_message(customer, "hello")
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        await tenv.app.scheduler.run_due()
        # tickets.transcript_dm_user asks for the DM explicitly, so it is sent although dm_notifications is off.
        assert len(customer.dms) == 1 and customer.dms[0].files
        # After a restart, a staff re-post (forced transcript) never DMs the customer again.
        app = restarted_app(tenv)
        await app.resources.refresh()
        staff = tenv.member(name="staff", staff=True)
        await click(app, user=staff, custom_id=cid("tk", "transcript", ticket.id))
        assert len(channel(tenv, "transcripts").messages) == 2
        assert len(customer.dms) == 1
    finally:
        await tenv.close()


def test_a_public_transcript_channel_is_a_settings_problem() -> None:
    public = make_settings({"tickets": {"transcript_channel": "shop"}})  # 'shop' is in the public support category
    problems = TICKETS.settings_problems(public) if TICKETS.settings_problems else []
    assert "tickets.transcript_channel: channel 'shop' must have staff or admin access (it is 'public_readonly')" in (
        problems
    )
    private = make_settings()
    assert not any(
        "transcript_channel" in p for p in (TICKETS.settings_problems(private) if TICKETS.settings_problems else [])
    )


async def test_a_public_transcript_channel_makes_tickets_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"transcript_channel": "shop"}})
    try:
        assert not tenv.app.ready("tickets")
    finally:
        await tenv.close()


async def test_the_transcript_post_refuses_a_channel_that_is_not_private(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        ch.add_user_message(customer, "private conversation")
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        # The configuration changes under the running bot (e.g. a reload) to a public transcript channel.
        settings = tenv.app.settings
        tenv.app.settings = dataclasses.replace(
            settings, tickets=dataclasses.replace(settings.tickets, transcript_channel="shop")
        )
        assert not await service.generate_transcript(tenv.app, ticket.id)
        assert not await service.generate_transcript(tenv.app, ticket.id, force=True)
        assert channel(tenv, "shop").messages == []
        assert all(not message.files for message in channel(tenv, "transcripts").messages)
        assert (await ticket_of(tenv, ticket.id)).transcript_status == TRANSCRIPT_PENDING  # kept until fixed
        # Back to the private channel: the outstanding transcript is posted there.
        tenv.app.settings = settings
        assert await service.generate_transcript(tenv.app, ticket.id)
        [posted] = channel(tenv, "transcripts").messages
        assert posted.files and isinstance(posted.files[0], discord.File)
    finally:
        await tenv.close()
