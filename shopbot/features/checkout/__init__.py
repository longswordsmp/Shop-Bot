"""STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule

MODULE = FeatureModule(name="checkout", is_enabled=lambda settings: settings.features.checkout)
