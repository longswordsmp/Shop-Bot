"""Helpers for the START HERE guide tests (#how-to-buy, #faq, #shipping)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.config.readiness import build_registry
from shopbot.config.settings import Settings
from tests.fakes import FakeMessage, FakeTextChannel
from tests.helpers import (
    TestEnv,
    deep_merge,
    make_banner_env,
    make_settings,
    make_test_env,
    provision_channels,
    server_extension,
)

GUIDE_CHANNELS = ("how_to_buy", "faq", "shipping", "purchase")

BASE_OVERRIDES: dict[str, Any] = {
    "features": {"how_to_buy": True, "faq": True, "shipping": True},
    "how_to_buy": {"channel": "how_to_buy", "steps": 3, "link_channels": ["catalog", "purchase", "faq"]},
    "faq": {
        "channel": "faq",
        "sections": [{"id": "general", "questions": 2}, {"id": "orders", "questions": 1}],
    },
    "shipping": {
        "channel": "shipping",
        "groups": [{"id": "north", "countries": ["CA", "US", "MX"]}, {"id": "europe", "countries": ["DE", "FR"]}],
        "excluded_areas": {"US": ["AK", "ND"]},
        "not_available": ["CU", "RU"],
        "info_sections": 2,
    },
}


def guide_overrides(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    channels = [{"key": key, "category": "support", "access": "inherit"} for key in GUIDE_CHANNELS]
    data = deep_merge(server_extension(channels=channels), BASE_OVERRIDES)
    return deep_merge(data, overrides) if overrides else data


def guide_settings(overrides: dict[str, Any] | None = None) -> Settings:
    return make_settings(guide_overrides(overrides))


async def guide_env(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    content: dict[str, str] | None = None,
    provision: bool = True,
    banners: bool = False,
) -> TestEnv:
    """``banners`` switches banners on with a tiny picture per banner id (``make_banner_env``)."""
    if banners:
        tenv = await make_banner_env(tmp_path, guide_overrides(overrides), content=content)
    else:
        tenv = await make_test_env(tmp_path, settings=guide_settings(overrides), content=content)
    if provision:
        await provision_channels(tenv)
    return tenv


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel), key
    return found


def messages(tenv: TestEnv, key: str) -> list[FakeMessage]:
    return list(channel(tenv, key).messages)


def apply_settings(tenv: TestEnv, settings: Settings, content: dict[str, str] | None = None) -> None:
    """Switch the running app to new settings (as /reload does), keeping fixture text for the new keys."""
    from tests.helpers import fixture_content

    app = tenv.app
    app.settings = settings
    registry = build_registry(settings, app.modules)
    values = fixture_content(registry)
    values.update(content or {})
    tenv.content.clear()
    tenv.content.update(values)
    app.content.replace(registry=registry, file_values=values)
    app.recompute_readiness()


def problems(tenv: TestEnv, feature: str) -> list[str]:
    return [p.message for p in tenv.app.readiness.statuses[feature].problems]


def problem_keys(tenv: TestEnv, feature: str) -> list[str | None]:
    return [p.key for p in tenv.app.readiness.statuses[feature].problems]
