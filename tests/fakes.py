"""Test doubles for discord.py objects.

Role, Member, TextChannel, and CategoryChannel fakes subclass the real discord.py classes (without
calling their constructors) so ``isinstance`` checks in production code behave exactly as in Discord.
Interaction fakes enforce Discord's response rules: a second initial response raises
``discord.InteractionResponded``, catching double-reply bugs in tests.

Nothing here talks to Discord. Passing tests with these fakes is evidence of correct logic, not proof
that the bot works in a real server.
"""

from __future__ import annotations

import itertools
import types
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands

_ids = itertools.count(300_000_000_000_000_001)


def next_id() -> int:
    return next(_ids)


def http_error(cls: type[discord.HTTPException], status: int = 404, code: int = 0, message: str = "error") -> Any:
    response = types.SimpleNamespace(status=status, reason=message)
    return cls(response, {"code": code, "message": message})  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------
# Roles and members
# --------------------------------------------------------------------------------------------------


class FakeRole(discord.Role):
    def __init__(
        self,
        guild: FakeGuild,
        *,
        id: int | None = None,
        name: str = "role",
        position: int = 1,
        permissions: discord.Permissions | None = None,
        managed: bool = False,
    ) -> None:
        self.id = id or next_id()
        self.name = name
        self.position = position
        self.guild = guild  # type: ignore[assignment]
        self.managed = managed
        self.hoist = False
        self.mentionable = False
        self._permissions = (permissions or discord.Permissions.none()).value
        self._colour = 0
        self.tags = None
        self._flags = 0
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    async def edit(self, **kwargs: Any) -> FakeRole:  # type: ignore[override]
        self.edits.append(kwargs)
        if "name" in kwargs:
            self.name = kwargs["name"]
        if "permissions" in kwargs:
            self._permissions = kwargs["permissions"].value
        if "hoist" in kwargs:
            self.hoist = kwargs["hoist"]
        if "mentionable" in kwargs:
            self.mentionable = kwargs["mentionable"]
        for key in ("colour", "color"):
            if key in kwargs:
                value = kwargs[key]
                self._colour = value.value if isinstance(value, discord.Colour) else int(value or 0)
        return self

    async def delete(self, *, reason: str | None = None) -> None:
        self.deleted = True
        self.guild._roles.pop(self.id, None)  # type: ignore[attr-defined]


