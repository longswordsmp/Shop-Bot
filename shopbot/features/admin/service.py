"""Admin logic: diagnostics snapshots, content overrides, configuration reload, backups, maintenance.

Functions here gather data and perform state changes; they never build Discord messages (see ``views``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import difflib
import logging
import re
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from shopbot.config.content import validate_value
from shopbot.config.content_keys import ContentKey
from shopbot.config.readiness import FeatureStatus, Problem, Readiness
from shopbot.config.settings import Settings
from shopbot.core.intents import PrivilegedIntents
from shopbot.core.intents import report as intents_report
from shopbot.core.panels import withdraw_and_audit
from shopbot.core.permissions import BOT_PERMISSIONS, missing_permissions
from shopbot.db.repos import state as state_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features.admin import repo as admin_repo
from shopbot.logging_setup import log_event
from shopbot.version import UNKNOWN, short_commit

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.base import FeatureModule

log = logging.getLogger(__name__)

FEATURE = "admin"
CONTENT_PROBLEM_KINDS = ("missing_content", "invalid_content")

BACKUP_KEEP = 10
BACKUP_DIR_NAME = "backups"
BACKUP_PREFIX = "bot"
# Only /backup files are rotated (``bot-<stamp>[-n].db``).
BACKUP_NAME_RE = re.compile(r"^bot-(\d{8}T\d{6}Z)(?:-(\d+))?\.db$")

MAINTENANCE_KIND = "admin.maintenance"
MAINTENANCE_DEDUPE = "admin.maintenance"
MAINTENANCE_INTERVAL_SECONDS = 86_400
MAINTENANCE_FIRST_RUN_DELAY_SECONDS = 300
TASK_RETENTION_SECONDS = 30 * 86_400


# --------------------------------------------------------------------------------------------------
# Readiness / status
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureLine:
    name: str
    enabled: bool
    ready: bool
    problems: int


def feature_line(status: FeatureStatus) -> FeatureLine:
    return FeatureLine(status.name, status.enabled, status.ready, len(status.problems))


def feature_lines(readiness: Readiness) -> tuple[FeatureLine, ...]:
    return tuple(feature_line(s) for s in readiness.statuses.values())


@dataclass(frozen=True)
class StatusSnapshot:
    features: tuple[FeatureLine, ...]
    unknown_keys: tuple[str, ...]
    guild_available: bool
    missing_permissions: tuple[tuple[str, tuple[str, ...]], ...]
    message_content_intent: bool
    # Requested in the settings but not switched on in the Developer Portal (left out when connecting).
    intents_not_enabled: tuple[str, ...]
    members_intent: bool
    transcripts_need_intent: bool
    task_counts: Mapping[str, int]
    db_path: str
    # Banner and icon pictures that could not be used (missing, unreadable, too large): that kind of message shows no
    # banner, that icon shows nothing.
    picture_problems: tuple[str, ...] = ()
    # The running code version: the short commit of the deployment (``shopbot.version``), or "unknown".
    version: str = UNKNOWN


def transcripts_enabled(settings: Settings) -> bool:
    flags = settings.features
    return bool(
        (flags.tickets and settings.tickets.transcript_channel)
        or (flags.checkout and settings.checkout.transcript_on_close)
    )


def required_capabilities(settings: Settings) -> list[str]:
    """Permission capabilities (keys of ``BOT_PERMISSIONS``) the enabled features rely on."""
    flags = settings.features
    capabilities = ["base", "setup"]
    if flags.tickets or flags.checkout:
        capabilities.append("tickets")  # private ticket and order channels
    if transcripts_enabled(settings):
        capabilities.append("transcripts")
    if flags.catalog:
        capabilities.append("catalog")
    return capabilities


def bot_missing_permissions(app: App) -> tuple[bool, tuple[tuple[str, tuple[str, ...]], ...]]:
    """(guild available, [(capability, missing permission names)]) using the bot's server-wide permissions."""
    guild = app.guild
    me = guild.me if guild is not None else None
    if me is None:
        return False, ()
    missing: list[tuple[str, tuple[str, ...]]] = []
    for capability in required_capabilities(app.settings):
        names = missing_permissions(me, None, BOT_PERMISSIONS[capability])
        if names:
            missing.append((capability, tuple(names)))
    return True, tuple(missing)


