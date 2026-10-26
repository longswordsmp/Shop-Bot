"""Banners on credits messages: the member's balance and adjustment notice show ``account-and-personal-records``,
staff lookups ``staff-dashboard`` (kept while paging), adjustments their outcome's banner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.credits import views
from tests.credits.util import grant, invoke
from tests.fakes import FakeInteraction, FakeMessage, click
from tests.helpers import TestEnv, banner_names, make_banner_env, uploaded


@pytest.fixture
async def bn(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await make_banner_env(tmp_path)
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None
    return banner.filename


def reply(interaction: FakeInteraction) -> FakeMessage:
    assert interaction.last_sent is not None and getattr(interaction.last_sent, "ephemeral", False)
    return interaction.last_sent


async def test_the_balance_button_answers_with_the_account_banner(bn: TestEnv) -> None:
    member = bn.member()
    await grant(bn, member.id, 1250)
    panel = FakeMessage(bn.guild.add_text_channel(name="shop"))
    answered = await click(bn.app, user=member, custom_id=views.balance_button_id(), message=panel)
    assert banner_names(reply(answered)) == [name(bn, "account-and-personal-records")]
    assert panel.edits == []  # the panel itself is untouched


async def test_adjustments_show_their_outcome_and_notify_the_member_with_the_account_banner(bn: TestEnv) -> None:
    admin, target = bn.member(admin=True), bn.member(name="target")
    added = await invoke(bn, admin, "adjust", member=target, action="add", amount="5", note="[goodwill]")
    assert banner_names(reply(added)) == [name(bn, "successful-confirmation")]
    [notice] = target.dms
    assert banner_names(notice) == [name(bn, "account-and-personal-records")]
    refused = await invoke(bn, admin, "adjust", member=target, action="remove", amount="50", note="[too much]")
    assert banner_names(reply(refused)) == [name(bn, "error")]


async def test_staff_lookups_show_the_staff_dashboard_banner_and_paging_keeps_it(bn: TestEnv) -> None:
    staff, target = bn.member(staff=True), bn.member(name="target")
    for amount in range(1, views.HISTORY_PAGE_SIZE + 3):
        await grant(bn, target.id, amount)
    balance = await invoke(bn, staff, "balance", member=target)
    assert banner_names(reply(balance)) == [name(bn, "staff-dashboard")]
    history = reply(await invoke(bn, staff, "history", member=target))
    assert banner_names(history) == [name(bn, "staff-dashboard")]
    await click(bn.app, user=staff, custom_id=views.history_page_id(target.id, 1), message=history)
    assert history.edits and uploaded(history.edits[-1]) == []  # the same banner: not uploaded again
    assert banner_names(history) == [name(bn, "staff-dashboard")]
    assert history.embeds[0].footer.text == bn.app.op("credits.history.page", page=2, pages=2)


async def test_commands_of_unready_credits_answer_with_the_error_banner(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, {"checkout": {"currency": ""}})
    try:
        assert not tenv.app.ready("credits")
        answered = await invoke(tenv, tenv.member(admin=True), "balance", member=tenv.member(name="target"))
        assert reply(answered).embeds[0].title == tenv.app.op("command.feature_disabled", feature="credits")
        assert banner_names(reply(answered)) == [name(tenv, "error")]
    finally:
        await tenv.close()
