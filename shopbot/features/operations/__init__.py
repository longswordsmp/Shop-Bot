"""Operations: the staff side of the server specification that is not tied to one record's controls.

- Staff alerts in ``operations.staff_channel`` (#staff-chat): a new order was submitted, an order was paid and waits
  to be completed (``operations.ping_roles`` are mentioned), and failures staff should know about (a private
  notification that could not be delivered, a scheduled task that failed permanently, a button/command error, a
  customer role that could not be changed). Every failure is also audited (#bot-log).
- The Owner's private alerts (``operations.owner_dm_alerts``): order alerts and critical failures by direct message,
  falling back to the staff channel with a mention of the Owner.
- Staff reminders (``operations.*_minutes``): unclaimed, unpaid and unfulfilled orders, support tickets waiting for a
  staff reply; repeated every ``operations.reminder_repeat_minutes``.
- The Customer role (``checkout.customer_role``): given on ``checkout.customer_role_on``, removed after a full refund.

The live order record in #orders (claim, notes, correction requests, tracking details, rejection reasons, details)
belongs to checkout (``checkout.staff_views``, ``checkout.staff``, ``checkout.handling_repo``).

Enabled whenever checkout or tickets is enabled (there is no separate feature switch). It is not customer-facing; it
reacts to other features through ``shopbot.core.events`` and works with durable, deduplicated scheduler tasks.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from shopbot.core.events import FAILURE, ORDER_STATUS_CHANGED, ORDER_SUBMITTED
from shopbot.features.base import FeatureModule
from shopbot.features.operations import alerts, customer_role, preview, reminders
from shopbot.features.operations import content_keys as keys
from shopbot.features.operations.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.config.settings import Settings
    from shopbot.context import App


def is_enabled(settings: Settings) -> bool:
    return settings.features.checkout or settings.features.tickets


def register(app: App) -> None:
    if app.bot is None:
        raise RuntimeError("register() requires app.bot")
    app.scheduler.register(alerts.TASK_ALERT, alerts.alert_task)
    app.scheduler.register(reminders.TASK_REMINDERS, reminders.reminders_task)
    app.scheduler.register(customer_role.TASK_CUSTOMER_ROLE, customer_role.customer_role_task)
    app.events.subscribe(ORDER_SUBMITTED, partial(alerts.on_order_submitted, app))
    app.events.subscribe(ORDER_STATUS_CHANGED, partial(alerts.on_order_status_changed, app))
    app.events.subscribe(ORDER_STATUS_CHANGED, partial(customer_role.on_order_status_changed, app))
    app.events.subscribe(FAILURE, partial(alerts.on_failure, app))
    app.bot.add_listener("message", partial(reminders.on_message, app))


async def on_ready(app: App) -> None:
    """Restart recovery: make sure the reminder sweep is scheduled (it re-arms itself)."""
    await reminders.schedule_sweep(app)


MODULE = FeatureModule(
    name=keys.FEATURE,
    is_enabled=is_enabled,
    content_keys=keys.content_keys,
    settings_problems=keys.settings_problems,
    operator_text=OPERATOR_TEXT,
    register=register,
    on_ready=on_ready,
    publish=alerts.publish,
    preview=preview.preview,
    customer_facing=False,
)
