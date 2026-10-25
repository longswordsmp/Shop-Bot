"""Structured logging with secret redaction.

Use ``log_event(logger, level, event, **fields)`` for machine-readable events. Fields are emitted as
JSON keys in production. Anything that looks like a Discord token is redacted before output.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}")
_REDACTED = "[REDACTED]"
_RESERVED = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def redact(text: str) -> str:
    """``text`` with token-shaped strings replaced (for operator-visible output such as audit embeds)."""
    return _TOKEN_PATTERN.sub(_REDACTED, text)


class RedactingFilter(logging.Filter):
    """Removes known secret values and token-shaped strings from log records."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 8]

    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        return _TOKEN_PATTERN.sub(_REDACTED, text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format args; keep the raw template
            message = str(record.msg)
        record.msg = self._clean(message)
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _RESERVED and isinstance(value, str):
                setattr(record, key, self._clean(value))
        if record.exc_info and not record.exc_text:
            record.exc_text = self._clean(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._clean(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{datetime.fromtimestamp(record.created, tz=UTC).strftime('%H:%M:%S')} "
            f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        )
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_text:
            base += "\n" + record.exc_text
        return base


def configure_logging(level: str, fmt: str, secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    # discord.py is chatty at DEBUG; keep it at INFO unless explicitly debugging gateway issues.
    logging.getLogger("discord").setLevel(max(logging.getLevelName(level), logging.INFO))
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured event. ``fields`` must not contain secrets or full private form submissions."""
    extra: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        extra[key if key not in _RESERVED else f"f_{key}"] = value
    logger.log(level, event, extra=extra)
