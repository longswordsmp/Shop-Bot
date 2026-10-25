"""Preview: one state per configured info panel, built with the runtime view functions.

When no panel is configured, a synthetic example panel shows the structure (title, description, two
fields, one link button) with ``[info_panels.example.*]`` markers instead of text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.config.settings import InfoPanelDef
from shopbot.features.info_panels.content_keys import INFO_PANELS
from shopbot.features.info_panels.views import panel_message, resolve_panel_text
from shopbot.ui.spec import PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

EXAMPLE_PANEL = InfoPanelDef(key="example", channel="", fields=2, inline_fields=False, links=("example_link",))


def preview(app: App) -> list[PreviewState]:
    states: list[PreviewState] = []
    for panel in app.settings.info_panels:
        text = resolve_panel_text(app.content.get, panel, preview=True)
        states.append(
            PreviewState(
                feature=INFO_PANELS,
                name=f"panel_{panel.key}",
                note=f"Info panel '{panel.key}' as posted in the '{panel.channel}' channel by /setup.",
                spec=panel_message(app.ui, text, inline_fields=panel.inline_fields),
            )
        )
    if not states:
        text = resolve_panel_text(lambda key: None, EXAMPLE_PANEL, preview=True)
        states.append(
            PreviewState(
                feature=INFO_PANELS,
                name="panel_example",
                note="Structure of an info panel (no [[info_panels]] are configured; markers show the content keys).",
                spec=panel_message(app.ui, text, inline_fields=EXAMPLE_PANEL.inline_fields),
            )
        )
    return states
