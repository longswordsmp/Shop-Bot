"""Per-process, in-memory helpers of the ticket system (anti-spam cooldowns, per-ticket locks).

Nothing here is business state: losing it on restart is harmless. Correctness (limits, single close,
single deletion) is enforced in the database.
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shopbot.core.ratelimit import Cooldowns

if TYPE_CHECKING:
    from shopbot.context import App

ACTIVITY_THROTTLE_SECONDS = 60


@dataclass
class TicketRuntime:
    cooldowns: Cooldowns
    transcript_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    # Tickets whose channel this process is deleting (the channel-delete event must not re-handle them).
    deleting: set[int] = field(default_factory=set)
    # channel_id -> last time activity was recorded (in-memory throttle before touching the database).
    activity_seen: dict[int, int] = field(default_factory=dict)
    warned_no_content_intent: bool = False

    def transcript_lock(self, ticket_id: int) -> asyncio.Lock:
        lock = self.transcript_locks.get(ticket_id)
        if lock is None:
            lock = asyncio.Lock()
            self.transcript_locks[ticket_id] = lock
        return lock

    def should_touch(self, channel_id: int, now: int) -> bool:
        last = self.activity_seen.get(channel_id)
        if last is not None and now - last < ACTIVITY_THROTTLE_SECONDS:
            return False
        self.activity_seen[channel_id] = now
        if len(self.activity_seen) > 10_000:
            cutoff = now - ACTIVITY_THROTTLE_SECONDS
            for key in [k for k, v in self.activity_seen.items() if v < cutoff]:
                del self.activity_seen[key]
        return True


_RUNTIMES: weakref.WeakKeyDictionary[App, TicketRuntime] = weakref.WeakKeyDictionary()


def runtime(app: App) -> TicketRuntime:
    state = _RUNTIMES.get(app)
    if state is None:
        clock = app.clock
        state = TicketRuntime(cooldowns=Cooldowns(clock=lambda: float(clock.now())))
        _RUNTIMES[app] = state
    return state
