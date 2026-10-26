"""Every message the bot can send shows a banner, except continuation messages (part 2 and later of one panel), and
the banner closes the message's last card unless that card shows a picture of its own."""

from __future__ import annotations

import re
from dataclasses import replace

from shopbot.ui.banners import banner_inside, banner_url, place_banner
from shopbot.ui.discord_render import send_kwargs
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import MessageSpec
from tools.offline import ROOT, build_offline_app

# Later parts of a panel or record spread over several Discord messages: only the first part shows the banner.
CONTINUATION = re.compile(r"(_part_[2-9]\d*$|_message_([2-9]|\d{2,})$|^problems/details\.)")
# The guides close with their banner: of a guide's messages (``<name>_message_<n>``) only the last shows it.
GUIDES = ("how_to_buy/", "faq/", "shipping/")
GUIDE_PART = re.compile(r"^(?P<base>.+)_message_(?P<n>\d+)$")
# Parts numbered after an unnumbered first message (the largest order's workflow message and record): part 1 is
# already the second message.
AFTER_FIRST = "_part_1"
# Short-lived pings deleted seconds later: no picture, so they stay light (REDESIGN_PLAN 4.1).
NO_BANNER = {"verification/welcome_mention"}


async def test_every_message_state_has_a_banner_and_continuations_have_none() -> None:
    app = await build_offline_app(ROOT / "config", content_mode="fixtures", all_features=True)
    try:
        assert app.banners.enabled and len(app.banners.files) == 31 and app.banners.problems == ()
        missing: list[str] = []
        extra: list[str] = []
        checked = 0
        states = [state for module in app.modules if module.preview is not None for state in module.preview(app)]
        names = {f"{state.feature}/{state.name}" for state in states}

        last_part: dict[str, int] = {}
        for name in names:
            part = GUIDE_PART.match(name)
            if part and name.startswith(GUIDES):
                last_part[part["base"]] = max(last_part.get(part["base"], 0), int(part["n"]))

        def continuation(name: str) -> bool:
            part = GUIDE_PART.match(name)
            if part and name.startswith(GUIDES):
                return int(part["n"]) != last_part[part["base"]]
            after_first = name.endswith(AFTER_FIRST) and name.removesuffix(AFTER_FIRST) in names
            return after_first or CONTINUATION.search(name) is not None

        for state in states:
            if not isinstance(state.spec, MessageSpec):
                continue  # forms cannot show pictures
            checked += 1
            name = f"{state.feature}/{state.name}"
            if continuation(name) or name in NO_BANNER:
                if state.spec.banner is not None:
                    extra.append(name)
            elif state.spec.banner is None:
                missing.append(name)
        assert checked > 200
        assert missing == [], "messages without a banner"
        assert extra == [], "continuation messages must not repeat the banner"
    finally:
        await app.db.close()


async def test_every_banner_closes_its_card_unless_the_card_shows_a_picture_of_its_own() -> None:
    """As sent (``place_banner``), a banner is the image of the message's last embed; it stays above the embeds only
    when that embed shows its own picture (the welcome image, an announcement image). Placing it never changes what
    the limits check finds."""
    app = await build_offline_app(ROOT / "config", content_mode="fixtures", all_features=True)
    try:
        inside: list[str] = []
        above: list[str] = []
        for module in app.modules:
            if module.preview is None:
                continue
            for state in module.preview(app):
                spec = state.spec
                if not isinstance(spec, MessageSpec) or spec.banner is None:
                    continue
                name = f"{state.feature}/{state.name}"
                placed = place_banner(spec)
                assert validate_message(placed) == validate_message(spec), name
                assert placed.banner == spec.banner and placed.files == spec.files and placed.rows == spec.rows, name
                if spec.embeds[-1].image_url or not spec.banner_in_card:
                    # Its own picture, or a product card (whose picture slot the banner must never fill).
                    assert placed == spec and not banner_inside(placed), name
                    above.append(name)
                else:
                    assert placed.embeds[:-1] == spec.embeds[:-1], name
                    assert placed.embeds[-1] == replace(spec.embeds[-1], image_url=banner_url(spec.banner)), name
                    inside.append(name)
                    if validate_message(spec):
                        continue  # reported by the checker; not sendable
                    kwargs = send_kwargs(spec)
                    assert kwargs["files"][0].filename == spec.banner.filename, name
                    assert kwargs["embeds"][-1].image.url == banner_url(spec.banner), name
        assert len(inside) > 200
        assert set(above) >= {"verification/welcome_panel", "announcements/announcement"}
    finally:
        await app.db.close()


async def test_product_cards_keep_the_banner_above_so_it_is_never_taken_for_the_product_photo() -> None:
    """The private catalog pages and the #new-arrivals updates show one product card each: even without a product
    picture, the banner stays above the card (the picture slot at its bottom belongs to the product)."""
    app = await build_offline_app(ROOT / "config", content_mode="fixtures", all_features=True)
    try:
        checked = []
        for module in app.modules:
            if module.preview is None or module.name not in ("catalog", "new_arrivals"):
                continue
            for state in module.preview(app):
                spec = state.spec
                product_card = state.name.startswith(("browse_", "update_")) and state.name != "browse_empty"
                if not product_card or not isinstance(spec, MessageSpec) or spec.banner is None:
                    continue
                assert not spec.banner_in_card, state.name
                assert place_banner(spec) == spec and not banner_inside(spec), state.name
                checked.append(state.name)
        assert any(n.startswith("update_") for n in checked)
        # The private catalog pages (browse_enabled is off in the shipped settings, so built directly).
        from shopbot.features.catalog import views as catalog_views
        from shopbot.features.catalog.preview import SAMPLE_PRODUCTS

        page = catalog_views.browse_page(app.ui, SAMPLE_PRODUCTS[1], 1, len(SAMPLE_PRODUCTS))
        assert page.banner is not None and not page.embeds[-1].image_url
        assert not page.banner_in_card and place_banner(page) == page
    finally:
        await app.db.close()
