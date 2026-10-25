from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.checkout.util import FULL_SETTINGS
from tests.customer_panel.util import env
from tests.helpers import TestEnv


@pytest.fixture
async def cp(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Checkout (no options/forms) and /myorders enabled, with fixture content and provisioned channels."""
    tenv = await env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


@pytest.fixture
async def cpf(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """The full workflow (fulfillment options, a details form, fees) with /myorders enabled."""
    tenv = await env(tmp_path, FULL_SETTINGS)
    try:
        yield tenv
    finally:
        await tenv.close()
