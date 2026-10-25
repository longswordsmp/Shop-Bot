from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv, make_test_env


@pytest.fixture
async def tenv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Fully configured test environment: all features enabled, every content key filled with fixtures."""
    env = await make_test_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()
