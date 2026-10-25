"""Load ``settings.toml`` and ``content.toml`` from the configuration directory."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from shopbot.config.content import load_content_file
from shopbot.config.settings import Settings, SettingsError, parse_settings

SETTINGS_FILE = "settings.toml"
CONTENT_FILE = "content.toml"


@dataclass(frozen=True)
class LoadedConfig:
    settings: Settings
    content_values: dict[str, str]
    config_dir: Path


def load_settings(path: Path) -> Settings:
    if not path.exists():
        raise SettingsError([f"{path}: settings file not found"])
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError([f"{path.name}: {exc}"]) from exc
    return parse_settings(data)


def load_config(config_dir: Path) -> LoadedConfig:
    """Raises ``SettingsError`` or ``ContentFileError`` with every problem found."""
    settings = load_settings(config_dir / SETTINGS_FILE)
    content_values = load_content_file(config_dir / CONTENT_FILE)
    return LoadedConfig(settings=settings, content_values=content_values, config_dir=config_dir)