async def status_snapshot(app: App) -> StatusSnapshot:
    guild_available, missing = bot_missing_permissions(app)
    counts = await app.db.run(admin_repo.task_counts)
    bot = app.settings.bot
    return StatusSnapshot(
        features=feature_lines(app.readiness),
        unknown_keys=tuple(app.readiness.unknown_content_keys),
        guild_available=guild_available,
        missing_permissions=missing,
        message_content_intent=app.gateway_intents.message_content,
        members_intent=app.gateway_intents.members,
        transcripts_need_intent=transcripts_enabled(app.settings) and not app.gateway_intents.message_content,
        intents_not_enabled=tuple(
            intents_report(PrivilegedIntents(bot.message_content_intent, bot.members_intent), app.portal_intents)
        ),
        task_counts=counts,
        db_path=str(app.env.db_path),
        picture_problems=(*app.banners.problems, *app.icons.problems),
        version=short_commit(),
    )


def feature_names(app: App) -> list[str]:
    return [module.name for module in app.modules]


def feature_status(app: App, feature: str) -> FeatureStatus | None:
    return app.readiness.statuses.get(feature)


# --------------------------------------------------------------------------------------------------
# Content inspection
# --------------------------------------------------------------------------------------------------


def shared_required_keys(app: App) -> set[str]:
    """Optional shared keys that are required because an enabled feature displays them."""
    keys: set[str] = set()
    for module in app.modules:
        if app.readiness.is_enabled(module.name):
            keys.update(module.shared_keys(app.settings))
    return keys


def missing_content(app: App, feature: str | None) -> list[tuple[str, list[Problem]]]:
    """Missing/invalid content problems grouped by feature (enabled features unless one is named)."""
    statuses = app.readiness.statuses
    names = [feature] if feature is not None else [n for n, s in statuses.items() if s.enabled]
    grouped: list[tuple[str, list[Problem]]] = []
    for name in names:
        status = statuses.get(name)
        if status is None:
            continue
        problems = [p for p in status.problems if p.kind in CONTENT_PROBLEM_KINDS]
        if problems:
            grouped.append((name, problems))
    return grouped


@dataclass(frozen=True)
class KeyRow:
    key: str
    kind: str
    required: bool
    source: str | None
    via_fallback: bool
    invalid: bool
    fallback: str | None = None


def key_rows(app: App, feature: str | None) -> list[tuple[str, list[KeyRow]]]:
    content = app.content
    shared = shared_required_keys(app)
    invalid = {vp.key for vp in content.value_problems()}
    names = [feature] if feature is not None else feature_names(app)
    known = set(names)
    if feature is None:
        names += sorted({d.feature for d in content.registry.all()} - known)
    grouped: list[tuple[str, list[KeyRow]]] = []
    for name in names:
        rows = [
            KeyRow(
                key=d.key,
                kind=d.kind.value,
                required=d.required or d.key in shared,
                source=content.source(d.key),
                via_fallback=content.raw(d.key) is None and content.get(d.key) is not None,
                invalid=d.key in invalid,
                fallback=d.fallback,
            )
            for d in content.registry.for_feature(name)
        ]
        if rows:
            grouped.append((name, rows))
    return grouped


@dataclass(frozen=True)
class KeyInfo:
    definition: ContentKey
    raw: str | None  # configured for exactly this key (override or file)
    source: str | None
    fallback_used: str | None
    required: bool
    problems: tuple[str, ...]
    effective: str | None = None  # what customers see: ``raw``, else the fallback's value

    @property
    def shown(self) -> str | None:
        return self.raw if self.raw is not None else self.effective


def key_info(app: App, key: str) -> KeyInfo | None:
    content = app.content
    definition = content.registry.get(key)
    if definition is None:
        return None
    raw = content.raw(key)
    effective = content.get(key)
    fallback_used = definition.fallback if raw is None and effective is not None else None
    return KeyInfo(
        definition=definition,
        raw=raw,
        source=content.source(key),
        fallback_used=fallback_used,
        required=definition.required or key in shared_required_keys(app),
        problems=tuple(validate_value(definition, raw, app.content.icon_names())) if raw is not None else (),
        effective=effective,
    )


