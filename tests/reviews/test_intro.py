"""The #reviews intro panel: posted and kept up to date by /setup, its line about our team's check chosen from the
settings, withdrawn while reviews are not ready."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.core.panels import withdraw_unready_panels
from shopbot.db.repos import state
from shopbot.features.reviews import MODULE, service, views
from shopbot.features.reviews import content_keys as k
from tests.helpers import GUILD_ID, TestEnv, banner_in_card, banner_names, make_banner_env
from tests.reviews.util import make_env, make_order, reviews_channel
from tests.reviews.write_util import make_write_env, write, write_overrides
from tests.setup.util import setup


def _intro_text(tenv: TestEnv, line_key: str | None) -> str:
    text = tenv.text(k.INTRO_DESCRIPTION)
    return text if line_key is None else f"{text}\n\n{tenv.text(line_key)}"


async def test_setup_posts_the_intro_panel_with_its_banner(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, write_overrides())
    try:
        report = await setup(tenv)
        assert report.counts.get("failed", 0) == 0, report.lines
        channel = reviews_channel(tenv)
        (panel,) = channel.messages
        card = panel.embeds[0]
        assert card.title == tenv.text(k.INTRO_TITLE)
        # As shipped: approval off, unverified reviews and reviews with a photo checked first.
        assert card.description == _intro_text(tenv, k.INTRO_CHECKED_PHOTOS_UNVERIFIED)
        assert len(panel.embeds) == 1
        (button,) = [getattr(child, "item", child) for child in panel.components()]
        assert isinstance(button, discord.ui.Button) and button.custom_id == views.start_id()
        assert button.label == tenv.text(k.INTRO_BUTTON)
        assert button.style is discord.ButtonStyle.primary
        names = banner_names(panel)
        assert len(names) == 1 and names[0].startswith("banner-reviews-")
        assert banner_in_card(panel) == names[0]  # the banner closes the card
        assert tenv.app.op("reviews.intro.created", channel=channel.mention) in report.lines["panels"]
        record = await tenv.app.db.run(state.published_get, GUILD_ID, service.INTRO_KEY)
        assert record is not None and record.message_id == panel.id
    finally:
        await tenv.close()


async def test_intro_panel_is_kept_up_to_date_and_never_duplicated(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        channel = reviews_channel(tenv)
        mention = channel.mention
        assert await service.publish_intro(tenv.app) == [tenv.app.op("reviews.intro.created", channel=mention)]
        (panel,) = channel.messages
        assert await service.publish_intro(tenv.app) == [tenv.app.op("reviews.intro.unchanged", channel=mention)]
        tenv.app.content.replace(file_values={**tenv.content, k.INTRO_TITLE: "[new intro title]"})
        assert await service.publish_intro(tenv.app) == [tenv.app.op("reviews.intro.updated", channel=mention)]
        assert channel.messages == [panel] and panel.embeds[0].title == "[new intro title]"  # edited in place
        await panel.delete()
        assert await service.publish_intro(tenv.app) == [tenv.app.op("reviews.intro.recreated", channel=mention)]
        assert len(channel.messages) == 1
    finally:
        await tenv.close()


async def test_the_check_line_follows_the_settings(tmp_path: Path) -> None:
    no_photo_check = {"photo_requires_approval": False}
    cases = (
        ("all", {"require_approval": True}, k.INTRO_CHECKED_ALL),
        ("all_without_unverified", {"require_approval": True, "allow_unverified": False}, k.INTRO_CHECKED_ALL),
        ("photos_unverified", {}, k.INTRO_CHECKED_PHOTOS_UNVERIFIED),
        ("photos", {"allow_unverified": False}, k.INTRO_CHECKED_PHOTOS),
        ("unverified", no_photo_check, k.INTRO_CHECKED_UNVERIFIED),
        ("none", {"allow_unverified": False, **no_photo_check}, None),
    )
    for name, reviews, line in cases:
        tenv = await make_env(tmp_path / name, write_overrides(reviews))
        try:
            await service.publish_intro(tenv.app)
            (panel,) = reviews_channel(tenv).messages
            assert panel.embeds[0].description == _intro_text(tenv, line), name
        finally:
            await tenv.close()


async def test_line_texts_are_required_only_when_they_are_shown() -> None:
    from tests.helpers import make_settings

    lines = (k.INTRO_CHECKED_ALL, k.INTRO_CHECKED_PHOTOS_UNVERIFIED, k.INTRO_CHECKED_UNVERIFIED, k.INTRO_CHECKED_PHOTOS)

    def required(reviews: dict[str, object]) -> set[str]:
        keys = {d.key: d for d in k.content_keys(make_settings(write_overrides(reviews)))}
        return {key for key in lines if keys[key].required}

    no_photo_check = {"photo_requires_approval": False}
    assert required({"require_approval": True}) == {k.INTRO_CHECKED_ALL}
    assert required({}) == {k.INTRO_CHECKED_PHOTOS_UNVERIFIED}
    assert required({"allow_unverified": False}) == {k.INTRO_CHECKED_PHOTOS}
    assert required(no_photo_check) == {k.INTRO_CHECKED_UNVERIFIED}
    assert required({"allow_unverified": False, **no_photo_check}) == set()


async def test_in_a_channel_that_already_shows_reviews_the_intro_comes_after_them(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await make_order(tenv, customer)
        await write(tenv, customer)
        channel = reviews_channel(tenv)
        (review_post,) = channel.messages
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        assert channel.messages[0] is review_post and channel.messages[1].embeds[0].title == tenv.text(k.INTRO_TITLE)
    finally:
        await tenv.close()


async def test_intro_panel_is_withdrawn_while_reviews_are_not_ready(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        await service.publish_intro(tenv.app)
        channel = reviews_channel(tenv)
        assert len(channel.messages) == 1
        tenv.app.content.replace(file_values={**tenv.content, k.INTRO_TITLE: ""})
        tenv.app.recompute_readiness()
        assert not tenv.app.ready(k.REVIEWS)
        withdrawn = await withdraw_unready_panels(tenv.app)
        assert [w.key for w in withdrawn] == [service.INTRO_KEY]
        assert channel.messages == []
    finally:
        await tenv.close()


async def test_intro_panel_reports_what_keeps_it_from_being_posted(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path)
    try:
        await reviews_channel(tenv).delete()
        await tenv.app.resources.refresh()
        assert await service.publish_intro(tenv.app) == [
            tenv.app.op("reviews.intro.channel_missing", channel=tenv.app.settings.reviews.channel)
        ]
    finally:
        await tenv.close()
    tenv = await make_env(tmp_path / "text", content={k.INTRO_DESCRIPTION: ""})
    try:
        assert await service.publish_intro(tenv.app) == [
            tenv.app.op("reviews.intro.missing_content", content_key=k.INTRO_DESCRIPTION)
        ]
    finally:
        await tenv.close()
