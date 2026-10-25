"""Shared customer-facing keys: generic errors, shared buttons, branding text, formats.

Shared buttons are *optional* in the core feature; each feature that displays one lists it in its
``shared_keys`` so it becomes required only when that feature is enabled.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.base import FeatureModule

CORE = "core"

ERROR_GENERIC = "common.error.generic"
ERROR_PERMISSION = "common.error.permission_denied"
ERROR_COOLDOWN = "common.error.cooldown"
ERROR_UNAVAILABLE = "common.error.unavailable"
ERROR_NOT_FOUND = "common.error.not_found"
ERROR_INVALID_INPUT = "common.error.invalid_input"
ERROR_BUSY = "common.error.busy"
ERROR_TITLE = "common.error.title"

BUTTON_CONFIRM = "common.button.confirm"
BUTTON_CANCEL = "common.button.cancel"
BUTTON_BACK = "common.button.back"
BUTTON_CONTINUE = "common.button.continue"
BUTTON_PREV = "common.button.previous_page"
BUTTON_NEXT = "common.button.next_page"

PAGE_INDICATOR = "common.page_indicator"
PROGRESS_FORMAT = "common.progress_format"
MONEY_FORMAT = "common.money_format"

FOOTER_TEXT = "branding.footer_text"
AUTHOR_NAME = "branding.author_name"

CORE_REQUIRED = (
    ERROR_GENERIC,
    ERROR_PERMISSION,
    ERROR_COOLDOWN,
    ERROR_UNAVAILABLE,
    ERROR_NOT_FOUND,
    ERROR_INVALID_INPUT,
)


def core_keys(settings: Settings) -> list[ContentKey]:
    del settings
    g_err = "Error messages (shown privately to the person who clicked)"
    g_btn = "Shared buttons"
    g_fmt = "Formats"
    g_brand = "Branding text"
    return [
        ck(
            ERROR_GENERIC,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when an action fails unexpectedly. Internal details are never shown.",
        ),
        ck(
            ERROR_PERMISSION,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when someone uses a control they are not allowed to use (e.g. a staff-only button).",
        ),
        ck(
            ERROR_COOLDOWN,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when someone acts too quickly in a row.",
            placeholders=("retry_after",),
        ),
        ck(
            ERROR_UNAVAILABLE,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when a feature is switched off or not fully configured.",
        ),
        ck(
            ERROR_NOT_FOUND,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when the ticket/order/item behind a control no longer exists.",
        ),
        ck(
            ERROR_INVALID_INPUT,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when a form entry is invalid (e.g. a quantity that is not a whole number).",
        ),
        ck(
            ERROR_BUSY,
            Kind.DESCRIPTION,
            CORE,
            g_err,
            "Shown when someone clicks again while their previous click is still being processed. "
            "Optional: if empty, the duplicate click is silently ignored.",
            required=False,
        ),
        ck(ERROR_TITLE, Kind.TITLE, CORE, g_err, "Optional title above every error message.", required=False),
        ck(BUTTON_CONFIRM, Kind.BUTTON_LABEL, CORE, g_btn, "Confirm button on confirmation prompts.", required=False),
        ck(BUTTON_CANCEL, Kind.BUTTON_LABEL, CORE, g_btn, "Cancel button on prompts and workflows.", required=False),
        ck(BUTTON_BACK, Kind.BUTTON_LABEL, CORE, g_btn, "Back button in multi-step workflows.", required=False),
        ck(BUTTON_CONTINUE, Kind.BUTTON_LABEL, CORE, g_btn, "Continue button in multi-step workflows.", required=False),
        ck(BUTTON_PREV, Kind.BUTTON_LABEL, CORE, g_btn, "Previous-page button in paginated lists.", required=False),
        ck(BUTTON_NEXT, Kind.BUTTON_LABEL, CORE, g_btn, "Next-page button in paginated lists.", required=False),
        ck(
            PAGE_INDICATOR,
            Kind.FORMAT,
            CORE,
            g_fmt,
            "Optional page indicator added to the footer of paginated lists.",
            required=False,
            placeholders=("page", "pages"),
        ),
        ck(
            PROGRESS_FORMAT,
            Kind.FORMAT,
            CORE,
            g_fmt,
            "Optional step indicator shown in multi-step workflows. {bar} uses the progress symbols "
            "from settings.branding.",
            required=False,
            placeholders=("current", "total", "bar"),
        ),
        ck(
            MONEY_FORMAT,
            Kind.FORMAT,
            CORE,
            g_fmt,
            "How prices are written, e.g. '{amount} {currency}'. {amount} is formatted with the "
            "separators from settings.checkout. Required by catalog and checkout.",
            required=False,
            placeholders=("amount", "currency"),
        ),
        ck(FOOTER_TEXT, Kind.FOOTER, CORE, g_brand, "Optional footer on every customer-facing embed.", required=False),
        ck(
            AUTHOR_NAME,
            Kind.AUTHOR,
            CORE,
            g_brand,
            "Optional author line at the top of every customer-facing embed (uses the logo as icon).",
            required=False,
        ),
    ]


MODULE = FeatureModule(
    name=CORE,
    # Enabled whenever any customer-facing feature is enabled (computed in readiness).
    is_enabled=lambda settings: bool(settings.features.enabled()),
    content_keys=core_keys,
    customer_facing=False,
)
