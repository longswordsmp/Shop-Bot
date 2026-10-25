"""Every UI state of the ticket system, rendered by the same view functions used at runtime.

Sample data is obviously synthetic (``[sample user]``, ``[sample answer 1]``, fixed sample ids). States
whose required content is not configured are left out (the content checklist reports those keys).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.config.settings import TicketCategory
from shopbot.features.tickets import content_keys as k
from shopbot.features.tickets import views
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_USER_ID = 100_000_000_000_000_001
SAMPLE_STAFF_ID = 100_000_000_000_000_002
SAMPLE_CHANNEL_ID = 100_000_000_000_000_003
SAMPLE_TICKET_ID = 1
SAMPLE_NUMBER = 1
SAMPLE_USER_NAME = "[sample user]"
SAMPLE_REASON = "[sample close reason]"


def sample_answers(category: TicketCategory) -> dict[str, str]:
    return {field.id: f"[sample answer {index}]" for index, field in enumerate(category.form, start=1)}


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    t = app.settings.tickets
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=k.FEATURE, name=name, note=note, spec=spec))

    add("panel.buttons", "Ticket panel with one button per category.", lambda: views.panel(ui, t, style="buttons"))
    add("panel.select", "Ticket panel with a category selection menu.", lambda: views.panel(ui, t, style="select"))

    for category in t.categories:
        _category_states(app, category, add)

    first = t.categories[0] if t.categories else None
    if first is not None:
        add(
            "controls.claimed",
            "Welcome message after a staff member claimed the ticket (unclaim button).",
            lambda: views.welcome_messages(
                ui,
                t,
                first,
                ticket_id=SAMPLE_TICKET_ID,
                number=SAMPLE_NUMBER,
                user_id=SAMPLE_USER_ID,
                user_name=SAMPLE_USER_NAME,
                answers=sample_answers(first),
                assigned=True,
            )[0],
        )
        add(
            "staff_alert",
            "New-ticket alert in the staff alert channel.",
            lambda: views.staff_alert(
                ui,
                t,
                first,
                number=SAMPLE_NUMBER,
                user_id=SAMPLE_USER_ID,
                channel_id=SAMPLE_CHANNEL_ID,
                ping_role_ids=(),
            ),
        )

    add("close_confirm", "Private prompt to confirm closing.", lambda: views.close_confirm(ui, SAMPLE_TICKET_ID))
    add(
        "close_confirm.answered",
        "Close prompt after it was answered (buttons removed).",
        lambda: views.resolved_prompt(views.close_confirm(ui, SAMPLE_TICKET_ID)),
    )
    add(
        "delete_confirm",
        "Private prompt to confirm deletion (staff).",
        lambda: views.delete_confirm(ui, SAMPLE_TICKET_ID),
    )

    def closed(transcripts: bool) -> Callable[[], MessageSpec]:
        return lambda: views.closed_message(
            ui, t, ticket_id=SAMPLE_TICKET_ID, number=SAMPLE_NUMBER, closer_id=SAMPLE_STAFF_ID, transcripts=transcripts
        )

    add("closed", "Ticket-closed message with its buttons.", closed(True))
    add("closed.no_transcripts", "Ticket-closed message when transcripts are disabled.", closed(False))
    add(
        "closed.with_reason",
        "Ticket-closed message with a staff-provided reason.",
        lambda: views.closed_message(
            ui,
            t,
            ticket_id=SAMPLE_TICKET_ID,
            number=SAMPLE_NUMBER,
            closer_id=SAMPLE_STAFF_ID,
            reason=SAMPLE_REASON,
            transcripts=bool(t.transcript_channel),
        ),
    )
    add(
        "auto_closed",
        "Ticket-closed message after an automatic close for inactivity.",
        lambda: views.closed_message(
            ui,
            t,
            ticket_id=SAMPLE_TICKET_ID,
            number=SAMPLE_NUMBER,
            closer_id=None,
            transcripts=bool(t.transcript_channel),
        ),
    )
    add("claimed", "Status message: ticket claimed.", lambda: views.claimed(ui, SAMPLE_STAFF_ID))
    add("unclaimed", "Status message: ticket unclaimed.", lambda: views.unclaimed(ui))
    add("reopened", "Status message: ticket reopened.", lambda: views.reopened(ui, SAMPLE_STAFF_ID))
    add(
        "deletion_scheduled",
        "Status message: the channel will be deleted automatically.",
        lambda: views.deletion_scheduled(ui, t.delete_after_close_minutes or 60),
    )
    add(
        "auto_close_warning",
        "Warning before an automatic close (pings the customer).",
        lambda: views.auto_close_warning(ui, minutes=t.auto_close_warning_minutes or 30, user_id=SAMPLE_USER_ID),
    )
    add("user_added", "Status message: member added.", lambda: views.user_added(ui, SAMPLE_USER_ID))
    add("user_removed", "Status message: member removed.", lambda: views.user_removed(ui, SAMPLE_USER_ID))
    add(
        "transcript",
        "Transcript message in the transcript channel (the HTML file is attached).",
        lambda: views.transcript_post(ui, t, number=SAMPLE_NUMBER, user_id=SAMPLE_USER_ID, closer_id=SAMPLE_STAFF_ID),
    )
    add(
        "transcript_dm",
        "Direct message with the transcript file.",
        lambda: views.transcript_dm(ui, t, number=SAMPLE_NUMBER),
    )
    add(
        "reply.created",
        "Private reply after a ticket was created.",
        lambda: views.created_reply(ui, t, channel_id=SAMPLE_CHANNEL_ID, number=SAMPLE_NUMBER),
    )
    add(
        "reply.limit_reached",
        "Private error: too many open tickets.",
        lambda: views.error_reply(ui, k.LIMIT_REACHED, limit=t.max_open_per_user),
    )
    add(
        "reply.not_open", "Private error: action not possible in this state.", lambda: views.error_reply(ui, k.NOT_OPEN)
    )
    return states


def _category_states(
    app: App, category: TicketCategory, add: Callable[[str, str, Callable[[], MessageSpec | ModalSpec]], None]
) -> None:
    ui = app.ui
    t = app.settings.tickets
    if category.form:
        add(
            f"form.{category.id}",
            f"Form before a '{category.id}' ticket is created.",
            lambda: views.form_modal(ui, category),
        )

    def welcome(answers: dict[str, str]) -> list[MessageSpec]:
        return views.welcome_messages(
            ui,
            t,
            category,
            ticket_id=SAMPLE_TICKET_ID,
            number=SAMPLE_NUMBER,
            user_id=SAMPLE_USER_ID,
            user_name=SAMPLE_USER_NAME,
            answers=answers,
        )

    add(
        f"welcome.{category.id}",
        f"Welcome message of a new '{category.id}' ticket (with form answers when the category has a form).",
        lambda: welcome(sample_answers(category))[0],
    )
    if not category.form:
        return
    # Longest possible answers: extra messages carry the answers that do not fit into the first one.
    long_answers = {field.id: _long_sample(field.max_length) for field in category.form}
    try:
        messages = welcome(long_answers)
    except MissingContent:
        return
    for index, spec in enumerate(messages, start=1):
        add(
            f"welcome.{category.id}.max_answers.{index}",
            f"Welcome message {index}/{len(messages)} with maximum-length form answers.",
            _constant(spec),
        )


def _constant(spec: MessageSpec) -> Callable[[], MessageSpec]:
    return lambda: spec


def _long_sample(length: int) -> str:
    lines: list[str] = []
    total = 0
    n = 1
    while total < length:
        line = f"[sample answer line {n}]"
        lines.append(line)
        total += len(line) + 1
        n += 1
    return "\n".join(lines)[:length]
