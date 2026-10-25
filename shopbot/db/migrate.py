"""Forward-only, transactional schema migrations.

Migrations live in ``shopbot/db/migrations/NNNN_name.sql``. Each pending file is applied inside its
own transaction and recorded in ``schema_migrations``. Before migrating a database that already holds
data, a backup copy is written next to it. If the database reports a version newer than this code
knows about (a downgrade), startup is refused rather than risking data loss.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from shopbot.logging_setup import log_event

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"invalid migration filename: {path.name}")
        found.append(Migration(int(match.group(1)), match.group(2), path))
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 1, got {versions}")
    return found


def split_statements(sql: str) -> list[str]:
    """Split a script into complete statements (handles triggers with BEGIN ... END)."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        stripped = line.strip()
        if not buffer and (not stripped or stripped.startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration ends with an incomplete statement")
    return statements


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _has_user_data(conn: sqlite3.Connection) -> bool:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_migrations'"
        )
    ]
    return any(conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone() for t in tables)  # noqa: S608


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    db_path: Path | None = None,
    migrations: list[Migration] | None = None,
) -> list[int]:
    """Apply all pending migrations. Returns the versions applied (possibly empty)."""
    available = migrations if migrations is not None else discover()
    latest_known = available[-1].version if available else 0
    version = current_version(conn)
    if version > latest_known:
        raise MigrationError(
            f"database schema version {version} is newer than this release supports ({latest_known}); "
            "refusing to start. Deploy the newer release or restore a matching backup."
        )
    pending = [m for m in available if m.version > version]
    if not pending:
        return []

    if db_path is not None and str(db_path) != ":memory:" and _has_user_data(conn):
        backup = db_path.with_name(f"{db_path.name}.pre-v{pending[0].version:04d}-{int(time.time())}.bak")
        target = sqlite3.connect(str(backup))
        try:
            conn.backup(target)
        finally:
            target.close()
        log_event(log, logging.INFO, "db_pre_migration_backup", path=str(backup))

    applied: list[int] = []
    for migration in pending:
        statements = split_statements(migration.path.read_text(encoding="utf-8"))
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, int(time.time())),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.version:04d}_{migration.name} failed: {exc}") from exc
        fk_problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_problems:
            raise MigrationError(f"migration {migration.version:04d} left {len(fk_problems)} foreign key violations")
        applied.append(migration.version)
        log_event(log, logging.INFO, "db_migration_applied", version=migration.version, name=migration.name)
    return applied
