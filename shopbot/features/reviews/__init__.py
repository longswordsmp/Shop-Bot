"""STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule

MODULE = FeatureModule(name="reviews", is_enabled=lambda settings: settings.features.reviews)
