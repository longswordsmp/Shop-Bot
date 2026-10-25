"""The contract every feature package implements.

A feature contributes:
- ``content_keys(settings)``: every customer-facing string it can display (the content checklist);
- ``settings_problems(settings)``: semantic configuration problems that keep it disabled;
- ``shared_keys(settings)``: required keys owned by another feature (e.g. ``common.button.back``);
- ``content_problems(settings, content)``: cross-key content rules (e.g. "title or description");
- ``operator_text``: default wording for admin-only command replies (never shown to customers);
- ``register(app)``: component routes, scheduled-task handlers, slash commands, event listeners;
- ``on_ready(app)``: restart recovery after the gateway connection is ready;
- ``publish(app)``: create or update this feature's panels (called by /setup only when the feature is
  ready); returns operator report lines. Must be idempotent: edit existing messages, never duplicate.
- ``preview(app)``: every UI state this feature can show, built by the same pure view functions used
  at runtime, for the offline limits checker and HTML preview. Sample *data* (not wording) must be
  obviously synthetic, e.g. ``[sample product 1]``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shopbot.config.content_keys import ContentKey
from shopbot.config.settings import Settings

if TYPE_CHECKING:
    from shopbot.config.content import ContentStore
    from shopbot.context import App
    from shopbot.ui.spec import PreviewState


def _no_keys(settings: Settings) -> list[ContentKey]:
    return []


def _no_problems(settings: Settings) -> list[str]:
    return []


def _no_shared(settings: Settings) -> list[str]:
    return []


def _no_content_problems(settings: Settings, content: ContentStore) -> list[str]:
    return []


@dataclass(frozen=True)
class FeatureModule:
    name: str
    is_enabled: Callable[[Settings], bool]
    content_keys: Callable[[Settings], list[ContentKey]] = _no_keys
    settings_problems: Callable[[Settings], list[str]] = _no_problems
    shared_keys: Callable[[Settings], list[str]] = _no_shared
    content_problems: Callable[[Settings, ContentStore], list[str]] = _no_content_problems
    depends_on: tuple[str, ...] = ()
    operator_text: Mapping[str, str] = field(default_factory=dict)
    register: Callable[[App], None] | None = None
    on_ready: Callable[[App], Awaitable[None]] | None = None
    publish: Callable[[App], Awaitable[list[str]]] | None = None
    preview: Callable[[App], list[PreviewState]] | None = None
    # Whether customers can see anything from this feature (requires the core error messages).
    customer_facing: bool = True
