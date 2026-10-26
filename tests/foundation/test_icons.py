"""The bot's custom icons: loading, the {icon_<name>} placeholder, uploading as application emojis, fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from shopbot.config.content import validate_value
from shopbot.config.content_keys import Kind, ck
from shopbot.config.settings import IconSettings
from shopbot.db.repos import app_emojis as repo
from shopbot.features.admin import edit_text
from shopbot.ui.component_emoji import WHITE_SUFFIX, shown_button
from shopbot.ui.icons import (
    MAX_ICON_BYTES,
    keep_uploaded_ids,
    load_icons,
    load_uploaded_ids,
    sync_application_emojis,
)
from shopbot.ui.spec import ButtonSpec
from tests.helpers import ROOT, make_settings, make_test_env, tiny_png

ICONS_ON = {"icons": {"enabled": True}}


def write_icons(config_dir: Path, *names: str) -> None:
    folder = config_dir / "emojis"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.png").write_bytes(tiny_png(name))


def test_the_shipped_icons_all_load() -> None:
    library = load_icons(ROOT / "config", IconSettings(enabled=True))
    assert library.problems == ()
    assert {"dot_blue", "dot_yellow", "dot_grey", "step_1", "truck", "gift", "st_shipped"} <= library.names
    assert all(len(icon.data) <= MAX_ICON_BYTES for icon in library.files.values())
    # The folder holds exactly the rendered set: every icon of tools/icons/icons.json (with the white-glyph variant
    # "<name>_w" of those marked "white"), nothing else.
    spec = json.loads((ROOT / "tools" / "icons" / "icons.json").read_text(encoding="utf-8"))
    names = [icon["name"] for icon in spec["icons"]]
    names += [icon["name"] + WHITE_SUFFIX for icon in spec["icons"] if icon.get("white")]
    assert len(names) == len(set(names))
    assert library.names == set(names)
    assert all(icon["color"] in spec["colors"] for icon in spec["icons"])
    # The badges of the coloured buttons have their white variant (a green check on a green button melts, a blue
    # arrow on a blurple one looks muddy).
    coloured = {"check", "cross", "alert", "flag", "trash", "door", "st_confirmed", "st_shipped", "arrow_right", "cart"}
    assert coloured <= {name.removesuffix(WHITE_SUFFIX) for name in library.names if name.endswith(WHITE_SUFFIX)}


def test_an_icon_records_its_uploaded_white_variant(tmp_path: Path) -> None:
    write_icons(tmp_path, "check", "check_w", "flag", "flag_w")
    library = load_icons(tmp_path, IconSettings(enabled=True))
    library.ids.update(check=111111111111111111, check_w=222222222222222222, flag=333333333333333333)
    assert library.text("check") == "<:check:111111111111111111>"
    assert library.text("flag") == "<:flag:333333333333333333>"  # its variant is not uploaded (yet)
    green = ButtonSpec(label="<:check:111111111111111111> Mark paid", custom_id="sb:x:a", style="success")
    red = ButtonSpec(label="<:flag:333333333333333333> Card problem", custom_id="sb:x:b", style="danger")
    assert shown_button(green).emoji == "<:check_w:222222222222222222>"
    assert shown_button(red).emoji == "<:flag:333333333333333333>"  # no variant: the badge as it is


def test_bad_icon_files_are_problems_not_failures(tmp_path: Path) -> None:
    write_icons(tmp_path, "ok_icon")
    (tmp_path / "emojis" / "Bad-Name.png").write_bytes(tiny_png("x"))
    (tmp_path / "emojis" / "text_file.png").write_bytes(b"not a picture")
    library = load_icons(tmp_path, IconSettings(enabled=True))
    assert library.names == {"ok_icon"}
    assert any("Bad-Name.png" in p for p in library.problems) and any("text_file.png" in p for p in library.problems)
    assert load_icons(tmp_path, IconSettings()).names == frozenset()  # off


def test_icon_text_and_fallbacks(tmp_path: Path) -> None:
    write_icons(tmp_path, "truck", "dot_blue")
    library = load_icons(tmp_path, IconSettings(enabled=True))
    assert library.text("truck") == "" and library.text("dot_blue") == "•"  # not uploaded yet
    library.ids["truck"] = 123456789012345678
    assert library.text("truck") == "<:truck:123456789012345678>"
    assert library.text("unknown") == ""


async def test_placeholders_render_in_text_kinds_only_and_unknown_icons_are_problems(tmp_path: Path) -> None:
    write_icons(tmp_path / "config", "truck")
    tenv = await make_test_env(tmp_path, settings=make_settings(ICONS_ON))
    try:
        app = tenv.app
        app.icons.ids["truck"] = 111111111111111111
        key = ck("x.description", Kind.DESCRIPTION, "core", "g", "doc", placeholders=("n",))
        assert validate_value(key, "{icon_truck} Order {n}", app.content.icon_names()) == []
        assert "icons that do not exist: nope" in validate_value(key, "{icon_nope} x", app.content.icon_names())[0]
        footer = ck("x.footer", Kind.FOOTER, "core", "g", "doc")
        assert "cannot show icons here" in validate_value(footer, "{icon_truck} Buy")[0]
        emoji = ck("x.emoji", Kind.EMOJI, "core", "g", "doc")
        assert validate_value(emoji, "{icon_truck}") == []
        # Rendering: a key with and without declared placeholders.
        app.content.replace(file_values={**tenv.content, "branding.footer_text": "{icon_truck} x"})
        assert app.content.render_icons("{icon_truck} Delivery") == "<:truck:111111111111111111> Delivery"
        assert app.content.render_icons("{icon_missing} Delivery") == "Delivery"
        # A button emoji that is an icon: the uploaded emoji, or no emoji at all until it is uploaded.
        app.content.replace(file_values={**tenv.content, "verification.button.emoji": "{icon_truck}"})
        assert app.content.render("verification.button.emoji") == "<:truck:111111111111111111>"
        app.icons.ids.clear()
        assert app.content.render("verification.button.emoji") is None
    finally:
        await tenv.close()


async def test_sync_uploads_missing_icons_once_and_replaces_changed_ones(tmp_path: Path) -> None:
    write_icons(tmp_path / "config", "truck", "dot_blue", "gift")
    tenv = await make_test_env(tmp_path, settings=make_settings(ICONS_ON))
    try:
        app, bot = tenv.app, tenv.bot
        bot.fail_emoji_upload.add("gift")
        assert sorted(await sync_application_emojis(app)) == ["dot_blue", "truck"]
        assert app.icons.text("truck").startswith("<:truck:") and app.icons.text("gift") == ""
        recorded = await app.db.run(repo.all_emojis)
        assert set(recorded) == {"dot_blue", "truck"}
        # The next start reuses them (no upload) and retries the failed one.
        bot.fail_emoji_upload.clear()
        bot.emoji_uploads.clear()
        app.icons.ids.clear()
        await load_uploaded_ids(app)
        assert set(app.icons.ids) == {"dot_blue", "truck"}
        assert await sync_application_emojis(app) == ["gift"]
        # A changed picture is uploaded again (new id); the old emoji is removed; unchanged ones keep their id.
        old_id, dot_id, gift_id = app.icons.ids["truck"], app.icons.ids["dot_blue"], app.icons.ids["gift"]
        (tmp_path / "config" / "emojis" / "truck.png").write_bytes(tiny_png("a new truck"))
        app.icons = keep_uploaded_ids(load_icons(tmp_path / "config", app.settings.icons), app.icons)  # as /reload
        assert app.icons.ids == {"dot_blue": dot_id, "gift": gift_id}
        assert await sync_application_emojis(app) == ["truck"]
        assert app.icons.ids["truck"] != old_id and old_id not in bot.app_emojis
    finally:
        await tenv.close()


def test_edit_text_matches_texts_with_icons() -> None:
    key = ck("x.title", Kind.DESCRIPTION, "core", "g", "doc")
    values = {"x.title": "{icon_truck} **Delivery options** for every order"}
    texts = ["<:truck:123456789012345678> **Delivery options** for every order"]
    assert edit_text.find_keys(texts, values, [key]) == ["x.title"]
