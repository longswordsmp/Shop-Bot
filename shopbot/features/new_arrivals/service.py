"""New-arrivals logic: the opt-in panel, the notification role, and automatic product updates.

Panel
-----
``publish`` (called by /setup only while the feature is ready) keeps exactly one panel message under
``new_arrivals:panel`` (``upsert_message``: edited in place, re-posted if deleted, never duplicated). It never posts a
product update and never changes anyone's roles, so rerunning /setup cannot notify or re-assign anybody.

Automatic updates
-----------------
The catalog publishes ``PRODUCT_CHANGED`` after a committed staff change. ``classify`` decides whether it qualifies:

- ``new``: action ``add`` with a listed (active, not archived) product, or ``toggle`` from inactive to active - when
  ``new_arrivals.announce_new_products`` is on;
- ``restock``: action ``stock`` or ``edit`` that turned a sold-out product (limited stock, nothing left to order) into
  an orderable one (units available, or unlimited stock) - when ``new_arrivals.announce_restocks`` is on.

Price, image, position and archive changes never qualify. A qualifying change schedules the durable task
``new_arrivals.announce`` (dedupe ``new_arrivals.announce:<product>:<event>:<version>``), so it survives a restart and
is scheduled once per product version. The task re-reads the product and posts the update in the new-arrivals
channel, mentioning the notification role only when ``new_arrivals.mention_role`` is on and the role exists
(``allowed_mentions`` is limited to that role).

The update is posted at most once per (product, event, version): the task claims the row in
``new_arrival_announcements`` (UNIQUE) before posting and records the message afterwards. If an earlier attempt left
its claim without a recorded message (the process stopped between posting and recording, or Discord's answer was
lost), the task first looks through the bot's recent messages in the channel for that post and records it instead of
posting a second copy.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import discord

from shopbot.config.content import MissingContent
from shopbot.core.panels import upsert_message
from shopbot.core.permissions import can_manage_role, channel_send_permissions, missing_permissions
from shopbot.core.scheduler import PermanentTaskError
from shopbot.features.catalog import images as catalog_images
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog.repo import Product
from shopbot.features.new_arrivals import content_keys as keys
from shopbot.features.new_arrivals import repo, views
from shopbot.features.new_arrivals.content_keys import NEW_ARRIVALS
from shopbot.features.server_keys import channel_mentions, channel_url
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, check_message, send_spec
from shopbot.ui.spec import FileSpec, MessageSpec

if TYPE_CHECKING:
    from shopbot.config.settings import NewArrivalsSettings, Settings
    from shopbot.context import App

log = logging.getLogger(__name__)

PANEL_PREFIX = "new_arrivals:"
PANEL_KEY = f"{PANEL_PREFIX}panel"
ANNOUNCE_TASK = "new_arrivals.announce"
# How many recent messages of the channel are searched for an unrecorded post of a claimed update.
HISTORY_LIMIT = 50
MAX_REPORTED_VIOLATIONS = 5

NEW_ACTIONS = frozenset({"add", "toggle"})
RESTOCK_ACTIONS = frozenset({"stock", "edit"})


def announce_dedupe(product_id: int, event: str, version: int) -> str:
    return f"{ANNOUNCE_TASK}:{product_id}:{event}:{version}"


# ---------------------------------------------------------------------------------------------------
# Role and text values
# ---------------------------------------------------------------------------------------------------


def notification_role(app: App, guild: discord.Guild) -> discord.Role | None:
    """The configured notification role, if /setup created (or adopted) it and it still exists."""
    key = app.settings.new_arrivals.role
    return app.resources.role(guild, key) if key else None


def has_role(member: discord.Member, role: discord.Role) -> bool:
    return any(r.id == role.id for r in member.roles)


def can_assign(guild: discord.Guild, role: discord.Role) -> bool:
    """Whether the bot may give/remove ``role``: not @everyone, not integration-managed, below the bot's top role."""
    me = guild.me
    return me is not None and not role.is_default() and can_manage_role(me, role)


