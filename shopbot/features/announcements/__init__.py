"""Staff announcements: ``/announce`` opens a form and posts one formatted embed in the announcements channel;
``/announce preset:<id>`` posts a ready-made one (with the claim button of its coupon) after a private preview.

Nothing is posted unsolicited: an announcement exists only because a staff member submitted the form or pressed Post
announcement. See ``service`` for the form's flow, ``presets`` for the presets (and the claim button switching to
"offer ended"), ``repo`` for the stored states, ``views`` for the messages and ``commands`` for the slash command.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from shopbot.core.events import DISCOUNT_CODE_ENDED, DISCOUNT_OFFER_ENDED
from shopbot.features.announcements import content_keys as announcement_keys
from shopbot.features.announcements import presets, service
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
    app.scheduler.register(presets.OFFER_ENDED_TASK, presets.run_offer_ended_task)
    app.events.subscribe(DISCOUNT_CODE_ENDED, partial(presets.on_offer_ended, app))
    app.events.subscribe(DISCOUNT_OFFER_ENDED, partial(presets.on_offer_ended, app, retry_failed=False))


async def on_ready(app: App) -> None:
    await service.recover(app)
    await presets.recover(app)


MODULE = FeatureModule(
    name=announcement_keys.ANNOUNCEMENTS,
    is_enabled=lambda settings: settings.features.announcements,
    content_keys=announcement_keys.content_keys,
    settings_problems=announcement_keys.settings_problems,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=on_ready,
    preview=preview,
)
