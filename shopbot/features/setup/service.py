"""/setup: reconcile the server structure from ``settings.server`` and publish feature panels.

Resolution for every role/category/channel definition, in order:
1. an explicit ``id`` in settings (if that object does not exist, it is reported and nothing is created);
2. the id recorded by an earlier /setup (``guild_resources``); a recorded object that was deleted is
   re-created;
3. an existing object with the configured name (roles: exact name; categories: case-insensitive;
   text channels: Discord's normalized name, inside the configured category when there is one);
4. otherwise it is created, but only when its name content key is configured.

Existing objects are updated to match settings/content (names, role permissions/color/hoist/mentionable,
channel topic/slowmode/category, permission overwrites of managed targets). Nothing is ever deleted.
The run is idempotent: a second run with unchanged configuration makes no Discord changes.

Role details: a non-empty ``permissions`` list is applied exactly (the role gets those permissions and no
others); an empty list leaves an existing role's permissions untouched (a role created by /setup starts
with none), so adopting an existing role never strips its permissions. ``color`` is applied only when set.
Roles that are managed by an integration, are @everyone, or are not below the bot's highest role are
never edited (the needed change is reported instead). A channel topic is set when configured and is
never cleared.

Every Discord failure on a single action is reported and the run continues. ``dry_run`` computes and
reports the plan without any Discord change or database write.

Entry-channel safety: every channel or category that /setup does not manage but ``@everyone`` can see (for example
Discord's default #general and General voice channel) is listed in the problems section, in dry runs too, with the
advice to delete or hide it. Such channels are never changed or deleted by the bot.

The ``setup.run`` audit entry (real runs only) lists what changed: per section the settings keys of created,
re-created, adopted, updated (with the change kinds) and failed resources, and the Owner-role assignment.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import discord

from shopbot.config.settings import CategoryDef, ChannelDef, RoleDef
from shopbot.core.channels import normalize_text_channel_name
from shopbot.core.panels import withdraw_unready_panels
from shopbot.core.permissions import can_manage_role, missing_permissions
from shopbot.features.server_keys import category_name_key, channel_name_key, channel_topic_key, role_name_key
from shopbot.features.setup import overwrites as ow
from shopbot.features.setup.report import SetupReport
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

# Guild-wide permissions /setup needs (checked and reported before anything else).
SETUP_PERMISSIONS = (
    "manage_roles",
    "manage_channels",
    "view_channel",
    "send_messages",
    "embed_links",
    "read_message_history",
    "add_reactions",
    "create_public_threads",
    "create_private_threads",
)

# At most this many unmanaged channels everyone can see are listed one by one in the report (the rest are counted).
MAX_PUBLIC_UNMANAGED_LINES = 25
SECTION_OF_KIND = {"role": "roles", "category": "categories", "channel": "channels"}

T = TypeVar("T")
How = Literal["explicit", "recorded", "adopted", "missing", "id_not_found"]

_locks: weakref.WeakKeyDictionary[App, asyncio.Lock] = weakref.WeakKeyDictionary()


def setup_lock(app: App) -> asyncio.Lock:
    """One /setup at a time per application (a second concurrent run could create duplicates)."""
    lock = _locks.get(app)
    if lock is None:
        lock = asyncio.Lock()
        _locks[app] = lock
    return lock


@dataclass
class Resolution:
    obj: Any
    how: How
    stale: bool  # a recorded id pointed to an object that no longer exists


def role_permissions(names: tuple[str, ...]) -> discord.Permissions:
    """Exactly the named permissions (names are validated by the settings parser)."""
    permissions = discord.Permissions.none()
    for name in names:
        setattr(permissions, name, True)
    return permissions


class Reconciler:
    def __init__(self, app: App, guild: discord.Guild, report: SetupReport, *, dry_run: bool, reason: str) -> None:
        self.app = app
        self.guild = guild
        self.report = report
        self.dry_run = dry_run
        self.reason = reason
        self.bot_member = guild.me
        self.bot = ow.bot_target(guild.me.id)
        self.roles: dict[str, discord.Role] = {}
        self.categories: dict[str, discord.CategoryChannel] = {}
        # Overwrites each category has (or, in a dry run, would have) after this run. Channels with
        # access "inherit" are synced against these instead of the gateway cache, which can lag behind
        # edits made moments ago.
        self.category_overwrites: dict[str, ow.Overwrites] = {}
        self.category_changed: set[str] = set()
        self.claimed: set[int] = set()

    # -- helpers ------------------------------------------------------------------------------------

    def op(self, key: str, **values: object) -> str:
        return self.app.op(key, **values)

    def kind_label(self, kind: str) -> str:
        return self.op(f"server.setup.kind.{kind}")

    def changes_text(self, changes: list[str]) -> str:
        return ", ".join(self.op(f"server.setup.change.{change}") for change in changes)

    def name_target(self, name: str) -> str:
        return self.op("server.setup.target_name", name=discord.utils.escape_markdown(name))

    def ok(self, section: str, item: str, outcome: str, text_key: str, **values: object) -> None:
        self.report.add(section, self.op(text_key, item=item, **values), outcome)

    def problem(self, kind: str, item: str, text_key: str, outcome: str = "skipped", **values: object) -> None:
        label = self.kind_label(kind)
        line = self.op(text_key, kind=label, kind_lower=label.lower(), item=item, **values)
        self.report.add("problems", line, outcome)

    def failed(self, kind: str, item: str, action: str, exc: discord.HTTPException) -> None:
        log_event(
            log,
            logging.WARNING,
            "setup_action_failed",
            kind=kind,
            key=item,
            action=action,
            status=exc.status,
            code=exc.code,
        )
        self.report.change(SECTION_OF_KIND[kind], item, f"failed ({action}, HTTP {exc.status})")
        self.problem(
            kind,
            item,
            "server.setup.failed",
            outcome="failed",
            action=self.op(f"server.setup.action.{action}"),
            http_status=exc.status,
            code=exc.code,
        )

    def resolve(
        self,
        kind: str,
        key: str,
        explicit_id: int,
        lookup: Callable[[int], Any],
        adopt: Callable[[], Any],
    ) -> Resolution:
        if explicit_id:
            found = lookup(explicit_id)
            return (
                Resolution(found, "explicit", False) if found is not None else Resolution(None, "id_not_found", False)
            )
        stale = False
        recorded = self.app.resources.id_for(kind, key)
        if recorded:
            found = lookup(recorded)
            if found is not None and found.id not in self.claimed:
                return Resolution(found, "recorded", False)
            stale = found is None
        adopted = adopt()
        if adopted is not None:
            return Resolution(adopted, "adopted", stale)
        return Resolution(None, "missing", stale)

    async def remember(self, kind: str, key: str, obj: Any) -> None:
        self.claimed.add(obj.id)
        if not self.dry_run and self.app.resources.id_for(kind, key) != obj.id:
            await self.app.resources.remember(kind, key, obj.id)

    async def forget_stale(self, kind: str, key: str, resolution: Resolution) -> None:
        if resolution.stale and not self.dry_run:
            await self.app.resources.forget(kind, key)

    def report_resolution(self, section: str, key: str, resolution: Resolution, target: str) -> bool:
        """Report how an existing object was found. Returns True if a line was written."""
        if resolution.how == "adopted":
            template = "server.setup.would_adopt" if self.dry_run else "server.setup.adopted"
            self.ok(section, key, "adopted", template, target=target)
            self.report.change(section, key, "adopted")
            return True
        return False

    def report_created(self, section: str, key: str, stale: bool, target: str) -> None:
        if self.dry_run:
            template = "server.setup.would_recreate" if stale else "server.setup.would_create"
        else:
            template = "server.setup.recreated" if stale else "server.setup.created"
        self.ok(section, key, "created", template, target=target)
        self.report.change(section, key, "recreated" if stale else "created")

    def report_updates(self, section: str, key: str, target: str, changes: list[str], *, noted: bool) -> None:
        if changes:
            template = "server.setup.would_update" if self.dry_run else "server.setup.updated"
            self.ok(section, key, "updated", template, target=target, changes=self.changes_text(changes))
            self.report.change(section, key, f"updated ({', '.join(changes)})")
        elif not noted:
            self.ok(section, key, "unchanged", "server.setup.unchanged", target=target)

    # -- entry point --------------------------------------------------------------------------------

    async def run(self) -> None:
        missing = missing_permissions(self.bot_member, None, SETUP_PERMISSIONS)
        if missing:
            self.report.add("problems", self.op("server.setup.missing_permissions", permissions=", ".join(missing)))
        server = self.app.settings.server
        for role_def in server.roles:
            await self.reconcile_role(role_def)
        await self.assign_owner_role()
        for category_def in server.categories:
            await self.reconcile_category(category_def)
        for channel_def in server.channels:
            await self.reconcile_channel(channel_def)
        self.report_public_unmanaged()

    # -- roles --------------------------------------------------------------------------------------

    def adopt_role(self, name: str | None) -> discord.Role | None:
        if not name:
            return None
        for role in self.guild.roles:
            if role.is_default() or role.managed or role.id in self.claimed:
                continue
            if role.name == name:
                return role
        return None

    def role_changes(self, role_def: RoleDef, role: discord.Role, name: str | None) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if name and role.name != name:
            changes["name"] = name
        if role_def.permissions:
            desired = role_permissions(role_def.permissions)
            if role.permissions.value != desired.value:
                changes["permissions"] = desired
        if role_def.color is not None and role.colour.value != role_def.color:
            changes["colour"] = discord.Colour(role_def.color)
        if role.hoist != role_def.hoist:
            changes["hoist"] = role_def.hoist
        if role.mentionable != role_def.mentionable:
            changes["mentionable"] = role_def.mentionable
        return changes

    async def reconcile_role(self, role_def: RoleDef) -> None:
        key = role_def.key
        name_key = role_name_key(key)
        name = self.app.content.get(name_key)
        resolution = self.resolve("role", key, role_def.id, self.guild.get_role, lambda: self.adopt_role(name))
        if resolution.how == "id_not_found":
            self.problem("role", key, "server.setup.id_not_found", id=role_def.id)
            return
        role: discord.Role | None = resolution.obj
        if role is None:
            await self.forget_stale("role", key, resolution)
            if not name:
                self.problem("role", key, "server.setup.name_missing", content_key=name_key)
                return
            await self.create_role(role_def, name, stale=resolution.stale)
            return
        await self.remember("role", key, role)
        self.roles[key] = role
        target = role.mention
        noted = self.report_resolution("roles", key, resolution, target)
        changes = self.role_changes(role_def, role, name)
        labels = [("color" if change == "colour" else change) for change in changes]
        if changes and (role.is_default() or not can_manage_role(self.bot_member, role)):
            self.problem(
                "role", key, "server.setup.role_not_manageable", target=target, changes=self.changes_text(labels)
            )
            return
        if changes and not self.dry_run:
            try:
                await role.edit(reason=self.reason, **changes)
            except discord.HTTPException as exc:
                self.failed("role", key, "update", exc)
                return
            log_event(log, logging.INFO, "setup_role_updated", key=key, role_id=role.id, changes=labels)
        self.report_updates("roles", key, target, labels, noted=noted)

    async def create_role(self, role_def: RoleDef, name: str, *, stale: bool) -> None:
        key = role_def.key
        if self.dry_run:
            self.report_created("roles", key, stale, self.name_target(name))
            return
        kwargs: dict[str, Any] = {
            "name": name,
            "permissions": role_permissions(role_def.permissions),
            "hoist": role_def.hoist,
            "mentionable": role_def.mentionable,
            "reason": self.reason,
        }
        if role_def.color is not None:
            kwargs["colour"] = discord.Colour(role_def.color)
        try:
            role = await self.guild.create_role(**kwargs)
        except discord.HTTPException as exc:
            self.failed("role", key, "create", exc)
            return
        await self.remember("role", key, role)
        self.roles[key] = role
        log_event(log, logging.INFO, "setup_role_created", key=key, role_id=role.id)
        self.report_created("roles", key, stale, role.mention)

    # -- permission targets -------------------------------------------------------------------------

    async def assign_owner_role(self) -> None:
        """Give ``permissions.owner_role`` to the OWNER_ID account (never removed from anyone)."""
        key = self.app.settings.permissions.owner_role
        owner_id = self.app.env.owner_id
        if not key or owner_id is None:
            return
        role = self.roles.get(key) or self.app.resources.role(self.guild, key)
        if role is None:
            return  # the role itself was reported in the roles section
        member = self.guild.get_member(owner_id)
        if member is None:
            try:
                member = await self.guild.fetch_member(owner_id)
            except discord.NotFound:
                self.problem("role", key, "server.setup.owner_not_member", user_id=owner_id)
                return
            except discord.HTTPException as exc:
                self.report.owner_role = {"role": key, "user_id": owner_id, "result": "failed"}
                self.failed("role", key, "assign", exc)
                return
        target = role.mention
        if any(r.id == role.id for r in member.roles):
            self.ok("roles", key, "unchanged", "server.setup.owner_role_present", target=target, user_id=owner_id)
            return
        if self.dry_run:
            self.ok("roles", key, "updated", "server.setup.owner_role_would_assign", target=target, user_id=owner_id)
            return
        try:
            await member.add_roles(role, reason=self.reason)
        except discord.HTTPException as exc:
            self.report.owner_role = {"role": key, "user_id": owner_id, "result": "failed"}
            self.failed("role", key, "assign", exc)
            return
        self.report.owner_role = {"role": key, "user_id": owner_id, "result": "assigned"}
        self.ok("roles", key, "updated", "server.setup.owner_role_assigned", target=target, user_id=owner_id)

    def role_objects(self, keys: tuple[str, ...]) -> list[discord.Role]:
        found: dict[int, discord.Role] = {}
        for key in keys:
            role = self.roles.get(key) or self.app.resources.role(self.guild, key)
            if role is not None:
                found[role.id] = role
        return list(found.values())

    def desired_overwrites(self, access: str) -> tuple[ow.Overwrites, set[int]]:
        permissions = self.app.settings.permissions
        staff = self.role_objects(permissions.staff_roles)
        admin = self.role_objects(permissions.admin_roles)
        members = self.role_objects((permissions.member_role,) if permissions.member_role else ())
        everyone = self.guild.default_role
        desired = ow.access_overwrites(
            access, everyone=everyone, staff_roles=staff, admin_roles=admin, bot=self.bot, member_roles=members
        )
        managed = ow.managed_ids(
            everyone=everyone, staff_roles=staff, admin_roles=admin, bot_id=self.bot.id, member_roles=members
        )
        return desired, managed

    # -- categories ---------------------------------------------------------------------------------

    def get_category(self, channel_id: int) -> discord.CategoryChannel | None:
        channel = self.guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.CategoryChannel) else None

    def adopt_category(self, name: str | None) -> discord.CategoryChannel | None:
        if not name:
            return None
        wanted = name.strip().casefold()
        for category in self.guild.categories:
            if category.id not in self.claimed and category.name.strip().casefold() == wanted:
                return category
        return None

    async def reconcile_category(self, category_def: CategoryDef) -> None:
        key = category_def.key
        name_key = category_name_key(key)
        name = self.app.content.get(name_key)
        resolution = self.resolve(
            "category", key, category_def.id, self.get_category, lambda: self.adopt_category(name)
        )
        if resolution.how == "id_not_found":
            self.problem("category", key, "server.setup.id_not_found", id=category_def.id)
            return
        desired, managed = self.desired_overwrites(category_def.access)
        category: discord.CategoryChannel | None = resolution.obj
        if category is None:
            await self.forget_stale("category", key, resolution)
            if not name:
                self.problem("category", key, "server.setup.name_missing", content_key=name_key)
                return
            await self.create_category(key, name, desired, stale=resolution.stale)
            return
        await self.remember("category", key, category)
        self.categories[key] = category
        target = category.mention
        noted = self.report_resolution("categories", key, resolution, target)
        changes: list[str] = []
        edit: dict[str, Any] = {}
        if name and category.name != name:
            changes.append("name")
            edit["name"] = name
        current = category.overwrites
        effective: ow.Overwrites = dict(current)
        if ow.needs_repair(current, desired, managed):
            changes.append("overwrites")
            effective = ow.merge(current, desired, managed)
            edit["overwrites"] = effective
        if edit and not self.dry_run:
            try:
                await category.edit(reason=self.reason, **edit)
            except discord.HTTPException as exc:
                self.failed("category", key, "update", exc)
                self.category_overwrites[key] = dict(current)
                return
            log_event(log, logging.INFO, "setup_category_updated", key=key, category_id=category.id, changes=changes)
        self.category_overwrites[key] = effective
        if "overwrites" in changes:
            self.category_changed.add(key)
        self.report_updates("categories", key, target, changes, noted=noted)

    async def create_category(self, key: str, name: str, desired: ow.Overwrites, *, stale: bool) -> None:
        if self.dry_run:
            self.category_overwrites[key] = desired
            self.category_changed.add(key)
            self.report_created("categories", key, stale, self.name_target(name))
            return
        try:
            category = await self.guild.create_category(name, overwrites=ow.creatable(desired), reason=self.reason)
        except discord.HTTPException as exc:
            self.failed("category", key, "create", exc)
            return
        await self.remember("category", key, category)
        self.categories[key] = category
        self.category_overwrites[key] = desired
        self.category_changed.add(key)
        log_event(log, logging.INFO, "setup_category_created", key=key, category_id=category.id)
        self.report_created("categories", key, stale, category.mention)

    # -- channels -----------------------------------------------------------------------------------

    def get_text_channel(self, channel_id: int) -> discord.TextChannel | None:
        channel = self.guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def adopt_channel(self, name: str | None, category: discord.CategoryChannel | None, in_category: bool) -> Any:
        if not name:
            return None
        if in_category and category is None:
            return None  # the category does not exist (yet), so neither does a channel inside it
        wanted = normalize_text_channel_name(name)
        candidates = [
            channel
            for channel in self.guild.text_channels
            if channel.id not in self.claimed and normalize_text_channel_name(channel.name) == wanted
        ]
        if category is not None:
            candidates = [channel for channel in candidates if channel.category_id == category.id]
        else:
            candidates.sort(key=lambda channel: channel.category_id is not None)  # prefer top-level channels
        return candidates[0] if candidates else None

    async def reconcile_channel(self, channel_def: ChannelDef) -> None:
        key = channel_def.key
        name_key = channel_name_key(key)
        name = self.app.content.get(name_key)
        topic = self.app.content.get(channel_topic_key(key))
        category = self.categories.get(channel_def.category) if channel_def.category else None
        category_planned = bool(channel_def.category) and channel_def.category in self.category_overwrites
        resolution = self.resolve(
            "channel",
            key,
            channel_def.id,
            self.get_text_channel,
            lambda: self.adopt_channel(name, category, bool(channel_def.category)),
        )
        if resolution.how == "id_not_found":
            self.problem("channel", key, "server.setup.id_not_found", id=channel_def.id)
            return
        if channel_def.access == "inherit":
            desired: ow.Overwrites = self.category_overwrites.get(channel_def.category, {})
            managed: set[int] = set()
        else:
            desired, managed = self.desired_overwrites(channel_def.access)
        channel: discord.TextChannel | None = resolution.obj
        if channel is None:
            await self.forget_stale("channel", key, resolution)
            if not name:
                self.problem("channel", key, "server.setup.name_missing", content_key=name_key)
                return
            unavailable = bool(channel_def.category) and (category is None and not (self.dry_run and category_planned))
            if unavailable:
                self.problem("channel", key, "server.setup.category_unavailable", category=channel_def.category)
                return
            await self.create_channel(channel_def, name, topic, category, desired, stale=resolution.stale)
            return
        await self.remember("channel", key, channel)
        target = channel.mention
        noted = self.report_resolution("channels", key, resolution, target)
        changes: list[str] = []
        edit: dict[str, Any] = {}
        if name and normalize_text_channel_name(channel.name) != normalize_text_channel_name(name):
            changes.append("name")
            edit["name"] = name
        if topic and channel.topic != topic:
            changes.append("topic")
            edit["topic"] = topic
        if channel.slowmode_delay != channel_def.slowmode_seconds:
            changes.append("slowmode")
            edit["slowmode_delay"] = channel_def.slowmode_seconds
        moved = category is not None and channel.category_id != category.id
        if moved and category is not None:
            changes.append("category")
            edit["category"] = category
        if channel_def.access == "inherit":
            if category is not None and (moved or not ow.same(channel.overwrites, desired)):
                changes.append("sync")
                if channel_def.category in self.category_changed:
                    # The category was created/changed moments ago; the cache discord.py would copy from
                    # may not reflect it yet, so send the category's overwrites explicitly.
                    edit["overwrites"] = dict(desired)
                else:
                    edit["sync_permissions"] = True
        elif ow.needs_repair(channel.overwrites, desired, managed):
            changes.append("overwrites")
            edit["overwrites"] = ow.merge(channel.overwrites, desired, managed)
        if edit and not self.dry_run:
            try:
                await channel.edit(reason=self.reason, **edit)
            except discord.HTTPException as exc:
                self.failed("channel", key, "update", exc)
                return
            log_event(log, logging.INFO, "setup_channel_updated", key=key, channel_id=channel.id, changes=changes)
        self.report_updates("channels", key, target, changes, noted=noted)

    async def create_channel(
        self,
        channel_def: ChannelDef,
        name: str,
        topic: str | None,
        category: discord.CategoryChannel | None,
        desired: ow.Overwrites,
        *,
        stale: bool,
    ) -> None:
        key = channel_def.key
        if self.dry_run:
            self.report_created("channels", key, stale, self.name_target(name))
            return
        kwargs: dict[str, Any] = {"overwrites": ow.creatable(desired), "reason": self.reason}
        if category is not None:
            kwargs["category"] = category
        if topic:
            kwargs["topic"] = topic
        if channel_def.slowmode_seconds:
            kwargs["slowmode_delay"] = channel_def.slowmode_seconds
        try:
            channel = await self.guild.create_text_channel(name, **kwargs)
        except discord.HTTPException as exc:
            self.failed("channel", key, "create", exc)
            return
        await self.remember("channel", key, channel)
        log_event(log, logging.INFO, "setup_channel_created", key=key, channel_id=channel.id)
        self.report_created("channels", key, stale, channel.mention)

    # -- entry-channel safety -------------------------------------------------------------------------

    def report_public_unmanaged(self) -> None:
        """Report every channel or category /setup does not manage that ``@everyone`` can see: users who have not
        accepted the rules must see only the configured entry channels (e.g. Discord's default #general and General
        voice channel would break that). Reported only; unmanaged channels are never changed or deleted."""
        everyone = self.guild.default_role
        visible = [
            channel
            for channel in self.guild.channels
            if channel.id not in self.claimed and channel.permissions_for(everyone).view_channel
        ]
        if not visible:
            return
        visible.sort(key=lambda channel: channel.id)
        self.report.public_unmanaged = len(visible)
        for channel in visible[:MAX_PUBLIC_UNMANAGED_LINES]:
            self.report.add(
                "problems",
                self.op(
                    "server.setup.public_unmanaged",
                    kind=self.op(f"server.setup.channel_kind.{channel_kind(channel)}"),
                    target=channel.mention,
                    name=discord.utils.escape_markdown(channel.name),
                ),
            )
        if len(visible) > MAX_PUBLIC_UNMANAGED_LINES:
            self.report.add(
                "problems",
                self.op("server.setup.public_unmanaged_more", count=len(visible) - MAX_PUBLIC_UNMANAGED_LINES),
            )
        self.report.add("problems", self.op("server.setup.public_unmanaged_hint"))
        log_event(log, logging.WARNING, "setup_public_unmanaged_channels", count=len(visible))


def channel_kind(channel: object) -> str:
    """The operator label suffix (``server.setup.channel_kind.<kind>``) of a guild channel."""
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return "other"


# -- feature panels ---------------------------------------------------------------------------------


async def publish_panels(app: App, report: SetupReport) -> None:
    """Call every ready feature's publish hook (in module order); one failure never stops the rest."""
    for module in app.modules:
        if module.publish is None or not app.ready(module.name):
            continue
        try:
            lines = await module.publish(app)
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            log_event(
                log,
                logging.ERROR,
                "setup_publish_failed",
                feature=module.name,
                error_id=error_id,
                error=type(exc).__name__,
            )
            log.exception("publish failed for %s (%s)", module.name, error_id)
            report.add(
                "panels", app.op("server.setup.publish_failed", feature=module.name, error_id=error_id), "failed"
            )
            continue
        for line in lines:
            report.add("panels", line)
    if not report.lines["panels"]:
        report.add("panels", app.op("server.setup.no_panels"))


