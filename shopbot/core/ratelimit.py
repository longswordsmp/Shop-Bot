"""In-memory anti-spam primitives. These protect against rapid repeated clicks; they are not business
state, so losing them on restart is harmless. Correctness against duplicates is enforced in the database
(state checks and uniqueness constraints), not here."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Hashable


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._clock = clock
        self._events: dict[Hashable, deque[float]] = {}

    def hit(self, key: Hashable) -> float:
        """Record an event. Returns 0 when allowed, else seconds until the next event is allowed."""
        now = self._clock()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.max_events:
            return max(0.0, self.window - (now - events[0]))
        events.append(now)
        if len(self._events) > 10_000:
            self._prune(now)
        return 0.0

    def wait(self, key: Hashable) -> float:
        """Seconds until ``hit`` would allow ``key`` again (0 when it would now), without recording an event."""
        events = self._events.get(key)
        if not events:
            return 0.0
        now = self._clock()
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) < self.max_events:
            return 0.0
        return max(0.0, self.window - (now - events[0]))

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._events.items() if not v or now - v[-1] >= self.window]:
            del self._events[key]


class Cooldowns:
    """Per-key minimum interval between successful actions (e.g. ticket creation)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[Hashable, float] = {}

    def remaining(self, key: Hashable, seconds: float) -> float:
        last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, seconds - (self._clock() - last))

    def mark(self, key: Hashable) -> None:
        self._last[key] = self._clock()
        if len(self._last) > 10_000:
            cutoff = self._clock() - 86_400
            for k in [k for k, v in self._last.items() if v < cutoff]:
                del self._last[k]


class InFlight:
    """Tracks operations currently being processed so a double click is not processed twice."""

    def __init__(self) -> None:
        self._keys: set[Hashable] = set()

    def try_acquire(self, key: Hashable) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def release(self, key: Hashable) -> None:
        self._keys.discard(key)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._keys
