"""Privileged intents: a requested intent the Developer Portal does not switch on is left out (the bot still starts),
and /status names it."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import ClassVar

import discord
import pytest

from shopbot.core import intents
from shopbot.core.intents import PrivilegedIntents, effective, portal_allows, report
from shopbot.features.admin import service as admin_service
from shopbot.features.admin import views as admin_views
from tests.helpers import TestEnv


def _flags(**values: bool) -> discord.ApplicationFlags:
    flags = discord.ApplicationFlags()
    for name, value in values.items():
        setattr(flags, name, value)
    return flags


def test_the_limited_flags_count_as_switched_on() -> None:
    assert portal_allows(_flags(gateway_message_content_limited=True, gateway_guild_members=True)) == PrivilegedIntents(
        message_content=True, members=True
    )
    assert portal_allows(_flags()) == PrivilegedIntents(message_content=False, members=False)


def test_a_requested_intent_the_portal_does_not_allow_is_left_out() -> None:
    requested = PrivilegedIntents(message_content=True, members=True)
    allowed = PrivilegedIntents(message_content=True, members=False)
    assert effective(requested, allowed) == PrivilegedIntents(message_content=True, members=False)
    assert report(requested, allowed) == ["members"]
    assert effective(requested, None) == requested and report(requested, None) == []  # unknown: as configured
    assert effective(PrivilegedIntents(), allowed) == PrivilegedIntents()  # never more than requested


async def test_status_names_an_intent_the_portal_does_not_allow(tenv: TestEnv) -> None:
    settings = tenv.app.settings
    tenv.app.settings = replace(settings, bot=replace(settings.bot, message_content_intent=True, members_intent=True))
    tenv.app.portal_intents = PrivilegedIntents(message_content=True, members=False)
    tenv.app.gateway_intents = effective(PrivilegedIntents(True, True), tenv.app.portal_intents)
    snapshot = await admin_service.status_snapshot(tenv.app)
    assert snapshot.intents_not_enabled == ("members",) and not snapshot.members_intent
    text = admin_views.status_overview(tenv.app.ui, tenv.app.op, snapshot).embeds[0]
    rendered = "\n".join(f.value for f in text.fields)
    assert tenv.app.op("admin.status.intent_not_enabled", intent="members") in rendered


class _FakeHTTP:
    """Stands in for discord.py's HTTPClient: ``answer`` is returned by application_info, or raised."""

    answer: object = {"flags": 0}
    login_delay = 0.0
    closed: ClassVar[list[bool]] = []

    def __init__(self, loop: object) -> None:
        del loop

    async def static_login(self, token: str) -> None:
        await asyncio.sleep(self.login_delay)

    async def application_info(self) -> object:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def close(self) -> None:
        _FakeHTTP.closed.append(True)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"flags": 1 << 15}, PrivilegedIntents(message_content=False, members=True)),  # members (limited)
        ({"flags": 1 << 19}, PrivilegedIntents(message_content=True, members=False)),
        ({"flags": None}, None),  # no flags: unknown, never "nothing allowed"
        ({}, None),
        (["not", "a", "dict"], None),
        ({"flags": "12"}, None),
        (ValueError("malformed JSON"), None),  # any error: connect as configured
        (KeyError("retry_after"), None),
    ],
)
async def test_the_portal_check_never_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, answer: object, expected: PrivilegedIntents | None
) -> None:
    monkeypatch.setattr(intents, "HTTPClient", _FakeHTTP)
    monkeypatch.setattr(_FakeHTTP, "answer", answer)
    _FakeHTTP.closed = []
    assert await intents.read_portal_intents("x" * 70) == expected
    assert _FakeHTTP.closed == [True]  # the session is always closed


async def test_the_portal_check_gives_up_after_its_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(intents, "HTTPClient", _FakeHTTP)
    monkeypatch.setattr(_FakeHTTP, "login_delay", 5.0)
    monkeypatch.setattr(intents, "PREFLIGHT_TIMEOUT_SECONDS", 0.05)
    _FakeHTTP.closed = []
    assert await intents.read_portal_intents("x" * 70) is None
    assert _FakeHTTP.closed == [True]
