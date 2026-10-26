"""/reset: delete the server's channels, categories and roles, so the next /setup builds everything new.

Owner only (``OWNER_ID`` or the server owner). /reset shows privately what it would delete (``plan_reset``); a form
asks for ``RESET <server name>``. On confirmation, with the /setup lock held (``reset_handlers``):

1. the plan is computed again; if it differs from the preview (the server changed meanwhile), nothing happens;
2. the owner gets a DM, because the channel the command ran in is deleted too: the progress and the result are sent
   there. If DMs are closed, nothing happens;
3. a database backup is made (``backups/before-reset-<UTC time>.db``, which the /backup rotation never removes); if
   it fails, nothing happens;
4. a private channel is created for the owner to run /setup in (a server without channels has nowhere to type, and
   it proves the bot may create channels); if that fails, nothing happens;
5. channels, then categories, then roles are deleted one at a time, ``PACE_SECONDS`` apart (Discord watches for
   mass deletions), while the router refuses every button and form (``Router.paused``); a failure is reported and
   the run continues;
6. with ``wipe_shop_data`` the shop data is deleted once every deletion was attempted (``reset_store.wipe_shop_data``,
   one transaction);
7. the records of what was deleted are forgotten (``reset_store.forget``), so /setup creates it all anew and posts
   every panel again; the run is audited (``server.reset``) and the result sent by DM. Steps 7 run even when the run
   is interrupted; a restart in the middle leaves the ``running`` marker, and the next start tells the owner
   (``report_interrupted``).

Never deleted: ``@everyone``; roles managed by a bot or an integration (the Server Booster role included) and every
role the bot holds; roles not below the bot's highest role; the Administrator roles of an owner who is not the server
owner (they could not run /setup any more); the channels that hold history (transcripts, the bot log, published
reviews: /setup moves them into the new layout); objects with an ``id`` in settings; the channels Discord requires in
a Community server; and channels where the bot lacks Manage Channels. Without ``wipe_shop_data`` every order, product
and review is kept in the database. Order and ticket channels are deleted like any other: tickets are recorded as
deleted (the channel-delete listeners), orders keep their status, and /setup posts the staff cards of open orders
again in the new orders channel and gives the new Customer role back (``customer_role.restore``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import discord

from shopbot.features.admin import service as admin_service
from shopbot.features.checkout import models as checkout_models
from shopbot.features.operations import customer_role
from shopbot.features.setup import reset_store
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, edit_spec, send_spec

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
KEEP_NO_MANAGE_ROLES: Final = "no_manage_roles"
KEEP_MANAGED: Final = "managed"
KEEP_BOT_ROLE: Final = "bot_role"
KEEP_ABOVE_BOT: Final = "above_bot"
KEEP_YOUR_ROLE: Final = "your_role"
KEEP_HISTORY: Final = "history"
KEEP_CONFIGURED: Final = "configured"


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


def keep_ids(app: App, guild: discord.Guild, actor: discord.Member) -> dict[int, str]:
    """Objects kept for a reason the guild alone does not show: the channels holding history, objects with an ``id``
    in settings, and the Administrator roles of an owner who is not the server owner."""
    keep: dict[int, str] = {}
    server = app.settings.server
    for explicit in (
        *(r.id for r in server.roles),
        *(c.id for c in server.categories),
        *(c.id for c in server.channels),
    ):
        if explicit:
            keep[explicit] = KEEP_CONFIGURED
    s = app.settings
    for key in (s.tickets.transcript_channel, s.audit.channel, s.reviews.channel):
        channel_id = app.resources.channel_id(key) if key else None
        if channel_id:
            keep.setdefault(channel_id, KEEP_HISTORY)
    if actor.id != guild.owner_id:
        for role in actor.roles:
            if not role.is_default() and role.permissions.administrator:
                keep.setdefault(role.id, KEEP_YOUR_ROLE)
    return keep


def _role_kept(role: discord.Role, me: discord.Member) -> str | None:
    if role.managed or role.is_bot_managed() or role.is_premium_subscriber() or role.is_integration():
        return KEEP_MANAGED
    if role in me.roles:
        return KEEP_BOT_ROLE  # deleting it could take the bot's own permissions away half-way
    if not me.guild_permissions.manage_roles:
        return KEEP_NO_MANAGE_ROLES
    if not role < me.top_role:
        return KEEP_ABOVE_BOT
    return None


def plan_reset(guild: discord.Guild, keep: Mapping[int, str] | None = None) -> ResetPlan:
    """What /reset deletes in ``guild`` (from the bot's cache) and what it keeps, and why (``keep``: ``keep_ids``)."""
    keep = keep or {}
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
        elif channel.id in keep:
            kept.append(Kept(kind, channel.name, keep[channel.id]))
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
        reason = _role_kept(role, me) or keep.get(role.id)
        if reason is None:
            roles.append(role)
        else:
            kept.append(Kept(ROLE, role.name, reason))
    return ResetPlan(channels=tuple(channels), categories=tuple(categories), roles=tuple(roles), kept=tuple(kept))


@dataclass(frozen=True)
class OpenWork:
    """Orders and tickets still in progress (shown in the preview: their channels are deleted too). ``unpaid``:
    orders waiting for the customer to pay, which is done in the order channel."""

    orders: int
    unpaid: int
    tickets: int


async def open_work(app: App) -> OpenWork:
    orders, tickets = await app.db.run(reset_store.open_counts, app.env.guild_id, checkout_models.OPEN_STATUSES)
    unpaid, _ = await app.db.run(reset_store.open_counts, app.env.guild_id, (checkout_models.PENDING,))
    return OpenWork(orders=orders, unpaid=unpaid, tickets=tickets)


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
    setup_channel_id: int | None = None
    deleted: Counter[str] = field(default_factory=Counter)
    failures: list[Failure] = field(default_factory=list)
    kept: int = 0
    wiped: dict[str, int] = field(default_factory=dict)
    forgotten: tuple[int, int] = (0, 0)
    completed: bool = False

    @property
    def deleted_total(self) -> int:
        return sum(self.deleted.values())

    def audit_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "backup": self.backup,
            "wipe_shop_data": self.wipe,
            "completed": self.completed,
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


async def create_setup_channel(app: App, guild: discord.Guild, owner: discord.Member) -> discord.TextChannel:
    """A channel only the owner and the bot can see, to run /setup in (the owner deletes it afterwards). Raises
    ``discord.HTTPException`` when Discord refuses it."""
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True
        ),
        # Only permissions the bot holds itself (Discord refuses others); slash commands are allowed by default.
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    return await guild.create_text_channel(
        app.op("server.reset.setup_channel_name"),
        overwrites=overwrites,
        topic=app.op("server.reset.setup_channel_topic"),
        reason=app.op("server.reset.audit_reason", user_id=owner.id),
    )


