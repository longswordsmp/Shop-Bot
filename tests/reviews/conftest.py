from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv
from tests.reviews.util import make_env


@pytest.fixture
async def rv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """All features enabled (rating scale 5, no prompt delay, comments allowed), every channel provisioned."""
    env = await make_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()
