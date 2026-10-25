"""The #bot-log post of every audit entry shows the activity-log banner (one per entry); ``[banners] off`` can switch
it off for the audit channel alone, and the entry is still posted."""

from __future__ import annotations

from pathlib import Path

from tests.fakes import FakeTextChannel
from tests.helpers import TestEnv, banner_names, make_banner_env, provision_channels


def audit_channel(tenv: TestEnv) -> FakeTextChannel:
    channel = tenv.app.resources.text_channel(tenv.guild, "audit_log")  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    return channel


async def test_every_audit_entry_shows_the_activity_log_banner(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path)
    try:
        await provision_channels(tenv)
        banner = tenv.app.ui.banner("activity-log")
        assert banner is not None
        await tenv.app.audit.record("checkout.order_paid", actor_id=1, target_type="order", target_id=1)
        await tenv.app.audit.record("operations.error", actor_id=None, details={"error_id": "abc"})
        posted = audit_channel(tenv).messages
        assert len(posted) == 2
        assert all(banner_names(message) == [banner.filename] for message in posted)
        assert [message.embeds[0].title for message in posted] == ["checkout.order_paid", "operations.error"]
    finally:
        await tenv.close()


async def test_the_audit_banner_can_be_switched_off_alone(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, {"banners": {"off": ["activity-log"]}})
    try:
        await provision_channels(tenv)
        assert tenv.app.ui.banner("activity-log") is None and tenv.app.ui.banner("error") is not None
        await tenv.app.audit.record("setup.run", actor_id=1)
        [posted] = audit_channel(tenv).messages
        assert banner_names(posted) == [] and posted.embeds[0].title == "setup.run"
    finally:
        await tenv.close()
