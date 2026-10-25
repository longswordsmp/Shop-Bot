"""Regression tests for defects found by the adversarial review of the foundation."""

from __future__ import annotations

import asyncio
import shutil
import tomllib
import types
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.content import ContentStore, flatten
from shopbot.config.content_keys import Kind, Registry, ck
from shopbot.core.channels import BOT_ALLOW, CHANNEL_NAME_MAX, OWNER_ALLOW, render_channel_name
from shopbot.core.panels import remove_message, upsert_message
from shopbot.core.permissions import BOT_PERMISSIONS
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import state
from shopbot.features.server_keys import effective_access, private_channel_problems, server_problems
from shopbot.ui.spec import EmbedSpec, MessageSpec
from tests.fakes import http_error
from tests.helpers import ROOT, TestEnv, make_settings


def test_bot_overwrite_never_grants_manage_permissions() -> None:
    # Discord rejects (403) a Manage Permissions grant in an overwrite from a non-administrator bot.
    assert "manage_roles" not in BOT_ALLOW


def test_owner_overwrite_denies_thread_creation() -> None:
    assert OWNER_ALLOW["create_public_threads"] is False
    assert OWNER_ALLOW["create_private_threads"] is False


def test_invite_includes_permissions_setup_sets_on_everyone() -> None:
    names = {name for group in BOT_PERMISSIONS.values() for name in group}
    assert {"add_reactions", "create_public_threads", "create_private_threads"} <= names


def test_channel_names_are_never_truncated() -> None:
    name = render_channel_name("{prefix}-{user}-{number}", prefix="a" * 60, number=1234, padding=10, user="u" * 32)
    assert len(name) > CHANNEL_NAME_MAX
    assert name.endswith("0000001234")


@pytest.mark.parametrize("kind", [Kind.TITLE, Kind.DESCRIPTION, Kind.FIELD_NAME, Kind.FIELD_VALUE, Kind.BUTTON_LABEL])
def test_whitespace_only_values_are_invalid_for_every_kind(kind: Kind) -> None:
    key = ck("x.y", kind, "f", "g", "doc")
    store = ContentStore(Registry([key]), {"x.y": "   "})
    problems = store.value_problems()
    assert problems and "whitespace" in problems[0].message


def test_all_configured_values_keeps_operator_and_unregistered_text() -> None:
    key = ck("a.b", Kind.TITLE, "f", "g", "doc")
    store = ContentStore(
        Registry([key]), {"a.b": "A", "gone.key": "kept", "operator.x.y": "op", "empty.key": ""}, {"a.b": "B"}
    )
    assert store.all_configured_values() == {"a.b": "B", "gone.key": "kept", "operator.x.y": "op"}


def test_checklist_template_preserves_all_text_and_honours_config_dir(tmp_path: Path) -> None:
    from tools import checklist

    config = tmp_path / "cfg"
    config.mkdir()
    shutil.copy(ROOT / "config" / "settings.toml", config / "settings.toml")
    (config / "content.toml").write_text(
        '[tickets.panel]\ntitle = "Support ✨"\n'
        '[tickets.category.removed]\nbutton_label = "Kept text"\n'
        '[operator.server.setup]\nbusy = "Op override"\n',
        encoding="utf-8",
    )
    before = (ROOT / "config" / "content.toml").read_bytes()
    assert checklist.main(["--config-dir", str(config), "--template"]) == 0
    assert (ROOT / "config" / "content.toml").read_bytes() == before  # the repository file is untouched
    with (config / "content.toml").open("rb") as handle:
        values, errors = flatten(tomllib.load(handle))
    assert errors == []
    assert values["tickets.panel.title"] == "Support ✨"
    assert values["tickets.category.removed.button_label"] == "Kept text"
    assert values["operator.server.setup.busy"] == "Op override"


def test_audit_channel_must_be_private() -> None:
    public = make_settings({"audit": {"channel": "shop"}})
    assert effective_access(public, "shop") == "public_readonly"
    assert any("audit.channel" in p for p in server_problems(public))
    private = make_settings()
    assert private_channel_problems(private, (("audit.channel", private.audit.channel),)) == []


async def test_audit_is_not_posted_to_a_public_channel(tmp_path: Path) -> None:
    from tests.helpers import make_test_env, provision_channels

    tenv = await make_test_env(tmp_path, settings=make_settings({"audit": {"channel": "shop"}}))
    try:
        await provision_channels(tenv)
        await tenv.app.audit.record("test.action", actor_id=1, target_type="x", target_id=1)
        shop = tenv.app.resources.text_channel(tenv.guild, "shop")
        assert shop is not None and shop.messages == []
        events = await tenv.app.db.run(audit_repo.recent, tenv.app.env.guild_id)
        assert [e.action for e in events] == ["test.action"]  # still recorded in the database
    finally:
        await tenv.close()


async def test_remove_message_keeps_record_when_delete_fails(tenv: TestEnv) -> None:
    app = tenv.app
    channel = tenv.guild.add_text_channel(name="panel")
    message, _ = await upsert_message(app, "panel:y", channel, MessageSpec(embeds=(EmbedSpec(title="a"),)))

    async def refuse(**_: Any) -> None:
        raise http_error(discord.Forbidden, 403, 50013, "Missing Permissions")

    message.delete = refuse  # type: ignore[method-assign]
    with pytest.raises(discord.Forbidden):
        await remove_message(app, "panel:y")
    assert await app.db.run(state.published_get, app.env.guild_id, "panel:y") is not None
    del message.delete  # restore the class method
    assert await remove_message(app, "panel:y") is True
    assert await app.db.run(state.published_get, app.env.guild_id, "panel:y") is None


async def test_startup_recovery_waits_for_an_available_guild(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from shopbot.bot import ShopBot

    calls: list[str] = []

    async def recovery(app: Any) -> None:
        calls.append("recovered")

    app = tenv.app
    app.modules = [replace(m, on_ready=recovery if m.name == "tickets" else None) for m in app.modules]
    bot = ShopBot(app)
    started: list[bool] = []
    monkeypatch.setattr(app.scheduler, "start", lambda app_: started.append(True))
    guild = types.SimpleNamespace(id=app.env.guild_id, unavailable=True)
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: guild)

    await bot.on_ready()  # guild unavailable: no recovery, no scheduler
    assert calls == [] and started == []

    guild.unavailable = False
    await asyncio.gather(bot.on_guild_available(guild), bot.on_guild_available(guild))  # type: ignore[arg-type]
    assert calls == ["recovered"] and started == [True]  # exactly once

    await bot.on_ready()  # a later READY (reconnect) does not re-run recovery
    assert calls == ["recovered"]


async def test_scheduler_starts_even_if_recovery_fails(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from shopbot.bot import ShopBot

    app = tenv.app
    bot = ShopBot(app)
    started: list[bool] = []
    monkeypatch.setattr(app.scheduler, "start", lambda app_: started.append(True))

    async def broken() -> None:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(app.resources, "refresh", broken)
    guild = types.SimpleNamespace(id=app.env.guild_id, unavailable=False)
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: guild)
    await bot.on_ready()
    assert started == [True]
