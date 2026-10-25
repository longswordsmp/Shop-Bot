"""Server structure (/setup): roles, categories, channels, permission overwrites, and panel publishing."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.server_keys import SERVER, server_keys, server_problems
from shopbot.features.setup.commands import build_setup_command
from shopbot.features.setup.listeners import on_channel_delete, on_ready
from shopbot.features.setup.preview import preview
from shopbot.features.setup.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    bot = app.bot
    if bot is None:
        raise RuntimeError("register() requires app.bot")
    bot.tree.add_command(build_setup_command(), guild=app.guild_object)
    bot.add_listener("guild_channel_delete", partial(on_channel_delete, app))


MODULE = FeatureModule(
    name=SERVER,
    is_enabled=lambda settings: True,
    content_keys=server_keys,
    settings_problems=server_problems,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=on_ready,
    preview=preview,
    customer_facing=False,
)
