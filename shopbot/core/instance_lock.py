"""Single-instance guard.

Railway does not overlap deployments that mount the same volume, but a manual second process (or a
misconfigured replica count) must never process the same interactions and scheduled jobs twice. An
exclusive ``flock`` on a file next to the database enforces one running bot per database.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from typing import IO

from shopbot.logging_setup import log_event

log = logging.getLogger(__name__)


class InstanceLockError(Exception):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self, *, wait_seconds: float = 120.0, poll_seconds: float = 2.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + wait_seconds
        logged = False
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not logged:
                    log_event(log, logging.WARNING, "instance_lock_waiting", path=str(self.path))
                    logged = True
                if time.monotonic() >= deadline:
                    handle.close()
                    raise InstanceLockError(
                        f"another bot process holds {self.path}; refusing to start a second instance"
                    ) from None
                time.sleep(poll_seconds)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        log_event(log, logging.INFO, "instance_lock_acquired", path=str(self.path))

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
