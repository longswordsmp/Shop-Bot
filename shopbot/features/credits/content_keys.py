"""Customer-facing content keys of the credits feature, its shared keys, and its settings checks."""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features import core_keys

CREDITS = "credits"

BUTTON_BALANCE = "credits.button.balance"
BALANCE_TITLE = "credits.balance.title"
BALANCE_DESCRIPTION = "credits.balance.description"
NOTICE_TITLE = "credits.adjusted_notice.title"
NOTICE_DESCRIPTION = "credits.adjusted_notice.description"


def content_keys(settings: Settings) -> list[ContentKey]:
    del settings
    g_balance = "Credits: 'my balance' button and the private balance reply"
    g_notice = "Credits: notice to a member after staff changed their balance (direct message)"
    return [
        ck(
            BUTTON_BALANCE,
            Kind.BUTTON_LABEL,
            CREDITS,
            g_balance,
            "Label of the 'my balance' button that other panels (for example the shop panel) can show. "
            "Clicking it shows the member their own credit balance privately.",
        ),
        ck(
            BALANCE_TITLE,
            Kind.TITLE,
            CREDITS,
            g_balance,
            "Optional title of the private reply to the 'my balance' button. If empty, the reply has no title.",
            required=False,
        ),
        ck(
            BALANCE_DESCRIPTION,
            Kind.DESCRIPTION,
            CREDITS,
            g_balance,
            "Text of the private reply to the 'my balance' button. {amount} is the member's current credit "
            "balance written with common.money_format.",
            placeholders=("amount",),
        ),
        ck(
            NOTICE_TITLE,
            Kind.TITLE,
            CREDITS,
            g_notice,
            "Optional title of the direct message sent to a member after an admin added or removed credits "
            "(/credits adjust). {amount} is the change written with common.money_format (negative for a "
            "removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not "
            "notified.",
            required=False,
            placeholders=("amount", "balance"),
        ),
        ck(
            NOTICE_DESCRIPTION,
            Kind.DESCRIPTION,
            CREDITS,
            g_notice,
            "Optional text of the direct message sent to a member after an admin added or removed credits "
            "(/credits adjust). {amount} is the change written with common.money_format (negative for a "
            "removal, e.g. -5.00), {balance} the new balance. If both notice keys are empty, members are not "
            "notified. Requires features.dm_notifications.",
            required=False,
            placeholders=("amount", "balance"),
        ),
    ]


def shared_keys(settings: Settings) -> list[str]:
    del settings
    return [core_keys.MONEY_FORMAT]


def settings_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    if not settings.checkout.currency:
        problems.append("checkout.currency is not set (credit balances are kept in the shop currency)")
    return problems
