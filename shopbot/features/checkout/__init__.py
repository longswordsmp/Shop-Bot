"""Checkout: the purchasing workflow.

Shop panel -> private order channel (a ticket of kind ``order``) -> ONE editable workflow message (choose
products, cart, fulfillment option, order details, review) -> order (stock reserved, credit applied) -> payment
(``payments`` providers; ``manual``: the business's own instructions, staff confirm receipt) -> staff lifecycle
(paid, processing, fulfilled, rejected, cancelled, expired, refunds) through one state machine.

Package layout: ``content_keys`` (customer text + settings checks), ``models`` (records, state machine),
``totals`` (the one pure totals function), ``flow`` (steps and forms), ``repo`` (synchronous database functions),
``service`` (async logic), ``views`` (pure message/form builders), ``handlers`` (buttons/forms), ``staff`` and
``commands`` (``/order``, ``/orders``), ``tasks`` (scheduled tasks, recovery, listeners), ``panel`` (shop panel),
``preview``. The staff side of orders (operations package): ``staff_views`` (the live order record in the staff
channel, staff forms, staff-action notifications), ``staff_keys`` (its content keys), ``handling_repo`` (claims,
notes, correction requests, tracking details, rejection reasons), ``staff_preview``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.checkout import commands, handlers, panel, preview, staff_keys, tasks
from shopbot.features.checkout import content_keys as keys
from shopbot.features.checkout.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def _register(app: App) -> None:
    handlers.register(app)
    tasks.register(app)
    commands.register(app)


MODULE = FeatureModule(
    name=keys.FEATURE,
    is_enabled=lambda settings: settings.features.checkout,
    content_keys=keys.content_keys,
    settings_problems=keys.settings_problems,
    shared_keys=keys.shared_keys,
    content_problems=staff_keys.content_problems,
    depends_on=("catalog",),
    operator_text=OPERATOR_TEXT,
    register=_register,
    on_ready=tasks.on_ready,
    publish=panel.publish,
    panel_prefixes=(keys.PANEL_KEY,),
    preview=preview.preview,
)