async def withdraw_panels(app: App, report: SetupReport, *, dry_run: bool) -> None:
    """Remove published panels of features that are disabled or not fully configured."""
    for result in await withdraw_unready_panels(app, dry_run=dry_run):
        if dry_run:
            report.add("panels", app.op("server.setup.panel_would_withdraw", feature=result.feature, key=result.key))
        elif result.ok:
            report.add("panels", app.op("server.setup.panel_withdrawn", feature=result.feature, key=result.key))
        else:
            report.add(
                "panels",
                app.op("server.setup.panel_withdraw_failed", feature=result.feature, key=result.key),
                "failed",
            )


def report_not_ready(app: App, report: SetupReport) -> None:
    not_ready = [status for status in app.readiness.statuses.values() if status.enabled and not status.ready]
    for status in not_ready:
        report.add(
            "problems", app.op("server.setup.feature_not_ready", feature=status.name, count=len(status.problems))
        )
    if not_ready:
        report.add("problems", app.op("server.setup.status_hint"))


async def run_setup(
    app: App, guild: discord.Guild, *, actor_id: int, dry_run: bool = False, publish: bool = True
) -> SetupReport:
    """Reconcile the server, publish panels (unless dry run / disabled), audit, and return the report."""
    report = SetupReport(dry_run=dry_run, publish=publish)
    await app.resources.refresh()
    reason = app.op("server.setup.audit_reason", user_id=actor_id)
    await Reconciler(app, guild, report, dry_run=dry_run, reason=reason).run()
    if dry_run:
        report.add("panels", app.op("server.setup.publish_skipped_dry_run"))
        await withdraw_panels(app, report, dry_run=True)
    elif not publish:
        report.add("panels", app.op("server.setup.publish_skipped"))
    else:
        await withdraw_panels(app, report, dry_run=False)
        await publish_panels(app, report)
    report_not_ready(app, report)
    log_event(log, logging.INFO, "setup_finished", actor_id=actor_id, **report.summary_details())
    if not dry_run:
        await app.audit.record(
            "setup.run", actor_id=actor_id, target_type="guild", target_id=guild.id, details=report.audit_details()
        )
    return report
