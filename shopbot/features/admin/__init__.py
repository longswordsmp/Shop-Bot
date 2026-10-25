"""Admin tools: /status, /content, /reload, /backup, /audit, /tasks and daily maintenance.

Everything here is administrator-only operator output (``customer_facing=False``); the feature has no
customer content keys and is always enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.admin.preview import preview
from shopbot.features.admin.service import FEATURE, ensure_maintenance_scheduled
from shopbot.features.admin.text import OPERATOR_TEXT
from shopbot.features.base import FeatureModule

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    from shopbot.features.admin.commands import register_commands
    from shopbot.features.admin.handlers import register_handlers
    from shopbot.features.admin.state import state_for
    from shopbot.features.admin.tasks import register_tasks

    state_for(app)
    register_handlers(app)
    register_tasks(app)
    register_commands(app)


async def on_ready(app: App) -> None:
    await ensure_maintenance_scheduled(app)


MODULE = FeatureModule(
    name=FEATURE,
    is_enabled=lambda settings: True,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=on_ready,
    preview=preview,
    customer_facing=False,
)
