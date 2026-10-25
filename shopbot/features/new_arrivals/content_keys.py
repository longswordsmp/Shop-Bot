"""Customer-facing content keys of the new-arrivals feature, its shared keys, and its settings checks.

The panel, the confirmations and the update titles are this feature's own keys. Product price and availability in
the automatic updates are written exactly like the catalog's product cards, with the catalog's keys (listed in
``shared_keys``), so a product reads the same everywhere.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features import core_keys
from shopbot.features.catalog import content_keys as catalog_keys
from shopbot.features.server_keys import (
    PRIVATE_ACCESS,
    channel_placeholders,
    effective_access,
    reference_problems,
    staff_role_lists,
)

NEW_ARRIVALS = "new_arrivals"

# -- opt-in panel ------------------------------------------------------------------------------------
PANEL_TITLE = "new_arrivals.panel.title"
PANEL_DESCRIPTION = "new_arrivals.panel.description"
BUTTON_TOGGLE = "new_arrivals.button.toggle"
BUTTON_TOGGLE_EMOJI = "new_arrivals.button.toggle_emoji"

# -- private confirmations ---------------------------------------------------------------------------
SUBSCRIBED_TITLE = "new_arrivals.subscribed.title"
SUBSCRIBED_DESCRIPTION = "new_arrivals.subscribed.description"
UNSUBSCRIBED_TITLE = "new_arrivals.unsubscribed.title"
UNSUBSCRIBED_DESCRIPTION = "new_arrivals.unsubscribed.description"

# -- automatic product updates -----------------------------------------------------------------------
NEW_PRODUCT_TITLE = "new_arrivals.new_product.title"
NEW_PRODUCT_FOOTER = "new_arrivals.new_product.footer"
RESTOCK_TITLE = "new_arrivals.restock.title"
RESTOCK_FOOTER = "new_arrivals.restock.footer"
BUTTON_VIEW_CATALOG = "new_arrivals.button.view_catalog"

ROLE_PLACEHOLDER = "role"
NAME_PLACEHOLDER = "name"

_ROLE_DOC = (
    "{role} shows the notification role as a mention (it displays the role's name and pings nobody). Channel links "
    "such as {channel_catalog} become clickable #channel links once /setup created the channel."
)


def text_placeholders(settings: Settings) -> tuple[str, ...]:
    """Placeholders of the panel and confirmation texts: the role mention and every configured channel link."""
    return (ROLE_PLACEHOLDER, *channel_placeholders(settings))


def content_keys(settings: Settings) -> list[ContentKey]:
    na = settings.new_arrivals
    placeholders = text_placeholders(settings)
    g_panel = "New arrivals: notification panel (posted by /setup in the new-arrivals channel)"
    g_confirm = "New arrivals: private confirmations (shown only to the member who clicked)"
    g_updates = "New arrivals: automatic product updates (posted in the new-arrivals channel)"
    return [
        ck(
            PANEL_TITLE,
            Kind.TITLE,
            NEW_ARRIVALS,
            g_panel,
            "Title of the persistent panel in the new-arrivals channel that lets members turn product notifications "
            "on or off.",
        ),
        ck(
            PANEL_DESCRIPTION,
            Kind.DESCRIPTION,
            NEW_ARRIVALS,
            g_panel,
            "Text of the notification panel: what members receive when they turn notifications on, and that the "
            f"same button turns them off again. {_ROLE_DOC}",
            placeholders=placeholders,
        ),
        ck(
            BUTTON_TOGGLE,
            Kind.BUTTON_LABEL,
            NEW_ARRIVALS,
            g_panel,
            "Label of the panel's single button. A click gives the member the notification role, or removes it when "
            "they already have it (so the label should describe both, e.g. turning notifications on or off).",
        ),
        ck(
            BUTTON_TOGGLE_EMOJI,
            Kind.EMOJI,
            NEW_ARRIVALS,
            g_panel,
            "Optional emoji shown on the panel's button.",
            required=False,
        ),
        ck(
            SUBSCRIBED_TITLE,
            Kind.TITLE,
            NEW_ARRIVALS,
            g_confirm,
            "Optional title of the private confirmation shown after a member turned notifications on.",
            required=False,
        ),
        ck(
            SUBSCRIBED_DESCRIPTION,
            Kind.DESCRIPTION,
            NEW_ARRIVALS,
            g_confirm,
            "Private confirmation shown after a member turned notifications on (they now have the notification role "
            f"and are mentioned in product updates). {_ROLE_DOC}",
            placeholders=placeholders,
        ),
        ck(
            UNSUBSCRIBED_TITLE,
            Kind.TITLE,
            NEW_ARRIVALS,
            g_confirm,
            "Optional title of the private confirmation shown after a member turned notifications off.",
            required=False,
        ),
        ck(
            UNSUBSCRIBED_DESCRIPTION,
            Kind.DESCRIPTION,
            NEW_ARRIVALS,
            g_confirm,
            "Private confirmation shown after a member turned notifications off (the notification role was removed; "
            f"clicking the button again turns them back on). {_ROLE_DOC}",
            placeholders=placeholders,
        ),
        ck(
            NEW_PRODUCT_TITLE,
            Kind.TITLE,
            NEW_ARRIVALS,
            g_updates,
            "Title of the update posted automatically when a product is added to the catalog or switched back on "
            "(new_arrivals.announce_new_products). {name} is the product name. Below it the update shows the "
            "product's description, image, price and availability exactly like its catalog card.",
            required=na.announce_new_products,
            placeholders=(NAME_PLACEHOLDER,),
        ),
        ck(
            NEW_PRODUCT_FOOTER,
            Kind.FOOTER,
            NEW_ARRIVALS,
            g_updates,
            "Optional small text at the bottom of a new-product update.",
            required=False,
        ),
        ck(
            RESTOCK_TITLE,
            Kind.TITLE,
            NEW_ARRIVALS,
            g_updates,
            "Title of the update posted automatically when a sold-out product can be ordered again "
            "(new_arrivals.announce_restocks). {name} is the product name. Below it the update shows the product's "
            "description, image, price and availability exactly like its catalog card.",
            required=na.announce_restocks,
            placeholders=(NAME_PLACEHOLDER,),
        ),
        ck(
            RESTOCK_FOOTER,
            Kind.FOOTER,
            NEW_ARRIVALS,
            g_updates,
            "Optional small text at the bottom of a restock update.",
            required=False,
        ),
        ck(
            BUTTON_VIEW_CATALOG,
            Kind.BUTTON_LABEL,
            NEW_ARRIVALS,
            g_updates,
            "Optional label of a link button under each product update that opens the catalog listing channel "
            "(catalog.listing_channel). If empty, the updates have no button.",
            required=False,
        ),
    ]


def shared_keys(settings: Settings) -> list[str]:
    """Catalog keys the updates use to write price and availability (the same text as the catalog cards)."""
    del settings
    return [
        core_keys.MONEY_FORMAT,
        catalog_keys.FIELD_PRICE,
        catalog_keys.FIELD_AVAILABILITY,
        catalog_keys.AVAILABILITY_IN_STOCK,
        catalog_keys.AVAILABILITY_UNLIMITED,
        catalog_keys.AVAILABILITY_OUT_OF_STOCK,
    ]


def _privileged_role_keys(settings: Settings) -> set[str]:
    """Role keys that grant access or staff powers anywhere (a self-assignable role must not be one of them)."""
    keys = {key for _, role_keys in staff_role_lists(settings) for key in role_keys}
    if settings.permissions.member_role:
        keys.add(settings.permissions.member_role)
    return keys


def settings_problems(settings: Settings) -> list[str]:
    na = settings.new_arrivals
    problems: list[str] = []
    if not na.channel:
        problems.append(
            "new_arrivals.channel: required (channel key of the notification panel and the product updates)"
        )
    if not na.role:
        problems.append("new_arrivals.role: required (role key of the notification role members turn on and off)")
    problems.extend(
        reference_problems(
            settings,
            roles=(na.role,) if na.role else (),
            channels=(("new_arrivals.channel", na.channel),),
        )
    )
    if not (na.announce_new_products or na.announce_restocks):
        problems.append(
            "new_arrivals: announce_new_products and announce_restocks are both false, so no product update would "
            "ever be posted; turn at least one on"
        )
    if na.channel and settings.server.channel(na.channel) is not None:
        access = effective_access(settings, na.channel)
        if access in PRIVATE_ACCESS:
            problems.append(
                f"new_arrivals.channel: channel '{na.channel}' is '{access}'-only; members could not see the "
                "notification panel or the updates (use a public or members channel)"
            )
    role = settings.server.role(na.role) if na.role else None
    if role is not None:
        if na.role in _privileged_role_keys(settings):
            problems.append(
                f"new_arrivals.role: '{na.role}' is an owner/staff/member/manager role; members give the notification "
                "role to themselves, so it must be a separate role"
            )
        if role.permissions:
            problems.append(
                f"new_arrivals.role: server.roles '{na.role}' grants permissions {list(role.permissions)}; members "
                "give this role to themselves, so it must not grant any permission (set permissions = [])"
            )
        if na.mention_role and not role.mentionable:
            problems.append(
                f"new_arrivals.mention_role = true requires server.roles '{na.role}'.mentionable = true (Discord does "
                "not notify members of a role that cannot be mentioned); or set mention_role = false"
            )
    return problems
