from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.checkout.util import FULL_SETTINGS, make_env
from tests.helpers import TestEnv


@pytest.fixture
async def co(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Shipped checkout settings (no options/forms/fees) with test business decisions; channels provisioned."""
    env = await make_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()


@pytest.fixture
async def cof(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Full workflow: fulfillment options (one with a fee and a form), a details form, a flat and a percent fee."""
    env = await make_env(tmp_path, FULL_SETTINGS)
    try:
        yield env
    finally:
        await env.close()
