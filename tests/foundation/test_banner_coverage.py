"""Every message the bot can send shows a banner, except continuation messages (part 2 and later of one panel)."""

from __future__ import annotations

import re

from shopbot.ui.spec import MessageSpec
from tools.offline import ROOT, build_offline_app

# Later parts of a panel or record spread over several Discord messages: only the first part shows the banner.
CONTINUATION = re.compile(r"(_part_[2-9]\d*$|_message_([2-9]|\d{2,})$|^problems/details\.)")


async def test_every_message_state_has_a_banner_and_continuations_have_none() -> None:
    app = await build_offline_app(ROOT / "config", content_mode="fixtures", all_features=True)
    try:
        assert app.banners.enabled and len(app.banners.files) == 31 and app.banners.problems == ()
        missing: list[str] = []
        extra: list[str] = []
        checked = 0
        for module in app.modules:
            if module.preview is None:
                continue
            for state in module.preview(app):
                if not isinstance(state.spec, MessageSpec):
                    continue  # forms cannot show pictures
                checked += 1
                name = f"{state.feature}/{state.name}"
                if CONTINUATION.search(name):
                    if state.spec.banner is not None:
                        extra.append(name)
                elif state.spec.banner is None:
                    missing.append(name)
        assert checked > 200
        assert missing == [], "messages without a banner"
        assert extra == [], "continuation messages must not repeat the banner"
    finally:
        await app.db.close()
