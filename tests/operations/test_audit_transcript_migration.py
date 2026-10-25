"""Audit masking, transcript placeholders (closure time, final status, close reason), and migration 0003 (staff notes
moved out of the status history)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shopbot.core.audit import REDACTED, sanitize_details
from shopbot.db.migrate import apply_migrations, discover
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_keys
from shopbot.features.tickets import content_keys as tk
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets import views as ticket_views
from tests.checkout.util import add_product, place_order
from tests.helpers import TestEnv, make_test_env
from tests.operations.util import audit_events, make_ops, mark, run_tasks

# Token-shaped (built at runtime so the repository secret scan does not flag a fake value).
TOKEN = "A" * 24 + "." + "B" * 6 + "." + "C" * 38


def test_sanitize_masks_secrets_errors_and_long_texts() -> None:
    details = {
        "api_key": "k-123",
        "password": "hunter2",
        "error": f"HTTPException: 401 Unauthorized ({TOKEN})",
        "reason": f"contains {TOKEN}",
        "nested": {"token": "x", "items": ["a", "y" * 400]},
        "count": 3,
        "key": "checkout.panel.title",
    }
    clean = sanitize_details(details)
    assert clean["api_key"] == REDACTED and clean["password"] == REDACTED
    assert clean["error"] == "HTTPException"
    assert TOKEN not in clean["reason"] and "[REDACTED]" in clean["reason"]
    assert clean["nested"]["token"] == REDACTED
    assert len(clean["nested"]["items"][1]) <= 301 and clean["count"] == 3
    assert clean["key"] == "checkout.panel.title"  # content key names are not secrets


async def test_audit_records_and_posts_sanitized_details(ops: TestEnv) -> None:
    await ops.app.audit.record("test.event", actor_id=None, details={"error": f"Boom: {TOKEN}", "secret": "s"})
    [event] = await audit_events(ops, "test.event")
    assert event.details == {"error": "Boom", "secret": REDACTED}
    audit_channel = ops.app.resources.text_channel(ops.guild, "audit_log")  # type: ignore[arg-type]
    assert audit_channel is not None
    assert TOKEN not in audit_channel.messages[-1].text()  # type: ignore[attr-defined]


# -- transcripts ------------------------------------------------------------------------------------------------


async def test_transcript_message_carries_closure_time_final_status_and_reason(tmp_path: Path) -> None:
    tenv = await make_ops(tmp_path, {"checkout": {"transcript_on_close": True}})
    try:
        staffer = tenv.member(name="staffer", staff=True)
        product = await add_product(tenv, "[p1]", price=1000, stock=5)
        placed, _, _ = await place_order(tenv, tenv.member(), [(product.id, 1)])
        for target in (m.PAID, m.FULFILLED):
            await mark(tenv, staffer, placed.id, target)
        await run_tasks(tenv)
        transcripts = tenv.app.resources.text_channel(tenv.guild, "transcripts")  # type: ignore[arg-type]
        assert transcripts is not None and transcripts.messages  # type: ignore[attr-defined]
        ticket = await tickets_service.get_ticket(tenv.app, placed.ticket_id or 0)
        assert ticket is not None and ticket.closed_at is not None
        expected = tenv.app.content.render(
            tk.TRANSCRIPT_DESCRIPTION,
            number=tickets_service.format_number(tenv.app, ticket.number),
            user_mention=f"<@{placed.user_id}>",
            closer_mention=f"<@{staffer.id}>",
            closed_at=f"<t:{ticket.closed_at}:f>",
            final_status=tenv.text(checkout_keys.status_label_key(m.FULFILLED)),
            close_reason=m.FULFILLED,
        )
        assert transcripts.messages[-1].embeds[0].description == expected  # type: ignore[attr-defined]
    finally:
        await tenv.close()


async def test_transcript_view_of_a_support_ticket_has_no_final_status(tenv: TestEnv) -> None:
    spec = ticket_views.transcript_post(
        tenv.app.ui,
        tenv.app.settings.tickets,
        number=7,
        user_id=1,
        closer_id=2,
        closed_at=1_700_000_000,
        final_status=None,
        close_reason="*resolved*",
    )
    expected = tenv.app.content.render(
        tk.TRANSCRIPT_DESCRIPTION,
        number=ticket_views.number_text(tenv.app.settings.tickets, 7),
        user_mention="<@1>",
        closer_mention="<@2>",
        closed_at="<t:1700000000:f>",
        final_status="",
        close_reason="\\*resolved\\*",
    )
    assert spec.embeds[0].description == expected


# -- migration 0003 ------------------------------------------------------------------------------------------------


def test_migration_moves_staff_notes_out_of_the_status_history(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrations = discover()
    apply_migrations(conn, migrations=[mig for mig in migrations if mig.version <= 2])
    conn.execute(
        "INSERT INTO orders(id, guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
        "payment_provider, created_at, updated_at) VALUES (1, 1, 1, 5, 'paid', 'USD', 100, 100, 'manual', 1, 1)"
    )
    rows = [
        (None, "pending_payment", 5, None, 10),
        ("pending_payment", "paid", 6, None, 20),
        ("paid", "paid", 6, "[old note]", 30),
        ("paid", "paid", 6, None, 31),
    ]
    for from_status, to_status, actor, note, at in rows:
        conn.execute(
            "INSERT INTO order_events(order_id, from_status, to_status, actor_id, note, created_at) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (from_status, to_status, actor, note, at),
        )
    conn.commit()
    applied = apply_migrations(conn, migrations=migrations)
    assert 3 in applied
    events = [(r["from_status"], r["to_status"]) for r in conn.execute("SELECT * FROM order_events ORDER BY id")]
    assert events == [(None, "pending_payment"), ("pending_payment", "paid"), ("paid", "paid")]
    notes = [
        (r["kind"], r["actor_id"], r["text"], r["created_at"]) for r in conn.execute("SELECT * FROM order_activity")
    ]
    assert notes == [("note", 6, "[old note]", 30)]
    # The schema refuses impossible handling rows.
    try:
        conn.execute("INSERT INTO order_handling(order_id, claimed_by, updated_at) VALUES (1, 6, 1)")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("a claim without a time must be refused")
    conn.close()


async def test_a_missing_record_text_keeps_checkout_unready(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={staff_keys.BUTTON_CLAIM: ""})
    try:
        status = tenv.app.readiness.statuses["checkout"]
        assert not status.ready and any(p.key == staff_keys.BUTTON_CLAIM for p in status.problems)
    finally:
        await tenv.close()
