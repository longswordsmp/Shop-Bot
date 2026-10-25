"""STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule

MODULE = FeatureModule(name="tickets", is_enabled=lambda settings: settings.features.tickets)
