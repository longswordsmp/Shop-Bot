from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.helpers import TestEnv
from tests.tickets.util import form_overrides, make_env


@pytest.fixture
async def tk(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Shipped ticket settings (one 'general' category, transcripts + staff alerts on), channels provisioned."""
    env = await make_env(tmp_path)
    try:
        yield env
    finally:
        await env.close()


@pytest.fixture
async def tkf(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Two categories: 'general' and 'billing' (form, category staff role, ping_staff, limit 2)."""
    env = await make_env(tmp_path, form_overrides())
    try:
        yield env
    finally:
        await env.close()
