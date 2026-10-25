from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv
from tests.operations.util import make_ops


@pytest.fixture
async def ops(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Checkout + operations ready: staff alerts in staff_alerts (staff role pinged), Owner alerts on, customer role."""
    env = await make_ops(tmp_path)
    try:
        assert env.app.ready("operations"), env.app.readiness.statuses["operations"].problems
        yield env
    finally:
        await env.close()
