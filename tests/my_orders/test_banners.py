"""Banners on /myorders: every page (and the reply without orders) shows ``account-and-personal-records``, and paging
edits the same message without uploading the banner again."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.my_orders import views
from tests.credits.util import make_order
from tests.customer_panel.util import MY_ORDERS, run_my_orders
from tests.fakes import FakeInteraction, FakeMessage, click
from tests.helpers import TestEnv, banner_names, deep_merge, make_banner_env, uploaded


@pytest.fixture
async def bn(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """/myorders enabled with one order per page, banners on."""
    tenv = await make_banner_env(tmp_path, deep_merge(MY_ORDERS, {"my_orders": {"orders_per_page": 1}}))
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


async def test_a_member_without_orders_gets_the_account_banner(bn: TestEnv) -> None:
    answered = reply(await run_my_orders(bn, bn.member()))
    assert banner_names(answered) == [name(bn, "account-and-personal-records")]


async def test_every_page_shows_the_account_banner_and_paging_keeps_it(bn: TestEnv) -> None:
    customer = bn.member()
    for _ in range(3):
        bn.clock.advance(60)
        await make_order(bn, customer.id)
    first = reply(await run_my_orders(bn, customer))
    assert banner_names(first) == [name(bn, "account-and-personal-records")]
    assert len(first.embeds) == 1  # one card with the orders: the banner is not an embed

    for page in (1, 2, 0):
        await click(bn.app, user=customer, custom_id=views.page_id(customer.id, page), message=first)
        assert uploaded(first.edits[-1]) == []  # the same banner: not uploaded again
        assert banner_names(first) == [name(bn, "account-and-personal-records")]
    assert len(first.edits) == 3
