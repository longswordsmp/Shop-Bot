"""Helpers for the orders_privacy tests: staff closing order tickets with ``/ticket close``, the customer's form answers
kept out of the staff channel, and the staff commands' visibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.db.repos import audit as audit_repo
from shopbot.features.checkout import repo
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import Ticket
from tests.checkout.util import make_env
from tests.fakes import FakeTextChannel
from tests.helpers import GUILD_ID, TestEnv, deep_merge

# A details form, a fulfillment option with its own form and a submission form (all synthetic), so every kind of
# typed answer exists.
FORMS: dict[str, Any] = {
    "checkout": {
        "fulfillment_options": [
            {
                "id": "delivery",
                "fee_minor": 0,
                "form": [{"id": "address", "style": "paragraph", "required": True, "max_length": 500}],
            },
        ],
        "details_form": [{"id": "name", "style": "short", "required": True, "min_length": 2, "max_length": 80}],
        "submission_form": [{"id": "reference", "style": "short", "required": True, "max_length": 40}],
    }
}

# Order tickets get a transcript when they close (the base test layout has transcripts off for orders).
TRANSCRIPTS: dict[str, Any] = {"checkout": {"transcript_on_close": True}}


async def env(tmp_path: Path, *overrides: dict[str, Any], **kwargs: Any) -> TestEnv:
    merged: dict[str, Any] = {}
    for override in overrides:
        merged = deep_merge(merged, override)
    return await make_env(tmp_path, merged, **kwargs)


async def ticket_of(tenv: TestEnv, ticket_id: int) -> Ticket:
    ticket = await tickets_service.get_ticket(tenv.app, ticket_id)
    assert ticket is not None
    return ticket


def number(tenv: TestEnv, ticket: Ticket) -> str:
    return tickets_service.format_number(tenv.app, ticket.number)


async def audit_actions(tenv: TestEnv) -> list[str]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e.action for e in reversed(events)]


async def audit_details(tenv: TestEnv, action: str) -> list[dict[str, Any]]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [dict(e.details) for e in reversed(events) if e.action == action]


async def check_task_status(tenv: TestEnv, ticket_id: int) -> str | None:
    from shopbot.db.repos import tasks as task_repo

    task = await tenv.app.db.run(task_repo.get_by_dedupe, repo.ticket_check_key(ticket_id))
    return None if task is None else str(task.status)


async def run_tasks(tenv: TestEnv, *, rounds: int = 4) -> None:
    for _ in range(rounds):
        if not await tenv.app.scheduler.run_due():
            return


def public_texts(tenv: TestEnv, *, except_channel: FakeTextChannel | None = None) -> str:
    """Every message text in every channel of the server except ``except_channel`` (the customer's own ticket)."""
    parts: list[str] = []
    for ch in tenv.guild.text_channels:
        if except_channel is not None and ch.id == except_channel.id:
            continue
        parts += [message.text() + "\n" + (message.content or "") for message in ch.messages]
    return "\n".join(parts)
