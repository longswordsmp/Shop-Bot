"""My orders: the customer command ``/myorders``.

A private reply listing the member's own orders (newest first, paged with buttons), each with its number, current
stage, total and timeline, a link to its ticket while it is open and the tracking details staff recorded. Enabled by
``features.my_orders``; depends on checkout (orders, stage names, timeline format).

Package layout: ``content_keys``, ``repo`` (read-only queries bound to the member), ``views`` (pure builders),
``service``, ``handlers`` (paging buttons), ``commands`` (/myorders), ``preview``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.my_orders import commands, handlers, preview
from shopbot.features.my_orders import content_keys as keys

if TYPE_CHECKING:
    from shopbot.context import App


def _register(app: App) -> None:
    handlers.register(app)
    commands.register(app)


MODULE = FeatureModule(
    name=keys.FEATURE,
    is_enabled=lambda settings: settings.features.my_orders,
    content_keys=keys.content_keys,
    shared_keys=keys.shared_keys,
    depends_on=("checkout",),
    register=_register,
    preview=preview.preview,
)
