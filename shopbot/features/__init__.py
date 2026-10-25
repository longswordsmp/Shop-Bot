"""Feature registry. Order matters: a feature must come after the features it depends on."""

from __future__ import annotations

from shopbot.features.base import FeatureModule


def all_modules() -> list[FeatureModule]:
    from shopbot.features import core_keys
    from shopbot.features.admin import MODULE as admin
    from shopbot.features.announcements import MODULE as announcements
    from shopbot.features.catalog import MODULE as catalog
    from shopbot.features.checkout import MODULE as checkout
    from shopbot.features.credits import MODULE as credits
    from shopbot.features.guides import FAQ_MODULE as faq
    from shopbot.features.guides import HOW_TO_BUY_MODULE as how_to_buy
    from shopbot.features.guides import SHIPPING_MODULE as shipping
    from shopbot.features.info_panels import MODULE as info_panels
    from shopbot.features.my_orders import MODULE as my_orders
    from shopbot.features.new_arrivals import MODULE as new_arrivals
    from shopbot.features.reviews import MODULE as reviews
    from shopbot.features.setup import MODULE as setup
    from shopbot.features.tickets import MODULE as tickets
    from shopbot.features.verification import MODULE as verification

    return [
        core_keys.MODULE,
        setup,
        admin,
        info_panels,
        verification,
        how_to_buy,
        faq,
        shipping,
        tickets,
        catalog,
        new_arrivals,
        checkout,
        my_orders,
        credits,
        reviews,
        announcements,
    ]
