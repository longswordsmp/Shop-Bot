"""Helpers shared by the catalog tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features import all_modules
from shopbot.features.catalog import repo
from shopbot.features.catalog.repo import Product
from tests.fakes import FakeBot, FakeInteraction, FakeMember, FakeMessage, FakeRole, FakeTextChannel
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env, provision_channels

MANAGER_SETTINGS: dict[str, Any] = {
    "server": {"roles": [{"key": "staff"}, {"key": "catalog_manager"}]},
    "catalog": {"manager_roles": ["catalog_manager"]},
}


async def manager_env(tmp_path: Path, overrides: dict[str, Any] | None = None) -> tuple[TestEnv, FakeRole]:
    """A test env whose settings define a 'catalog_manager' role listed in catalog.manager_roles."""
    settings: dict[str, Any] = dict(MANAGER_SETTINGS)
    if overrides:
        settings.update(overrides)
    tenv = await make_test_env(tmp_path, settings=make_settings(settings))
    role = tenv.guild.add_role(name="catalog-manager", position=8)
    await tenv.app.resources.remember("role", "catalog_manager", role.id)
    return tenv, role


async def add_product(
    tenv: TestEnv,
    name: str = "[p]",
    *,
    price: int = 1000,
    stock: int | None = None,
    reserved: int = 0,
    **kwargs: Any,
) -> Product:
    now = tenv.clock.now()
    product = await tenv.app.db.transaction(
        repo.create_product, GUILD_ID, name=name, price_minor=price, stock_on_hand=stock, now=now, **kwargs
    )
    if reserved:
        assert await tenv.app.db.transaction(repo.reserve, product.id, reserved, now)
    return await get(tenv, product.id)


async def get(tenv: TestEnv, product_id: int) -> Product:
    product = await tenv.app.db.run(repo.get_product, GUILD_ID, product_id)
    assert product is not None
    return product


async def listing_channel(tenv: TestEnv) -> FakeTextChannel:
    await provision_channels(tenv)
    channel = tenv.app.resources.text_channel(tenv.guild, "catalog")  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    return channel


def product_command(app: App, name: str) -> Any:
    assert app.bot is not None
    group = app.bot.tree.get_command("product", guild=app.guild_object)
    assert group is not None
    command = group.get_command(name)
    assert command is not None
    return command


async def invoke(tenv: TestEnv, user: FakeMember, name: str, **kwargs: Any) -> FakeInteraction:
    """Run /product <name> like Discord would: checks first (errors go to the tree handler), then the callback."""
    command = product_command(tenv.app, name)
    interaction = FakeInteraction(
        client=tenv.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        allowed = await command._check_can_run(interaction)
    except discord.app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    assert allowed
    await command.callback(interaction, **kwargs)
    return interaction


def buttons(message: FakeMessage) -> list[tuple[str | None, str | None, bool]]:
    """(custom_id, label, disabled) of every button in a message."""
    out: list[tuple[str | None, str | None, bool]] = []
    for child in message.components():
        item = getattr(child, "item", child)
        out.append((getattr(item, "custom_id", None), getattr(item, "label", None), bool(item.disabled)))
    return out


def embed_texts(message: FakeMessage) -> list[str]:
    return [e.title or "" for e in message.embeds]


async def audit_actions(tenv: TestEnv, prefix: str = "catalog.") -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e for e in reversed(events) if e.action.startswith(prefix)]


def restarted_app(tenv: TestEnv) -> App:
    """A fresh App on the same database and guild, as after a process restart."""
    old = tenv.app
    app = App(
        env=old.env,
        settings=old.settings,
        content_values=dict(tenv.content),
        overrides={},
        db=old.db,
        clock=tenv.clock,
        modules=all_modules(),
        config_dir=old.config_dir,
    )
    FakeBot(app, tenv.guild)
    for module in app.modules:
        if module.register is not None:
            module.register(app)
    app.scheduler.bind(app)
    return app