async def run_reset(
    app: App,
    guild: discord.Guild,
    *,
    actor: discord.Member,
    plan: ResetPlan,
    wipe: bool,
    backup: str,
    setup_channel_id: int,
    progress_message: discord.Message | None,
    progress: Callable[[int, int, int], MessageSpec],
) -> ResetResult:
    """Steps 5 to 7 (the caller has checked the plan, sent the DM, made the backup and the setup channel).
    ``progress(done, total, failed)`` builds the progress DM shown in ``progress_message``. The records, the audit
    entry and the ``running`` marker are taken care of even when the run is interrupted."""
    result = ResetResult(backup=backup, wipe=wipe, setup_channel_id=setup_channel_id, kept=len(plan.kept))
    reason = app.op("server.reset.audit_reason", user_id=actor.id)
    c = app.settings.checkout
    customer_role_id = app.resources.role_id(c.customer_role) if c.customer_role and not wipe else None
    recorded = await app.db.run(reset_store.recorded_ids, app.env.guild_id)
    await app.db.transaction(reset_store.mark_running, actor.id, app.clock.now())
    gone: set[int] = set()
    targets = plan.targets
    try:
        with app.router.paused():
            for index, (kind, obj) in enumerate(targets, start=1):
                if index > 1:
                    await asyncio.sleep(PACE_SECONDS)
                if kind == ROLE and obj.id == customer_role_id:
                    # The next /setup gives the new Customer role back to customers.
                    await app.db.transaction(customer_role.request_restore, app.clock.now())
                if await _delete(kind, obj, reason, result):
                    gone.add(obj.id)
                if progress_message is not None and (index % PROGRESS_EVERY == 0 or index == len(targets)):
                    await _show(progress_message, progress(index, len(targets), len(result.failures)))
            result.completed = True
            if wipe:
                result.wiped = await app.db.transaction(reset_store.wipe_shop_data, app.clock.now())
                log_event(log, logging.WARNING, "reset_shop_data_wiped", **{k: v for k, v in result.wiped.items() if v})
    finally:
        await _bookkeeping(app, guild, actor, result, recorded, gone)
    return result


