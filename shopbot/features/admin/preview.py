"""Offline preview hook.

Admin output is operator text shown only to administrators (never to customers), so it has no customer
preview states. The pure builders in ``views`` are unit-tested against Discord limits instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.ui.spec import PreviewState


def preview(app: App) -> list[PreviewState]:
    del app
    return []
