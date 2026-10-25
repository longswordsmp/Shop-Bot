"""The Report a Problem button for the #purchase panel (checkout's panel calls ``report_button``).

Kept separate from the service so checkout can import it without loading the rest of this feature.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.problems import views
from shopbot.features.problems.content_keys import FEATURE
from shopbot.logging_setup import log_event
from shopbot.ui.spec import ButtonSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


def report_button(app: App) -> ButtonSpec | None:
    """The button, or None while the Report a Problem feature is not ready (the panel then shows no button)."""
    if not app.ready(FEATURE):
        return None
    try:
        return views.report_button(app.ui)
    except MissingContent as exc:  # readiness guarantees the label; never break the purchase panel
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="problems.report_button")
        return None
