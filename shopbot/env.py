"""Environment variable loading and validation.

Secrets are read from the process environment (Railway variables in production, a local ``.env``
file in development). Values are never logged; diagnostics name the variable only.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_LOG_FORMATS = frozenset({"json", "text"})
_APP_ENVS = frozenset({"production", "development", "test"})
_TRUE = frozenset({"1", "true", "yes"})
# Railway sets these on every deployment; RAILWAY_VOLUME_MOUNT_PATH is set only when a volume is attached.
_RAILWAY_MARKERS = ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_ENVIRONMENT_ID")


class EnvError(Exception):
    """Raised when required environment configuration is missing or invalid.

    ``problems`` lists one human-readable diagnostic per variable. Diagnostics never include values.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Env:
    discord_token: str = field(repr=False)
    guild_id: int
    owner_id: int | None
    db_path: Path
    config_dir: Path
    log_level: str
    log_format: str
    app_env: str
    startup_failure_delay_seconds: int
    # The key that encrypts submitted gift card codes (a Fernet key). Unset: a key file next to the database is used.
    gift_card_key: str | None = field(default=None, repr=False)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def _parse_snowflake(name: str, raw: str | None, problems: list[str], *, required: bool) -> int | None:
    if raw is None or raw.strip() == "":
        if required:
            problems.append(f"{name} is not set")
        return None
    value = raw.strip()
    if not value.isdigit() or not (15 <= len(value) <= 21) or int(value) == 0:
        problems.append(f"{name} must be a numeric Discord ID (15-21 digits)")
        return None
    return int(value)


def _volume_problem(env: Mapping[str, str], db_path: Path) -> str | None:
    """On Railway, a database outside the attached volume is lost on every redeploy: refuse to start."""
    if not any((env.get(name) or "").strip() for name in _RAILWAY_MARKERS):
        return None
    if (env.get("ALLOW_EPHEMERAL_DB") or "").strip().lower() in _TRUE:
        return None
    escape = "; set ALLOW_EPHEMERAL_DB=true only for a throwaway test service"
    mount = (env.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if not mount:
        return (
            "DB_PATH is not on persistent storage: no Railway volume is attached to this service, so every "
            "redeploy would start with an empty database (attach a volume at /data and set DB_PATH=/data/bot.db)"
            + escape
        )
    if not Path(os.path.abspath(db_path)).is_relative_to(os.path.abspath(mount)):
        return (
            f"DB_PATH is not inside the Railway volume mounted at {mount}, so every redeploy would start with an "
            f"empty database (set DB_PATH={mount.rstrip('/')}/bot.db)" + escape
        )
    return None


def load_env(environ: Mapping[str, str] | None = None, *, require_token: bool = True) -> Env:
    """Read and validate environment variables.

    ``require_token=False`` is used by offline tools (limits checker, preview) that never connect.
    """
    env = os.environ if environ is None else environ
    problems: list[str] = []

    token = (env.get("DISCORD_TOKEN") or "").strip()
    if require_token:
        if not token:
            problems.append("DISCORD_TOKEN is not set")
        elif token.startswith("replace-with") or len(token) < 50:
            problems.append("DISCORD_TOKEN does not look like a Discord bot token")

    guild_id = _parse_snowflake("GUILD_ID", env.get("GUILD_ID"), problems, required=require_token)
    owner_id = _parse_snowflake("OWNER_ID", env.get("OWNER_ID"), problems, required=False)

    db_raw = (env.get("DB_PATH") or "").strip()
    if not db_raw:
        if require_token:
            problems.append("DB_PATH is not set (use /data/bot.db on Railway)")
        db_raw = "./data/bot.db"
    db_path = Path(db_raw)
    if db_path.exists() and db_path.is_dir():
        problems.append("DB_PATH points to a directory; it must be a file path such as /data/bot.db")
    if require_token and (volume_problem := _volume_problem(env, db_path)):
        problems.append(volume_problem)

    config_dir = Path((env.get("CONFIG_DIR") or "./config").strip())

    log_level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}")
        log_level = "INFO"

    log_format = (env.get("LOG_FORMAT") or "json").strip().lower()
    if log_format not in _LOG_FORMATS:
        problems.append(f"LOG_FORMAT must be one of {sorted(_LOG_FORMATS)}")
        log_format = "json"

    app_env = (env.get("APP_ENV") or "production").strip().lower()
    if app_env not in _APP_ENVS:
        problems.append(f"APP_ENV must be one of {sorted(_APP_ENVS)}")
        app_env = "production"

    delay_raw = (env.get("STARTUP_FAILURE_DELAY_SECONDS") or "30").strip()
    if not delay_raw.isdigit() or int(delay_raw) > 600:
        problems.append("STARTUP_FAILURE_DELAY_SECONDS must be an integer between 0 and 600")
        delay_raw = "30"

    gift_card_key = (env.get("GIFT_CARD_KEY") or "").strip() or None
    if gift_card_key is not None and not _is_fernet_key(gift_card_key):
        problems.append(
            "GIFT_CARD_KEY must be a Fernet key (44 characters of URL-safe base64), or unset to use a key file next "
            "to the database"
        )

    if problems:
        raise EnvError(problems)

    return Env(
        discord_token=token,
        guild_id=guild_id or 0,
        owner_id=owner_id,
        db_path=db_path,
        config_dir=config_dir,
        log_level=log_level,
        log_format=log_format,
        app_env=app_env,
        startup_failure_delay_seconds=int(delay_raw),
        gift_card_key=gift_card_key,
    )


def _is_fernet_key(value: str) -> bool:
    """32 bytes, URL-safe base64 encoded (what ``cryptography.fernet.Fernet.generate_key()`` returns)."""
    try:
        return len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32 and len(value) == 44
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return False