class FakeMember(discord.Member):
    def __init__(
        self,
        guild: FakeGuild,
        *,
        id: int | None = None,
        name: str = "member",
        roles: list[FakeRole] | None = None,
        administrator: bool = False,
        bot: bool = False,
        permissions: discord.Permissions | None = None,
    ) -> None:
        self._fake_id = id or next_id()
        self._fake_name = name
        self.guild = guild  # type: ignore[assignment]
        self._fake_roles: list[FakeRole] = list(roles or [])
        self._fake_admin = administrator
        self._fake_bot = bot
        self._fake_permissions = permissions
        self.dms: list[FakeMessage] = []
        self.dm_forbidden = False

    # identity --------------------------------------------------------------------------------
    @property
    def id(self) -> int:  # type: ignore[override]
        return self._fake_id

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._fake_name

    @property
    def display_name(self) -> str:
        return self._fake_name

    @property
    def global_name(self) -> str | None:  # type: ignore[override]
        return None

    @property
    def mention(self) -> str:
        return f"<@{self._fake_id}>"

    @property
    def bot(self) -> bool:  # type: ignore[override]
        return self._fake_bot

    @property
    def display_avatar(self) -> Any:  # type: ignore[override]
        return types.SimpleNamespace(url="https://cdn.discordapp.com/embed/avatars/0.png")

    # roles & permissions ---------------------------------------------------------------------
    @property
    def roles(self) -> list[discord.Role]:  # type: ignore[override]
        return [self.guild.default_role, *sorted(self._fake_roles, key=lambda r: r.position)]

    @property
    def top_role(self) -> discord.Role:
        return max(self.roles, key=lambda r: (r.position, -r.id))

    @property
    def guild_permissions(self) -> discord.Permissions:
        if self._fake_admin:
            return discord.Permissions.all()
        if self._fake_permissions is not None:
            return self._fake_permissions
        value = 0
        for role in self.roles:
            value |= role.permissions.value
        return discord.Permissions(value)

    def add_role(self, role: FakeRole) -> None:
        self._fake_roles.append(role)

    async def add_roles(self, *roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        self._fake_roles.extend(roles)

    async def remove_roles(self, *roles: Any, reason: str | None = None, atomic: bool = True) -> None:
        for role in roles:
            if role in self._fake_roles:
                self._fake_roles.remove(role)

    # DMs -------------------------------------------------------------------------------------
    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:  # type: ignore[override]
        if self.dm_forbidden:
            raise http_error(discord.Forbidden, 403, 50007, "Cannot send messages to this user")
        message = FakeMessage(None, author=self.guild.me, content=content, **_message_kwargs(kwargs))
        self.dms.append(message)
        return message

    def __hash__(self) -> int:  # discord.Member hashes its user; the fake hashes its id
        return hash(self._fake_id)

    def __repr__(self) -> str:
        return f"<FakeMember id={self._fake_id} name={self._fake_name!r}>"


# --------------------------------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------------------------------


def _message_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    if kwargs.get("file") is not None and kwargs.get("files") is not None:
        raise TypeError("Cannot mix file and files keyword arguments.")  # as discord.py does
    out: dict[str, Any] = {}
    if "embeds" in kwargs and kwargs["embeds"] is not None:
        out["embeds"] = list(kwargs["embeds"])
    elif "embed" in kwargs and kwargs["embed"] is not None:
        out["embeds"] = [kwargs["embed"]]
    out["view"] = kwargs.get("view")
    out["files"] = list(kwargs.get("files") or ([kwargs["file"]] if kwargs.get("file") else []))
    out["allowed_mentions"] = kwargs.get("allowed_mentions")
    return out


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        url: str = "https://cdn.discordapp.com/attachments/1/2/file",
        *,
        file: discord.File | None = None,
    ) -> None:
        self.id = next_id()
        self.filename = filename
        self.url = url
        self.size = 1
        self.content_type = None
        self.file = file  # the uploaded file this attachment came from (bot messages)


