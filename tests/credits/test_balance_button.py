"""The customer 'my balance' button: helper, end-to-end click, missing content, restart."""

from __future__ import annotations

from pathlib import Path

import pytest

from shopbot.features import core_keys as ck
from shopbot.features.credits import service
from shopbot.features.credits.content_keys import BALANCE_DESCRIPTION, BALANCE_TITLE, BUTTON_BALANCE
from shopbot.ui.spec import ButtonSpec
from tests.credits.util import grant, make_order, reply_embed, restarted_app
from tests.fakes import FakeInteraction, FakeMessage, click
from tests.helpers import GUILD_ID, TestEnv, make_test_env

BALANCE_ID = "sb:cr:balance"


def _assert_only_unavailable(tenv: TestEnv, interaction: FakeInteraction) -> None:
    """Exactly one private reply: the configured 'unavailable' error (with the configured branding only)."""
    assert len(interaction.sent) == 1
    embed = reply_embed(interaction)
    assert embed.description == tenv.text(ck.ERROR_UNAVAILABLE)
    assert embed.title == tenv.text(ck.ERROR_TITLE)
    assert embed.fields == []


def _expected(tenv: TestEnv, minor: int) -> str:
    money = tenv.app.ui.money(minor)
    assert money is not None
    return tenv.text(BALANCE_DESCRIPTION).replace("{amount}", money)


async def test_balance_button_helper(tenv: TestEnv) -> None:
    button = service.balance_button(tenv.app)
    assert button == ButtonSpec(label=tenv.text(BUTTON_BALANCE), custom_id=BALANCE_ID, style="secondary")


async def test_balance_button_is_hidden_when_credits_are_not_ready(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={BALANCE_DESCRIPTION: ""})
    try:
        assert not tenv.app.ready("credits")
        assert service.balance_button(tenv.app) is None
        assert not service.available_at_checkout(tenv.app)
    finally:
        await tenv.close()


async def test_click_shows_the_members_own_balance(tenv: TestEnv) -> None:
    member, other = tenv.member(), tenv.member(name="other")
    await grant(tenv, member.id, 12_345)
    await grant(tenv, other.id, 999)
    panel = FakeMessage(tenv.guild.add_text_channel(name="shop"))
    interaction = await click(tenv.app, user=member, custom_id=BALANCE_ID, message=panel)
    assert interaction.response_kinds() == ["send_message"]
    message = interaction.last_sent
    assert message is not None and message.ephemeral  # type: ignore[attr-defined]
    embed = reply_embed(interaction)
    assert embed.title == tenv.text(BALANCE_TITLE)
    assert embed.description == _expected(tenv, 12_345)
    assert panel.edits == []  # the panel itself is never changed


async def test_zero_balance(tenv: TestEnv) -> None:
    interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID)
    assert reply_embed(interaction).description == _expected(tenv, 0)


async def test_balance_reflects_order_debits_and_returns(tenv: TestEnv) -> None:
    from shopbot.features.credits import repo

    member = tenv.member()
    await grant(tenv, member.id, 1_000)
    order_id, _ = await make_order(tenv, member.id)
    await tenv.app.db.transaction(
        repo.debit_for_order, guild_id=GUILD_ID, user_id=member.id, order_id=order_id, amount_minor=400, now=1
    )
    assert reply_embed(await click(tenv.app, user=member, custom_id=BALANCE_ID)).description == _expected(tenv, 600)
    await tenv.app.db.transaction(repo.return_for_order, guild_id=GUILD_ID, order_id=order_id, now=2)
    assert reply_embed(await click(tenv.app, user=member, custom_id=BALANCE_ID)).description == _expected(tenv, 1_000)


async def test_title_is_omitted_when_not_configured(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={BALANCE_TITLE: ""})
    try:
        assert tenv.app.ready("credits")
        interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID)
        embed = reply_embed(interaction)
        assert embed.title is None
        assert embed.description == _expected(tenv, 0)
    finally:
        await tenv.close()


@pytest.mark.parametrize("missing", [BALANCE_DESCRIPTION, BUTTON_BALANCE, ck.MONEY_FORMAT])
async def test_missing_required_content_makes_the_button_unavailable(tmp_path: Path, missing: str) -> None:
    tenv = await make_test_env(tmp_path, content={missing: ""})
    try:
        status = tenv.app.readiness.statuses["credits"]
        assert not status.ready
        assert any(p.key == missing for p in status.problems)
        member = tenv.member()
        await grant(tenv, member.id, 500)
        interaction = await click(tenv.app, user=member, custom_id=BALANCE_ID)
        _assert_only_unavailable(tenv, interaction)
    finally:
        await tenv.close()


async def test_content_removed_at_runtime_never_sends_invented_text(tenv: TestEnv) -> None:
    """Readiness not yet recomputed after a change: the view refuses and the router answers 'unavailable'."""
    values = dict(tenv.content)
    values[BALANCE_DESCRIPTION] = ""
    tenv.app.content.replace(file_values=values)
    interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID)
    _assert_only_unavailable(tenv, interaction)


async def test_money_format_removed_at_runtime_never_sends_invented_text(tenv: TestEnv) -> None:
    values = dict(tenv.content)
    values[ck.MONEY_FORMAT] = ""
    tenv.app.content.replace(file_values=values)
    interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID)
    _assert_only_unavailable(tenv, interaction)


async def test_nothing_is_sent_when_even_the_error_text_is_missing(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={BALANCE_DESCRIPTION: "", ck.ERROR_UNAVAILABLE: ""})
    try:
        interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID)
        assert interaction.sent == []
        assert interaction.response_kinds() == ["defer"]
    finally:
        await tenv.close()


async def test_dm_clicks_are_ignored(tenv: TestEnv) -> None:
    interaction = await click(tenv.app, user=tenv.member(), custom_id=BALANCE_ID, dm=True)
    assert interaction.sent == []


async def test_bot_accounts_are_refused(tenv: TestEnv) -> None:
    other_bot = tenv.guild.add_member(name="other-bot", bot=True)
    interaction = await click(tenv.app, user=other_bot, custom_id=BALANCE_ID)
    assert reply_embed(interaction).description == tenv.text(ck.ERROR_PERMISSION)


async def test_custom_id_arguments_cannot_select_another_member(tenv: TestEnv) -> None:
    member, other = tenv.member(), tenv.member(name="other")
    await grant(tenv, other.id, 777)
    interaction = await click(tenv.app, user=member, custom_id=f"{BALANCE_ID}:{other.id}")
    assert reply_embed(interaction).description == _expected(tenv, 0)


async def test_rapid_double_click_answers_each_click_once(tenv: TestEnv) -> None:
    member = tenv.member()
    await grant(tenv, member.id, 50)
    first = await click(tenv.app, user=member, custom_id=BALANCE_ID)
    second = await click(tenv.app, user=member, custom_id=BALANCE_ID)
    for interaction in (first, second):
        assert interaction.response_kinds() == ["send_message"]
        assert reply_embed(interaction).description == _expected(tenv, 50)


async def test_button_works_after_restart(tenv: TestEnv) -> None:
    member = tenv.member()
    await grant(tenv, member.id, 4_200)
    app = await restarted_app(tenv)
    interaction = await click(app, user=member, custom_id=BALANCE_ID)
    assert reply_embed(interaction).description == _expected(tenv, 4_200)
