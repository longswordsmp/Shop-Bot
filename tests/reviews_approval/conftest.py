from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv
from tests.reviews_approval.util import make_approval_env


@pytest.fixture
async def ap(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Reviews with staff approval on (approval channel 'staff_chat'), every channel provisioned."""
    env = await make_approval_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()