class FakeMessage:
    def __init__(
        self,
        channel: FakeTextChannel | None,
        *,
        id: int | None = None,
        author: Any = None,
        content: str | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        files: list[discord.File] | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        attachments: list[FakeAttachment] | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id or next_id()
        self.channel = channel
        self.guild = channel.guild if channel is not None else None
        self.author = author
        self.content = content or ""
        self.embeds: list[discord.Embed] = list(embeds or [])
        self.view = view
        self.files = list(files or [])
        self.allowed_mentions = allowed_mentions
        # Uploaded files become the message's attachments (a user message passes its attachments directly).
        self.attachments = (
            list(attachments) if attachments is not None else [FakeAttachment(f.filename, file=f) for f in self.files]
        )
        self.created_at = created_at or datetime.now(tz=UTC)
        self.edits: list[dict[str, Any]] = []
        self.deleted = False
        self.flags = discord.MessageFlags()
        self.type = discord.MessageType.default

    @property
    def jump_url(self) -> str:
        channel_id = self.channel.id if self.channel else 0
        return f"https://discord.com/channels/0/{channel_id}/{self.id}"

    async def edit(self, **kwargs: Any) -> FakeMessage:
        if self.deleted:
            raise http_error(discord.NotFound, 404, 10008, "Unknown Message")
        self.edits.append(kwargs)
        if "embeds" in kwargs:
            self.embeds = list(kwargs["embeds"] or [])
        if "view" in kwargs:
            self.view = kwargs["view"]
        if "content" in kwargs:
            self.content = kwargs["content"] or ""
        if "attachments" in kwargs:  # Discord keeps exactly these: kept attachments and newly uploaded files
            files: list[discord.File] = []
            attachments: list[FakeAttachment] = []
            for item in kwargs["attachments"] or []:
                if isinstance(item, FakeAttachment):
                    if item not in self.attachments:
                        raise http_error(discord.HTTPException, 400, 50035, "Invalid Form Body (unknown attachment)")
                    if item.file is not None:
                        files.append(item.file)
                    attachments.append(item)
                else:
                    files.append(item)
                    attachments.append(FakeAttachment(item.filename, file=item))
            self.files = files
            self.attachments = attachments
        return self

    async def fetch(self) -> FakeMessage:
        """``PartialMessage.fetch``: the current message (the fakes hand out full messages as partial ones)."""
        if self.deleted:
            raise http_error(discord.NotFound, 404, 10008, "Unknown Message")
        return self

    async def delete(self, *, delay: float | None = None) -> None:
        self.deleted = True
        if self.channel is not None and self in self.channel.messages:
            self.channel.messages.remove(self)

    # helpers for assertions ----------------------------------------------------------------
    def custom_ids(self) -> list[str]:
        if self.view is None:
            return []
        return [cid for item in self.view.children if (cid := getattr(item, "custom_id", None))]

    def components(self) -> list[Any]:
        return [] if self.view is None else list(self.view.children)

    def text(self) -> str:
        parts: list[str] = []
        for embed in self.embeds:
            parts += [embed.title or "", embed.description or "", embed.footer.text or "", embed.author.name or ""]
            for f in embed.fields:
                parts += [f.name or "", f.value or ""]
        return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------------------------------


class _ChannelMixin:
    guild: FakeGuild
    id: int
    _fake_overwrites: dict[Any, discord.PermissionOverwrite]

    def _init_common(self, overwrites: dict[Any, discord.PermissionOverwrite] | None) -> None:
        self._fake_overwrites = dict(overwrites or {})
        self.permission_edits: list[tuple[Any, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    @property
    def overwrites(self) -> dict[Any, discord.PermissionOverwrite]:
        return dict(self._fake_overwrites)

    def overwrites_for(self, obj: Any) -> discord.PermissionOverwrite:
        for target, overwrite in self._fake_overwrites.items():
            if target.id == obj.id:
                return overwrite
        return discord.PermissionOverwrite()

    async def set_permissions(
        self, target: Any, *, overwrite: Any = discord.utils.MISSING, reason: str | None = None, **perms: Any
    ) -> None:
        self.permission_edits.append((target, overwrite if overwrite is not discord.utils.MISSING else perms))
        for existing in list(self._fake_overwrites):
            if existing.id == target.id:
                del self._fake_overwrites[existing]
        if overwrite is None:
            return
        if overwrite is discord.utils.MISSING:
            overwrite = discord.PermissionOverwrite(**perms)
        self._fake_overwrites[target] = overwrite

    def permissions_for(self, obj: Any, /) -> discord.Permissions:
        return self.guild.compute_permissions(obj, self)

    async def delete(self, *, reason: str | None = None) -> None:
        if self.guild.fail_channel_delete is not None:
            raise self.guild.fail_channel_delete
        self.deleted = True
        self.guild._channels.pop(self.id, None)


class FakeCategory(_ChannelMixin, discord.CategoryChannel):
    def __init__(
        self,
        guild: FakeGuild,
        *,
        id: int | None = None,
        name: str = "category",
        overwrites: dict[Any, discord.PermissionOverwrite] | None = None,
        position: int = 0,
    ) -> None:
        self.id = id or next_id()
        self.name = name
        self.guild = guild  # type: ignore[assignment]
        self.position = position
        self.nsfw = False
        self.category_id = None
        self._init_common(overwrites)

    @property
    def channels(self) -> list[Any]:  # type: ignore[override]
        return [c for c in self.guild._channels.values() if getattr(c, "category_id", None) == self.id]

    @property
    def text_channels(self) -> list[Any]:  # type: ignore[override]
        return [c for c in self.channels if isinstance(c, FakeTextChannel)]

    async def edit(self, **kwargs: Any) -> FakeCategory:  # type: ignore[override]
        self.edits.append(kwargs)
        if "name" in kwargs:
            self.name = kwargs["name"]
        if "overwrites" in kwargs:
            self._fake_overwrites = dict(kwargs["overwrites"])
        return self


class FakeTextChannel(_ChannelMixin, discord.TextChannel):
    def __init__(
        self,
        guild: FakeGuild,
        *,
        id: int | None = None,
        name: str = "channel",
        category: FakeCategory | None = None,
        overwrites: dict[Any, discord.PermissionOverwrite] | None = None,
        topic: str | None = None,
    ) -> None:
        self.id = id or next_id()
        self.name = name
        self.guild = guild  # type: ignore[assignment]
        self.topic = topic
        self.category_id = category.id if category is not None else None
        self.position = 0
        self.nsfw = False
        self.slowmode_delay = 0
        self.last_message_id = None
        self.messages: list[FakeMessage] = []
        self.fail_send: BaseException | None = None
        self._init_common(overwrites)

    @property
    def category(self) -> FakeCategory | None:  # type: ignore[override]
        channel = self.guild.get_channel(self.category_id) if self.category_id else None
        return channel if isinstance(channel, FakeCategory) else None

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.guild.id}/{self.id}"

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:  # type: ignore[override]
        if self.deleted:
            raise http_error(discord.NotFound, 404, 10003, "Unknown Channel")
        if self.fail_send is not None:
            raise self.fail_send
        message = FakeMessage(self, author=self.guild.me, content=content, **_message_kwargs(kwargs))
        self.messages.append(message)
        return message

    def add_user_message(
        self, author: Any, content: str, attachments: list[FakeAttachment] | None = None
    ) -> FakeMessage:
        message = FakeMessage(self, author=author, content=content, attachments=attachments)
        self.messages.append(message)
        return message

    async def fetch_message(self, id: int, /) -> FakeMessage:  # type: ignore[override]
        for message in self.messages:
            if message.id == id:
                return message
        raise http_error(discord.NotFound, 404, 10008, "Unknown Message")

    def get_partial_message(self, message_id: int, /) -> Any:
        for message in self.messages:
            if message.id == message_id:
                return message
        missing = FakeMessage(self, id=message_id)
        missing.deleted = True
        return missing

    async def history(  # type: ignore[override]
        self,
        *,
        limit: int | None = 100,
        oldest_first: bool | None = None,
        before: Any = None,
        after: Any = None,
        around: Any = None,
    ) -> AsyncIterator[FakeMessage]:
        messages = list(self.messages)
        if not oldest_first:
            messages.reverse()
        for message in messages[: limit if limit is not None else None]:
            yield message

    async def edit(self, **kwargs: Any) -> FakeTextChannel:  # type: ignore[override]
        if self.deleted:
            raise http_error(discord.NotFound, 404, 10003, "Unknown Channel")
        self.edits.append(kwargs)
        for attr in ("name", "topic"):
            if attr in kwargs:
                setattr(self, attr, kwargs[attr])
        if "slowmode_delay" in kwargs:
            self.slowmode_delay = kwargs["slowmode_delay"]
        if "category" in kwargs:
            category = kwargs["category"]
            self.category_id = category.id if category is not None else None
        if "overwrites" in kwargs:
            self._fake_overwrites = dict(kwargs["overwrites"])
        if kwargs.get("sync_permissions") and self.category is not None:
            self._fake_overwrites = dict(self.category.overwrites)
        return self

    def __repr__(self) -> str:
        return f"<FakeTextChannel id={self.id} name={self.name!r}>"


# --------------------------------------------------------------------------------------------------
# Guild and client
# --------------------------------------------------------------------------------------------------


BOT_GUILD_PERMISSIONS = discord.Permissions(
    add_reactions=True,
    create_public_threads=True,
    create_private_threads=True,
    send_messages_in_threads=True,
    view_channel=True,
    send_messages=True,
    embed_links=True,
    read_message_history=True,
    attach_files=True,
    manage_channels=True,
    manage_roles=True,
    manage_messages=True,
    use_external_emojis=True,
)


class FakeGuild:
    def __init__(self, *, id: int, name: str = "Test Guild") -> None:
        self.id = id
        self.name = name
        self.owner_id = 1
        self._roles: dict[int, FakeRole] = {}
        self._channels: dict[int, Any] = {}
        self._members: dict[int, FakeMember] = {}
        self.default_role = FakeRole(self, id=id, name="@everyone", position=0)
        self._roles[id] = self.default_role
        self.bot_role = FakeRole(self, name="ShopBot", position=50, permissions=BOT_GUILD_PERMISSIONS, managed=True)
        self._roles[self.bot_role.id] = self.bot_role
        self.me = FakeMember(self, name="ShopBot", roles=[self.bot_role], bot=True)
        self._members[self.me.id] = self.me
        self.fail_channel_create: BaseException | None = None
        self.fail_channel_delete: BaseException | None = None
        self.fail_role_create: BaseException | None = None
        self.audit: list[tuple[str, Any]] = []

    # lookups ------------------------------------------------------------------------------------
    @property
    def roles(self) -> list[FakeRole]:
        return sorted(self._roles.values(), key=lambda r: r.position)

    @property
    def channels(self) -> list[Any]:
        return list(self._channels.values())

    @property
    def categories(self) -> list[FakeCategory]:
        return [c for c in self._channels.values() if isinstance(c, FakeCategory)]

    @property
    def text_channels(self) -> list[FakeTextChannel]:
        return [c for c in self._channels.values() if isinstance(c, FakeTextChannel)]

    @property
    def members(self) -> list[FakeMember]:
        return list(self._members.values())

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_channel(self, channel_id: int | None) -> Any:
        return self._channels.get(channel_id) if channel_id is not None else None

    def get_channel_or_thread(self, channel_id: int) -> Any:
        return self.get_channel(channel_id)

    def get_member(self, member_id: int) -> FakeMember | None:
        return self._members.get(member_id)

    async def fetch_member(self, member_id: int) -> FakeMember:
        member = self._members.get(member_id)
        if member is None:
            raise http_error(discord.NotFound, 404, 10007, "Unknown Member")
        return member

    async def fetch_channel(self, channel_id: int) -> Any:
        channel = self._channels.get(channel_id)
        if channel is None:
            raise http_error(discord.NotFound, 404, 10003, "Unknown Channel")
        return channel

    # creation ---------------------------------------------------------------------------------
    def add_member(self, **kwargs: Any) -> FakeMember:
        member = FakeMember(self, **kwargs)
        self._members[member.id] = member
        return member

    def add_role(self, **kwargs: Any) -> FakeRole:
        role = FakeRole(self, **kwargs)
        self._roles[role.id] = role
        return role

    def add_category(self, **kwargs: Any) -> FakeCategory:
        category = FakeCategory(self, **kwargs)
        self._channels[category.id] = category
        return category

    def add_text_channel(self, **kwargs: Any) -> FakeTextChannel:
        channel = FakeTextChannel(self, **kwargs)
        self._channels[channel.id] = channel
        return channel

    async def create_role(
        self,
        *,
        name: str = "new role",
        permissions: discord.Permissions | None = None,
        colour: Any = None,
        color: Any = None,
        hoist: bool = False,
        mentionable: bool = False,
        reason: str | None = None,
        **_: Any,
    ) -> FakeRole:
        if self.fail_role_create is not None:
            raise self.fail_role_create
        role = self.add_role(name=name, position=len(self._roles), permissions=permissions)
        role.hoist = hoist
        role.mentionable = mentionable
        value = colour if colour is not None else color
        if value is not None:
            role._colour = value.value if isinstance(value, discord.Colour) else int(value)
        self.audit.append(("create_role", role))
        return role

    async def create_category(
        self, name: str, *, overwrites: Any = None, reason: str | None = None, position: int | None = None
    ) -> FakeCategory:
        if self.fail_channel_create is not None:
            raise self.fail_channel_create
        category = self.add_category(name=name, overwrites=overwrites)
        self.audit.append(("create_category", category))
        return category

    async def create_text_channel(
        self,
        name: str,
        *,
        category: FakeCategory | None = None,
        overwrites: Any = None,
        topic: str | None = None,
        reason: str | None = None,
        slowmode_delay: int | None = None,
        **_: Any,
    ) -> FakeTextChannel:
        if self.fail_channel_create is not None:
            raise self.fail_channel_create
        channel = self.add_text_channel(name=name, category=category, overwrites=overwrites, topic=topic)
        if slowmode_delay:
            channel.slowmode_delay = slowmode_delay
        self.audit.append(("create_text_channel", channel))
        return channel

    # permission model (simplified Discord rules) ------------------------------------------------
    def compute_permissions(self, member: Any, channel: Any) -> discord.Permissions:
        if isinstance(member, FakeRole):
            base = discord.Permissions(member.permissions.value | self.default_role.permissions.value)
            roles = [self.default_role, member]
            member_id = None
        else:
            base = member.guild_permissions
            if base.administrator:
                return discord.Permissions.all()
            roles = member.roles
            member_id = member.id
        allow = discord.Permissions(base.value)
        if channel is None:
            return allow
        if member is self.me or getattr(member, "id", None) == self.me.id:
            # The fake bot keeps its guild permissions unless explicitly denied.
            pass
        value = allow.value
        role_ids = {r.id for r in roles}
        role_allow = role_deny = 0
        for target, overwrite in channel.overwrites.items():
            a, d = overwrite.pair()
            if target.id == self.id:
                value = (value & ~d.value) | a.value
            elif target.id in role_ids:
                role_allow |= a.value
                role_deny |= d.value
        value = (value & ~role_deny) | role_allow
        for target, overwrite in channel.overwrites.items():
            if member_id is not None and target.id == member_id:
                a, d = overwrite.pair()
                value = (value & ~d.value) | a.value
        return discord.Permissions(value)


class FakeUser:
    """A user outside the guild (for DM-only paths)."""

    def __init__(self, *, id: int | None = None, name: str = "user") -> None:
        self.id = id or next_id()
        self.name = name
        self.display_name = name
        self.mention = f"<@{self.id}>"
        self.bot = False
        self.dms: list[FakeMessage] = []
        self.dm_forbidden = False

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        if self.dm_forbidden:
            raise http_error(discord.Forbidden, 403, 50007, "Cannot send messages to this user")
        message = FakeMessage(None, content=content, **_message_kwargs(kwargs))
        self.dms.append(message)
        return message


class FakeBot:
    """Stands in for ``ShopBot``: lookups, listeners, and a real (offline) command tree."""

    def __init__(self, app: Any, guild: FakeGuild) -> None:
        self.app = app
        app.bot = self
        self.guild = guild
        self.user = guild.me
        self.http = None
        self._connection = types.SimpleNamespace(_command_tree=None)
        self.tree: app_commands.CommandTree[Any] = app_commands.CommandTree(self)  # type: ignore[arg-type]
        self.listeners: dict[str, list[Any]] = {}
        self.extra_users: dict[int, FakeUser] = {}
        self.dynamic_items: list[Any] = []

    @property
    def guilds(self) -> list[FakeGuild]:
        return [self.guild]

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self.guild if guild_id == self.guild.id else None

    def get_channel(self, channel_id: int) -> Any:
        return self.guild.get_channel(channel_id)

    async def fetch_channel(self, channel_id: int) -> Any:
        return await self.guild.fetch_channel(channel_id)

    def get_user(self, user_id: int) -> Any:
        return self.guild.get_member(user_id) or self.extra_users.get(user_id)

    async def fetch_user(self, user_id: int) -> Any:
        user = self.get_user(user_id)
        if user is None:
            raise http_error(discord.NotFound, 404, 10013, "Unknown User")
        return user

    def add_listener(self, event: str, listener: Any) -> None:
        self.listeners.setdefault(event, []).append(listener)

    async def emit(self, event: str, *args: Any) -> None:
        for listener in self.listeners.get(event, []):
            await listener(*args)

    def add_dynamic_items(self, *items: Any) -> None:
        self.dynamic_items.extend(items)


# --------------------------------------------------------------------------------------------------
# Interactions
# --------------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._interaction = interaction
        self._done = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.modal: discord.ui.Modal | None = None

    def is_done(self) -> bool:
        return self._done

    def _mark(self) -> None:
        if self._done:
            raise discord.InteractionResponded(self._interaction)  # type: ignore[arg-type]
        self._done = True

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self._mark()
        self.calls.append(("send_message", {"content": content, **kwargs}))
        message = FakeMessage(
            self._interaction.channel,
            author=self._interaction.guild.me if self._interaction.guild else None,
            content=content,
            **_message_kwargs(kwargs),
        )
        message.ephemeral = kwargs.get("ephemeral", False)  # type: ignore[attr-defined]
        message.flags = discord.MessageFlags(ephemeral=bool(kwargs.get("ephemeral", False)))
        self._interaction.sent.append(message)
        self._interaction._original = message

    async def edit_message(self, **kwargs: Any) -> None:
        self._mark()
        self.calls.append(("edit_message", kwargs))
        if self._interaction.message is not None:
            await self._interaction.message.edit(**kwargs)

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._mark()
        self.calls.append(("defer", {"ephemeral": ephemeral, "thinking": thinking}))
        if thinking:
            self._interaction._thinking = True

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self._mark()
        self.calls.append(("send_modal", {"modal": modal}))
        self.modal = modal


class FakeFollowup:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._interaction = interaction
        self.calls: list[dict[str, Any]] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        if not self._interaction.response.is_done():
            raise AssertionError("followup.send before the interaction was responded to")
        self.calls.append({"content": content, **kwargs})
        message = FakeMessage(self._interaction.channel, content=content, **_message_kwargs(kwargs))
        message.ephemeral = kwargs.get("ephemeral", False)  # type: ignore[attr-defined]
        message.flags = discord.MessageFlags(ephemeral=bool(kwargs.get("ephemeral", False)))
        self._interaction.sent.append(message)
        return message


class FakeInteraction:
    def __init__(
        self,
        *,
        client: Any,
        user: Any,
        guild: FakeGuild | None = None,
        channel: Any = None,
        message: FakeMessage | None = None,
        type: discord.InteractionType = discord.InteractionType.component,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.user = user
        self.guild = guild
        self.guild_id = guild.id if guild is not None else None
        self.channel = channel if channel is not None else (message.channel if message is not None else None)
        self.channel_id = self.channel.id if self.channel is not None else None
        self.message = message
        self.type = type
        self.data = data or {}
        self.command = None
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.sent: list[FakeMessage] = []
        self.original_edits: list[dict[str, Any]] = []
        self._original: FakeMessage | None = None
        self._thinking = False
        self.id = next_id()
        self.locale = discord.Locale.american_english

    async def edit_original_response(self, **kwargs: Any) -> Any:
        if not self.response.is_done():
            raise AssertionError("edit_original_response before responding")
        self.original_edits.append(kwargs)
        target = self._original if self._original is not None else self.message
        if target is not None:
            await target.edit(**kwargs)
        return target

    async def delete_original_response(self) -> None:
        if not self.response.is_done():
            raise AssertionError("delete_original_response before responding")
        self.original_deleted = True
        if self._original is not None and self._original in self.sent:
            self.sent.remove(self._original)

    async def original_response(self) -> Any:
        return self._original if self._original is not None else self.message

    # assertion helpers ---------------------------------------------------------------------
    @property
    def last_sent(self) -> FakeMessage | None:
        return self.sent[-1] if self.sent else None

    def response_kinds(self) -> list[str]:
        return [name for name, _ in self.response.calls]


async def click(
    app: Any,
    *,
    user: Any,
    custom_id: str,
    message: FakeMessage | None = None,
    channel: Any = None,
    values: tuple[str, ...] | list[str] = (),
    guild: FakeGuild | None = None,
    dm: bool = False,
) -> FakeInteraction:
    """Simulate a button/select click routed exactly like production (through ``Router.dispatch``)."""
    bot = app.bot
    interaction = FakeInteraction(
        client=bot,
        user=user,
        guild=None if dm else (guild or bot.guild),
        channel=channel,
        message=message,
        type=discord.InteractionType.component,
    )
    await app.router.dispatch(interaction, custom_id, kind="component", values=tuple(values))
    return interaction


async def submit_modal(
    app: Any,
    *,
    user: Any,
    custom_id: str,
    fields: dict[str, str],
    message: FakeMessage | None = None,
    channel: Any = None,
    guild: FakeGuild | None = None,
    dm: bool = False,
) -> FakeInteraction:
    """Simulate a form submission routed like production (on_interaction → Router.dispatch)."""
    from shopbot.ui.routing import parse_modal_fields

    data = {
        "custom_id": custom_id,
        "components": [
            {"type": 18, "component": {"type": 4, "custom_id": key, "value": value}} for key, value in fields.items()
        ],
    }
    bot = app.bot
    interaction = FakeInteraction(
        client=bot,
        user=user,
        guild=None if dm else (guild or bot.guild),
        channel=channel,
        message=message,
        type=discord.InteractionType.modal_submit,
        data=data,
    )
    await app.router.dispatch(interaction, custom_id, kind="modal", fields=parse_modal_fields(data))
    return interaction


def modal_of(interaction: FakeInteraction) -> discord.ui.Modal | None:
    return interaction.response.modal


def modal_input_ids(modal: discord.ui.Modal) -> list[str]:
    ids: list[str] = []
    for item in modal.walk_children():
        custom_id = getattr(item, "custom_id", None)
        if isinstance(item, discord.ui.TextInput) and custom_id:
            ids.append(custom_id)
    return ids
