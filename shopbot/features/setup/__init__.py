"""Server structure (/setup). STUB — replaced by the feature implementation."""

from shopbot.features.base import FeatureModule
from shopbot.features.server_keys import SERVER, server_keys, server_problems

MODULE = FeatureModule(
    name=SERVER,
    is_enabled=lambda settings: True,
    content_keys=server_keys,
    settings_problems=server_problems,
    customer_facing=False,
)
