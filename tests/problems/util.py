"""Helpers for Report a Problem tests: environments with the feature enabled, orders, and the customer/staff flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import state
from shopbot.db.repos import tasks as task_repo
from shopbot.features import all_modules
from shopbot.features.problems import repo, views
from shopbot.features.problems.models import Report
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import Ticket
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv, deep_merge, make_settings, make_test_env, provision_channels

DETAILS_FORM = [{"id": "details", "style": "paragraph", "required": True, "min_length": 3, "max_length": 1000}]

# The test base layout has no #purchase category: report tickets go to the staff-only 'tickets' category.
PROBLEM_SETTINGS: dict[str, Any] = {
    "bot": {"rate_limit_per_10s": 100},
    "features": {"problems": True},
    "problems": {
        "discord_category": "tickets",
        "eligible_statuses": ["paid", "processing", "fulfilled"],
        "allow_without_order": True,
        "max_open_per_user": 1,
        "types": [{"id": "general", "form": DETAILS_FORM}],
    },
}

# Several issue types: one with a two-field form, one without a form.
MULTI_TYPES: dict[str, Any] = {
    "problems": {
        "types": [
            {"id": "general", "form": DETAILS_FORM},
            {
                "id": "billing",
                "form": [
                    {"id": "what", "style": "short", "required": True, "max_length": 100},
                    {"id": "extra", "style": "paragraph", "required": False, "max_length": 4000},
                ],
            },
            {"id": "other"},
        ]
    }
}


async def make_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    settings = make_settings(deep_merge(PROBLEM_SETTINGS, overrides or {}))
    tenv = await make_test_env(tmp_path, settings=settings, **kwargs)
    await provision_channels(tenv)
    return tenv


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return found


def render(tenv: TestEnv, key: str, **values: str | int) -> str:
    text = tenv.app.content.render(key, **values)
    assert text is not None, key
    return text


async def add_order(tenv: TestEnv, user: FakeMember, *, status: str = "paid", total: int = 1500) -> int:
    """A minimal order row of ``user`` in ``status`` (orders are created by checkout in production)."""
    now = tenv.clock.now()

    def insert(conn: Any) -> int:
        number = state.next_counter(conn, GUILD_ID, "order")
        cursor = conn.execute(
            "INSERT INTO orders(guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
            "payment_provider, created_at, updated_at) VALUES (?, ?, ?, ?, 'USD', ?, ?, 'manual', ?, ?)",
            (GUILD_ID, number, user.id, status, total, total, now, now),
        )
        return int(cursor.lastrowid or 0)

    return await tenv.app.db.transaction(insert)


async def order_number(tenv: TestEnv, order_id: int) -> str:
    row = await tenv.app.db.run(
        lambda conn: conn.execute("SELECT number FROM orders WHERE id = ?", (order_id,)).fetchone()
    )
    return str(row["number"])


async def order_notes(tenv: TestEnv, order_id: int) -> list[str]:
    rows = await tenv.app.db.run(
        lambda conn: conn.execute(
            "SELECT note FROM order_events WHERE order_id = ? AND note IS NOT NULL ORDER BY id", (order_id,)
        ).fetchall()
    )
    return [str(r["note"]) for r in rows]


async def reports(tenv: TestEnv) -> list[Report]:
    rows = await tenv.app.db.run(lambda conn: conn.execute("SELECT id FROM problem_reports ORDER BY id").fetchall())
    found = [await tenv.app.db.run(repo.get, int(r["id"])) for r in rows]
    return [r for r in found if r is not None]


async def report_of(tenv: TestEnv, report_id: int) -> Report:
    found = await tenv.app.db.run(repo.get, report_id)
    assert found is not None
    return found


async def ticket_of(tenv: TestEnv, report: Report) -> Ticket:
    ticket = await tickets_service.get_ticket(tenv.app, report.ticket_id)
    assert ticket is not None
    return ticket


async def ticket_channel(tenv: TestEnv, report: Report) -> FakeTextChannel:
    ticket = await ticket_of(tenv, report)
    assert ticket.channel_id is not None
    found = tenv.guild.get_channel(ticket.channel_id)
    assert isinstance(found, FakeTextChannel)
    return found


def message_by_id(ch: FakeTextChannel, message_id: int | None) -> FakeMessage:
    for message in ch.messages:
        if message.id == message_id:
            return message
    raise AssertionError(f"message {message_id} not found")


async def panel_message(tenv: TestEnv, report: Report) -> FakeMessage:
    return message_by_id(await ticket_channel(tenv, report), report.panel_message_id)


def nonce_of(custom_id: str) -> str:
    return custom_id.split(":")[3]


def private_step(interaction: FakeInteraction) -> FakeMessage:
    """The private choice step the start button answered with."""
    message = interaction.last_sent
    assert message is not None and message.flags.ephemeral, "no private step"
    return message


async def start(tenv: TestEnv, user: FakeMember, *, app: App | None = None) -> FakeInteraction:
    return await click(app or tenv.app, user=user, custom_id=views.start_id())


async def pick(
    tenv: TestEnv, user: FakeMember, step: FakeMessage, prefix: str, value: str, *, app: App | None = None
) -> FakeInteraction:
    """Pick ``value`` in the menu of ``step`` whose custom id starts with ``sb:pr:<prefix>``."""
    custom_id = next(cid for cid in step.custom_ids() if cid.startswith(f"sb:pr:{prefix}:"))
    return await click(app or tenv.app, user=user, custom_id=custom_id, message=step, values=[value])


def continue_id_of(step: FakeMessage) -> str:
    return next(cid for cid in step.custom_ids() if cid.startswith("sb:pr:go:"))


async def submit_form(
    tenv: TestEnv,
    user: FakeMember,
    custom_id: str,
    fields: dict[str, str] | None = None,
    *,
    message: FakeMessage | None = None,
    app: App | None = None,
) -> FakeInteraction:
    return await submit_modal(
        app or tenv.app,
        user=user,
        custom_id=custom_id,
        fields=fields if fields is not None else {"details": "it arrived broken"},
        message=message,
    )


async def report_without_order(
    tenv: TestEnv, user: FakeMember, details: str = "it arrived broken"
) -> tuple[Report, FakeInteraction]:
    """Default settings, no eligible order: the button opens the form right away; submit it."""
    opened = await start(tenv, user)
    modal = opened.response.modal
    assert modal is not None, opened.last_sent and opened.last_sent.text()
    submitted = await submit_form(tenv, user, str(modal.custom_id), {"details": details}, message=opened.message)
    found = await reports(tenv)
    assert found, submitted.last_sent and submitted.last_sent.text()
    return found[-1], submitted


async def report_about_order(
    tenv: TestEnv, user: FakeMember, order_id: int, details: str = "it arrived broken"
) -> tuple[Report, FakeInteraction]:
    """Choose ``order_id`` in the private step, press Continue, submit the form."""
    step = private_step(await start(tenv, user))
    await pick(tenv, user, step, "order", str(order_id))
    proceed = await click(tenv.app, user=user, custom_id=continue_id_of(step), message=step)
    modal = proceed.response.modal
    assert modal is not None, proceed.last_sent and proceed.last_sent.text()
    submitted = await submit_form(tenv, user, str(modal.custom_id), {"details": details}, message=step)
    found = await reports(tenv)
    assert found, submitted.last_sent and submitted.last_sent.text()
    return found[-1], submitted


async def decide(
    tenv: TestEnv,
    staff: FakeMember,
    report: Report,
    action: str,
    reason: str = "",
    *,
    app: App | None = None,
) -> FakeInteraction:
    """Press Resolve/Reject on the panel and submit its staff form."""
    target = app or tenv.app
    panel = await panel_message(tenv, report)
    button_id = views.resolve_id(report.id) if action == "resolve" else views.reject_id(report.id)
    pressed = await click(target, user=staff, custom_id=button_id, message=panel)
    if pressed.response.modal is None:
        return pressed
    form_id = views.resolve_form_id(report.id) if action == "resolve" else views.reject_form_id(report.id)
    return await submit_modal(target, user=staff, custom_id=form_id, fields={views.REASON_INPUT: reason}, message=panel)


async def audit_actions(tenv: TestEnv, prefix: str = "problems.") -> list[str]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e.action for e in reversed(events) if e.action.startswith(prefix)]


async def task(tenv: TestEnv, dedupe_key: str) -> Any:
    return await tenv.app.db.run(task_repo.get_by_dedupe, dedupe_key)


async def restarted_app(tenv: TestEnv) -> App:
    """A fresh App on the same database and guild, as after a process restart (resources loaded as at startup)."""
    old = tenv.app
    app = App(
        env=old.env,
        settings=old.settings,
        content_values=dict(tenv.content),
        overrides={},
        db=old.db,
        clock=tenv.clock,
        modules=all_modules(),
        config_dir=old.config_dir,
    )
    FakeBot(app, tenv.guild)
    for module in app.modules:
        if module.register is not None:
            module.register(app)
    app.scheduler.bind(app)
    await app.resources.refresh()
    return app


def texts(interaction: FakeInteraction) -> str:
    return "\n".join(message.text() for message in interaction.sent)


def last_description(interaction: FakeInteraction) -> str | None:
    message = interaction.last_sent
    if message is None or not message.embeds:
        return None
    return message.embeds[0].description
