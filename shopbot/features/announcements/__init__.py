"""Staff announcements: ``/announce`` opens a form and posts one formatted embed in the announcements channel.

Nothing is posted unsolicited: an announcement exists only because a staff member submitted the form. See
``service`` for the flow, ``repo`` for the stored form states, ``views`` for the messages and ``commands`` for the
slash command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.announcements import content_keys as announcement_keys
from shopbot.features.announcements import service
from shopbot.features.announcements.commands import build_announce_command
from shopbot.features.announcements.handlers import register_handlers
from shopbot.features.announcements.preview import preview
from shopbot.features.announcements.text import OPERATOR_TEXT
from shopbot.features.base import FeatureModule

if TYPE_CHECKING:
    from shopbot.context import App

__all__ = ["MODULE"]


def register(app: App) -> None:
    bot = app.bot
    if bot is None:
        raise RuntimeError("register() requires app.bot")
    register_handlers(app)
    bot.tree.add_command(build_announce_command(), guild=app.guild_object)


MODULE = FeatureModule(
    name=announcement_keys.ANNOUNCEMENTS,
    is_enabled=lambda settings: settings.features.announcements,
    content_keys=announcement_keys.content_keys,
    settings_problems=announcement_keys.settings_problems,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=service.recover,
    preview=preview,
)
