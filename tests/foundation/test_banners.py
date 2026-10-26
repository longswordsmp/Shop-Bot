"""Banner pictures: loading, placement at the bottom of the last card, upload order, keeping/replacing/removing them on
edits, limits, hashing, transcripts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.banner_ids import BANNER_IDS
from shopbot.config.settings import BannerSettings, SettingsError
from shopbot.core.panels import spec_hash, upsert_message
from shopbot.features.tickets.transcript import collect_messages
from shopbot.ui.banners import MAX_BANNER_BYTES, banner_inside, load_banners, place_banner
from shopbot.ui.discord_render import edit_attachments, edit_kwargs, send_kwargs
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import EmbedSpec, FileSpec, MessageSpec
from tests.fakes import FakeAttachment
from tests.helpers import (
    banner_in_card,
    banner_names,
    make_banner_env,
    make_settings,
    tiny_png,
    uploaded,
    write_test_banners,
)


def banner(tag: str = "welcome") -> FileSpec:
    data = tiny_png(tag)
    return FileSpec.of(f"banner-{tag}-{FileSpec.of('x', data).sha256[:12]}.png", data, "image/png")


def spec(title: str = "t", **kwargs: Any) -> MessageSpec:
    return MessageSpec(embeds=(EmbedSpec(title=title),), **kwargs)


def at(file: FileSpec) -> str:
    return f"attachment://{file.filename}"


PRODUCT = FileSpec.of("product-1-abc.png", b"\x89PNG\r\n\x1a\nproduct", "image/png")


# -- loading ------------------------------------------------------------------------------------------------------


def test_banners_are_off_unless_switched_on(tmp_path: Path) -> None:
    write_test_banners(tmp_path)
    library = load_banners(tmp_path, BannerSettings())
    assert library.files == {} and library.problems == () and not library.enabled


def test_every_banner_id_loads_with_a_name_that_follows_its_picture(tmp_path: Path) -> None:
    write_test_banners(tmp_path)
    library = load_banners(tmp_path, BannerSettings(enabled=True))
    assert set(library.files) == set(BANNER_IDS) and library.problems == ()
    welcome = library.get("welcome")
    assert welcome is not None and welcome.content_type == "image/png" and welcome.data == tiny_png("welcome")
    assert welcome.filename == f"banner-welcome-{welcome.sha256[:12]}.png"
    (tmp_path / "banners" / "welcome.png").write_bytes(tiny_png("a new picture"))
    changed = load_banners(tmp_path, BannerSettings(enabled=True)).get("welcome")
    assert changed is not None and changed.filename != welcome.filename


def test_missing_wrong_or_too_large_pictures_are_problems_not_failures(tmp_path: Path) -> None:
    write_test_banners(tmp_path, ["welcome", "faq"])
    (tmp_path / "banners" / "error.png").write_bytes(b"not a picture")
    (tmp_path / "banners" / "reminder.png").write_bytes(tiny_png("x") + b"0" * MAX_BANNER_BYTES)
    library = load_banners(tmp_path, BannerSettings(enabled=True, off=("cancellation",)))
    assert set(library.files) == {"welcome", "faq"}
    joined = "\n".join(library.problems)
    assert "banner 'error': error.png is not a PNG, JPEG, GIF or WebP picture" in joined
    assert "banner 'reminder': reminder.png is" in joined and "at most" in joined
    assert "banner 'shipping': no shipping.png" in joined
    assert "cancellation" not in joined  # switched off: not expected
    assert load_banners(tmp_path / "nowhere", BannerSettings(enabled=True)).problems[0].startswith("banners folder")


def test_other_picture_formats_are_accepted(tmp_path: Path) -> None:
    folder = tmp_path / "banners"
    folder.mkdir()
    (folder / "faq.jpg").write_bytes(b"\xff\xd8\xff" + b"jpeg")
    faq = load_banners(tmp_path, BannerSettings(enabled=True)).get("faq")
    assert faq is not None and faq.content_type == "image/jpeg" and faq.filename.endswith(".jpg")


def test_banner_settings_are_checked() -> None:
    with pytest.raises(SettingsError) as exc:
        make_settings({"banners": {"enabled": True, "off": ["welcome", "nope"]}})
    assert "banners.off: unknown banner 'nope'" in "\n".join(exc.value.errors)
    with pytest.raises(SettingsError):
        make_settings({"banners": {"directory": "../outside"}})
    assert make_settings({"banners": {"enabled": True, "off": ["activity-log"]}}).banners.off == ("activity-log",)


# -- placement -----------------------------------------------------------------------------------------------------


def test_the_banner_becomes_the_image_of_the_last_card() -> None:
    first, last = EmbedSpec(title="one"), EmbedSpec(title="two")
    built = MessageSpec(embeds=(first, last), banner=banner())
    placed = place_banner(built)
    assert placed.embeds == (first, replace(last, image_url=at(banner())))
    assert placed.banner == built.banner and placed.files is None  # still uploaded as the first attachment
    assert banner_inside(built) and banner_inside(placed)
    assert place_banner(placed) == placed  # placing twice changes nothing
    assert validate_message(placed) == [] == validate_message(built)


def test_a_last_card_with_its_own_picture_keeps_the_banner_above_the_embeds() -> None:
    product = MessageSpec(embeds=(EmbedSpec(title="p", image_url=at(PRODUCT)),), banner=banner(), files=(PRODUCT,))
    linked = MessageSpec(embeds=(EmbedSpec(title="p", image_url="https://example.com/p.png"),), banner=banner())
    for message in (product, linked):
        assert place_banner(message) == message and not banner_inside(message)
    # Only the last card counts: a picture on an earlier card does not keep the banner out of the last one.
    earlier = MessageSpec(embeds=(EmbedSpec(title="a", image_url="https://example.com/a.png"), EmbedSpec(title="b")))
    assert place_banner(replace(earlier, banner=banner())).embeds[1].image_url == at(banner())


def test_nothing_is_placed_without_a_banner_or_without_embeds() -> None:
    assert place_banner(spec()) == spec() and not banner_inside(spec())
    bare = MessageSpec(banner=banner())
    assert place_banner(bare) == bare and not banner_inside(bare)


def test_the_banner_may_only_be_the_last_cards_image() -> None:
    def problems(*embeds: EmbedSpec) -> list[str]:
        return [str(v) for v in validate_message(MessageSpec(embeds=embeds, banner=banner()))]

    assert problems(EmbedSpec(title="a"), EmbedSpec(title="b", image_url=at(banner()))) == []
    assert problems(EmbedSpec(title="a", image_url=at(banner())), EmbedSpec(title="b")) == [
        "embeds[0].image_url: the banner can only be the last embed's image"
    ]
    assert problems(EmbedSpec(title="a", thumbnail_url=at(banner()))) == [
        "embeds[0].thumbnail_url: the banner can only be the last embed's image"
    ]


# -- sending and editing -------------------------------------------------------------------------------------------


def test_the_banner_is_uploaded_first_and_counts_toward_the_limits() -> None:
    image = FileSpec.of("a.png", b"\x89PNG\r\n\x1a\nimage", "image/png")
    message = replace(spec(), banner=banner(), files=(image,))
    files = send_kwargs(message)["files"]
    assert [f.filename for f in files] == [banner().filename, "a.png"]
    too_many = replace(spec(), banner=banner(), files=tuple(FileSpec.of(f"f{i}.png", b"x") for i in range(10)))
    assert any("11 files exceeds 10" in str(v) for v in validate_message(too_many))


def test_a_sent_message_shows_the_banner_at_the_bottom_of_its_last_card() -> None:
    kwargs = send_kwargs(MessageSpec(embeds=(EmbedSpec(title="one"), EmbedSpec(title="two")), banner=banner()))
    assert [f.filename for f in kwargs["files"]] == [banner().filename]
    assert [e.image.url for e in kwargs["embeds"]] == [None, at(banner())]
    # A product card keeps its picture; the banner is still uploaded first and no embed uses it (shown above).
    product = MessageSpec(embeds=(EmbedSpec(title="p", image_url=at(PRODUCT)),), banner=banner(), files=(PRODUCT,))
    kwargs = send_kwargs(product)
    assert [f.filename for f in kwargs["files"]] == [banner().filename, PRODUCT.filename]
    assert [e.image.url for e in kwargs["embeds"]] == [at(PRODUCT)]


def _attachment(name: str) -> Any:
    return FakeAttachment(name)


def test_edits_keep_an_unchanged_banner_and_replace_or_remove_others() -> None:
    old = _attachment(banner("old").filename)
    same = _attachment(banner().filename)
    other = _attachment("receipt.txt")
    # Unknown attachments: what the spec wants is uploaded; nothing wanted leaves the message alone.
    assert edit_attachments(spec(), None) is None
    sent = edit_attachments(replace(spec(), banner=banner()), None)
    assert sent is not None and [f.filename for f in sent] == [banner().filename]
    # The same banner is already there: kept, not uploaded again. The card shows it, so the edit sends the kept
    # attachments (Discord resolves the card's attachment:// image against them).
    assert edit_attachments(replace(spec(), banner=banner()), [same, other]) == [same, other]
    # A banner shown above the embeds (the last card has its own picture) is left alone.
    above = MessageSpec(embeds=(EmbedSpec(title="p", image_url="https://example.com/p.png"),), banner=banner())
    assert edit_attachments(above, [same, other]) is None
    # A different banner replaces the old one; other attachments are kept.
    swapped = edit_attachments(replace(spec(), banner=banner()), [old, other])
    assert swapped is not None and swapped[1] is other
    assert isinstance(swapped[0], discord.File) and swapped[0].filename == banner().filename
    # No banner any more: the old one is removed.
    assert edit_attachments(spec(), [old, other]) == [other]
    # A spec that manages its files keeps the banner and uploads exactly its files.
    managed = edit_attachments(replace(spec(), banner=banner(), files=(FileSpec.of("a.png", b"x"),)), [same, other])
    assert managed is not None and managed[0] is same
    assert [getattr(f, "filename", None) for f in managed[1:]] == ["a.png"]


def test_an_edit_still_shows_the_kept_banner_in_the_card() -> None:
    same = _attachment(banner().filename)
    kwargs = edit_kwargs(replace(spec(), banner=banner()), [same])
    assert kwargs["embeds"][0].image.url == at(banner()) and kwargs["attachments"] == [same]
    # Unknown attachments (a message that was not fetched): the banner is uploaded again with the reference.
    kwargs = edit_kwargs(replace(spec(), banner=banner()), None)
    assert kwargs["embeds"][0].image.url == at(banner())
    assert [getattr(f, "filename", None) for f in kwargs["attachments"]] == [banner().filename]
    # A changed banner: the new picture is uploaded, the old one dropped, and the card shows the new one.
    kwargs = edit_kwargs(replace(spec(), banner=banner("faq")), [same])
    assert kwargs["embeds"][0].image.url == at(banner("faq"))
    assert [(type(a) is discord.File, a.filename) for a in kwargs["attachments"]] == [(True, banner("faq").filename)]


async def test_panels_keep_swap_and_drop_their_banner_without_needless_uploads(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path)
    try:
        app = tenv.app
        channel = tenv.guild.add_text_channel(name="panel")
        welcome = app.ui.banner("welcome")
        faq = app.ui.banner("faq")
        assert welcome is not None and faq is not None
        await upsert_message(app, "panel:x", channel, spec("a", banner=welcome))
        message = channel.messages[0]
        assert banner_names(message) == [welcome.filename] and banner_in_card(message) == welcome.filename

        await upsert_message(app, "panel:x", channel, spec("b", banner=welcome))  # text changed, same banner
        assert uploaded(message.edits[-1]) == [] and banner_names(message) == [welcome.filename]
        assert banner_in_card(message) == welcome.filename  # the kept picture still closes the card

        await upsert_message(app, "panel:x", channel, spec("b", banner=faq))  # another banner
        assert banner_names(message) == [faq.filename] and len(message.attachments) == 1
        assert uploaded(message.edits[-1]) == [faq.filename] and banner_in_card(message) == faq.filename

        await upsert_message(app, "panel:x", channel, spec("b"))  # banner switched off
        assert message.edits[-1]["attachments"] == [] and message.files == []
        assert message.embeds[0].image.url is None
    finally:
        await tenv.close()


def test_the_banner_counts_in_the_panel_hash() -> None:
    plain = spec()
    assert spec_hash(plain) != spec_hash(replace(plain, banner=banner()))
    assert spec_hash(replace(plain, banner=banner())) != spec_hash(replace(plain, banner=banner("faq")))
    assert spec_hash(replace(plain, banner=banner())) == spec_hash(replace(plain, banner=replace(banner(), data=None)))


async def test_ui_banner_follows_settings_and_reload(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, {"banners": {"off": ["faq"]}})
    try:
        assert tenv.app.ui.banner("welcome") is not None
        assert tenv.app.ui.banner("faq") is None  # switched off
        (tmp_path / "config" / "banners" / "welcome.png").write_bytes(tiny_png("replaced"))
        before = tenv.app.ui.banner("welcome")
        tenv.app.banners = load_banners(tmp_path / "config", tenv.app.settings.banners)
        after = tenv.app.ui.banner("welcome")
        assert before is not None and after is not None and before.filename != after.filename
    finally:
        await tenv.close()


# -- transcripts -----------------------------------------------------------------------------------------------------


class _Author:
    def __init__(self, id: int, bot: bool) -> None:
        self.id = id
        self.bot = bot
        self.display_name = "name"


class _History:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    async def _iter(self) -> Any:
        for message in self._messages:
            yield message

    def history(self, **kwargs: Any) -> Any:
        return self._iter()


async def test_transcripts_leave_out_the_bots_banners_but_keep_customer_files() -> None:
    from datetime import UTC, datetime

    def message(author: _Author, *names: str) -> Any:
        attachments = [FakeAttachment(name, url=f"https://cdn.example/{name}") for name in names]
        return type(
            "M",
            (),
            {
                "id": 1,
                "author": author,
                "content": "",
                "embeds": [],
                "attachments": attachments,
                "created_at": datetime.now(tz=UTC),
            },
        )()

    bot, customer = _Author(1, True), _Author(2, False)
    collected = await collect_messages(
        _History([message(bot, "banner-welcome-abc.png", "receipt.txt"), message(customer, "banner-mine.png")])
    )
    assert collected[0].attachments == ("https://cdn.example/receipt.txt",)
    assert collected[1].attachments == ("https://cdn.example/banner-mine.png",)


async def test_transcripts_leave_out_a_banner_shown_inside_the_card(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path)
    try:
        channel = tenv.guild.add_text_channel(name="ticket")
        welcome = tenv.app.ui.banner("welcome")
        image = FileSpec.of("receipt.png", b"\x89PNG\r\n\x1a\nreceipt", "image/png")
        await channel.send(**send_kwargs(spec("hello", banner=welcome, files=(image,))))
        [message] = channel.messages
        assert banner_in_card(message) is not None  # the card shows the banner, the message still carries it
        [collected] = await collect_messages(channel)
        assert collected.attachments == (message.attachments[1].url,) and len(message.attachments) == 2
        assert collected.embeds[0].title == "hello"
    finally:
        await tenv.close()


# -- interaction replies that upload ------------------------------------------------------------------------------


async def test_replies_that_upload_a_banner_are_acknowledged_first(tmp_path: Path) -> None:
    from shopbot.ui.routing import Ctx
    from tests.fakes import FakeInteraction

    tenv = await make_banner_env(tmp_path)
    try:
        app = tenv.app
        welcome, faq = app.ui.banner("welcome"), app.ui.banner("faq")
        user = tenv.member()

        def ctx(message: Any = None) -> tuple[Ctx, FakeInteraction]:
            interaction = FakeInteraction(client=app.bot, user=user, guild=tenv.guild, message=message)
            return Ctx(app, interaction, None, ()), interaction  # type: ignore[arg-type]

        # A new private reply with a banner: acknowledged ("thinking"), then sent as a follow-up.
        c, interaction = ctx()
        await c.respond(spec("hello", banner=welcome))
        assert interaction.response_kinds() == ["defer"] and banner_names(interaction.sent[-1]) == [welcome.filename]
        # Without files it is answered directly.
        c, interaction = ctx()
        await c.respond(spec("hello"))
        assert interaction.response_kinds() == ["send_message"]

        channel = tenv.guild.add_text_channel(name="panel")
        message = await channel.send(**send_kwargs(spec("a", banner=welcome)))
        # The clicked message keeps its banner: edited in the same response, nothing uploaded.
        c, interaction = ctx(message)
        await c.update(spec("b", banner=welcome))
        assert interaction.response_kinds() == ["edit_message"] and uploaded(message.edits[-1]) == []
        assert banner_in_card(message) == welcome.filename
        # A new banner must be uploaded: the click is acknowledged first, then the message is edited.
        c, interaction = ctx(message)
        await c.update(spec("c", banner=faq))
        assert interaction.response_kinds() == ["defer"] and banner_names(message) == [faq.filename]
    finally:
        await tenv.close()


async def test_pictures_that_cannot_be_used_are_listed_by_status(tmp_path: Path) -> None:
    from shopbot.features.admin import service, views

    tenv = await make_banner_env(tmp_path)
    try:
        (tenv.app.config_dir / "banners" / "faq.png").write_bytes(b"not a picture")
        tenv.app.banners = load_banners(tenv.app.config_dir, tenv.app.settings.banners)
        snapshot = await service.status_snapshot(tenv.app)
        assert any("faq" in problem for problem in snapshot.picture_problems)
        overview = views.status_overview(tenv.app.ui, tenv.app.op, snapshot)
        names = [field.name for field in overview.embeds[0].fields]
        assert tenv.app.op("admin.status.pictures") in names
    finally:
        await tenv.close()