def text_values(app: App) -> dict[str, str | int]:
    """Values for the panel/confirmation placeholders: ``{role}`` and every ``{channel_<key>}`` link."""
    role_id = app.resources.role_id(app.settings.new_arrivals.role)
    values: dict[str, str | int] = {keys.ROLE_PLACEHOLDER: f"<@&{role_id}>" if role_id else ""}
    values.update(channel_mentions(app))
    return values


def build_panel(app: App) -> MessageSpec:
    """The live panel. Raises ``MissingContent`` when required text is not configured."""
    return views.panel_message(app.ui, text_values(app))


# ---------------------------------------------------------------------------------------------------
# Panel publishing (/setup)
# ---------------------------------------------------------------------------------------------------


def _role_lines(app: App, guild: discord.Guild) -> list[str]:
    """Operator warnings when the panel's button cannot work yet (the role is missing or above the bot)."""
    key = app.settings.new_arrivals.role
    role = notification_role(app, guild)
    if role is None:
        return [app.op("new_arrivals.publish.role_missing", role=key)]
    if not can_assign(guild, role):
        return [app.op("new_arrivals.publish.role_not_manageable", role=role.mention)]
    return []


async def publish(app: App) -> list[str]:
    """Create or update the panel (idempotent). Returns operator report lines for /setup."""
    op = app.op
    guild = app.guild
    if guild is None:
        return [op("new_arrivals.publish.no_guild")]
    channel_key = app.settings.new_arrivals.channel
    channel = app.resources.text_channel(guild, channel_key)
    if channel is None:
        return [op("new_arrivals.publish.channel_missing", channel=channel_key)]
    try:
        spec = build_panel(app)
    except MissingContent as exc:
        return [op("new_arrivals.publish.missing_content", content_key=exc.key)]
    me = guild.me
    missing = (
        missing_permissions(me, channel, channel_send_permissions(uploads=app.banners.enabled))
        if me is not None
        else []
    )
    if missing:
        return [op("new_arrivals.publish.missing_permissions", permissions=", ".join(missing), channel=channel.mention)]
    try:
        _, action = await upsert_message(app, PANEL_KEY, channel, spec)
    except RenderError as exc:
        violations = "; ".join(str(v) for v in exc.violations[:MAX_REPORTED_VIOLATIONS])
        return [op("new_arrivals.publish.limits", violations=violations)]
    except discord.HTTPException as exc:
        log_event(
            log,
            logging.WARNING,
            "new_arrivals_panel_publish_failed",
            channel_id=channel.id,
            status=exc.status,
            code=exc.code,
        )
        return [op("new_arrivals.publish.failed", http_status=exc.status, code=exc.code)]
    return [op(f"new_arrivals.publish.{action}", channel=channel.mention), *_role_lines(app, guild)]


# ---------------------------------------------------------------------------------------------------
# Qualifying product events
# ---------------------------------------------------------------------------------------------------


def is_sold_out(product: Product) -> bool:
    """Limited stock and nothing left to order (all units sold or held by pending orders)."""
    return product.stock_on_hand is not None and product.available == 0


def is_orderable(product: Product) -> bool:
    """Listed and at least one unit can be ordered (or the stock is unlimited)."""
    available = product.available
    return product.listed and (available is None or available > 0)


def classify(settings: Settings, action: str, before: Product | None, after: Product) -> views.Event | None:
    """The update a committed catalog change qualifies for, or None (see the module docstring)."""
    na = settings.new_arrivals
    if not after.listed:
        return None
    if action in NEW_ACTIONS:
        if not na.announce_new_products:
            return None
        if action == "add":
            return views.NEW
        if before is not None and not before.active and after.active:
            return views.NEW
        return None
    if action in RESTOCK_ACTIONS:
        if not na.announce_restocks or before is None or not before.listed:
            return None
        if is_sold_out(before) and is_orderable(after):
            return views.RESTOCK
    return None


