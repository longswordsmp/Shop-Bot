"""Informational panels (rules, FAQ, announcements...) published by /setup."""

from __future__ import annotations

from shopbot.features.base import FeatureModule
from shopbot.features.info_panels.content_keys import (
    INFO_PANELS,
    info_panel_content_problems,
    info_panel_keys,
    info_panel_settings_problems,
)
from shopbot.features.info_panels.preview import preview
from shopbot.features.info_panels.service import publish
from shopbot.features.info_panels.text import OPERATOR_TEXT

MODULE = FeatureModule(
    name=INFO_PANELS,
    is_enabled=lambda settings: settings.features.info_panels,
    content_keys=info_panel_keys,
    settings_problems=info_panel_settings_problems,
    content_problems=info_panel_content_problems,
    operator_text=OPERATOR_TEXT,
    publish=publish,
    panel_prefixes=("info_panel:",),
    preview=preview,
)
