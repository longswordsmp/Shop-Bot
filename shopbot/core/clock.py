"""Injectable clock so time-dependent logic (expiry, scheduling) is testable."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> int:
        """Current UNIX time in whole seconds (UTC)."""
        ...


class SystemClock:
    def now(self) -> int:
        return int(time.time())


class FakeClock:
    """Manually advanced clock for tests and the offline preview."""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self._now = start

    def now(self) -> int:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += seconds


def to_datetime(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)