async def schedule_announcement(app: App, product: Product, event: views.Event) -> int | None:
    """Schedule the durable update task (once per product + event + version). Returns the task id or None."""
    return await app.scheduler.schedule(
        ANNOUNCE_TASK,
        run_at=app.clock.now(),
        payload={"product_id": product.id, "event": event, "version": product.version},
        dedupe_key=announce_dedupe(product.id, event, product.version),
    )


async def on_product_changed(
    app: App,
    *,
    action: str,
    before: Product | None,
    after: Product,
    actor_id: int | None = None,
    **_: Any,
) -> None:
    """``PRODUCT_CHANGED`` listener: schedule an update for a qualifying change. Never raises."""
    try:
        if not app.ready(NEW_ARRIVALS):
            log_event(log, logging.DEBUG, "new_arrivals_event_ignored", product_id=after.id, reason="not_ready")
            return
        if after.guild_id != app.env.guild_id:
            return
        event = classify(app.settings, action, before, after)
        if event is None:
            return
        task_id = await schedule_announcement(app, after, event)
        log_event(
            log,
            logging.INFO,
            "new_arrivals_update_scheduled",
            product_id=after.id,
            update=event,
            version=after.version,
            task_id=task_id,
            actor_id=actor_id,
        )
    except Exception as exc:  # the staff's catalog change must never fail because of an announcement
        log_event(log, logging.ERROR, "new_arrivals_schedule_failed", product_id=after.id, error=type(exc).__name__)
        log.exception("could not schedule the new-arrivals update of product %s", after.id)


# ---------------------------------------------------------------------------------------------------
# The announcement task
# ---------------------------------------------------------------------------------------------------


class AnnouncePending(Exception):
    """The update could not be posted yet; the scheduler retries with backoff."""


def _event_enabled(na: NewArrivalsSettings, event: str) -> bool:
    return na.announce_new_products if event == views.NEW else na.announce_restocks


def _still_qualifies(event: str, product: Product) -> bool:
    """A new product must still be listed; a restocked one must still be orderable when the update is posted."""
    return is_orderable(product) if event == views.RESTOCK else product.listed


def build_announcement(
    app: App,
    guild: discord.Guild,
    event: views.Event,
    product: Product,
    image: tuple[str | None, Sequence[FileSpec]] | None = None,
) -> MessageSpec:
    """The update for ``product``. Raises ``MissingContent`` when required text is not configured."""
    na = app.settings.new_arrivals
    role = notification_role(app, guild) if na.mention_role else None
    listing = app.settings.catalog.listing_channel
    catalog_link = channel_url(app, listing) if listing else None
    return views.announcement(
        app.ui,
        event,
        product,
        role_id=role.id if role is not None else None,
        catalog_url=catalog_link,
        image=image,
        low_stock_threshold=app.settings.catalog.low_stock_threshold,
    )


def _post_key(message: discord.Message) -> tuple[str, str] | None:
    if not message.embeds:
        return None
    embed = message.embeds[0]
    return (embed.title or "").strip(), (embed.description or "").strip()


async def _find_unrecorded_post(app: App, channel: discord.TextChannel, spec: MessageSpec) -> int | None:
    """The newest recent bot message in ``channel`` showing this update that no announcement has recorded yet.

    Raises ``discord.HTTPException`` when the channel history cannot be read.
    """
    bot_user = app.bot.user if app.bot is not None else None
    if bot_user is None or not spec.embeds:
        return None
    expected = ((spec.embeds[0].title or "").strip(), (spec.embeds[0].description or "").strip())
    recorded = await app.db.run(repo.recorded_message_ids, app.env.guild_id, channel.id)
    async for message in channel.history(limit=HISTORY_LIMIT):
        if message.author.id != bot_user.id or message.id in recorded:
            continue
        if _post_key(message) == expected:
            return message.id
    return None


