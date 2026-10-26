"""/reset: delete the server's channels, categories and roles, so the next /setup builds everything new.

Owner only (``OWNER_ID`` or the server owner). /reset shows privately what it would delete (``plan_reset``); a form
asks for ``RESET <server name>``. On confirmation, with the /setup lock held (``reset_handlers``):

1. the plan is computed again; if it differs from the preview (the server changed meanwhile), nothing happens;
2. the owner gets a DM, because the channel the command ran in is deleted too: the progress and the result are sent
   there. If DMs are closed, nothing happens;
3. a database backup is made (``backups/before-reset-<UTC time>.db``, which the /backup rotation never removes); if
   it fails, nothing happens;
4. with ``wipe_shop_data`` the shop data is deleted (``reset_store.wipe_shop_data``, one transaction);
5. channels, then categories, then roles are deleted one at a time, ``PACE_SECONDS`` apart (Discord watches for
   mass deletions); a failure is reported and the run continues;
6. the records of what was deleted are forgotten (``reset_store.forget``), so /setup creates it all anew and posts
   every panel again;
7. a private channel is created for the owner to run /setup in (a server without channels has nowhere to type);
8. the run is audited (``server.reset``) and the result sent by DM.

Never deleted: ``@everyone``, roles managed by a bot or an integration (the Server Booster role included), roles not
below the bot's highest role, the channels Discord requires in a Community server (rules, community updates, safety
alerts) and channels where the bot lacks Manage Channels. Without ``wipe_shop_data`` every order, product and review
is kept. Order and ticket channels are deleted like any other: tickets are recorded as deleted (the channel-delete
listeners), orders keep their status, and /setup posts the staff cards of open orders again in the new orders
channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import discord

from shopbot.features.admin import service as admin_service
from shopbot.features.checkout import models as checkout_models
from shopbot.features.operations import customer_role
from shopbot.features.setup import reset_store
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, edit_spec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.ui.spec import MessageSpec

log = logging.getLogger(__name__)

# Seconds between two deletions.
PACE_SECONDS: Final = 1.0
# The progress DM is edited after this many deletions.
PROGRESS_EVERY: Final = 10
BACKUP_PREFIX: Final = "before-reset"

CHANNEL: Final = "channel"
CATEGORY: Final = "category"
ROLE: Final = "role"
KINDS: Final = (CHANNEL, CATEGORY, ROLE)

# Why an object is kept.
KEEP_COMMUNITY: Final = "community"
KEEP_NO_PERMISSION: Final = "no_permission"
KEEP_MANAGED: Final = "managed"
KEEP_ABOVE_BOT: Final = "above_bot"


def can_reset(app: App, guild: discord.Guild, user_id: int) -> bool:
    """Only the bot's owner (``OWNER_ID``) and the server owner may reset the server."""
    return app.perms.is_owner(user_id) or guild.owner_id == user_id


def confirmation_phrase(server_name: str) -> str:
    return f"RESET {server_name}"


def phrase_matches(typed: str, server_name: str) -> bool:
    """The typed confirmation, ignoring case and extra spaces (the server name can be hard to type exactly)."""

    def norm(text: str) -> str:
        return " ".join(text.split()).casefold()

    return norm(typed) == norm(confirmation_phrase(server_name))


@dataclass(frozen=True)
class Kept:
    kind: str
    name: str
    reason: str


@dataclass(frozen=True)
class ResetPlan:
    channels: tuple[discord.abc.GuildChannel, ...]
    categories: tuple[discord.CategoryChannel, ...]
    roles: tuple[discord.Role, ...]
    kept: tuple[Kept, ...]

    @property
    def targets(self) -> list[tuple[str, Any]]:
        """In deletion order: channels, then categories (empty by then), then roles."""
        return [
            *((CHANNEL, c) for c in self.channels),
            *((CATEGORY, c) for c in self.categories),
            *((ROLE, r) for r in self.roles),
        ]

    @property
    def total(self) -> int:
        return len(self.channels) + len(self.categories) + len(self.roles)

    @property
    def fingerprint(self) -> str:
        """Identifies exactly what would be deleted; the confirmation is refused when it changed since the preview."""
        ids = ",".join(f"{kind}:{obj.id}" for kind, obj in sorted(self.targets, key=lambda t: (t[0], t[1].id)))
        return hashlib.sha256(ids.encode()).hexdigest()[:16]


