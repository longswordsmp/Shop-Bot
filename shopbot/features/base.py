"""The contract every feature package implements.

A feature contributes:
- ``content_keys(settings)``: every customer-facing string it can display (the content checklist);
- ``settings_problems(settings)``: semantic configuration problems that keep it disabled;
- ``shared_keys(settings)``: required keys owned by another feature (e.g. ``common.button.back``);
- ``operator_text``: default wording for admin-only command replies (never shown to customers);
- ``register(app)``: component routes, scheduled-task handlers, slash commands, event listeners;
- ``on_ready(app)``: restart recovery after the gateway connection is ready;
- ``publish(app)``: create or update this feature's panels (called by /setup only when the feature is
  ready); returns operator report lines. Must be idempotent: edit existing messages, never duplicate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import Settings

if TYPE_CHECKING:
    from shopbot.context import App


def _no_keys(settings: Settings) -> list[ContentKey]:
    return []


def _no_problems(settings: Settings) -> list[str]:
    return []


def _no_shared(settings: Settings) -> list[str]:
    return []


@dataclass(frozen=True)
class FeatureModule:
    name: str
    is_enabled: Callable[[Settings], bool]
    content_keys: Callable[[Settings], list[ContentKey]] = _no_keys
    settings_problems: Callable[[Settings], list[str]] = _no_problems
    shared_keys: Callable[[Settings], list[str]] = _no_shared
    depends_on: tuple[str, ...] = ()
    operator_text: Mapping[str, str] = field(default_factory=dict)
    register: Callable[[App], None] | None = None
    on_ready: Callable[[App], Awaitable[None]] | None = None
    publish: Callable[[App], Awaitable[list[str]]] | None = None
    # Whether customers can see anything from this feature (requires the core error messages).
    customer_facing: bool = True
