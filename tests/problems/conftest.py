from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv
from tests.problems.util import make_env


@pytest.fixture
async def pr(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Report a Problem enabled (one issue type with a form, orders eligible, 'not about an order' allowed)."""
    env = await make_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()
