"""SQLite access for an asyncio application.

Design:
- One connection, owned by one dedicated worker thread. Every database operation runs on that thread,
  so operations are serialized and never block the Discord event loop.
- A *unit of work* is a plain synchronous function ``fn(conn, *args)``. ``run`` executes it as-is
  (reads, single statements); ``transaction`` wraps it in ``BEGIN IMMEDIATE`` ... ``COMMIT`` and
  rolls back on any exception. Because a unit of work cannot ``await``, no other operation can
  interleave with it: check-then-write logic inside one ``transaction`` call is race-free.
- Durability: WAL journal, ``synchronous=FULL``, foreign keys on, busy timeout for external readers
  (backup tools, sqlite3 CLI).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

from shopbot.logging_setup import log_event

log = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

_BUSY_RETRIES = 5
_BUSY_BACKOFF_SECONDS = 0.2


class DatabaseError(Exception):
    pass


def _is_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc).lower() or "busy" in str(exc).lower())


def connect(path: Path | str, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open a connection with the application's pragmas. Used by the worker thread and offline tools."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=busy_timeout_ms / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if str(path) != ":memory:":
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            log_event(log, logging.WARNING, "db_wal_unavailable", journal_mode=str(mode))
    conn.execute("PRAGMA synchronous = FULL")
    # Deleted content is overwritten in the file instead of staying in free pages (e.g. the bytes of a released review
    # photo or a wiped gift card code); a backup taken earlier still holds it.
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def run_in_transaction(conn: sqlite3.Connection, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Execute ``fn`` inside ``BEGIN IMMEDIATE``/``COMMIT`` on the calling thread (with busy retries)."""
    attempt = 0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_busy(exc) and attempt < _BUSY_RETRIES:
                attempt += 1
                time.sleep(_BUSY_BACKOFF_SECONDS * attempt)
                continue
            raise
        try:
            result = fn(conn, *args, **kwargs)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        try:
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            conn.execute("ROLLBACK")
            if _is_busy(exc) and attempt < _BUSY_RETRIES:
                attempt += 1
                time.sleep(_BUSY_BACKOFF_SECONDS * attempt)
                continue
            raise
        return result


class Database:
    def __init__(self, path: Path | str, *, busy_timeout_ms: int = 5000) -> None:
        self.path = path
        self._busy_timeout_ms = busy_timeout_ms
        self._executor: ThreadPoolExecutor | None = None
        self._conn: sqlite3.Connection | None = None
        self._thread_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    async def open(self) -> None:
        if self._conn is not None:
            return
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        loop = asyncio.get_running_loop()

        def _open() -> sqlite3.Connection:
            self._thread_id = threading.get_ident()
            conn = connect(self.path, busy_timeout_ms=self._busy_timeout_ms)
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                conn.close()
                raise DatabaseError(f"database integrity check failed: {result}")
            return conn

        self._conn = await loop.run_in_executor(self._executor, _open)
        log_event(log, logging.INFO, "db_opened", path=str(self.path))

    def _require(self) -> tuple[ThreadPoolExecutor, sqlite3.Connection]:
        if self._executor is None or self._conn is None:
            raise DatabaseError("database is not open")
        return self._executor, self._conn

    async def run(self, fn: Callable[Concatenate[sqlite3.Connection, P], T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run ``fn(conn, ...)`` on the database thread without an explicit transaction."""
        executor, conn = self._require()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: fn(conn, *args, **kwargs))

    async def transaction(
        self, fn: Callable[Concatenate[sqlite3.Connection, P], T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Run ``fn(conn, ...)`` atomically. Any exception rolls back every write made by ``fn``."""
        executor, conn = self._require()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: run_in_transaction(conn, fn, *args, **kwargs))

    async def close(self) -> None:
        if self._executor is None:
            return
        executor, conn = self._executor, self._conn
        loop = asyncio.get_running_loop()
        if conn is not None:

            def _close() -> None:
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    conn.close()

            try:
                await loop.run_in_executor(executor, _close)
            except sqlite3.Error as exc:
                log_event(log, logging.WARNING, "db_close_error", error=str(exc))
        executor.shutdown(wait=True)
        self._executor = None
        self._conn = None
        log_event(log, logging.INFO, "db_closed")

    async def backup_to(self, destination: Path) -> Path:
        """Consistent online backup using SQLite's backup API."""
        destination.parent.mkdir(parents=True, exist_ok=True)

        def _backup(conn: sqlite3.Connection) -> Path:
            target = sqlite3.connect(str(destination))
            try:
                conn.backup(target)
            finally:
                target.close()
            return destination

        return await self.run(_backup)
