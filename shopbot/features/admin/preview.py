"""Offline preview hook.

Admin output is operator text shown only to administrators (never to customers). The pure builders in ``views`` are
unit-tested against Discord limits; the preview shows the ``/status`` overview, with its running version, so the
owner can see where to check that a deploy reached the server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.admin import service, views
from shopbot.ui.spec import PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_VERSION = "0123abc"  # a synthetic short commit


def preview(app: App) -> list[PreviewState]:
    snapshot = service.StatusSnapshot(
        features=service.feature_lines(app.readiness),
        unknown_keys=(),
        guild_available=True,
        missing_permissions=(),
        message_content_intent=True,
        intents_not_enabled=(),
        members_intent=False,
        transcripts_need_intent=False,
        task_counts={"pending": 2, "running": 0, "failed": 0},
        db_path="/data/bot.db",
        version=SAMPLE_VERSION,
    )
    return [
        PreviewState(
            feature=service.FEATURE,
            name="status",
            note="Admin-only /status overview (sample data): readiness, the running version (the short commit of the "
            "deployment; 'unknown' when the host does not name it), permissions, intents, tasks and the database.",
            spec=views.status_overview(app.ui, app.op, snapshot),
        )
    ]
