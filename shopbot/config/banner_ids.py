"""The banner pictures messages can show at the top (one picture per kind of message).

Each id names one picture file ``<id>.png`` (or ``.jpg``/``.gif``/``.webp``) in the banners folder
(``[banners] directory``). Feature builders choose the id that fits each message; ``[banners] off`` switches ids off.
"""

from __future__ import annotations

from typing import Literal, get_args

BannerId = Literal[
    "welcome",
    "how-it-works",
    "faq",
    "shipping",
    "announcements",
    "catalog",
    "new-arrivals",
    "reviews",
    "purchase-panel",
    "ticket-created",
    "selection-and-cart",
    "details-and-options",
    "review-and-summary",
    "submission-and-processing",
    "successful-confirmation",
    "pending-review",
    "rejected-or-unsuccessful-submission",
    "order-status",
    "completed-or-dispatched",
    "staff-dashboard",
    "staff-notifications",
    "activity-log",
    "transcripts",
    "direct-message-notification",
    "reminder",
    "support-request",
    "resolution",
    "cancellation",
    "account-and-personal-records",
    "error",
    "general-response",
]

BANNER_IDS: tuple[BannerId, ...] = get_args(BannerId)