def suggest_keys(app: App, query: str, *, limit: int = 25) -> list[str]:
    """Registered keys matching ``query`` (case-insensitive): keys starting with it, keys containing it, then keys
    whose current text contains it (so a few words copied from a message find the key that shows them)."""
    needle = query.strip().lower()
    keys = [d.key for d in app.content.registry.all()]
    if not needle:
        return keys[:limit]
    prefix = [k for k in keys if k.startswith(needle)]
    inside = [k for k in keys if needle in k and not k.startswith(needle)]
    named = set(prefix) | set(inside)
    phrase = _searchable(needle)  # words copied from a message: without the markdown the message showed
    texts = {k: _searchable(app.content.raw(k)) for k in keys if k not in named}
    exact = [k for k, text in texts.items() if phrase in text]
    words = [k for k, text in texts.items() if phrase not in text and all(w in text for w in phrase.split())]
    return (prefix + inside + exact + words)[:limit]


async def _has_posted_panel(app: App, module: FeatureModule) -> bool:
    for prefix in module.panel_prefixes:
        if await app.db.run(state_repo.published_with_prefix, app.env.guild_id, prefix):
            return True
    return False


_PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKDOWN = re.compile(r"[*_~|`>]")


def _searchable(value: str | None) -> str:
    """A value as searched by text: lower case, single spaces, without {placeholder} names (icons included) and
    without markdown (``**bold**``, links, quotes), so words copied from the message as it is shown find it."""
    text = _PLACEHOLDER.sub(" ", value or "")
    text = _MARKDOWN.sub("", _MARKDOWN_LINK.sub(r"\1", text))
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class PanelRefresh:
    """What refreshing the posted panels did: each refreshed feature's publish report lines (which also name a panel
    that could not be posted, e.g. a missing permission), and the features whose refresh raised (logged with
    ``error_id``)."""

    lines: tuple[str, ...] = ()
    failed: int = 0
    error_id: str | None = None


async def refresh_panels(app: App) -> PanelRefresh:
    """Update the posted panels of every ready feature (unchanged ones are left alone), e.g. after a text was edited.

    Only features that already have a posted panel are refreshed: posting new panels (and creating their channels)
    stays the job of /setup. One failure never stops the rest."""
    lines: list[str] = []
    failed = 0
    error_id: str | None = None
    for module in app.modules:
        if module.publish is None or not app.ready(module.name) or not await _has_posted_panel(app, module):
            continue
        try:
            lines += await module.publish(app)
        except Exception as exc:
            failed += 1
            error_id = error_id or uuid.uuid4().hex[:12]
            log_event(
                log,
                logging.ERROR,
                "panel_refresh_failed",
                feature=module.name,
                error_id=error_id,
                error=type(exc).__name__,
            )
            log.exception("panel refresh failed for %s (%s)", module.name, error_id)
    return PanelRefresh(tuple(lines), failed, error_id)


def close_keys(app: App, key: str, *, limit: int = 5) -> list[str]:
    keys = [d.key for d in app.content.registry.all()]
    return difflib.get_close_matches(key, keys, n=limit, cutoff=0.6)


# --------------------------------------------------------------------------------------------------
# Content changes
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadinessChange:
    feature: str
    before: FeatureLine
    after: FeatureLine

    @property
    def changed(self) -> bool:
        b, a = self.before, self.after
        return (b.enabled, b.ready, b.problems) != (a.enabled, a.ready, a.problems)


def readiness_changes(before: Readiness, after: Readiness, focus: str) -> list[ReadinessChange]:
    """The focused feature's status plus every feature whose readiness changed (focused first)."""
    changes: list[ReadinessChange] = []
    for name, status in after.statuses.items():
        new = feature_line(status)
        old_status = before.statuses.get(name)
        old = feature_line(old_status) if old_status is not None else new
        change = ReadinessChange(name, old, new)
        if name == focus or change.changed:
            changes.append(change)
    changes.sort(key=lambda c: c.feature != focus)
    return changes


def check_value(app: App, definition: ContentKey, value: str) -> list[str]:
    """Problems that prevent saving ``value`` for ``definition`` (empty list: valid)."""
    if value == "":
        return [app.op("admin.content.problem_empty")]
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return [app.op("admin.content.problem_encoding")]
    return validate_value(definition, value, app.content.icon_names())


@dataclass(frozen=True)
class SaveResult:
    key: str
    saved: bool
    problems: tuple[str, ...] = ()
    changes: tuple[ReadinessChange, ...] = ()


