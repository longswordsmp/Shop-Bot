"""Feature registry. Order matters: a feature must come after the features it depends on."""

from __future__ import annotations

from shopbot.features.base import FeatureModule


def all_modules() -> list[FeatureModule]:
    from shopbot.features import core_keys
    from shopbot.features.admin import MODULE as admin
    from shopbot.features.catalog import MODULE as catalog
    from shopbot.features.checkout import MODULE as checkout
    from shopbot.features.credits import MODULE as credits
    from shopbot.features.info_panels import MODULE as info_panels
    from shopbot.features.reviews import MODULE as reviews
    from shopbot.features.setup import MODULE as setup
    from shopbot.features.tickets import MODULE as tickets

    return [core_keys.MODULE, setup, admin, info_panels, tickets, catalog, checkout, credits, reviews]
