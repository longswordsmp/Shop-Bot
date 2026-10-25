from __future__ import annotations

import logging

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.logging_setup import RedactingFilter
from shopbot.ui.routing import Ctx, HandlerError, Route
from shopbot.ui.spec import EmbedSpec, MessageSpec
from tests.fakes import FakeInteraction, click, submit_modal
from tests.helpers import GUILD_ID, TestEnv


def _register(tenv: TestEnv, action: str, handler, **kwargs) -> str:  # type: ignore[no-untyped-def]
    tenv.app.router.add(Route("zz", action, handler, kwargs.pop("kind", "component"), **kwargs))
    return f"sb:zz:{action}"


async def test_handler_runs_and_can_update(tenv: TestEnv) -> None:
    seen: list[tuple[str, ...]] = []

    async def handler(ctx: Ctx) -> None:
        seen.append(ctx.args)
        await ctx.respond(MessageSpec(embeds=(EmbedSpec(title="ok"),)))

    cid = _register(tenv, "go", handler, feature=None)
    user = tenv.member()
    interaction = await click(tenv.app, user=user, custom_id=cid + ":7")
    assert seen == [("7",)]
    assert interaction.last_sent is not None and interaction.last_sent.embeds[0].title == "ok"


async def test_foreign_guild_is_ignored(tenv: TestEnv) -> None:
    ran: list[bool] = []

    async def handler(ctx: Ctx) -> None:
        ran.append(True)

    cid = _register(tenv, "fg", handler, feature=None)
    user = tenv.member()
    interaction = FakeInteraction(client=tenv.bot, user=user, guild=tenv.guild)
    interaction.guild_id = GUILD_ID + 1
    await tenv.app.router.dispatch(interaction, cid, kind="component")
    assert ran == [] and interaction.response_kinds() == ["defer"]


async def test_dm_only_for_allowed_routes(tenv: TestEnv) -> None:
    ran: list[str] = []

    async def handler(ctx: Ctx) -> None:
        ran.append(ctx.route.action)

    no_dm = _register(tenv, "nodm", handler, feature=None)
    yes_dm = _register(tenv, "yesdm", handler, feature=None, allow_dm=True)
    user = tenv.member()
    await click(tenv.app, user=user, custom_id=no_dm, dm=True)
    await click(tenv.app, user=user, custom_id=yes_dm, dm=True)
    assert ran == ["yesdm"]


async def test_staff_access_is_enforced_server_side(tenv: TestEnv) -> None:
    ran: list[int] = []

    async def handler(ctx: Ctx) -> None:
        ran.append(ctx.user_id)

    cid = _register(tenv, "staffonly", handler, feature=None, access="staff")
    customer = tenv.member()
    staff = tenv.member(staff=True)
    denied = await click(tenv.app, user=customer, custom_id=cid)
    await click(tenv.app, user=staff, custom_id=cid)
    assert ran == [staff.id]
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)


async def test_feature_not_ready_answers_unavailable(tenv: TestEnv) -> None:
    async def handler(ctx: Ctx) -> None:
        raise AssertionError("must not run")

    cid = _register(tenv, "feat", handler, feature="tickets")
    tenv.app.readiness.statuses["tickets"].ready = False
    interaction = await click(tenv.app, user=tenv.member(), custom_id=cid)
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)


async def test_missing_error_content_means_silent_ack_never_invented_text(tenv: TestEnv) -> None:
    async def handler(ctx: Ctx) -> None:
        raise RuntimeError("boom")

    cid = _register(tenv, "boom", handler, feature=None)
    tenv.app.content.replace(file_values={})
    interaction = await click(tenv.app, user=tenv.member(), custom_id=cid)
    assert interaction.sent == []
    assert interaction.response_kinds() == ["defer"]


async def test_handler_error_and_exception_use_configured_messages(tenv: TestEnv) -> None:
    async def not_found(ctx: Ctx) -> None:
        raise HandlerError(ck.ERROR_NOT_FOUND)

    async def crash(ctx: Ctx) -> None:
        raise ValueError("secret internal detail")

    a = _register(tenv, "nf", not_found, feature=None)
    b = _register(tenv, "crash", crash, feature=None)
    user = tenv.member()
    first = await click(tenv.app, user=user, custom_id=a)
    second = await click(tenv.app, user=user, custom_id=b)
    assert first.last_sent is not None and first.last_sent.embeds[0].description == tenv.text(ck.ERROR_NOT_FOUND)
    assert second.last_sent is not None
    assert second.last_sent.embeds[0].description == tenv.text(ck.ERROR_GENERIC)
    assert "secret" not in second.last_sent.text()


