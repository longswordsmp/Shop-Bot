"""START HERE guides: #how-to-buy (step-by-step guide), #faq (questions and answers), #shipping (regions).

Three independent features, each enabled by its own flag (``features.how_to_buy`` / ``faq`` / ``shipping``) and
published by /setup into its own channel. Every word comes from content keys; texts may link channels with
``{channel_<key>}`` placeholders.
"""

from __future__ import annotations

from shopbot.features.base import FeatureModule
from shopbot.features.guides import faq, how_to_buy, shipping
from shopbot.features.guides.text import OPERATOR_TEXT

HOW_TO_BUY_MODULE = FeatureModule(
    name=how_to_buy.HOW_TO_BUY,
    is_enabled=lambda settings: settings.features.how_to_buy,
    content_keys=how_to_buy.content_keys,
    settings_problems=how_to_buy.settings_problems,
    content_problems=how_to_buy.content_problems,
    operator_text=OPERATOR_TEXT,
    publish=how_to_buy.publish,
    panel_prefixes=(how_to_buy.PREFIX,),
    preview=how_to_buy.preview,
)

FAQ_MODULE = FeatureModule(
    name=faq.FAQ,
    is_enabled=lambda settings: settings.features.faq,
    content_keys=faq.content_keys,
    settings_problems=faq.settings_problems,
    content_problems=faq.content_problems,
    operator_text=OPERATOR_TEXT,
    publish=faq.publish,
    panel_prefixes=(faq.PREFIX,),
    preview=faq.preview,
)

SHIPPING_MODULE = FeatureModule(
    name=shipping.SHIPPING,
    is_enabled=lambda settings: settings.features.shipping,
    content_keys=shipping.content_keys,
    settings_problems=shipping.settings_problems,
    content_problems=shipping.content_problems,
    operator_text=OPERATOR_TEXT,
    publish=shipping.publish,
    panel_prefixes=(shipping.PREFIX,),
    preview=shipping.preview,
)

MODULES = (HOW_TO_BUY_MODULE, FAQ_MODULE, SHIPPING_MODULE)
