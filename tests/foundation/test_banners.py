"""Banner pictures: loading, upload order, keeping/replacing/removing them on edits, limits, hashing, transcripts."""

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
from shopbot.ui.banners import MAX_BANNER_BYTES, load_banners
from shopbot.ui.discord_render import edit_attachments, send_kwargs
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import EmbedSpec, FileSpec, MessageSpec
from tests.fakes import FakeAttachment
from tests.helpers import (
    banner_names,
    make_banner_env,
    make_settings,
    tiny_png,
    write_test_banners,
)


def banner(tag: str = "welcome") -> FileSpec:
    data = tiny_png(tag)
    return FileSpec.of(f"banner-{tag}-{FileSpec.of('x', data).sha256[:12]}.png", data, "image/png")


def spec(title: str = "t", **kwargs: Any) -> MessageSpec:
    return MessageSpec(embeds=(EmbedSpec(title=title),), **kwargs)


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


# -- sending and editing -------------------------------------------------------------------------------------------


def test_the_banner_is_uploaded_first_and_counts_toward_the_limits() -> None:
    image = FileSpec.of("a.png", b"\x89PNG\r\n\x1a\nimage", "image/png")
    message = replace(spec(), banner=banner(), files=(image,))
    files = send_kwargs(message)["files"]
    assert [f.filename for f in files] == [banner().filename, "a.png"]
    too_many = replace(spec(), banner=banner(), files=tuple(FileSpec.of(f"f{i}.png", b"x") for i in range(10)))
    assert any("11 files exceeds 10" in str(v) for v in validate_message(too_many))
    shown_in_embed = MessageSpec(
        embeds=(EmbedSpec(title="t", image_url=f"attachment://{banner().filename}"),), banner=banner()
    )
    assert any("must not show the banner" in str(v) for v in validate_message(shown_in_embed))


def _attachment(name: str) -> Any:
    return FakeAttachment(name)


def test_edits_keep_an_unchanged_banner_and_replace_or_remove_others() -> None:
    old = _attachment(banner("old").filename)
    same = _attachment(banner().filename)
    other = _attachment("receipt.txt")
    # Unknown attachments: what the spec wants is uploaded; nothing wanted leaves the message alone.
    assert edit_attachments(spec(), None) is None
    uploaded = edit_attachments(replace(spec(), banner=banner()), None)
    assert uploaded is not None and [f.filename for f in uploaded] == [banner().filename]
    # The same banner is already there: no upload, attachments left alone.
    assert edit_attachments(replace(spec(), banner=banner()), [same, other]) is None
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
        assert banner_names(message) == [welcome.filename]

        await upsert_message(app, "panel:x", channel, spec("b", banner=welcome))  # text changed, same banner
        assert "attachments" not in message.edits[-1] and banner_names(message) == [welcome.filename]

        await upsert_message(app, "panel:x", channel, spec("b", banner=faq))  # another banner
        assert banner_names(message) == [faq.filename] and len(message.attachments) == 1

        await upsert_message(app, "panel:x", channel, spec("b"))  # banner switched off
        assert message.edits[-1]["attachments"] == [] and message.files == []
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
