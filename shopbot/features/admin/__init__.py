"""Admin tools. STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule

MODULE = FeatureModule(name="admin", is_enabled=lambda settings: True, customer_facing=False)
