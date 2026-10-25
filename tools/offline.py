"""Build a fully wired ``App`` without connecting to Discord.

Used by the limits checker, the content checklist, and the HTML preview. The database is in-memory and
starts empty; nothing is read from or written to the production database, and no token is needed.

Content modes:
- ``actual``   - exactly what config/content.toml (plus nothing else) contains.
- ``markers``  - unconfigured keys render as ``⟦key⟧`` so the preview shows where text is still missing.
- ``fixtures`` - unconfigured keys get short synthetic tokens (``[t17]``) so the limits checker can build and
  verify every state (structure, limits, handler coverage) before any real text exists. These tokens exist
  only in memory for the check; they are never written anywhere and never sent to Discord.
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path
from typing import Any, Literal

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import Registry
from shopbot.config.loader import load_config
from shopbot.config.settings import Settings, parse_settings
from shopbot.context import App
from shopbot.core.clock import FakeClock
from shopbot.db.database import Database
from shopbot.db.migrate import apply_migrations
from shopbot.env import Env
from shopbot.features import all_modules

ROOT = Path(__file__).resolve().parent.parent
ContentMode = Literal["actual", "markers", "fixtures"]

# Used only when previewing every feature regardless of the operator's flags. "XXX" is the ISO 4217 code
# for "no currency", so the preview never implies a real currency or payment decision.
PREVIEW_ASSUMPTIONS: dict[str, Any] = {
    "features": {
        "info_panels": True,
        "tickets": True,
        "catalog": True,
        "checkout": True,
        "credits": True,
        "reviews": True,
        "dm_notifications": True,
    },
    "checkout": {"currency": "XXX", "currency_exponent": 2},
    "payments": {"provider": "manual"},
    "reviews": {"prompt_delay_minutes": 0, "rating_scale": 5},
}


class MarkerContentStore(ContentStore):
    """Content store that shows ``⟦key⟧`` for required text that is not configured (preview only). Optional text
    left empty is left out, exactly as the bot leaves it out."""

    def raw(self, key: str) -> str | None:
        value = super().raw(key)
        definition = self.get_definition(key)
        if value is None and definition.fallback is None and definition.required:
            return f"⟦{key}⟧"
        return value

    def get_definition(self, key: str) -> Any:
        return self._definition(key)

    def is_marker(self, key: str) -> bool:
        return super().raw(key) is None


class TrackingContentStore(ContentStore):
    """Records which required keys were requested but not configured (limits checker). Optional keys left empty
    (an optional footer, image link or note) are simply not shown and do not make a message incomplete."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.missing: set[str] = set()

    def get(self, key: str) -> str | None:
        value = super().get(key)
        if value is None and self._definition(key).required:
            self.missing.add(key)
        return value


def fixture_values(registry: Registry, configured: dict[str, str]) -> dict[str, str]:
    """Configured values plus short synthetic tokens for every unconfigured key (in-memory checks only)."""
    from shopbot.config.content_keys import Kind

    values = dict(configured)
    for index, definition in enumerate(registry.all()):
        if values.get(definition.key):
            continue
        token = f"[t{index}]"
        if definition.kind is Kind.EMOJI:
            value = "⭐"
        elif definition.kind is Kind.URL:
            value = f"https://example.com/t{index}.png"
        elif definition.kind in (Kind.CHANNEL_NAME, Kind.CHANNEL_PREFIX):
            value = f"t{index}"
        elif definition.placeholders:
            value = token + " " + " ".join("{" + p + "}" for p in definition.placeholders)
        else:
            value = token
        values[definition.key] = value if len(value) <= definition.max_length else token[: definition.max_length]
    return values


class OfflineBot:
    """Minimal client so feature ``register`` hooks can run offline (routes, task kinds, command tree).

    It has no gateway connection and no HTTP session: lookups return nothing and nothing is sent.
    """

    def __init__(self, app: App) -> None:
        import types

        from discord import app_commands

        self.app = app
        app.bot = self  # type: ignore[assignment]
        self.http = None
        self.user = None
        self._connection = types.SimpleNamespace(_command_tree=None)
        self.tree = app_commands.CommandTree(self)  # type: ignore[type-var]
        self.listeners: dict[str, list[Any]] = {}

    def add_listener(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    def add_dynamic_items(self, *items: Any) -> None:
        return None

    def get_guild(self, guild_id: int) -> None:
        return None

    def get_channel(self, channel_id: int) -> None:
        return None

    def get_user(self, user_id: int) -> None:
        return None


def register_offline(app: App) -> OfflineBot:
    bot = OfflineBot(app)
    for module in app.modules:
        if module.register is not None:
            module.register(app)
    return bot


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings_dict(config_dir: Path) -> dict[str, Any]:
    import tomllib

    with (config_dir / "settings.toml").open("rb") as handle:
        return tomllib.load(handle)


async def build_offline_app(
    config_dir: Path,
    *,
    content_mode: ContentMode = "actual",
    all_features: bool = False,
    extra_settings: dict[str, Any] | None = None,
) -> App:
    loaded = load_config(config_dir)
    settings: Settings = loaded.settings
    if all_features or extra_settings:
        data = load_settings_dict(config_dir)
        if all_features:
            # Never override business decisions the operator already made.
            assumptions = deep_merge(PREVIEW_ASSUMPTIONS, {})
            if settings.checkout.currency:
                assumptions["checkout"] = {}
            if settings.payments.provider:
                assumptions["payments"] = {}
            if settings.reviews.prompt_delay_minutes >= 0 and settings.reviews.rating_scale:
                assumptions["reviews"] = {}
            data = deep_merge(data, assumptions)
        if extra_settings:
            data = deep_merge(data, extra_settings)
        settings = parse_settings(data)

    env = Env(
        discord_token="",
        guild_id=0,
        owner_id=None,
        db_path=Path(":memory:"),
        config_dir=config_dir,
        log_level="WARNING",
        log_format="text",
        app_env="development",
        startup_failure_delay_seconds=0,
    )
    db = Database(":memory:")
    await db.open()
    await db.run(apply_migrations)
    app = App(
        env=env,
        settings=settings,
        content_values=loaded.content_values,
        overrides={},
        db=db,
        clock=FakeClock(),
        modules=all_modules(),
        config_dir=config_dir,
    )
    registry: Registry = app.content.registry
    store_cls = MarkerContentStore if content_mode == "markers" else TrackingContentStore
    file_values = (
        fixture_values(registry, loaded.content_values) if content_mode == "fixtures" else loaded.content_values
    )
    # Offline, every icon gets a stable stand-in emoji id (as long as a real one), so rendered texts have their real
    # length and the preview can show the icon picture by name.
    app.icons.ids = {name: offline_emoji_id(name) for name in app.icons.files}
    app.icons_ready.set()
    store = store_cls(
        registry,
        file_values,
        {},
        icons=lambda name: app.icons.text(name),
        icon_names=lambda: app.icons.names if app.icons.enabled else None,
    )
    app.content = store
    app.ui.content = store
    app.recompute_readiness()
    register_offline(app)
    return app


def offline_emoji_id(name: str) -> int:
    """A stand-in application emoji id for ``name`` (19 digits like a real snowflake; stable across runs)."""
    return 1_300_000_000_000_000_000 + zlib.crc32(name.encode("utf-8"))


def ensure_import_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
