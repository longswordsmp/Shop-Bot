"""Banner pictures on the guides: only the first message of each guide shows its banner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.guides import faq, how_to_buy, shipping
from shopbot.ui.banners import load_banners
from tests.guides.test_publish import LONG_FAQ, long_answers
from tests.guides.util import guide_env, messages
from tests.helpers import TestEnv, banner_names, tiny_png

GUIDES: tuple[tuple[Any, str, BannerId], ...] = (
    (how_to_buy, "how_to_buy", "how-it-works"),
    (faq, "faq", "faq"),
    (shipping, "shipping", "shipping"),
)


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await guide_env(tmp_path, LONG_FAQ, content=long_answers("general", 25), banners=True)
    try:
        yield tenv
    finally:
        await tenv.close()


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


async def test_each_guide_shows_its_banner_on_the_first_message_only(benv: TestEnv) -> None:
    for module, channel_key, banner_id in GUIDES:
        await module.publish(benv.app)
        found = messages(benv, channel_key)
        assert banner_names(found[0]) == [banner_file(benv, banner_id)], channel_key
        assert all(banner_names(message) == [] for message in found[1:]), channel_key
    assert len(messages(benv, "faq")) > 3  # the long FAQ continues in further messages without banners


async def test_republishing_a_changed_guide_keeps_the_uploaded_banner(benv: TestEnv) -> None:
    await faq.publish(benv.app)
    first, *rest = messages(benv, "faq")
    benv.app.content.set_override(faq.TITLE, "[changed title]")
    await faq.publish(benv.app)
    assert messages(benv, "faq") == [first, *rest]
    assert len(first.edits) == 1 and "attachments" not in first.edits[-1]
    assert banner_names(first) == [banner_file(benv, "faq")]
    assert all(message.edits == [] for message in rest)  # unchanged continuation messages are left alone


async def test_new_picture_replaces_the_banner_of_the_first_message_only(benv: TestEnv) -> None:
    await faq.publish(benv.app)
    first, *rest = messages(benv, "faq")
    old = banner_file(benv, "faq")
    (benv.app.config_dir / "banners" / "faq.png").write_bytes(tiny_png("faq v2"))
    benv.app.banners = load_banners(benv.app.config_dir, benv.app.settings.banners)
    new = banner_file(benv, "faq")
    assert new != old
    await faq.publish(benv.app)
    assert messages(benv, "faq") == [first, *rest]
    assert [item.filename for item in first.edits[-1]["attachments"]] == [new]
    assert banner_names(first) == [new]
    assert all(message.edits == [] and banner_names(message) == [] for message in rest)


async def test_deleted_first_message_is_reposted_with_its_banner(benv: TestEnv) -> None:
    await how_to_buy.publish(benv.app)
    await messages(benv, "how_to_buy")[0].delete()
    await how_to_buy.publish(benv.app)
    (reposted,) = messages(benv, "how_to_buy")
    assert banner_names(reposted) == [banner_file(benv, "how-it-works")]