async def save_override(app: App, key: str, value: str, *, actor_id: int) -> SaveResult:
    """Validate and store ``value`` exactly as given as the override for ``key``."""
    definition = app.content.registry.get(key)
    if definition is None:
        raise KeyError(key)
    problems = check_value(app, definition, value)
    if problems:
        return SaveResult(key=key, saved=False, problems=tuple(problems))
    before = app.readiness
    await app.db.transaction(state_repo.override_set, key, value, actor_id, app.clock.now())
    app.content.set_override(key, value)
    after = app.recompute_readiness()
    changes = readiness_changes(before, after, definition.feature)
    await app.audit.record(
        "admin.content_set",
        actor_id=actor_id,
        target_type="content_key",
        target_id=key,
        details={"key": key, "length": len(value), "feature": definition.feature},
    )
    # A valid value can still break a rule across keys (e.g. the longest possible ticket channel name).
    await withdraw_and_audit(app, actor_id=actor_id, reason="content_set")
    return SaveResult(key=key, saved=True, changes=tuple(changes))


@dataclass(frozen=True)
class ClearResult:
    key: str
    removed: bool
    source_after: str | None
    changes: tuple[ReadinessChange, ...] = ()


def clearable(app: App, key: str, overrides: Mapping[str, str]) -> bool:
    return key in app.content.registry or key in overrides


async def clear_override(app: App, key: str, *, actor_id: int) -> ClearResult:
    """Remove the database override for ``key`` (the file value, if any, applies again)."""
    before = app.readiness
    removed = await app.db.transaction(state_repo.override_delete, key)
    app.content.clear_override(key)
    after = app.recompute_readiness()
    definition = app.content.registry.get(key)
    focus = definition.feature if definition is not None else ""
    changes = readiness_changes(before, after, focus) if removed else []
    source_after = app.content.source(key) if definition is not None else None
    if removed:
        await app.audit.record(
            "admin.content_clear",
            actor_id=actor_id,
            target_type="content_key",
            target_id=key,
            details={"key": key},
        )
        # Clearing required text can make a feature not ready: its live panels are withdrawn at once.
        await withdraw_and_audit(app, actor_id=actor_id, reason="content_clear")
    return ClearResult(key=key, removed=removed, source_after=source_after, changes=tuple(changes))


def export_values(app: App) -> dict[str, str]:
    """Every registered key with its effective configured value (override wins; unset keys are ''), plus every
    other configured value (``operator.*`` overrides and keys not registered for the current settings) so an
    export never loses text."""
    values = app.content.all_configured_values()
    for definition in app.content.registry.all():
        values.setdefault(definition.key, "")
    return values


# --------------------------------------------------------------------------------------------------
# Reload
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReloadReport:
    ok: bool
    errors: tuple[str, ...]
    features: tuple[FeatureLine, ...]


def changed_sections(before: Settings, after: Settings) -> list[str]:
    """Top-level settings sections (``[checkout]``, ``[server]``, ...) whose values differ, in schema order."""
    return [f.name for f in dataclasses.fields(Settings) if getattr(before, f.name) != getattr(after, f.name)]


def changed_content_count(before: Mapping[str, str], after: Mapping[str, str]) -> int:
    """How many content keys were added, removed or changed (values are never reported)."""
    return sum(1 for key in before.keys() | after.keys() if before.get(key) != after.get(key))


async def reload_configuration(app: App, *, actor_id: int) -> ReloadReport:
    settings_before = app.settings
    content_before = app.content.all_configured_values()
    result = await app.reload_config()
    # What changed is audited by section name and count only: settings values and text are never logged.
    await app.audit.record(
        "admin.reload",
        actor_id=actor_id,
        target_type="config",
        target_id="reload",
        details={
            "ok": result.ok,
            "errors": len(result.errors),
            "settings_sections_changed": changed_sections(settings_before, app.settings),
            "content_keys_changed": changed_content_count(content_before, app.content.all_configured_values()),
        },
    )
    return ReloadReport(ok=result.ok, errors=tuple(result.errors), features=feature_lines(app.readiness))


# --------------------------------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupReport:
    path: Path
    size: int
    kept: int
    removed: tuple[Path, ...]


class BackupUnavailable(Exception):
    """The database is not a file (e.g. in-memory), so there is nothing to back up to disk."""


