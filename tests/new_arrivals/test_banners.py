"""Banners on new-arrivals messages: the panel and every update show ``new-arrivals`` (next to an uploaded product
image), the private confirmations show the outcome's banner, and an earlier unrecorded update is still adopted."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals import repo, service
from shopbot.ui.discord_render import send_spec
from tests.catalog.test_images import FakeUpload, png
from tests.catalog.util import invoke
from tests.fakes import FakeMessage, FakeTextChannel
from tests.helpers import GUILD_ID, banner_names, make_banner_env, provision_channels
from tests.new_arrivals.util import (
    CHANNEL_KEY,
    NA_SETTINGS,
    ROLE_KEY,
    NaEnv,
    add_product,
    announcements,
    audit,
    product,
    run_tasks,
    set_stock,
    toggle,
)


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[NaEnv]:
    """The new-arrivals environment of ``na_env`` with banners on (a tiny picture per banner id)."""
    tenv = await make_banner_env(tmp_path, NA_SETTINGS)
    await provision_channels(tenv)
    role = tenv.guild.add_role(name="restock-alerts", position=5)
    await tenv.app.resources.remember("role", ROLE_KEY, role.id)
    channel = tenv.app.resources.text_channel(tenv.guild, CHANNEL_KEY)  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    na = NaEnv(tenv=tenv, role=role, channel=channel)
    try:
        yield na
    finally:
        await na.close()


def name(env: NaEnv, banner_id: BannerId) -> str:
    banner = env.app.ui.banner(banner_id)
    assert banner is not None
    return banner.filename


def uploaded_banners(edit: dict[str, Any]) -> list[str]:
    """Banner files an edit uploads again (a kept attachment is not an upload)."""
    return [
        a.filename
        for a in edit.get("attachments") or []
        if isinstance(a, discord.File) and a.filename.startswith("banner-")
    ]


def only_update(env: NaEnv) -> FakeMessage:
    [update] = env.updates()
    return update


async def test_the_panel_shows_the_new_arrivals_banner_and_keeps_it_on_edits(env: NaEnv) -> None:
    await service.publish(env.app)
    [panel] = env.panels()
    assert banner_names(panel) == [name(env, "new-arrivals")]
    assert panel.embeds[-1].image.url == f"attachment://{name(env, 'new-arrivals')}"  # closes the card

    await service.publish(env.app)  # unchanged: not edited
    assert panel.edits == []
    env.app.content.set_override(keys.PANEL_TITLE, "[changed title]")
    await service.publish(env.app)
    assert panel.embeds[0].title == "[changed title]"
    assert uploaded_banners(panel.edits[-1]) == [] and banner_names(panel) == [name(env, "new-arrivals")]
    assert panel.embeds[-1].image.url == f"attachment://{name(env, 'new-arrivals')}"  # the kept picture


async def test_confirmations_show_the_banner_of_the_outcome(env: NaEnv) -> None:
    member = env.member()
    on = await toggle(env, member)
    assert on.last_sent is not None and banner_names(on.last_sent) == [name(env, "successful-confirmation")]
    off = await toggle(env, member)
    assert off.last_sent is not None and banner_names(off.last_sent) == [name(env, "general-response")]


async def test_updates_show_the_banner_next_to_the_uploaded_product_image(env: NaEnv) -> None:
    product_id = await add_product(env, "[fresh]", stock="0")
    await invoke(env.tenv, env.tenv.member(admin=True), "image", product_id=product_id, file=FakeUpload(png(0x70)))
    await run_tasks(env)
    new = only_update(env)
    assert banner_names(new) == [name(env, "new-arrivals")]
    images = [f.filename for f in new.files if not f.filename.startswith("banner-")]
    assert len(images) == 1 and new.embeds[0].image.url == f"attachment://{images[0]}"  # the banner stays above

    await set_stock(env, product_id, "set", 3)  # sold out -> orderable again
    await run_tasks(env)
    restock = env.updates()[-1]
    assert restock is not new and banner_names(restock) == [name(env, "new-arrivals")]


async def test_an_unrecorded_update_with_a_banner_is_still_adopted(env: NaEnv) -> None:
    """The process stopped after Discord posted the update (banner included) but before it was recorded."""
    product_id = await add_product(env)
    guild: Any = env.tenv.guild
    spec = service.build_announcement(env.app, guild, "new", await product(env, product_id))
    assert spec.banner is not None
    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="new",
        version=1,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )
    posted = await send_spec(env.channel, spec)  # the earlier attempt's post
    other = await send_spec(env.channel, service.build_panel(env.app))  # a newer bot post, also with a banner
    await run_tasks(env)
    assert env.updates() == [posted] and other in env.channel.messages
    [record] = await announcements(env, product_id)
    assert record.status == "posted" and record.message_id == posted.id
    assert (await audit(env, "new_arrivals.announced"))[0].details["adopted"] is True


async def test_an_unrecorded_update_whose_title_has_an_icon_is_still_adopted(env: NaEnv) -> None:
    """Discord stores the title without the custom emoji it cannot show there; the earlier post is still found."""
    env.app.content.set_override("new_arrivals.new_product.title", "<:sparkles:123456789012345678> New: {name}")
    product_id = await add_product(env)
    guild: Any = env.tenv.guild
    spec = service.build_announcement(env.app, guild, "new", await product(env, product_id))
    assert spec.embeds[0].title is not None and spec.embeds[0].title.startswith("<:sparkles:")
    await env.app.db.transaction(
        repo.claim,
        guild_id=GUILD_ID,
        product_id=product_id,
        event="new",
        version=1,
        channel_id=env.channel.id,
        now=env.tenv.clock.now(),
    )
    posted = await send_spec(env.channel, spec)
    assert posted.embeds[0].title is not None and not posted.embeds[0].title.startswith("<:")
    await run_tasks(env)
    assert env.updates() == [posted]
    [record] = await announcements(env, product_id)
    assert record.status == "posted" and record.message_id == posted.id
