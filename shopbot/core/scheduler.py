"""Persistent task scheduler.

Tasks are rows in ``scheduled_tasks``; a single background loop claims due tasks and runs the handler
registered for their ``kind``. Properties:
- survives restarts: interrupted ('running') tasks are returned to 'pending' at startup;
- no duplicates: ``dedupe_key`` is unique; claiming is a conditional UPDATE inside a transaction;
- retries with exponential backoff, then 'failed' (logged and recorded in the audit log);
- exactly one loop per process (``start`` is idempotent), and one process per database (instance lock).

Handlers must be idempotent: a task may run again if the process stops between the handler finishing
and the task being marked done. Handlers re-check current state before acting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from shopbot.core.clock import Clock
from shopbot.db.database import Database
from shopbot.db.repos import tasks as task_repo
from shopbot.db.repos.tasks import Task
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TaskHandler = Callable[["App", Task], Awaitable[None]]
_MAX_BACKOFF_SECONDS = 3600


class PermanentTaskError(Exception):
    """Raise from a handler when retrying cannot help (the task is marked failed immediately)."""


class Scheduler:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        *,
        poll_seconds: int,
        batch_size: int,
        max_attempts: int,
        retry_base_seconds: int,
    ) -> None:
        self._db = db
        self._clock = clock
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self._handlers: dict[str, TaskHandler] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._app: App | None = None

    def register(self, kind: str, handler: TaskHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"task handler for {kind!r} registered twice")
        self._handlers[kind] = handler

    @property
    def kinds(self) -> list[str]:
        return sorted(self._handlers)

    async def schedule(
        self,
        kind: str,
        *,
        run_at: int,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        replace: bool = False,
        max_attempts: int | None = None,
    ) -> int | None:
        if kind not in self._handlers:
            raise ValueError(f"no handler registered for task kind {kind!r}")
        task_id = await self._db.transaction(
            task_repo.schedule,
            kind=kind,
            run_at=run_at,
            payload=payload or {},
            max_attempts=max_attempts or self.max_attempts,
            dedupe_key=dedupe_key,
            replace=replace,
            now=self._clock.now(),
        )
        if task_id is not None and run_at <= self._clock.now():
            self._wake.set()
        return task_id

    async def cancel(self, dedupe_key: str) -> int:
        return await self._db.transaction(task_repo.cancel, dedupe_key=dedupe_key, now=self._clock.now())

    async def cancel_prefix(self, prefix: str) -> int:
        return await self._db.transaction(task_repo.cancel_prefix, prefix, self._clock.now())

    async def recover(self) -> int:
        count = await self._db.transaction(task_repo.recover_interrupted, self._clock.now())
        if count:
            log_event(log, logging.WARNING, "scheduler_recovered_interrupted_tasks", count=count)
        return count

    def start(self, app: App) -> None:
        if self._task is not None and not self._task.done():
            return
        self._app = app
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="shopbot-scheduler")
        log_event(log, logging.INFO, "scheduler_started", kinds=self.kinds)

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30)
            except TimeoutError:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        log_event(log, logging.INFO, "scheduler_stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_due()
            except Exception:
                log.exception("scheduler iteration failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)

    async def run_due(self) -> int:
        """Claim and run due tasks once. Returns the number of tasks processed (used by tests)."""
        if self._app is None:
            raise RuntimeError("scheduler is not bound to an app; call start() or bind() first")
        claimed = await self._db.transaction(task_repo.claim_due, self._clock.now(), self.batch_size)
        for task in claimed:
            if self._stopping.is_set():
                # Leave remaining claimed tasks for recovery on next start.
                break
            await self._execute(task)
        return len(claimed)

    def bind(self, app: App) -> None:
        """Attach the app without starting the loop (tests, offline tools)."""
        self._app = app

    async def _execute(self, task: Task) -> None:
        handler = self._handlers.get(task.kind)
        now = self._clock.now()
        if handler is None:
            log_event(log, logging.ERROR, "scheduler_no_handler", task_id=task.id, kind=task.kind)
            await self._db.transaction(task_repo.mark_failed_attempt, task.id, "no handler", None, now)
            return
        if self._app is None:
            raise RuntimeError("scheduler is not bound to an app")
        try:
            await handler(self._app, task)
        except PermanentTaskError as exc:
            await self._fail(task, str(exc) or type(exc).__name__, permanent=True)
            return
        except Exception as exc:
            log.exception("scheduled task %s (%s) failed", task.id, task.kind)
            await self._fail(task, f"{type(exc).__name__}: {exc}", permanent=False)
            return
        await self._db.transaction(task_repo.mark_done, task.id, self._clock.now())
        log_event(log, logging.INFO, "scheduled_task_done", task_id=task.id, kind=task.kind)

    async def _fail(self, task: Task, error: str, *, permanent: bool) -> None:
        now = self._clock.now()
        exhausted = permanent or task.attempts >= task.max_attempts
        retry_at = (
            None
            if exhausted
            else now + min(_MAX_BACKOFF_SECONDS, self.retry_base_seconds * 2 ** max(0, task.attempts - 1))
        )
        await self._db.transaction(task_repo.mark_failed_attempt, task.id, error, retry_at, now)
        level = logging.ERROR if exhausted else logging.WARNING
        log_event(
            log,
            level,
            "scheduled_task_failed",
            task_id=task.id,
            kind=task.kind,
            attempts=task.attempts,
            permanent=exhausted,
            error=error[:300],
        )
        if exhausted and self._app is not None:
            with contextlib.suppress(Exception):
                await self._app.audit.record(
                    "task.failed",
                    actor_id=None,
                    target_type="task",
                    target_id=task.id,
                    details={"kind": task.kind, "attempts": task.attempts, "error": error[:300]},
                )
