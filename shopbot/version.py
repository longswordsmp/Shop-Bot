"""The code version that is running: the git commit the deployment was built from.

Railway sets ``RAILWAY_GIT_COMMIT_SHA`` for deployments from GitHub; ``SOURCE_COMMIT`` and ``GIT_COMMIT`` (set by other
hosts and build setups) are accepted too, in that order. ``/status``, the ``/setup`` report and the ``starting`` log
line show the short form, so the owner can see whether a push reached the server (``docs/DEPLOYMENT.md``). Without
any of them (a local run, a deploy from the Railway CLI) the version is ``unknown``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

COMMIT_VARIABLES = ("RAILWAY_GIT_COMMIT_SHA", "SOURCE_COMMIT", "GIT_COMMIT")
UNKNOWN = "unknown"
SHORT_LENGTH = 7  # as GitHub shows a commit
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")


def commit(environ: Mapping[str, str] | None = None) -> str | None:
    """The full commit hash from the first of ``COMMIT_VARIABLES`` that holds one (lower case), or None. A value
    that is not a hexadecimal hash is skipped."""
    source = os.environ if environ is None else environ
    for name in COMMIT_VARIABLES:
        value = source.get(name, "").strip().lower()
        if _COMMIT_RE.match(value):
            return value
    return None


def short_commit(environ: Mapping[str, str] | None = None) -> str:
    """The running version as shown to the owner: the first 7 characters of the commit, or ``unknown``."""
    full = commit(environ)
    return full[:SHORT_LENGTH] if full else UNKNOWN