def _required_channel_ids(guild: discord.Guild) -> set[int]:
    """Channels a Community server must have; Discord refuses to delete them."""
    ids: set[int] = set()
    for name in ("rules_channel", "public_updates_channel", "safety_alerts_channel"):
        channel = getattr(guild, name, None)
        if channel is not None:
            ids.add(channel.id)
    return ids


def _role_kept(role: discord.Role, me: discord.Member) -> str | None:
    if role.managed or role.is_bot_managed() or role.is_premium_subscriber() or role.is_integration():
        return KEEP_MANAGED
    if not me.guild_permissions.manage_roles or not role < me.top_role:
        return KEEP_ABOVE_BOT
    return None


def plan_reset(guild: discord.Guild) -> ResetPlan:
    """What /reset deletes in ``guild`` (from the bot's cache) and what it keeps, and why."""
    me = guild.me
    required = _required_channel_ids(guild)
    channels: list[discord.abc.GuildChannel] = []
    categories: list[discord.CategoryChannel] = []
    kept: list[Kept] = []

    def position(channel: Any) -> tuple[int, int, int]:
        category = guild.get_channel(channel.category_id) if getattr(channel, "category_id", None) else None
        return (getattr(category, "position", -1), getattr(channel, "position", 0), channel.id)

    for channel in sorted(guild.channels, key=position):
        kind = CATEGORY if isinstance(channel, discord.CategoryChannel) else CHANNEL
        if channel.id in required:
            kept.append(Kept(kind, channel.name, KEEP_COMMUNITY))
        elif not channel.permissions_for(me).manage_channels:
            kept.append(Kept(kind, channel.name, KEEP_NO_PERMISSION))
        elif isinstance(channel, discord.CategoryChannel):
            categories.append(channel)
        else:
            channels.append(channel)
    roles: list[discord.Role] = []
    for role in sorted(guild.roles, key=lambda r: (-r.position, r.id)):
        if role.is_default():
            continue
        reason = _role_kept(role, me)
        if reason is None:
            roles.append(role)
        else:
            kept.append(Kept(ROLE, role.name, reason))
    return ResetPlan(channels=tuple(channels), categories=tuple(categories), roles=tuple(roles), kept=tuple(kept))


@dataclass(frozen=True)
class OpenWork:
    """Orders and tickets still in progress (shown in the preview: their channels are deleted too)."""

    orders: int
    tickets: int


async def open_work(app: App) -> OpenWork:
    orders, tickets = await app.db.run(reset_store.open_counts, app.env.guild_id, checkout_models.OPEN_STATUSES)
    return OpenWork(orders=orders, tickets=tickets)


@dataclass(frozen=True)
class Failure:
    kind: str
    name: str
    status: int
    code: int


@dataclass
class ResetResult:
    backup: str
    wipe: bool
    deleted: Counter[str] = field(default_factory=Counter)
    failures: list[Failure] = field(default_factory=list)
    kept: int = 0
    wiped: dict[str, int] = field(default_factory=dict)
    forgotten: tuple[int, int] = (0, 0)
    setup_channel_id: int | None = None

    @property
    def deleted_total(self) -> int:
        return sum(self.deleted.values())

    def audit_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "backup": self.backup,
            "wipe_shop_data": self.wipe,
            "deleted": {kind: self.deleted.get(kind, 0) for kind in KINDS},
            "failed": len(self.failures),
            "kept": self.kept,
            "forgotten_resources": self.forgotten[0],
            "forgotten_messages": self.forgotten[1],
            "setup_channel_id": self.setup_channel_id,
        }
        if self.wipe:
            details["wiped_orders"] = self.wiped.get("orders", 0)
            details["wiped_products"] = self.wiped.get("products", 0)
            details["wiped_reviews"] = self.wiped.get("reviews", 0)
        return details


class BackupFailed(Exception):
    """The backup before the reset could not be made; nothing was changed."""


