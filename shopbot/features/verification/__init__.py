"""Verification: the #welcome panel (welcome card, server rules, "I agree, enter the server" button) that grants
``permissions.member_role`` and so unlocks the ``members_*`` channels.

See ``handlers`` for the button flow, ``service`` for publishing and role diagnostics, ``repo`` for the acceptance
history, ``content_keys`` for the text and the settings checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.verification import content_keys as keys
from shopbot.features.verification import preview as preview_module
from shopbot.features.verification.handlers import register_handlers
from shopbot.features.verification.service import PANEL_PREFIX, publish
from shopbot.features.verification.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    register_handlers(app)


MODULE = FeatureModule(
    name=keys.VERIFICATION,
    is_enabled=lambda settings: settings.features.verification,
    content_keys=keys.content_keys,
    settings_problems=keys.settings_problems,
    operator_text=OPERATOR_TEXT,
    register=register,
    publish=publish,
    panel_prefixes=(PANEL_PREFIX,),
    preview=preview_module.preview,
)
