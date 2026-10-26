"""In-process events between features, so one feature can react to another without importing it.

Example: the catalog publishes ``PRODUCT_CHANGED`` after a committed product change; the new-arrivals feature
subscribes in its ``register`` hook and schedules a durable announcement task. Handlers run in subscription
order after the publisher's database change has committed; a failing handler is logged and never breaks the
publisher or the other handlers. Handlers needing durability must schedule a scheduler task themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None]]

# catalog: a staff change to a product committed. Payload: action (add|edit|price|stock|image|toggle|archive|sort),
# before (Product | None for "add"), after (Product), actor_id (int).
PRODUCT_CHANGED = "catalog.product_changed"

# checkout: published from the order's durable follow-up steps (so they are published again when a follow-up is
# retried after a crash: handlers must be idempotent, e.g. schedule a task with a dedupe key).
# ORDER_SUBMITTED payload: order_id (int). ORDER_STATUS_CHANGED payload: order_id (int), status (str, the new status),
# actor_id (int | None; None = automatic).
ORDER_SUBMITTED = "checkout.order_submitted"
ORDER_STATUS_CHANGED = "checkout.order_status_changed"
# checkout: a customer chose a payment method our team handles (``payments.methods`` kind "staff"); the staff are
# alerted to send the payment details. Payload: order_id (int), method (str, the method's id). Published once per
# order and method.
PAYMENT_METHOD_REQUESTED = "checkout.payment_method_requested"

# A failure staff should hear about (the operations feature posts a #staff-chat alert). Published by the router
# (interaction handler errors), the command tree (command errors), the scheduler (a task failed permanently), the
# notifier (a customer notification could not be delivered by DM nor in the fallback channel, or a channel post
# failed) and features. The payload never carries private data: kind ("interaction" | "command" | "task" |
# "notification" | "role") plus identifiers - source (route/command/task/notification kind), error_id, error
# (exception type name), task_id, attempts, user_id, channel_id.
FAILURE = "bot.failure"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, name: str, handler: Handler) -> None:
        handlers = self._handlers.setdefault(name, [])
        if handler not in handlers:
            handlers.append(handler)

    def handlers(self, name: str) -> list[Handler]:
        return list(self._handlers.get(name, ()))

    async def publish(self, name: str, /, **payload: Any) -> None:
        for handler in self.handlers(name):
            try:
                await handler(**payload)
            except Exception:
                log.exception("event handler failed for %s", name)
