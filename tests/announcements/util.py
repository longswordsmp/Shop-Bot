"""Helpers shared by the /announce tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features import all_modules
from shopbot.features.announcements import repo
from tests.fakes import (
    FakeBot,
    FakeInteraction,
    FakeMember,
    FakeRole,
    FakeTextChannel,
    modal_input_ids,
    modal_of,
    submit_modal,
)
from tests.helpers import (
    GUILD_ID,
    TestEnv,
    deep_merge,
    make_settings,
    make_test_env,
    provision_channels,
    server_extension,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
CDN_URL = "https://cdn.discordapp.com/ephemeral-attachments/1/2/picture.png"

FIELDS: list[dict[str, Any]] = [
    {"id": "when", "style": "short", "required": True, "max_length": 100},
    {"id": "details", "style": "paragraph", "required": False, "max_length": 1024},
]

BASE_OVERRIDES: dict[str, Any] = deep_merge(
    server_extension(
        roles=[{"key": "announcer"}, {"key": "alerts", "mentionable": True}, {"key": "quiet"}],
        channels=[{"key": "announcements", "category": "support", "access": "inherit"}],
    ),
    {
        "features": {"announcements": True},
        "announcements": {
            "channel": "announcements",
            "roles": ["announcer"],
            "mention_roles": ["alerts", "quiet"],
            "fields": FIELDS,
        },
    },
)


class FakeCdn:
    """Stands in for ``bot.http.get_from_cdn`` (downloads of uploaded attachments)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail: BaseException | None = None
        self.requests: list[str] = []

    async def get_from_cdn(self, url: str) -> bytes:
        self.requests.append(url)
        if self.fail is not None:
            raise self.fail
        if url not in self.files:
            raise _not_found()
        return self.files[url]


def _not_found() -> discord.NotFound:
    from tests.fakes import http_error

    error: discord.NotFound = http_error(discord.NotFound, 404, 0, "asset not found")
    return error


@dataclass
class Upload:
    """What the ``image`` option of /announce provides (``discord.Attachment``)."""

    filename: str = "picture.png"
    url: str = CDN_URL
    size: int = len(PNG)
    content_type: str | None = "image/png"


@dataclass
class AnnounceEnv:
    tenv: TestEnv
    channel: FakeTextChannel
    announcer_role: FakeRole
    alerts_role: FakeRole
    quiet_role: FakeRole
    cdn: FakeCdn

    @property
    def app(self) -> App:
        return self.tenv.app

    def staff(self, name: str = "staff") -> FakeMember:
        return self.tenv.member(name=name, staff=True)

    def customer(self, name: str = "customer") -> FakeMember:
        return self.tenv.member(name=name)

    def announcer(self) -> FakeMember:
        return self.tenv.guild.add_member(name="announcer", roles=[self.announcer_role])

    def text(self, key: str) -> str:
        return self.tenv.text(key)

    async def close(self) -> None:
        await self.tenv.close()


async def announce_env(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    content: dict[str, str] | None = None,
    fill_content: bool = True,
) -> AnnounceEnv:
    settings = make_settings(deep_merge(BASE_OVERRIDES, overrides or {}))
    tenv = await make_test_env(tmp_path, settings=settings, content=content, fill_content=fill_content)
    await provision_channels(tenv)
    roles: dict[str, FakeRole] = {}
    for key in ("announcer", "alerts", "quiet"):
        role = tenv.guild.add_role(name=f"role-{key}", position=5)
        await tenv.app.resources.remember("role", key, role.id)
        roles[key] = role
    roles["alerts"].mentionable = True
    channel = tenv.app.resources.text_channel(tenv.guild, "announcements")  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    cdn = FakeCdn()
    cdn.files[CDN_URL] = PNG
    tenv.bot.http = cdn  # type: ignore[assignment]
    return AnnounceEnv(tenv, channel, roles["announcer"], roles["alerts"], roles["quiet"], cdn)


def announce_command(app: App) -> Any:
    assert app.bot is not None
    command = app.bot.tree.get_command("announce", guild=app.guild_object)
    assert command is not None
    return command


async def invoke(env: AnnounceEnv, user: Any, **kwargs: Any) -> FakeInteraction:
    """Run /announce like Discord would: checks first (errors go to the tree handler), then the callback."""
    command = announce_command(env.app)
    interaction = FakeInteraction(
        client=env.app.bot, user=user, guild=env.tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        allowed = await command._check_can_run(interaction)
    except discord.app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    assert allowed
    await command.callback(interaction, **kwargs)
    return interaction


async def open_form(env: AnnounceEnv, user: Any, **kwargs: Any) -> discord.ui.Modal:
    interaction = await invoke(env, user, **kwargs)
    modal = modal_of(interaction)
    assert modal is not None, [m.text() for m in interaction.sent]
    return modal


def form_fields(title: str = "[title]", message: str = "[message]", **values: str) -> dict[str, str]:
    fields = {"title": title, "message": message}
    for field_id, value in values.items():
        fields[f"field_{field_id}"] = value
    return fields


async def submit(
    env: AnnounceEnv, user: Any, modal_or_id: discord.ui.Modal | str, fields: dict[str, str], app: App | None = None
) -> FakeInteraction:
    custom_id = modal_or_id if isinstance(modal_or_id, str) else str(modal_or_id.custom_id)
    return await submit_modal(app or env.app, user=user, custom_id=custom_id, fields=fields)


def input_ids(modal: discord.ui.Modal) -> list[str]:
    return modal_input_ids(modal)


def inputs_by_id(modal: discord.ui.Modal) -> dict[str, discord.ui.TextInput[Any]]:
    return {
        str(item.custom_id): item
        for item in modal.walk_children()
        if isinstance(item, discord.ui.TextInput) and item.custom_id
    }


def reply_title(interaction: FakeInteraction) -> str | None:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].title


def reply_text(interaction: FakeInteraction) -> str:
    assert interaction.last_sent is not None
    return interaction.last_sent.text()


def token_of(modal: discord.ui.Modal) -> str:
    return str(modal.custom_id).rsplit(":", 1)[-1]


async def row(env: AnnounceEnv, token: str) -> repo.Announcement:
    item = await env.app.db.run(repo.get_by_token, GUILD_ID, token)
    assert item is not None
    return item


async def rows(env: AnnounceEnv) -> list[dict[str, Any]]:
    def fetch(conn: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute("SELECT * FROM announcements ORDER BY id")]

    return await env.app.db.run(fetch)


async def audit_events(env: AnnounceEnv, prefix: str = "announcements.") -> list[audit_repo.AuditEvent]:
    events = await env.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e for e in reversed(events) if e.action.startswith(prefix)]


def restarted_app(env: AnnounceEnv) -> App:
    """A fresh App on the same database and guild, as after a process restart."""
    old = env.app
    app = App(
        env=old.env,
        settings=old.settings,
        content_values=dict(env.tenv.content),
        overrides={},
        db=old.db,
        clock=env.tenv.clock,
        modules=all_modules(),
        config_dir=old.config_dir,
    )
    bot = FakeBot(app, env.tenv.guild)
    bot.http = env.cdn  # type: ignore[assignment]
    for module in app.modules:
        if module.register is not None:
            module.register(app)
    app.scheduler.bind(app)
    return app


def button_ids(interaction: FakeInteraction) -> list[str]:
    assert interaction.last_sent is not None
    return interaction.last_sent.custom_ids()


def link_urls(interaction: FakeInteraction) -> list[str]:
    assert interaction.last_sent is not None
    return [
        str(child.url)
        for child in interaction.last_sent.components()
        if isinstance(child, discord.ui.Button) and child.url
    ]
