"""#new-arrivals: the persistent opt-in panel for the notification role (Restock Alerts) and automatic product updates.

- The panel (published by /setup) has one button that adds the notification role to the clicking member, or removes
  it when they already have it, with a private confirmation.
- Catalog changes (``shopbot.core.events.PRODUCT_CHANGED``) that add a product or make a sold-out product orderable
  again schedule a durable task that posts one update per qualifying change, mentioning the role when configured.

See ``service`` for the rules, ``handlers`` for the button, ``tasks`` for the scheduled task, ``repo`` for the
announcement records that make a second post of the same update impossible.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from shopbot.core.events import PRODUCT_CHANGED
from shopbot.features.base import FeatureModule
from shopbot.features.new_arrivals import content_keys as na_keys
from shopbot.features.new_arrivals import service, tasks
from shopbot.features.new_arrivals.handlers import register_handlers
from shopbot.features.new_arrivals.preview import preview
from shopbot.features.new_arrivals.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    register_handlers(app)
    app.scheduler.register(service.ANNOUNCE_TASK, tasks.run_announce_task)
    app.events.subscribe(PRODUCT_CHANGED, partial(service.on_product_changed, app))


MODULE = FeatureModule(
    name=na_keys.NEW_ARRIVALS,
    is_enabled=lambda settings: settings.features.new_arrivals,
    content_keys=na_keys.content_keys,
    settings_problems=na_keys.settings_problems,
    shared_keys=na_keys.shared_keys,
    depends_on=("catalog",),
    operator_text=OPERATOR_TEXT,
    register=register,
    publish=service.publish,
    panel_prefixes=(service.PANEL_PREFIX,),
    preview=preview,
)