def backup_directory(db_path: Path) -> Path:
    return db_path.parent / BACKUP_DIR_NAME


def backup_stamp(now: int) -> str:
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def next_backup_path(directory: Path, now: int, prefix: str = BACKUP_PREFIX) -> Path:
    """``<prefix>-<UTC timestamp>.db`` in ``directory`` (a numeric suffix avoids overwriting a same-second file)."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = backup_stamp(now)
    candidate = directory / f"{prefix}-{stamp}.db"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{prefix}-{stamp}-{counter}.db"
        counter += 1
    return candidate


def list_backups(directory: Path) -> list[Path]:
    """Backup files created by /backup, oldest first (other files in the directory are ignored)."""
    if not directory.is_dir():
        return []
    entries: list[tuple[tuple[str, int], Path]] = []
    for path in directory.iterdir():
        match = BACKUP_NAME_RE.match(path.name)
        if match and path.is_file():
            entries.append(((match.group(1), int(match.group(2) or 0)), path))
    entries.sort()
    return [path for _, path in entries]


def rotate_backups(directory: Path, keep: int = BACKUP_KEEP) -> tuple[list[Path], list[Path]]:
    """Delete all but the newest ``keep`` backups. Returns (kept, removed)."""
    backups = list_backups(directory)
    removed = backups[:-keep] if len(backups) > keep else []
    for path in removed:
        path.unlink(missing_ok=True)
    return backups[len(removed) :], removed


async def create_backup(app: App, *, actor_id: int, prefix: str = BACKUP_PREFIX) -> BackupReport:
    """Consistent online copy of the database into ``<DB_PATH parent>/backups``, keeping the newest 10 /backup files.
    Another ``prefix`` (the copy /reset makes first) names a file the rotation never removes."""
    db_path = Path(app.env.db_path)
    if str(app.env.db_path) == ":memory:":
        raise BackupUnavailable()
    directory = backup_directory(db_path)
    path = await asyncio.to_thread(next_backup_path, directory, app.clock.now(), prefix)
    try:
        await app.db.backup_to(path)
    except (sqlite3.Error, OSError):
        with contextlib.suppress(OSError):
            await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    size = (await asyncio.to_thread(path.stat)).st_size
    kept, removed = await asyncio.to_thread(rotate_backups, directory, BACKUP_KEEP)
    log_event(log, logging.INFO, "db_backup_created", path=str(path), size=size, removed=len(removed))
    await app.audit.record(
        "admin.backup",
        actor_id=actor_id,
        target_type="database",
        target_id=path.name,
        details={"file": path.name, "size": size, "removed": len(removed)},
    )
    return BackupReport(path=path, size=size, kept=len(kept), removed=tuple(removed))


# --------------------------------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MaintenanceResult:
    tasks_purged: int
    audit_purged: int


async def run_maintenance(app: App) -> MaintenanceResult:
    """Purge finished scheduled tasks older than 30 days and audit events past the retention period."""
    now = app.clock.now()
    tasks_purged = await app.db.transaction(task_repo.purge_finished, now - TASK_RETENTION_SECONDS)
    audit_purged = 0
    retention_days = app.settings.audit.retention_days
    if retention_days > 0:
        audit_purged = await app.db.transaction(admin_repo.purge_audit_before, now - retention_days * 86_400)
    log_event(log, logging.INFO, "admin_maintenance", tasks_purged=tasks_purged, audit_purged=audit_purged)
    if tasks_purged or audit_purged:
        await app.audit.record(
            "admin.maintenance",
            actor_id=None,
            target_type="maintenance",
            target_id="daily",
            details={"tasks_purged": tasks_purged, "audit_purged": audit_purged, "retention_days": retention_days},
        )
    return MaintenanceResult(tasks_purged=tasks_purged, audit_purged=audit_purged)


async def ensure_maintenance_scheduled(app: App) -> bool:
    """Schedule the daily maintenance task unless one is already pending/running. Returns True if scheduled."""
    now = app.clock.now()
    return await app.db.transaction(
        admin_repo.ensure_task,
        kind=MAINTENANCE_KIND,
        dedupe_key=MAINTENANCE_DEDUPE,
        run_at=now + MAINTENANCE_FIRST_RUN_DELAY_SECONDS,
        max_attempts=app.scheduler.max_attempts,
        now=now,
    )
