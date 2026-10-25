"""In-process state of the admin tools (per ``App``).

``/content set`` opens a form whose custom_id carries only a short random token; the content key it edits
(which may be longer than custom_id arguments allow) is kept here. A restart loses pending forms on
purpose: the submit handler then asks the administrator to run the command again.
"""

from __future__ import annotations

import asyncio
import secrets
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shopbot.context import App

PENDING_TTL_SECONDS = 3600
PENDING_MAX = 200


@dataclass(frozen=True)
class PendingEdit:
    key: str
    user_id: int
    created_at: int


class PendingEdits:
    """Short-lived tokens for open ``/content set`` forms, bound to the administrator who opened them."""

    def __init__(self, *, ttl_seconds: int = PENDING_TTL_SECONDS, max_entries: int = PENDING_MAX) -> None:
        self._items: OrderedDict[str, PendingEdit] = OrderedDict()
        self._ttl = ttl_seconds
        self._max = max_entries

    def __len__(self) -> int:
        return len(self._items)

    def _expire(self, now: int) -> None:
        for token in [t for t, p in self._items.items() if now - p.created_at > self._ttl]:
            del self._items[token]

    def create(self, key: str, user_id: int, now: int) -> str:
        self._expire(now)
        while len(self._items) >= self._max:
            self._items.popitem(last=False)
        token = secrets.token_urlsafe(9)  # 12 characters from [A-Za-z0-9_-]: valid custom_id argument
        while token in self._items:
            token = secrets.token_urlsafe(9)
        self._items[token] = PendingEdit(key=key, user_id=user_id, created_at=now)
        return token

    def take(self, token: str, user_id: int, now: int) -> PendingEdit | None:
        """Consume a token. Returns None if unknown, expired, or opened by someone else (kept for them)."""
        self._expire(now)
        pending = self._items.get(token)
        if pending is None or pending.user_id != user_id:
            return None
        del self._items[token]
        return pending


@dataclass
class AdminState:
    pending: PendingEdits = field(default_factory=PendingEdits)
    backup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_STATES: weakref.WeakKeyDictionary[App, AdminState] = weakref.WeakKeyDictionary()


def state_for(app: App) -> AdminState:
    state = _STATES.get(app)
    if state is None:
        state = AdminState()
        _STATES[app] = state
    return state
