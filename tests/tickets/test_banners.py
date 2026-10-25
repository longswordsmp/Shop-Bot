"""Banner pictures on every ticket message (banners switched on, a tiny picture per banner id).

Live messages edited with view-only edits (the welcome's controls, the closed message, the select panel) keep the
banner they were posted with; the private close/delete prompt keeps its banner (no new upload) when answered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.tickets import MODULE, service, views
from shopbot.features.tickets import content_keys as k
from shopbot.ui.custom_ids import cid
from shopbot.ui.limits import FILES_TOTAL_BYTES
from tests.fakes import FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import TestEnv, banner_names, deep_merge, make_banner_env, provision_channels
from tests.tickets.util import (
    advance_past_cooldown,
    bot_messages,
    channel,
    control_message,
    number,
    open_ticket,
    run_command,
    tickets,
)


async def banner_env(tmp_path: Path, overrides: dict[str, Any] | None = None) -> TestEnv:
    tenv = await make_banner_env(tmp_path, deep_merge({"bot": {"rate_limit_per_10s": 100}}, overrides or {}))
    await provision_channels(tenv)
    return tenv


@pytest.fixture
async def bt(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    found = tenv.app.ui.banner(banner_id)
    assert found is not None, banner_id
    return found.filename


def _closed_message(ch: FakeTextChannel, ticket_id: int) -> FakeMessage:
    found = [m for m in ch.messages if cid("tk", "delete", ticket_id) in m.custom_ids()]
    assert found, "closed message not found"
    return found[-1]


def _view_only(message: FakeMessage) -> bool:
    return all(set(edit) == {"view"} for edit in message.edits)


async def test_ticket_lifecycle_messages_carry_their_banners(bt: TestEnv) -> None:
    customer, staff = bt.member(), bt.member(name="staff", staff=True)
    opened, ticket, ch = await open_ticket(bt, customer)
    assert opened.last_sent is not None and banner_names(opened.last_sent) == [name(bt, "ticket-created")]
    control = control_message(ch, ticket)
    assert banner_names(control) == [name(bt, "ticket-created")]
    [alert] = channel(bt, "staff_alerts").messages
    assert banner_names(alert) == [name(bt, "staff-notifications")]

    await click(bt.app, user=staff, custom_id=cid("tk", "claim", ticket.id), message=control)
    assert banner_names(bot_messages(ch)[-1]) == [name(bt, "submission-and-processing")]
    await click(bt.app, user=staff, custom_id=cid("tk", "unclaim", ticket.id), message=control)
    assert banner_names(bot_messages(ch)[-1]) == [name(bt, "pending-review")]

    # Close with the private confirmation: the answered prompt keeps its banner without uploading it again.
    prompt = (await click(bt.app, user=customer, custom_id=cid("tk", "close", ticket.id))).last_sent
    assert prompt is not None and banner_names(prompt) == [name(bt, "general-response")]
    await click(bt.app, user=customer, custom_id=cid("tk", "closeok", ticket.id), message=prompt)
    assert prompt.view is None and "attachments" not in prompt.edits[-1]
    assert banner_names(prompt) == [name(bt, "general-response")]
    closed = _closed_message(ch, ticket.id)
    assert banner_names(closed) == [name(bt, "resolution")]

    # The transcript: its banner first, then the transcript file.
    await bt.app.scheduler.run_due()
    [posted] = channel(bt, "transcripts").messages
    assert [f.filename for f in posted.files] == [name(bt, "transcripts"), f"{number(bt, 1)}.html"]

    # Reopen: the closed message loses its buttons (view-only edit), the status message shows its own banner.
    await click(bt.app, user=staff, custom_id=cid("tk", "reopen", ticket.id), message=closed)
    assert closed.edits == [{"view": None}] and banner_names(closed) == [name(bt, "resolution")]
    assert banner_names(bot_messages(ch)[-1]) == [name(bt, "support-request")]

    # Every change of the welcome's controls was a view-only edit: its banner stayed as posted.
    assert len(control.edits) >= 4 and _view_only(control)
    assert banner_names(control) == [name(bt, "ticket-created")]


async def test_only_the_first_welcome_message_has_a_banner(tmp_path: Path) -> None:
    long_form = [
        {"id": "one", "style": "paragraph", "required": True, "max_length": 4000},
        {"id": "two", "style": "paragraph", "required": True, "max_length": 4000},
    ]
    tenv = await banner_env(
        tmp_path, {"tickets": {"categories": [{"id": "general", "discord_category": "tickets", "form": long_form}]}}
    )
    try:
        customer = tenv.member()
        await submit_modal(
            tenv.app,
            user=customer,
            custom_id=cid("tk", "form", "general"),
            fields={"one": "a" * 4000, "two": "b" * 4000},
        )
        [ticket] = await tickets(tenv)
        ch = tenv.guild.get_channel(ticket.channel_id)
        assert isinstance(ch, FakeTextChannel)
        first, *overflow = bot_messages(ch)
        assert first.id == ticket.control_message_id and banner_names(first) == [name(tenv, "ticket-created")]
        assert overflow and all(banner_names(m) == [] and m.files == [] for m in overflow)
    finally:
        await tenv.close()


async def test_automatic_close_warning_and_deletion_notice(tmp_path: Path) -> None:
    tenv = await banner_env(
        tmp_path,
        {
            "tickets": {
                "auto_close_inactive_minutes": 60,
                "auto_close_warning_minutes": 15,
                "delete_after_close_minutes": 5,
            }
        },
    )
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        tenv.clock.advance(45 * 60)
        await tenv.app.scheduler.run_due()
        warning = bot_messages(ch)[-1]
        assert warning.content == customer.mention and banner_names(warning) == [name(tenv, "reminder")]
        tenv.clock.advance(15 * 60)
        await tenv.app.scheduler.run_due()
        assert banner_names(_closed_message(ch, ticket.id)) == [name(tenv, "cancellation")]  # nobody resolved it
        notice = bot_messages(ch)[-1]
        assert notice.embeds[0].description == tenv.app.content.render(k.DELETION_SCHEDULED, minutes=5)
        assert banner_names(notice) == [name(tenv, "reminder")]
    finally:
        await tenv.close()


async def test_delete_prompt_and_transcript_button_reply(bt: TestEnv) -> None:
    customer, staff = bt.member(), bt.member(name="staff", staff=True)
    _, ticket, ch = await open_ticket(bt, customer)
    await service.close_ticket(bt.app, ticket.id, actor_id=staff.id)
    closed = _closed_message(ch, ticket.id)
    prompt = (await click(bt.app, user=staff, custom_id=cid("tk", "delete", ticket.id), message=closed)).last_sent
    assert prompt is not None and banner_names(prompt) == [name(bt, "general-response")]
    await click(bt.app, user=staff, custom_id=cid("tk", "delno", ticket.id), message=prompt)
    assert prompt.view is None and "attachments" not in prompt.edits[-1]
    assert banner_names(prompt) == [name(bt, "general-response")]

    reply = (await click(bt.app, user=staff, custom_id=cid("tk", "transcript", ticket.id), message=closed)).last_sent
    assert reply is not None and banner_names(reply) == [name(bt, "transcripts")]


async def test_error_replies_carry_the_error_banner(bt: TestEnv) -> None:
    customer = bt.member()
    _, ticket, _ = await open_ticket(bt, customer)
    cooldown = (await click(bt.app, user=customer, custom_id=cid("tk", "open", "general"))).last_sent
    advance_past_cooldown(bt)
    limited = (await click(bt.app, user=customer, custom_id=cid("tk", "open", "general"))).last_sent
    await service.close_ticket(bt.app, ticket.id, actor_id=customer.id)
    not_open = (await click(bt.app, user=customer, custom_id=cid("tk", "close", ticket.id))).last_sent
    for reply in (cooldown, limited, not_open):
        assert reply is not None and banner_names(reply) == [name(bt, "error")]
    assert limited is not None and limited.embeds[0].description == bt.app.content.render(k.LIMIT_REACHED, limit=1)
    assert not_open is not None and not_open.embeds[0].description == bt.text(k.NOT_OPEN)


async def test_select_panel_keeps_its_banner_when_its_menu_is_reset(tmp_path: Path) -> None:
    tenv = await banner_env(tmp_path, {"tickets": {"panel_style": "select"}})
    try:
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        [panel] = channel(tenv, "ticket_panel").messages
        assert banner_names(panel) == [name(tenv, "support-request")]
        picked = await click(
            tenv.app, user=tenv.member(), custom_id=cid("tk", "pick"), values=["general"], message=panel
        )
        assert panel.edits and _view_only(panel) and banner_names(panel) == [name(tenv, "support-request")]
        assert picked.last_sent is not None and banner_names(picked.last_sent) == [name(tenv, "ticket-created")]
        # Publishing again with the same picture changes nothing.
        await MODULE.publish(tenv.app)
        assert _view_only(panel)
    finally:
        await tenv.close()


async def test_staff_command_replies_and_status_messages(bt: TestEnv) -> None:
    customer, staff, friend = bt.member(), bt.member(name="staff", staff=True), bt.member(name="friend")
    _, _, ch = await open_ticket(bt, customer)

    def reply(interaction: Any) -> list[str]:
        assert interaction.last_sent is not None and interaction.last_sent.ephemeral
        return banner_names(interaction.last_sent)

    assert reply(await run_command(bt, "add", user=staff, channel=ch, member=friend)) == [name(bt, "general-response")]
    assert banner_names(bot_messages(ch)[-1]) == [name(bt, "general-response")]  # member added
    assert reply(await run_command(bt, "remove", user=staff, channel=ch, member=friend)) == [
        name(bt, "general-response")
    ]
    assert banner_names(bot_messages(ch)[-1]) == [name(bt, "general-response")]  # member removed
    assert reply(await run_command(bt, "claim", user=staff, channel=ch)) == [name(bt, "general-response")]
    assert reply(await run_command(bt, "info", user=staff, channel=ch)) == [name(bt, "staff-dashboard")]
    assert reply(await run_command(bt, "list", user=staff)) == [name(bt, "staff-dashboard")]
    assert reply(await run_command(bt, "list", user=staff, status="failed")) == [name(bt, "staff-dashboard")]
    assert reply(await run_command(bt, "transcript", user=staff, channel=ch)) == [name(bt, "transcripts")]
    assert reply(await run_command(bt, "close", user=staff, channel=ch)) == [name(bt, "general-response")]
    # Refusals: wrong state, not a ticket channel.
    assert reply(await run_command(bt, "close", user=staff, channel=ch)) == [name(bt, "error")]
    assert reply(await run_command(bt, "info", user=staff, channel=channel(bt, "staff_alerts"))) == [name(bt, "error")]
    # A customer is refused by the shared error path.
    assert reply(await run_command(bt, "close", user=customer, channel=ch)) == [name(bt, "error")]


async def test_transcript_files_follow_the_banner_and_a_large_transcript_drops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenv = await banner_env(tmp_path, {"tickets": {"transcript_channel": ""}})
    try:
        customer, staff = tenv.member(), tenv.member(name="staff", staff=True)
        _, _, ch = await open_ticket(tenv, customer)
        attached = (await run_command(tenv, "transcript", user=staff, channel=ch)).last_sent
        assert attached is not None
        assert [f.filename for f in attached.files] == [name(tenv, "transcripts"), f"{number(tenv, 1)}.html"]

        # The banner gives way when it and the transcript would not fit in one upload.
        async def huge(app: Any, ticket: Any) -> tuple[bytes, str]:
            return b"x" * (FILES_TOTAL_BYTES - 1), "big.html"

        monkeypatch.setattr(service, "render_transcript", huge)
        big = (await run_command(tenv, "transcript", user=staff, channel=ch)).last_sent
        assert big is not None and [f.filename for f in big.files] == ["big.html"]
    finally:
        await tenv.close()


async def test_transcript_post_and_direct_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tenv = await banner_env(tmp_path, {"tickets": {"transcript_dm_user": True}})
    try:
        customer = tenv.member()
        _, ticket, _ = await open_ticket(tenv, customer)
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        await tenv.app.scheduler.run_due()
        expected = [name(tenv, "transcripts"), f"{number(tenv, 1)}.html"]
        [posted] = channel(tenv, "transcripts").messages
        assert [f.filename for f in posted.files] == expected
        [dm] = customer.dms
        assert [f.filename for f in dm.files] == expected

        async def huge(app: Any, ticket: Any) -> tuple[bytes, str]:
            return b"x" * (FILES_TOTAL_BYTES - 1), "big.html"

        monkeypatch.setattr(service, "render_transcript", huge)
        assert await service.generate_transcript(tenv.app, ticket.id, force=True)
        assert [f.filename for f in channel(tenv, "transcripts").messages[-1].files] == ["big.html"]
    finally:
        await tenv.close()


def test_room_for_file() -> None:
    from shopbot.ui.spec import FileSpec

    banner = FileSpec.of("banner-x-0.png", b"\x89PNG\r\n\x1a\nx", "image/png")
    assert service.room_for_file(banner, 100) is banner
    assert service.room_for_file(banner, FILES_TOTAL_BYTES - banner.size) is banner
    assert service.room_for_file(banner, FILES_TOTAL_BYTES - banner.size + 1) is None
    assert service.room_for_file(None, 1) is None


async def test_preview_states_show_the_mapped_banners(bt: TestEnv) -> None:
    from shopbot.features.tickets.preview import preview

    states = {state.name: state.spec for state in preview(bt.app)}
    expected: dict[str, BannerId] = {
        "panel.buttons": "support-request",
        "welcome.general": "ticket-created",
        "controls.claimed": "ticket-created",
        "staff_alert": "staff-notifications",
        "close_confirm": "general-response",
        "close_confirm.answered": "general-response",
        "delete_confirm": "general-response",
        "closed": "resolution",
        "closed.with_reason": "resolution",
        "auto_closed": "cancellation",
        "claimed": "submission-and-processing",
        "unclaimed": "pending-review",
        "reopened": "support-request",
        "deletion_scheduled": "reminder",
        "auto_close_warning": "reminder",
        "user_added": "general-response",
        "user_removed": "general-response",
        "transcript": "transcripts",
        "transcript_dm": "transcripts",
        "reply.created": "ticket-created",
        "reply.limit_reached": "error",
        "reply.not_open": "error",
    }
    for state, banner_id in expected.items():
        spec = states[state]
        banner = getattr(spec, "banner", None)
        assert banner is not None and banner.filename == name(bt, banner_id), state
    assert views.TRANSCRIPT_BANNER == "transcripts"
