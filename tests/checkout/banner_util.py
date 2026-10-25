"""Helpers for the checkout banner tests: environments with banners switched on, and what an edit uploaded."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.config.banner_ids import BannerId
from tests.fakes import FakeMessage
from tests.helpers import TestEnv, banner_names, deep_merge, make_banner_env, provision_channels


async def make_banner_checkout_env(tmp_path: Path, overrides: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    """Like ``tests.checkout.util.make_env``, with banners switched on (a tiny picture for every banner id)."""
    tenv = await make_banner_env(tmp_path, deep_merge({"bot": {"rate_limit_per_10s": 100}}, overrides or {}), **kwargs)
    await provision_channels(tenv)
    return tenv


def banner_file(tenv: TestEnv, banner_id: BannerId) -> str:
    """The attachment name the banner ``banner_id`` is uploaded with."""
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None, banner_id
    return banner.filename


def shows(tenv: TestEnv, message: Any, banner_id: BannerId | None) -> bool:
    """``message`` carries exactly the banner ``banner_id`` (None: no banner)."""
    return banner_names(message) == ([] if banner_id is None else [banner_file(tenv, banner_id)])


class Watch:
    """The edits of one message since the last check: which pictures they uploaded."""

    def __init__(self, tenv: TestEnv, message: FakeMessage) -> None:
        self.tenv = tenv
        self.message = message
        self.seen = len(message.edits)

    def _uploads(self) -> tuple[int, list[str]]:
        edits = self.message.edits[self.seen :]
        self.seen = len(self.message.edits)
        names = [
            item.filename for edit in edits for item in edit.get("attachments") or [] if isinstance(item, discord.File)
        ]
        return len(edits), names

    def kept(self, banner_id: BannerId) -> bool:
        """Edited, still showing ``banner_id``, which was not uploaded again."""
        edits, names = self._uploads()
        return edits > 0 and names == [] and shows(self.tenv, self.message, banner_id)

    def swapped(self, banner_id: BannerId) -> bool:
        """Edited to show ``banner_id`` instead of the earlier banner: that picture alone was uploaded, once."""
        edits, names = self._uploads()
        return edits > 0 and names == [banner_file(self.tenv, banner_id)] and shows(self.tenv, self.message, banner_id)