async def backup_first(app: App, actor_id: int) -> str:
    """The database copy made before anything is deleted. Returns its file name."""
    try:
        report = await admin_service.create_backup(app, actor_id=actor_id, prefix=BACKUP_PREFIX)
    except admin_service.BackupUnavailable as exc:
        raise BackupFailed("unavailable") from exc
    except (sqlite3.Error, OSError) as exc:
        log_event(log, logging.ERROR, "reset_backup_failed", error=type(exc).__name__)
        raise BackupFailed(type(exc).__name__) from exc
    return report.path.name


async def run_reset(
    app: App,
    guild: discord.Guild,
    *,
    actor: discord.Member,
    plan: ResetPlan,
    wipe: bool,
    backup: str,
    progress_message: discord.Message | None,
    progress: Callable[[int, int, int], MessageSpec],
) -> ResetResult:
    """Steps 4 to 7 (the caller has checked the plan, sent the DM and made the backup); audited at the end.
    ``progress(done, total, failed)`` builds the progress DM shown in ``progress_message``."""
    result = ResetResult(backup=backup, wipe=wipe, kept=len(plan.kept))
    reason = app.op("server.reset.audit_reason", user_id=actor.id)
    if wipe:
        result.wiped = await app.db.transaction(reset_store.wipe_shop_data, app.clock.now())
        log_event(log, logging.WARNING, "reset_shop_data_wiped", **{k: v for k, v in result.wiped.items() if v})
    recorded = await app.db.run(reset_store.recorded_ids, app.env.guild_id)
    gone: set[int] = set()
    targets = plan.targets
    for index, (kind, obj) in enumerate(targets, start=1):
        if index > 1:
            await asyncio.sleep(PACE_SECONDS)
        if await _delete(kind, obj, reason, result):
            gone.add(obj.id)
        if progress_message is not None and (index % PROGRESS_EVERY == 0 or index == len(targets)):
            await _show(progress_message, progress(index, len(targets), len(result.failures)))
    # Also forget records of objects that were already deleted before the reset.
    present = {c.id for c in guild.channels} | {r.id for r in guild.roles}
    gone |= {i for i in recorded if i not in present}
    result.forgotten = await app.db.transaction(reset_store.forget, app.env.guild_id, gone)
    if not wipe and result.deleted[ROLE]:
        await app.db.transaction(customer_role.request_restore, app.clock.now())
    await app.resources.refresh()
    result.setup_channel_id = await _setup_channel(app, guild, actor, reason)
    log_event(
        log,
        logging.WARNING,
        "server_reset",
        actor_id=actor.id,
        deleted=result.deleted_total,
        failed=len(result.failures),
        wipe=wipe,
        backup=backup,
    )
    await app.audit.record(
        "server.reset", actor_id=actor.id, target_type="guild", target_id=guild.id, details=result.audit_details()
    )
    return result


async def _delete(kind: str, obj: Any, reason: str, result: ResetResult) -> bool:
    """Delete one channel, category or role; True when it is gone (a 404 means someone was quicker)."""
    try:
        await obj.delete(reason=reason)
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "reset_delete_failed", kind=kind, id=obj.id, status=exc.status, code=exc.code)
        result.failures.append(Failure(kind, obj.name, exc.status, exc.code))
        return False
    result.deleted[kind] += 1
    return True


async def _show(message: discord.Message, spec: MessageSpec) -> None:
    with contextlib.suppress(discord.HTTPException, RenderError):
        await edit_spec(message, spec)


async def _setup_channel(app: App, guild: discord.Guild, owner: discord.Member, reason: str) -> int | None:
    """A channel only the owner and the bot can see, to run /setup in (the owner deletes it afterwards)."""
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True
        ),
        # Only permissions the bot holds itself (Discord refuses others); slash commands are allowed by default.
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    try:
        channel = await guild.create_text_channel(
            app.op("server.reset.setup_channel_name"),
            overwrites=overwrites,
            topic=app.op("server.reset.setup_channel_topic"),
            reason=reason,
        )
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "reset_setup_channel_failed", status=exc.status, code=exc.code)
        return None
    return channel.id


def kept_by_reason(kept: Sequence[Kept]) -> dict[str, list[Kept]]:
    grouped: dict[str, list[Kept]] = {}
    for item in kept:
        grouped.setdefault(item.reason, []).append(item)
    return grouped
