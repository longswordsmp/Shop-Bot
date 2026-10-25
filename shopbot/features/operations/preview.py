"""Preview: every operations message (order alerts, reminders, failure alerts, the Owner's private alerts), built by
the runtime view functions from obviously synthetic sample values (``[sample ...]``). States whose text is not
configured yet are skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.operations import content_keys as k
from shopbot.features.operations import repo, views
from shopbot.ui.spec import MessageSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_USER_ID = 100_000_000_000_000_001
SAMPLE_CHANNEL_ID = 100_000_000_000_000_003
SAMPLE_ROLE_ID = 100_000_000_000_000_005
SAMPLE_TIME = 1_700_000_000
SAMPLE_URL = "https://discord.com/channels/0/100000000000000004/100000000000000006"

_ORDER_VALUES: dict[str, str | int] = {
    "order_number": "1001",
    "customer_mention": f"<@{SAMPLE_USER_ID}>",
    "total": "[sample total]",
    "status": "[sample status]",
    "order_channel": f"<#{SAMPLE_CHANNEL_ID}>",
}
_FAILURE_VALUES: dict[str, dict[str, str | int]] = {
    k.FAILURE_NOTIFICATION: {"user_mention": f"<@{SAMPLE_USER_ID}>", "notification": "[sample notification]"},
    k.FAILURE_TASK: {"task": "[sample task]", "task_id": 42, "attempts": 5},
    k.FAILURE_ERROR: {"source": "[sample control]", "error_id": "[sample reference]", "error": "[sample error]"},
    k.FAILURE_ROLE: {"user_mention": f"<@{SAMPLE_USER_ID}>", "role_mention": f"<@&{SAMPLE_ROLE_ID}>"},
}


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    states: list[PreviewState] = []
    ping = (SAMPLE_ROLE_ID,)

    def add(name: str, note: str, build: Callable[[], MessageSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=k.FEATURE, name=name, note=note, spec=spec))

    for alert in k.ORDER_ALERTS:
        add(
            f"alert_{alert}",
            f"Staff alert in operations.staff_channel: {alert.replace('_', ' ')} (mentions operations.ping_roles).",
            partial(views.order_alert, ui, alert, _ORDER_VALUES, ping_role_ids=ping, record_url=SAMPLE_URL),
        )
        add(
            f"owner_{alert}",
            f"The Owner's private alert: {alert.replace('_', ' ')} (direct message; posted in the staff channel with "
            "a mention of the Owner when the direct message fails).",
            partial(views.order_alert, ui, alert, _ORDER_VALUES, owner=True, record_url=SAMPLE_URL),
        )
    for kind in repo.REMINDER_KINDS:
        if kind == repo.UNANSWERED:
            values: dict[str, str | int] = {
                "ticket_number": "[sample ticket]",
                "customer_mention": f"<@{SAMPLE_USER_ID}>",
                "ticket_channel": f"<#{SAMPLE_CHANNEL_ID}>",
            }
        else:
            values = dict(_ORDER_VALUES)
        values |= {"waiting_since": f"<t:{SAMPLE_TIME}:R>", "minutes": 60}
        add(
            f"reminder_{kind}",
            f"Staff reminder in operations.staff_channel: {kind} record.",
            partial(views.reminder, ui, kind, values, ping_role_ids=ping, link_url=SAMPLE_URL),
        )
    for failure in k.FAILURES:
        add(
            f"failure_{failure}",
            f"Failure alert in operations.staff_channel: {failure}.",
            partial(views.failure_alert, ui, failure, _FAILURE_VALUES[failure], ping_role_ids=ping),
        )
        if failure in k.CRITICAL_FAILURES:
            add(
                f"owner_failure_{failure}",
                f"The Owner's private alert about a critical failure: {failure}.",
                partial(views.failure_alert, ui, failure, _FAILURE_VALUES[failure], owner=True),
            )
    return states
