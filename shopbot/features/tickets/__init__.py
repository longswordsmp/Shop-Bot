"""Support tickets: panel, private ticket channels, staff controls, transcripts, and automation.

The service layer (``shopbot.features.tickets.service``) is also the private-channel API used by the
checkout feature for ORDER channels (``kind='order'``); see its module docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.tickets import commands, events, handlers, panel, preview, tasks
from shopbot.features.tickets import content_keys as keys
from shopbot.features.tickets.operator_text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def _register(app: App) -> None:
    handlers.register(app)
    tasks.register(app)
    commands.register(app)
    events.register(app)


MODULE = FeatureModule(
    name=keys.FEATURE,
    is_enabled=lambda settings: settings.features.tickets,
    content_keys=keys.content_keys,
    settings_problems=keys.settings_problems,
    shared_keys=keys.shared_keys,
    operator_text=OPERATOR_TEXT,
    register=_register,
    on_ready=events.on_ready,
    publish=panel.publish,
    preview=preview.preview,
)