async def _bookkeeping(
    app: App, guild: discord.Guild, actor: discord.Member, result: ResetResult, recorded: set[int], gone: set[int]
) -> None:
    # Also forget records of objects that were already deleted before the reset.
    present = {c.id for c in guild.channels} | {r.id for r in guild.roles}
    gone |= {i for i in recorded if i not in present}
    result.forgotten = await app.db.transaction(reset_store.forget, app.env.guild_id, gone)
    await app.resources.refresh()
    log_event(
        log,
        logging.WARNING,
        "server_reset",
        actor_id=actor.id,
        completed=result.completed,
        deleted=result.deleted_total,
        failed=len(result.failures),
        wipe=result.wipe,
        backup=result.backup,
    )
    await app.audit.record(
        "server.reset", actor_id=actor.id, target_type="guild", target_id=guild.id, details=result.audit_details()
    )


async def _delete(kind: str, obj: Any, reason: str, result: ResetResult) -> bool:
    """Delete one channel, category or role; True when it is gone (a 404 means someone was quicker). Any failure
    (Discord's answer, or the connection) is reported and the run goes on."""
    try:
        await obj.delete(reason=reason)
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        log_event(log, logging.WARNING, "reset_delete_failed", kind=kind, id=obj.id, status=exc.status, code=exc.code)
        result.failures.append(Failure(kind, obj.name, exc.status, exc.code))
        return False
    except Exception as exc:  # a connection error after discord.py's own retries
        log_event(log, logging.WARNING, "reset_delete_failed", kind=kind, id=obj.id, error=type(exc).__name__)
        result.failures.append(Failure(kind, obj.name, 0, 0))
        return False
    result.deleted[kind] += 1
    return True


async def _show(message: discord.Message, spec: MessageSpec) -> None:
    with contextlib.suppress(discord.HTTPException, RenderError):
        await edit_spec(message, spec)


async def finished(app: App) -> None:
    """The owner was told the result: the next start has nothing to report."""
    await app.db.transaction(reset_store.clear_running)


async def report_interrupted(app: App, notice: Callable[[], MessageSpec]) -> None:
    """At startup: a reset that a restart interrupted is reported to the owner who ran it (by DM, else the log)."""
    running = await app.db.run(reset_store.running)
    if running is None:
        return
    actor_id, started_at = running
    log_event(log, logging.WARNING, "reset_interrupted", actor_id=actor_id, started_at=started_at)
    if app.bot is not None:
        try:
            user = await app.bot.fetch_user(actor_id)
            await send_spec(user, notice())
        except (discord.HTTPException, RenderError) as exc:
            log_event(log, logging.WARNING, "reset_interrupted_dm_failed", error=type(exc).__name__)
    await app.db.transaction(reset_store.clear_running)


def kept_by_reason(kept: Sequence[Kept]) -> dict[str, list[Kept]]:
    grouped: dict[str, list[Kept]] = {}
    for item in kept:
        grouped.setdefault(item.reason, []).append(item)
    return grouped
