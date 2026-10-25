from __future__ import annotations

import re
from pathlib import Path

import discord

from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import service
from shopbot.features.tickets.transcript import TranscriptMessage, build_html, participants_of
from shopbot.ui.custom_ids import cid
from tests.fakes import FakeAttachment, click
from tests.helpers import TestEnv
from tests.tickets.util import channel, make_env, number, open_ticket, render, ticket_of


def _html(message) -> str:  # type: ignore[no-untyped-def]
    file = message.files[0]
    return file.fp.getvalue().decode("utf-8")


def _visible_text(document: str) -> str:
    """Text a reader sees, minus URLs (which are data, e.g. .../attachments/...)."""
    without_style = re.sub(r"<style>.*?</style>", "", document, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", without_style)
    return re.sub(r"https?://\S+", " ", text)


async def test_transcript_contains_escaped_data_only(tk: TestEnv) -> None:
    customer = tk.member(name="Eve <b>")
    _, ticket, ch = await open_ticket(tk, customer)
    ch.add_user_message(
        customer,
        "<script>alert('x')</script> & \"quotes\"",
        attachments=[
            FakeAttachment("a.png", url="https://cdn.discordapp.com/attachments/1/2/a<b>.png"),
            FakeAttachment("b.txt", url="javascript:alert(1)"),
        ],
    )
    await service.close_ticket(tk.app, ticket.id, actor_id=customer.id)
    assert await service.generate_transcript(tk.app, ticket.id)

    [posted] = channel(tk, "transcripts").messages
    assert posted.embeds[0].title == render(tk, k.TRANSCRIPT_TITLE, number=number(tk, 1))
    assert posted.files[0].filename == f"transcript-{number(tk, 1)}.html"
    document = _html(posted)
    assert "<script>" not in document
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; &quot;quotes&quot;" in document
    assert 'href="https://cdn.discordapp.com/attachments/1/2/a&lt;b&gt;.png"' in document
    assert 'href="javascript' not in document and "javascript:alert(1)" in document
    assert "Eve &lt;b&gt;" in document and str(customer.id) in document
    assert f"<title>{number(tk, 1)}</title>" in document
    # Bot messages (welcome embed with its fields) are part of the record.
    welcome_title = render(
        tk,
        k.category_key("general", "welcome_title"),
        user_mention=customer.mention,
        user_name=discord.utils.escape_markdown("Eve <b>"),
        number=number(tk, 1),
    )
    assert welcome_title.replace("<", "&lt;").replace(">", "&gt;") in document
    # No invented wording: the visible text holds no English labels.
    visible = _visible_text(document).lower()
    for word in ("transcript", "participants", "messages", "created", "closed", "author", "attachments"):
        assert word not in visible
    current = await ticket_of(tk, ticket.id)
    assert current.transcript_status == "done" and current.transcript_message_id == posted.id
    # Not forced: a completed transcript is not posted again.
    assert await service.generate_transcript(tk.app, ticket.id)
    assert len(channel(tk, "transcripts").messages) == 1


def test_build_html_escapes_embed_fields_and_participants() -> None:
    from datetime import UTC, datetime

    from shopbot.features.tickets.transcript import TranscriptEmbed

    messages = [
        TranscriptMessage(
            id=1,
            created_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            author_id=7,
            author_name="<i>bot</i>",
            content="",
            embeds=(TranscriptEmbed(title="<t>", description="d&d", fields=(("<n>", "<v>"),)),),
        )
    ]
    document = build_html(
        number="0042",
        category_id="general",
        created_at=1_700_000_000,
        closed_at=None,
        participants=participants_of(5, [6], messages, {5: 'owner"name'}),
        messages=messages,
    )
    assert "&lt;t&gt;" in document and "d&amp;d" in document and "<dt>&lt;n&gt;</dt><dd>&lt;v&gt;</dd>" in document
    assert 'datetime="2024-01-02T03:04:05+00:00"' in document
    assert "2023-11-14T22:13:20+00:00" in document  # created_at in UTC ISO
    assert 'class="closed"' not in document
    order = re.findall(r'data-user-id="(\d+)"', document)
    assert order == ["5", "6", "7"]
    assert "owner&quot;name" in document


async def test_transcript_task_and_dm_to_owner(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"transcript_dm_user": True}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        ch.add_user_message(customer, "hi")
        await service.close_ticket(tenv.app, ticket.id, actor_id=customer.id)
        assert await tenv.app.scheduler.run_due() == 1
        assert (await ticket_of(tenv, ticket.id)).transcript_status == "done"
        assert len(customer.dms) == 1
        dm = customer.dms[0]
        assert dm.embeds[0].title == render(tenv, k.TRANSCRIPT_DM_TITLE, number=number(tenv, 1))
        assert dm.files[0].filename == f"transcript-{number(tenv, 1)}.html"
        # A forced re-post (staff) never DMs the customer twice.
        staff = tenv.member(name="staff", staff=True)
        await click(tenv.app, user=staff, custom_id=cid("tk", "transcript", ticket.id))
        assert len(channel(tenv, "transcripts").messages) == 2
        assert len(customer.dms) == 1
    finally:
        await tenv.close()


async def test_transcript_task_retries_then_fails_permanently(tk: TestEnv) -> None:
    customer = tk.member()
    _, ticket, _ = await open_ticket(tk, customer)
    transcripts = channel(tk, "transcripts")
    transcripts.fail_send = _forbidden()
    await service.close_ticket(tk.app, ticket.id, actor_id=customer.id)
    attempts = tk.app.scheduler.max_attempts
    for _ in range(attempts):
        await tk.app.scheduler.run_due()
        tk.clock.advance(3600)
    current = await ticket_of(tk, ticket.id)
    assert current.transcript_status == "failed"
    from tests.tickets.util import audit_actions, task

    assert (await task(tk, service.dedupe_transcript(ticket.id))).status == "failed"
    assert "tickets.transcript_failed" in await audit_actions(tk)


async def test_transcript_without_content_intent_still_produced(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    tenv = await make_env(tmp_path, {"bot": {"message_content_intent": False}})
    try:
        customer = tenv.member()
        _, ticket, ch = await open_ticket(tenv, customer)
        ch.add_user_message(customer, "")
        assert await service.generate_transcript(tenv.app, ticket.id, force=True, requested_by=customer.id)
        assert await service.generate_transcript(tenv.app, ticket.id, force=True, requested_by=customer.id)
        assert len(channel(tenv, "transcripts").messages) == 2
        warnings = [r for r in caplog.records if r.getMessage() == "tickets_transcript_without_content_intent"]
        assert len(warnings) == 1
    finally:
        await tenv.close()


def _forbidden() -> discord.Forbidden:
    from tests.fakes import http_error

    return http_error(discord.Forbidden, 403, 50013, "Missing Permissions")  # type: ignore[no-any-return]