async def announce(app: App, *, product_id: int, event: views.Event, version: int) -> str:
    """Task body: post the update once. Returns the outcome (for logs and tests). Raises to retry later."""
    na = app.settings.new_arrivals
    fields: dict[str, object] = {"product_id": product_id, "update": event, "version": version}
    if not app.ready(NEW_ARRIVALS):
        log_event(log, logging.INFO, "new_arrivals_update_skipped", reason="not_ready", **fields)
        return "skipped"
    if not _event_enabled(na, event):
        log_event(log, logging.INFO, "new_arrivals_update_skipped", reason="event_disabled", **fields)
        return "skipped"
    product = await app.db.run(catalog_repo.get_product, app.env.guild_id, product_id)
    if product is None or not _still_qualifies(event, product):
        log_event(log, logging.INFO, "new_arrivals_update_skipped", reason="no_longer_qualifies", **fields)
        return "skipped"
    guild = app.guild
    if guild is None:
        raise AnnouncePending("the server is not available")
    channel = app.resources.text_channel(guild, na.channel)
    if channel is None:
        log_event(log, logging.WARNING, "new_arrivals_update_pending", reason="channel_missing", **fields)
        raise AnnouncePending(f"channel '{na.channel}' does not exist")
    try:
        spec = build_announcement(app, guild, event, product, await catalog_images.image_for(app, product))
    except MissingContent as exc:
        log_event(log, logging.WARNING, "new_arrivals_update_skipped", reason="missing_content", key=exc.key, **fields)
        return "skipped"
    try:
        check_message(spec)  # before claiming: an update Discord would reject is never half-recorded
    except RenderError as exc:
        log_event(log, logging.ERROR, "new_arrivals_update_failed", reason="limits", **fields)
        raise PermanentTaskError("the update exceeds Discord limits") from exc
    mentioned = bool(spec.mention_role_ids)
    claimed, record = await app.db.transaction(
        repo.claim,
        guild_id=app.env.guild_id,
        product_id=product_id,
        event=event,
        version=version,
        channel_id=channel.id,
        now=app.clock.now(),
    )
    if claimed == "posted":
        log_event(log, logging.INFO, "new_arrivals_update_skipped", reason="already_posted", **fields)
        return "duplicate"
    message_id: int | None = None
    if claimed == "stale":
        try:
            message_id = await _find_unrecorded_post(app, channel, spec)
        except discord.HTTPException as exc:
            # Posting without knowing whether the earlier attempt's post exists could notify everyone twice.
            log_event(log, logging.WARNING, "new_arrivals_history_unavailable", status=exc.status, **fields)
            raise AnnouncePending("the channel history cannot be read") from exc
    adopted = message_id is not None
    if message_id is None:
        try:
            message = await send_spec(channel, spec)
        except discord.HTTPException as exc:
            if exc.status < 500:  # Discord refused it: nothing was posted, a retry may post it
                await app.db.transaction(repo.release, record.id)
            log_event(
                log,
                logging.WARNING,
                "new_arrivals_update_failed",
                reason="discord_error",
                status=exc.status,
                code=exc.code,
                **fields,
            )
            raise AnnouncePending(f"Discord error {exc.status}") from exc
        message_id = message.id
    await app.db.transaction(
        repo.record_posted,
        record.id,
        channel_id=channel.id,
        message_id=message_id,
        mentioned=mentioned,
        now=app.clock.now(),
    )
    log_event(
        log,
        logging.INFO,
        "new_arrivals_update_posted",
        message_id=message_id,
        mentioned=mentioned,
        adopted=adopted,
        **fields,
    )
    await app.audit.record(
        "new_arrivals.announced",
        actor_id=None,
        target_type="product",
        target_id=product_id,
        details={
            "event": event,
            "version": version,
            "message_id": message_id,
            "mentioned": mentioned,
            "adopted": adopted,
        },
    )
    return "posted"
