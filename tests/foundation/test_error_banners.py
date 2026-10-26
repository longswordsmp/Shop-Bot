"""The shared error replies, built in one place (``Ui.error_message``) for the router (``Ctx.fail``), slash commands
(``reply_content_error``) and the previews: a real failure (the generic error, an error reference) carries the error
banner, a routine refusal that tells the customer what to do the gentler general response banner. Both keep the error
title and colour. The staff "command failed" reply carries the error banner too."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest
from discord import app_commands

from shopbot.commands import on_tree_error, reply_content_error
from shopbot.features import core_keys as ck
from shopbot.ui.routing import Ctx, HandlerError, Route
from tests.fakes import FakeInteraction, click
from tests.helpers import TestEnv, banner_names, make_banner_env


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await make_banner_env(tmp_path, {"bot": {"rate_limit_per_10s": 100}})
    try:
        yield tenv
    finally:
        await tenv.close()


def error_banner(tenv: TestEnv) -> list[str]:
    found = tenv.app.ui.banner("error")
    assert found is not None
    return [found.filename]


def refusal_banner(tenv: TestEnv) -> list[str]:
    found = tenv.app.ui.banner("general-response")
    assert found is not None and [found.filename] != error_banner(tenv)
    return [found.filename]


def _route(tenv: TestEnv, action: str, handler, **kwargs) -> str:  # type: ignore[no-untyped-def]
    tenv.app.router.add(Route("zz", action, handler, "component", **kwargs))
    return f"sb:zz:{action}"


def test_error_message_is_the_error_embed_above_its_banner(benv: TestEnv) -> None:
    ui = benv.app.ui
    # A routine refusal: the error embed (title, colour), closed by the general response banner.
    spec = ui.error_message(ck.ERROR_COOLDOWN, retry_after=3)
    assert spec is not None and spec.embeds == (ui.error_embed(ck.ERROR_COOLDOWN, retry_after=3),)
    assert spec.embeds[0].title == benv.text(ck.ERROR_TITLE) and spec.embeds[0].color == ui.color("danger")
    assert spec.banner is ui.banner("general-response") and spec.rows == () and spec.files is None
    # A real failure: the generic error (with or without its reference) or any reply with an error reference.
    for failure in (
        ui.error_message(ck.ERROR_GENERIC, error_id="abc"),
        ui.error_message(ck.ERROR_GENERIC),
        ui.error_message(ck.ERROR_UNAVAILABLE, error_id="abc"),
    ):
        assert failure is not None and failure.banner is ui.banner("error")
        assert failure.embeds[0].color == ui.color("danger")
    values = dict(benv.content)
    del values[ck.ERROR_NOT_FOUND]
    benv.app.content.replace(file_values=values)
    assert ui.error_message(ck.ERROR_NOT_FOUND) is None  # no text: nothing is sent, not even the banner


def test_error_message_has_no_banner_when_banners_are_off(tenv: TestEnv) -> None:
    spec = tenv.app.ui.error_message(ck.ERROR_GENERIC, error_id="abc")
    assert spec is not None and spec.banner is None


async def test_router_failures_carry_the_error_banner_and_refusals_the_general_one(benv: TestEnv) -> None:
    async def not_found(ctx: Ctx) -> None:
        raise HandlerError(ck.ERROR_NOT_FOUND)

    async def crash(ctx: Ctx) -> None:
        raise ValueError("internal")

    async def forbidden(ctx: Ctx) -> None:
        raise discord.Forbidden(type("R", (), {"status": 403, "reason": "Forbidden"})(), "no")

    async def never(ctx: Ctx) -> None:
        raise AssertionError("must not run")

    user = benv.member()
    failures = [
        await click(benv.app, user=user, custom_id=_route(benv, "crash", crash, feature=None)),
        await click(benv.app, user=user, custom_id=_route(benv, "forbidden", forbidden, feature=None)),
    ]
    refusals = [
        await click(benv.app, user=user, custom_id=_route(benv, "nf", not_found, feature=None)),
        await click(benv.app, user=user, custom_id=_route(benv, "staff", never, feature=None, access="staff")),
        await click(benv.app, user=user, custom_id="sb:nope:nothing"),  # unknown (stale) control
    ]
    for interactions, banner in ((failures, error_banner(benv)), (refusals, refusal_banner(benv))):
        for interaction in interactions:
            reply = interaction.last_sent
            assert reply is not None and reply.flags.ephemeral
            assert banner_names(reply) == banner and reply.embeds[0].description
            assert reply.embeds[0].title == benv.text(ck.ERROR_TITLE)

    # A failure after the click was acknowledged is a new private message, with the banner too.
    async def late(ctx: Ctx) -> None:
        await ctx.defer_update()
        await ctx.fail(ck.ERROR_INVALID_INPUT)

    deferred = await click(benv.app, user=user, custom_id=_route(benv, "late", late, feature=None))
    assert deferred.response_kinds() == ["defer"] and len(deferred.sent) == 1
    assert banner_names(deferred.sent[0]) == refusal_banner(benv)


async def test_slash_command_errors_carry_their_banner(benv: TestEnv) -> None:
    def interaction(user: object) -> FakeInteraction:
        return FakeInteraction(
            client=benv.bot, user=user, guild=benv.guild, type=discord.InteractionType.application_command
        )

    customer, staff = benv.member(), benv.member(name="staff", staff=True)
    denied = interaction(customer)
    await on_tree_error(denied, app_commands.CheckFailure("no"))  # type: ignore[arg-type]
    failed = interaction(customer)
    await on_tree_error(failed, app_commands.AppCommandError("boom"))  # type: ignore[arg-type]
    staff_failed = interaction(staff)
    await on_tree_error(staff_failed, app_commands.AppCommandError("boom"))  # type: ignore[arg-type]
    for sent in (failed, staff_failed):
        assert sent.last_sent is not None and banner_names(sent.last_sent) == error_banner(benv)
    assert denied.last_sent is not None and banner_names(denied.last_sent) == refusal_banner(benv)
    assert denied.last_sent is not None and denied.last_sent.embeds[0].description == benv.text(ck.ERROR_PERMISSION)
    staff_title = staff_failed.last_sent.embeds[0].title if staff_failed.last_sent is not None else None
    assert staff_title is not None and staff_title.startswith(benv.app.op("command.failed", error_id="X").split("X")[0])

    # No text: the deferred response is deleted; nothing (and no banner) remains.
    values = dict(benv.content)
    del values[ck.ERROR_UNAVAILABLE]
    benv.app.content.replace(file_values=values)
    silent = interaction(customer)
    await reply_content_error(silent, ck.ERROR_UNAVAILABLE)  # type: ignore[arg-type]
    assert silent.sent == [] and silent.response_kinds() == ["defer"]
