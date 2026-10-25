"""STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule

MODULE = FeatureModule(name="catalog", is_enabled=lambda settings: settings.features.catalog)