async def test_rate_limit_answers_cooldown(tenv: TestEnv) -> None:
    async def handler(ctx: Ctx) -> None:
        await ctx.ack()

    cid = _register(tenv, "rl", handler, feature=None)
    user = tenv.member()
    limit = tenv.app.settings.bot.rate_limit_per_10s
    for _ in range(limit):
        await click(tenv.app, user=user, custom_id=cid)
    blocked = await click(tenv.app, user=user, custom_id=cid)
    assert blocked.last_sent is not None
    assert blocked.last_sent.embeds[0].description.startswith(tenv.text(ck.ERROR_COOLDOWN).split(" ")[0])


async def test_double_click_while_processing_is_not_processed_twice(tenv: TestEnv) -> None:
    import asyncio

    gate = asyncio.Event()
    runs: list[int] = []

    async def slow(ctx: Ctx) -> None:
        runs.append(1)
        await gate.wait()
        await ctx.ack()

    cid = _register(tenv, "slow", slow, feature=None)
    user = tenv.member()
    first = asyncio.create_task(click(tenv.app, user=user, custom_id=cid))
    await asyncio.sleep(0)
    second = await click(tenv.app, user=user, custom_id=cid)
    gate.set()
    await first
    assert runs == [1]
    assert second.response_kinds() in (["defer"], ["send_message"])


async def test_unknown_route_answers_unavailable(tenv: TestEnv) -> None:
    interaction = await click(tenv.app, user=tenv.member(), custom_id="sb:nope:nothing:1")
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_UNAVAILABLE)


async def test_modal_routing_receives_fields(tenv: TestEnv) -> None:
    received: dict[str, str] = {}

    async def handler(ctx: Ctx) -> None:
        received.update(ctx.fields)
        await ctx.ack()

    cid = _register(tenv, "form", handler, feature=None, kind="modal")
    await submit_modal(tenv.app, user=tenv.member(), custom_id=cid, fields={"qty": "2"})
    assert received == {"qty": "2"}


async def test_notifier_dm_then_channel_fallback_and_dedupe(tenv: TestEnv) -> None:
    app = tenv.app
    user = tenv.member()
    channel = tenv.guild.add_text_channel(name="ticket")
    spec = MessageSpec(embeds=(EmbedSpec(title="hello"),))
    assert await app.notifier.notify_user(user_id=user.id, spec=spec, kind="t", dedupe_key="n1") == "dm"
    assert len(user.dms) == 1
    assert await app.notifier.notify_user(user_id=user.id, spec=spec, kind="t", dedupe_key="n1") == "duplicate"
    user.dm_forbidden = True
    result = await app.notifier.notify_user(
        user_id=user.id, spec=spec, kind="t", dedupe_key="n2", fallback_channel_id=channel.id
    )
    assert result == "channel"
    assert channel.messages[-1].content == user.mention
    mentions = channel.messages[-1].allowed_mentions
    assert mentions is not None and mentions.everyone is False


async def test_permissions_owner_admin_staff(tenv: TestEnv) -> None:
    perms = tenv.app.perms
    customer = tenv.member()
    staff = tenv.member(staff=True)
    admin = tenv.member(admin=True)
    assert not perms.is_staff(customer)
    assert perms.is_staff(staff) and not perms.is_admin(staff)
    assert perms.is_admin(admin) and perms.is_staff(admin)
    assert perms.is_admin(None, tenv.app.env.owner_id)


def test_redacting_filter_removes_tokens() -> None:
    token = "M" + "T" * 23 + "." + "G" * 6 + "." + "x" * 30
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", (token,), None)
    record.extra_field = f"value {token}"
    RedactingFilter([token]).filter(record)
    assert token not in record.getMessage()
    assert token not in record.extra_field


@pytest.mark.parametrize("access", ["admin"])
async def test_admin_route_denies_staff(tenv: TestEnv, access: str) -> None:
    async def handler(ctx: Ctx) -> None:
        raise AssertionError("must not run")

    cid = _register(tenv, "adm", handler, feature=None, access=access)
    interaction = await click(tenv.app, user=tenv.member(staff=True), custom_id=cid)
    assert interaction.last_sent is not None
    assert isinstance(interaction.last_sent.embeds[0], discord.Embed)


async def test_route_specific_staff_roles(tenv: TestEnv) -> None:
    ran: list[int] = []

    async def handler(ctx: Ctx) -> None:
        ran.append(ctx.user_id)

    tenv.app.router.add(Route("zz", "extra", handler, "component", None, "staff", False, lambda app: ("helpers",)))
    helper_role = tenv.guild.add_role(name="helpers", position=5)
    await tenv.app.resources.remember("role", "helpers", helper_role.id)
    helper = tenv.guild.add_member(name="helper", roles=[helper_role])
    await click(tenv.app, user=helper, custom_id="sb:zz:extra")
    await click(tenv.app, user=tenv.member(), custom_id="sb:zz:extra")
    assert ran == [helper.id]
