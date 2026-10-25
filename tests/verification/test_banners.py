"""Banner pictures on the verification messages: the welcome panel, the private replies and the direct message."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.verification import content_keys as k
from shopbot.features.verification import views
from shopbot.ui.banners import EMPTY_LIBRARY, load_banners
from tests.helpers import TestEnv, banner_names, tiny_png
from tests.verification.util import agree, panel_messages, publish, reply, verification_env


@pytest.fixture
async def benv(tmp_path: Path) -> AsyncIterator[TestEnv]:
    tenv = await verification_env(tmp_path, {"verification": {"dm_confirmation": True}}, banners=True)
    try:
        yield tenv
    finally:
        await tenv.close()


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def new_picture(tenv: TestEnv, banner_id: BannerId) -> str:
    """The owner replaces one picture and reloads: the banner gets a new attachment name."""
    (tenv.app.config_dir / "banners" / f"{banner_id}.png").write_bytes(tiny_png(f"{banner_id} v2"))
    tenv.app.banners = load_banners(tenv.app.config_dir, tenv.app.settings.banners)
    return banner_file(tenv, banner_id)


async def test_welcome_panel_shows_the_welcome_banner_above_its_cards(benv: TestEnv) -> None:
    await publish(benv)
    (panel,) = panel_messages(benv)
    assert banner_names(panel) == [banner_file(benv, "welcome")]
    assert banner_file(benv, "welcome").startswith("banner-welcome-")
    assert len(panel.embeds) == 2  # the banner is an attachment, never an extra card
    assert all(not (embed.image.url or "").startswith("attachment://") for embed in panel.embeds)


async def test_text_change_edits_the_panel_without_uploading_the_banner_again(benv: TestEnv) -> None:
    await publish(benv)
    (panel,) = panel_messages(benv)
    uploaded = panel.attachments[0]
    benv.app.content.set_override(k.rule_value_key(1), "[changed rule]")
    await publish(benv)
    assert panel_messages(benv) == [panel] and len(panel.edits) == 1
    assert "attachments" not in panel.edits[-1]  # the banner already on the message is kept as it is
    assert panel.attachments == [uploaded] and banner_names(panel) == [banner_file(benv, "welcome")]


async def test_new_picture_replaces_the_banner_and_banners_off_remove_it(benv: TestEnv) -> None:
    await publish(benv)
    (panel,) = panel_messages(benv)
    old = banner_file(benv, "welcome")
    new = new_picture(benv, "welcome")
    assert new != old
    await publish(benv)
    assert [item.filename for item in panel.edits[-1]["attachments"]] == [new]
    assert banner_names(panel) == [new]
    benv.app.banners = EMPTY_LIBRARY
    await publish(benv)
    assert panel.edits[-1]["attachments"] == [] and banner_names(panel) == []
    assert panel_messages(benv) == [panel]


async def test_private_replies_and_the_dm_carry_the_banner_of_their_outcome(benv: TestEnv) -> None:
    await publish(benv)
    newcomer = benv.member(name="newcomer")
    confirmed = reply(await agree(benv, newcomer))
    assert banner_names(confirmed) == [banner_file(benv, "successful-confirmation")]
    (dm,) = newcomer.dms
    assert banner_names(dm) == [banner_file(benv, "successful-confirmation")]
    already = reply(await agree(benv, newcomer))
    assert banner_names(already) == [banner_file(benv, "general-response")]


async def test_builders_attach_the_mapped_banner(benv: TestEnv) -> None:
    ui = benv.app.ui
    values = views.reply_values({}, user_name="[member]", user_id=1)
    assert views.build_panel(ui, benv.app.settings.verification, {}).banner == ui.banner("welcome")
    assert views.confirmation_view(ui, values).banner == ui.banner("successful-confirmation")
    assert views.already_verified_view(ui, values).banner == ui.banner("general-response")
    assert views.dm_view(ui, values).banner == ui.banner("successful-confirmation")
