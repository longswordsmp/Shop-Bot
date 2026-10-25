"""Test application factory.

``make_test_env`` builds a real ``App`` (real settings parser, content store, readiness, SQLite database
with migrations, router, scheduler) wired to a ``FakeBot``/``FakeGuild``.

Fixture content (``fixture_content``) fills keys with short bracketed tokens such as ``[t17]``. These
are internal test fixtures generated at test time; they never exist in config/content.toml and cannot
reach the production server.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shopbot.config.content_keys import Kind, Registry
from shopbot.config.settings import Settings, parse_settings
from shopbot.context import App
from shopbot.core.clock import FakeClock
from shopbot.db.database import Database
from shopbot.db.migrate import apply_migrations
from shopbot.env import Env
from shopbot.features import all_modules
from tests.fakes import FakeBot, FakeGuild, FakeMember, FakeRole

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_SETTINGS = ROOT / "config" / "settings.toml"
GUILD_ID = 111_111_111_111_111_111
OWNER_ID = 222_222_222_222_222_222


def shipped_settings_dict() -> dict[str, Any]:
    with SHIPPED_SETTINGS.open("rb") as handle:
        return tomllib.load(handle)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# Business decisions filled in ONLY for tests, so every feature can be exercised.
TEST_BUSINESS_SETTINGS: dict[str, Any] = {
    "features": {
        "info_panels": True,
        "tickets": True,
        "catalog": True,
        "checkout": True,
        "credits": True,
        "reviews": True,
        "dm_notifications": True,
    },
    "checkout": {"currency": "USD"},
    "payments": {"provider": "manual"},
    "reviews": {"prompt_delay_minutes": 0, "rating_scale": 5},
}


# Tests click the same control repeatedly on purpose; the 700 ms anti-double-click spacing has its own router test.
TEST_TECHNICAL_SETTINGS: dict[str, Any] = {"bot": {"interaction_cooldown_ms": 0}}


def make_settings(overrides: dict[str, Any] | None = None, *, all_features: bool = True) -> Settings:
    data = deep_merge(shipped_settings_dict(), TEST_TECHNICAL_SETTINGS)
    if all_features:
        data = deep_merge(data, TEST_BUSINESS_SETTINGS)
    if overrides:
        data = deep_merge(data, overrides)
    return parse_settings(data)


def fixture_content(registry: Registry) -> dict[str, str]:
    """Short, unique, limit-respecting fixture values for every registered key (tests only)."""
    values: dict[str, str] = {}
    for index, definition in enumerate(registry.all()):
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
        if len(value) > definition.max_length:
            value = token[: definition.max_length]
        values[definition.key] = value
    return values


@dataclass
class TestEnv:
    __test__ = False  # not a pytest test class

    app: App
    bot: FakeBot
    guild: FakeGuild
    clock: FakeClock
    content: dict[str, str]
    staff_role: FakeRole

    def member(self, *, name: str = "customer", staff: bool = False, admin: bool = False) -> FakeMember:
        roles = [self.staff_role] if staff else []
        return self.guild.add_member(name=name, roles=roles, administrator=admin)

    def text(self, key: str) -> str:
        return self.content[key]

    async def close(self) -> None:
        await self.app.db.close()


async def make_test_env(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    content: dict[str, str] | None = None,
    fill_content: bool = True,
    register: bool = True,
) -> TestEnv:
    settings = settings or make_settings()
    env = Env(
        discord_token="x" * 70,
        guild_id=GUILD_ID,
        owner_id=OWNER_ID,
        db_path=tmp_path / "bot.db",
        config_dir=tmp_path / "config",
        log_level="DEBUG",
        log_format="text",
        app_env="test",
        startup_failure_delay_seconds=0,
    )
    db = Database(env.db_path)
    await db.open()
    await db.run(apply_migrations, db_path=env.db_path)
    clock = FakeClock()
    app = App(
        env=env,
        settings=settings,
        content_values={},
        overrides={},
        db=db,
        clock=clock,
        modules=all_modules(),
        config_dir=env.config_dir,
    )
    values = fixture_content(app.content.registry) if fill_content else {}
    if content:
        values.update(content)
    app.content.replace(file_values=values)
    app.recompute_readiness()

    guild = FakeGuild(id=GUILD_ID)
    bot = FakeBot(app, guild)
    staff_role = guild.add_role(name="staff", position=10)
    await app.resources.remember("role", "staff", staff_role.id)
    if register:
        for module in app.modules:
            if module.register is not None:
                module.register(app)
    app.scheduler.bind(app)
    return TestEnv(app=app, bot=bot, guild=guild, clock=clock, content=values, staff_role=staff_role)


async def provision_channels(tenv: TestEnv) -> None:
    """Create every configured category/channel in the fake guild and record it (as /setup would)."""
    app, guild = tenv.app, tenv.guild
    categories: dict[str, Any] = {}
    for category in app.settings.server.categories:
        created = guild.add_category(name=f"cat-{category.key}")
        categories[category.key] = created
        await app.resources.remember("category", category.key, created.id)
    for channel in app.settings.server.channels:
        created = guild.add_text_channel(name=f"ch-{channel.key}", category=categories.get(channel.category))
        await app.resources.remember("channel", channel.key, created.id)
