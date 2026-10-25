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
