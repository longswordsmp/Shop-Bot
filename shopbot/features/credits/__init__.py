"""Store credit: an append-only ledger per member, staff tools, and a 'my balance' button.

Public API for other features:
- ``shopbot.features.credits.repo`` (synchronous, conn-first; call INSIDE your own ``app.db.transaction``):
  ``balance``, ``applicable_amount``, ``history``, ``add_entry``, the idempotent order helpers
  ``debit_for_order`` / ``return_for_order``, ``refund_to_credit``, and the exceptions ``CreditError``,
  ``InsufficientCredit``, ``OrderCreditConflict``. See the module docstring for the invariants.
- ``shopbot.features.credits.service``: ``balance_button(app)`` (place it on your panels; None when it cannot be
  shown), ``available_at_checkout(app)``, ``get_balance(app, user_id)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopbot.features.base import FeatureModule
from shopbot.features.credits import content_keys as credit_keys
from shopbot.features.credits.commands import build_credits_group
from shopbot.features.credits.handlers import register_handlers
from shopbot.features.credits.preview import preview
from shopbot.features.credits.text import OPERATOR_TEXT

if TYPE_CHECKING:
    from shopbot.context import App


def register(app: App) -> None:
    bot = app.bot
    if bot is None:
        raise RuntimeError("register() requires app.bot")
    register_handlers(app)
    bot.tree.add_command(build_credits_group(), guild=app.guild_object)


MODULE = FeatureModule(
    name=credit_keys.CREDITS,
    is_enabled=lambda settings: settings.features.credits,
    content_keys=credit_keys.content_keys,
    settings_problems=credit_keys.settings_problems,
    shared_keys=credit_keys.shared_keys,
    depends_on=("checkout",),
    operator_text=OPERATOR_TEXT,
    register=register,
    preview=preview,
)
