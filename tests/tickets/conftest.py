from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.fakes import FakeMember
from tests.helpers import TestEnv
from tests.tickets.util import form_overrides, make_env


@pytest.fixture(autouse=True)
def _hashable_fake_members(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real ``discord.Member`` objects are hashable (``hash(member._user)``); ``FakeMember`` has no ``_user``.
    ``core.channels.private_overwrites`` uses the bot member as a dict key, so give the fake the same
    behaviour for these tests (requested as a fix in tests/fakes.py)."""
    monkeypatch.setattr(FakeMember, "__hash__", lambda self: hash(self.id), raising=False)


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
