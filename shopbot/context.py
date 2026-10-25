"""The application container passed to every feature."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from shopbot.config.content import ContentStore
from shopbot.config.loader import load_config
from shopbot.config.operator_text import CORE_OPERATOR_TEXT, render_operator
from shopbot.config.readiness import Readiness, build_registry, compute_readiness
from shopbot.config.settings import Settings
from shopbot.core.audit import AuditLog
from shopbot.core.clock import Clock
from shopbot.core.events import EventBus
from shopbot.core.notifier import Notifier
from shopbot.core.panels import withdraw_and_audit
from shopbot.core.permissions import Permissions
from shopbot.core.resources import Resources
from shopbot.core.scheduler import Scheduler
from shopbot.db.database import Database
from shopbot.db.repos import state
from shopbot.env import Env
from shopbot.features.base import FeatureModule
from shopbot.logging_setup import log_event
from shopbot.ui.routing import Router
from shopbot.ui.style import Ui

if TYPE_CHECKING:
    from shopbot.bot import ShopBot

log = logging.getLogger(__name__)


@dataclass
class ReloadResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


class App:
    """Holds configuration and services. ``settings`` and the content store can be reloaded at runtime;
    services read settings through ``lambda: app.settings`` so they always see the current values."""

    def __init__(
        self,
        *,
        env: Env,
        settings: Settings,
        content_values: dict[str, str],
        overrides: dict[str, str],
        db: Database,
        clock: Clock,
        modules: Sequence[FeatureModule],
        config_dir: Path,
    ) -> None:
        self.env = env
        self.settings = settings
        self.db = db
        self.clock = clock
        self.modules = list(modules)
        self.config_dir = config_dir
        self.bot: ShopBot | None = None
        registry = build_registry(settings, self.modules)
        self.content = ContentStore(registry, content_values, overrides)
        self.readiness: Readiness = compute_readiness(settings, self.content, self.modules)
        self.operator_defaults: dict[str, str] = dict(CORE_OPERATOR_TEXT)
        for module in self.modules:
            for key, value in module.operator_text.items():
                if key in self.operator_defaults and self.operator_defaults[key] != value:
                    raise ValueError(f"operator text key {key!r} defined twice")
                self.operator_defaults[key] = value
        self.resources = Resources(db, lambda: self.settings, env.guild_id, clock)
        self.perms = Permissions(lambda: self.settings, self.resources, env.owner_id)
        self.ui = Ui(lambda: self.settings, self.content, clock)
        self.router = Router(self)
        bot_settings = settings.bot
        self.scheduler = Scheduler(
            db,
            clock,
            poll_seconds=bot_settings.scheduler_poll_seconds,
            batch_size=bot_settings.scheduler_batch_size,
            max_attempts=bot_settings.scheduler_max_attempts,
            retry_base_seconds=bot_settings.scheduler_retry_base_seconds,
        )
        self.notifier = Notifier(self)
        self.audit = AuditLog(self)
        self.events = EventBus()

    # -- helpers ----------------------------------------------------------------------------------

    @property
    def guild_object(self) -> discord.Object:
        return discord.Object(self.env.guild_id)

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.env.guild_id) if self.bot is not None else None

    def module(self, name: str) -> FeatureModule | None:
        return next((m for m in self.modules if m.name == name), None)

    def text(self, key: str, /, **values: str | int) -> str | None:
        return self.content.render(key, **values)

    def op(self, key: str, /, **values: object) -> str:
        """Operator (admin-only) text, overridable via ``operator.<key>`` in content.toml."""
        template = self.content.operator_value(key) or self.operator_defaults.get(key)
        if template is None:
            raise KeyError(f"operator text {key!r} is not defined")
        return render_operator(template, values)

    def ready(self, feature: str) -> bool:
        return self.readiness.is_ready(feature)

    # -- configuration lifecycle ------------------------------------------------------------------

    def recompute_readiness(self) -> Readiness:
        self.readiness = compute_readiness(self.settings, self.content, self.modules)
        return self.readiness

    async def load_overrides(self) -> None:
        overrides = await self.db.run(state.overrides_all)
        self.content.replace(overrides=overrides)
        self.recompute_readiness()

    async def reload_config(self) -> ReloadResult:
        """Re-read settings.toml/content.toml. On any error the running configuration is kept."""
        try:
            loaded = load_config(self.config_dir)
            registry = build_registry(loaded.settings, self.modules)
        except Exception as exc:
            errors = getattr(exc, "errors", None) or [str(exc)]
            log_event(log, logging.ERROR, "config_reload_failed", errors=errors[:20])
            return ReloadResult(ok=False, errors=list(errors))
        overrides = await self.db.run(state.overrides_all)
        self.settings = loaded.settings
        self.content.replace(registry=registry, file_values=loaded.content_values, overrides=overrides)
        self.recompute_readiness()
        self.router.reset_limits()
        await self.resources.refresh()
        log_event(
            log, logging.INFO, "config_reloaded", ready=[n for n, s in self.readiness.statuses.items() if s.ready]
        )
        # A feature that is no longer ready must not keep a live panel.
        await withdraw_and_audit(self, actor_id=None, reason="reload")
        return ReloadResult(ok=True)
