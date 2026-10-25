"""Report a Problem: the #purchase button, a private step to choose the order and issue type, the issue form, a
private ticket per report (created through the ticket system), staff decisions (resolve / reject with a reason) and
the customer's notification.

Package layout: ``content_keys`` (customer text + settings checks), ``models``/``repo`` (records, synchronous
database functions), ``service`` (async logic), ``views`` (pure message/form builders), ``handlers`` (buttons, menus,
forms), ``tasks`` (durable follow-ups), ``panel`` (/setup diagnostics), ``button`` (the button checkout places on the
#purchase panel), ``preview``.

Public API for other features: ``shopbot.features.problems.button.report_button(app)`` (None while not ready).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.problems import content_keys as keys
from shopbot.features.problems import handlers, panel, preview, service, tasks
from shopbot.features.problems.text import OPERATOR_TEXT
from shopbot.features.tickets import service as tickets_service

if TYPE_CHECKING:
    from shopbot.context import App


def _register(app: App) -> None:
    handlers.register(app)
    tasks.register(app)
    # Report tickets are support tickets of their own category: the ticket system's controls and /ticket commands
    # treat problems.staff_roles as staff of those tickets too.
    tickets_service.register_external_category(
        app,
        keys.TICKET_CATEGORY,
        service.extra_ticket_staff_keys,
        discord_category=lambda a: a.settings.problems.discord_category,
    )


MODULE = FeatureModule(
    name=keys.FEATURE,
    is_enabled=lambda settings: settings.features.problems,
    content_keys=keys.content_keys,
    settings_problems=keys.settings_problems,
    shared_keys=keys.shared_keys,
    content_problems=keys.content_problems,
    depends_on=("tickets", "checkout"),
    operator_text=OPERATOR_TEXT,
    register=_register,
    publish=panel.publish,
    preview=preview.preview,
)
