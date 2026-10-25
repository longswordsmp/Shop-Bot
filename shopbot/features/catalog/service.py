"""Catalog logic: manager permissions, the live listing, private browsing, and product administration.

Public API for other features
-----------------------------
- ``browse_view(app, page, *, page_custom_id=..., extra_buttons=...)`` builds one page of private browsing
  (one product per page, with its uploaded image attached and its buy button while checkout is on). Another
  feature can embed it in its own flow: pass ``page_custom_id`` to route the previous/next buttons to its own
  handler (e.g. ``lambda p: cid("co", "browse", session_id, p)``; the value may be -1 or ``total`` on a disabled
  end button), ``extra_buttons`` (a callable receiving the shown ``Product`` and returning ``ButtonSpec``s) and
  ``banner_id`` (the banner its flow shows; ``catalog`` by default).
- ``schedule_listing_refresh(app)``: call after committing a transaction that changed stock/reservations
  (``repo.reserve``/``release``/``commit_sale``); the listing is refreshed by a durable, coalesced task.
- ``request_listing_refresh(app)``: refresh now (serialized and coalesced) and get the operator report.
- ``is_manager(app, member, user_id)``: product-management permission (admins + ``catalog.manager_roles``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
import weakref
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import discord

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.core.events import PRODUCT_CHANGED
from shopbot.core.money import MoneyError, parse_amount
from shopbot.core.panels import remove_message, upsert_message
from shopbot.core.permissions import CHANNEL_SEND_PERMISSIONS, missing_permissions
from shopbot.db.repos import state
from shopbot.features.catalog import images, repo, views
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.features.catalog.repo import Product
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError
from shopbot.ui.limits import validate_message
from shopbot.ui.spec import ButtonSpec, EmbedField, MessageSpec

if TYPE_CHECKING:
    import sqlite3

    from shopbot.context import App


CHECKOUT_FEATURE = "checkout"  # the buy buttons are handled by the checkout feature
log = logging.getLogger(__name__)

LISTING_PREFIX = "catalog:listing:"
REFRESH_TASK = "catalog.refresh_listing"
REFRESH_DEDUPE = "catalog.refresh_listing"
REFRESH_DEDUPE_NEXT = "catalog.refresh_listing.next"
_MAX_REFRESH_ROUNDS = 3
_NONCE_RE = re.compile(r"^\d{1,20}$")
_WHOLE_RE = re.compile(r"^\d{1,10}$")


def listing_key(index: int) -> str:
    return f"{LISTING_PREFIX}{index}"


def _listing_index(key: str) -> int | None:
    suffix = key[len(LISTING_PREFIX) :]
    return int(suffix) if suffix.isdigit() else None


# ---------------------------------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------------------------------


def manager_role_keys(app: App) -> tuple[str, ...]:
    return app.settings.catalog.manager_roles


def is_manager(app: App, member: discord.Member | None, user_id: int | None = None) -> bool:
    """Admins, or members holding a role listed in ``catalog.manager_roles``. General staff are not managers."""
    if app.perms.is_admin(member, user_id):
        return True
    if member is None:
        return False
    role_ids = app.resources.role_ids(app.settings.catalog.manager_roles)
    return bool(role_ids & {role.id for role in member.roles})


# ---------------------------------------------------------------------------------------------------
# Live listing refresh (serialized per app, coalesced)
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshReport:
    ok: bool
    lines: tuple[str, ...]
    retry: bool = False


@dataclass
class _RefreshState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requested: int = 0
    completed: int = 0
    last: RefreshReport | None = None
    last_failure: str | None = None


_STATES: dict[int, tuple[weakref.ref[App], _RefreshState]] = {}


def _state(app: App) -> _RefreshState:
    entry = _STATES.get(id(app))
    if entry is not None and entry[0]() is app:
        return entry[1]
    for key in [k for k, (ref, _) in _STATES.items() if ref() is None]:
        del _STATES[key]
    new = _RefreshState()
    _STATES[id(app)] = (weakref.ref(app), new)
    return new


async def request_listing_refresh(app: App) -> RefreshReport:
    """Bring the live listing up to date now and return the operator report. Never raises.

    Refreshes are serialized; a caller that waited while another refresh started *after* its request
    simply receives that refresh's report (bursts are coalesced).
    """
    st = _state(app)
    st.requested += 1
    ticket = st.requested
    async with st.lock:
        if st.last is not None and st.completed >= ticket:
            return st.last
        return await _run_rounds(app, st)


async def refresh_if_needed(app: App) -> RefreshReport | None:
    """Refresh unless a successful refresh already covered every request made in this process."""
    st = _state(app)
    async with st.lock:
        if st.last is not None and st.last.ok and st.completed >= st.requested:
            return None
        return await _run_rounds(app, st)


async def _run_rounds(app: App, st: _RefreshState) -> RefreshReport:
    report = RefreshReport(ok=True, lines=())
    for _ in range(_MAX_REFRESH_ROUNDS):
        started = st.requested
        report = await _refresh_once(app, st)
        st.completed = started
        st.last = report
        if st.requested == started:
            break
    return report


async def schedule_listing_refresh(app: App) -> None:
    """Ask for a listing refresh by a durable background task (coalesced; survives restarts)."""
    st = _state(app)
    st.requested += 1
    if not app.ready(CATALOG):
        return
    now = app.clock.now()
    task_id = await app.scheduler.schedule(REFRESH_TASK, run_at=now, dedupe_key=REFRESH_DEDUPE, replace=True)
    if task_id is None:
        # The primary task is running right now and may already have read the products: queue one more run.
        await app.scheduler.schedule(REFRESH_TASK, run_at=now, dedupe_key=REFRESH_DEDUPE_NEXT, replace=True)


async def _refresh_once(app: App, st: _RefreshState) -> RefreshReport:
    try:
        report = await _refresh_listing(app, st)
    except Exception as exc:  # a refresh must never break the caller (admin command, task, /setup)
        error_id = uuid.uuid4().hex[:12]
        log_event(log, logging.ERROR, "catalog_listing_refresh_crashed", error_id=error_id, error=type(exc).__name__)
        log.exception("catalog listing refresh failure %s", error_id)
        report = await _failed(app, st, app.op("catalog.refresh.error", error_id=error_id), "error")
    if report.ok:
        st.last_failure = None
    return report


async def _failed(app: App, st: _RefreshState, line: str, reason: str, *, retry: bool = False) -> RefreshReport:
    log_event(log, logging.WARNING, "catalog_listing_refresh_failed", reason=reason)
    if st.last_failure != reason:  # audit each new failure once, not on every retry
        st.last_failure = reason
        try:
            await app.audit.record(
                "catalog.listing_refresh_failed",
                actor_id=None,
                target_type="listing",
                target_id=app.settings.catalog.listing_channel or None,
                details={"reason": reason},
            )
        except Exception:
            log.exception("failed to audit catalog listing refresh failure")
    return RefreshReport(ok=False, lines=(line,), retry=retry)


async def _refresh_listing(app: App, st: _RefreshState) -> RefreshReport:
    op = app.op
    if not app.ready(CATALOG):
        return RefreshReport(ok=True, lines=(op("catalog.refresh.not_ready"),))
    guild_id = app.env.guild_id
    channel_key = app.settings.catalog.listing_channel
    records = await app.db.run(state.published_with_prefix, guild_id, LISTING_PREFIX)
    if not channel_key:
        removed = 0
        for record in records:
            removed += int(await remove_message(app, record.key))
        return RefreshReport(ok=True, lines=(op("catalog.refresh.disabled", removed=removed),))
    guild = app.guild
    if guild is None:
        return await _failed(app, st, op("catalog.refresh.no_guild"), "no_guild")
    channel = app.resources.text_channel(guild, channel_key)
    if channel is None:
        return await _failed(app, st, op("catalog.refresh.channel_missing", channel=channel_key), "channel_missing")
    me = guild.me
    missing = missing_permissions(me, channel, CHANNEL_SEND_PERMISSIONS) if me is not None else []
    if missing:
        line = op("catalog.refresh.missing_permissions", permissions=", ".join(missing), channel=channel.mention)
        return await _failed(app, st, line, "missing_permissions")

    products = await app.db.run(repo.list_products, guild_id)
    # Image metadata only: the bytes are loaded (``images.load_files``) just for the messages that are sent or edited.
    image_refs = await images.refs_for(app, products)
    try:
        # Buy buttons only while ordering works: a click on one must never just answer 'unavailable'.
        specs = views.listing_messages(app.ui, app.settings, products, image_refs, buy=app.ready(CHECKOUT_FEATURE))
    except MissingContent as exc:
        return await _failed(app, st, op("catalog.refresh.missing_content", content_key=exc.key), "missing_content")
    for index, spec in enumerate(specs):  # validate everything before changing anything
        violations = validate_message(spec)
        if violations:
            problems = "; ".join(str(v) for v in violations[:3])
            return await _failed(app, st, op("catalog.refresh.too_large", index=index + 1, problems=problems), "limits")

    counts: Counter[str] = Counter()
    existing = {record.key for record in records}
    removed = 0
    try:
        repost = False
        for index, spec in enumerate(specs):
            if not app.ready(CATALOG):
                break  # readiness changed while posting (/reload, /content): stop and withdraw below
            key = listing_key(index)
            if repost and key in existing:
                # An earlier message was (re)posted at the bottom: re-post the following ones too so the
                # listing keeps its order in the channel.
                await remove_message(app, key)
            _, action = await upsert_message(app, key, channel, spec, prepare=partial(images.load_files, app))
            counts[action] += 1
            repost = repost or action in ("created", "recreated", "moved")
        if not app.ready(CATALOG):
            # The automatic withdrawal may have run while this refresh was posting: remove what it re-posted.
            for record in await app.db.run(state.published_with_prefix, guild_id, LISTING_PREFIX):
                await remove_message(app, record.key)
            return RefreshReport(ok=True, lines=(op("catalog.refresh.not_ready"),))
        for record in records:
            index_of = _listing_index(record.key)
            if index_of is None or index_of >= len(specs):
                removed += int(await remove_message(app, record.key))
    except discord.Forbidden:
        return await _failed(app, st, op("catalog.refresh.forbidden", channel=channel.mention), "forbidden")
    except discord.NotFound:
        return await _failed(app, st, op("catalog.refresh.not_found", channel=channel.mention), "not_found")
    except discord.HTTPException as exc:
        line = op("catalog.refresh.http_error", status=exc.status)
        return await _failed(app, st, line, f"http_{exc.status}", retry=True)
    except images.StaleImage:
        return await _failed(app, st, op("catalog.refresh.image_changed"), "image_changed", retry=True)
    except RenderError:
        return await _failed(app, st, op("catalog.refresh.too_large", index="?", problems="render"), "limits")

    unchanged = counts["unchanged"]
    line = op(
        "catalog.refresh.done",
        channel=channel.mention,
        messages=len(specs),
        products=sum(1 for p in products if p.listed),
        changed=sum(counts.values()) - unchanged,
        unchanged=unchanged,
        removed=removed,
    )
    log_event(log, logging.INFO, "catalog_listing_refreshed", messages=len(specs), changed=dict(counts))
    return RefreshReport(ok=True, lines=(line,))


# ---------------------------------------------------------------------------------------------------
# Private browsing
# ---------------------------------------------------------------------------------------------------


def _browse_data(conn: sqlite3.Connection, guild_id: int, page: int) -> tuple[int, Product | None, int]:
    total = repo.count_products(conn, guild_id)
    if total == 0:
        return 0, None, 0
    page = min(max(0, page), total - 1)
    products = repo.list_products(conn, guild_id, offset=page, limit=1)
    return total, (products[0] if products else None), page


async def browse_view(
    app: App,
    page: int,
    *,
    page_custom_id: views.PageCustomId = views.browse_custom_id,
    extra_buttons: Callable[[Product], Sequence[ButtonSpec]] | None = None,
    banner_id: BannerId = "catalog",
) -> MessageSpec:
    """Page ``page`` (0-based, clamped to the existing pages) of private browsing; see the module docs."""
    total, product, page = await app.db.run(_browse_data, app.env.guild_id, page)
    buttons: Sequence[ButtonSpec] = extra_buttons(product) if (extra_buttons is not None and product) else ()
    image = await app.db.run(images.get_image, app.env.guild_id, product.id) if product is not None else None
    return views.browse_page(
        app.ui,
        product,
        page,
        total,
        page_custom_id=page_custom_id,
        extra_buttons=tuple(buttons),
        image=image,
        buy=app.ready(CHECKOUT_FEATURE),
        low_stock_threshold=app.settings.catalog.low_stock_threshold,
        banner_id=banner_id,
    )


# ---------------------------------------------------------------------------------------------------
# Product administration (operator replies)
# ---------------------------------------------------------------------------------------------------


# Banners of the operator replies. They follow the outcome, never the tone: a saved change whose listing refresh
# failed is still a saved change (warning tone, success banner).
SAVED: BannerId = "successful-confirmation"  # the change is saved (created, updated, price, stock, image, ...)
REFUSED: BannerId = "error"  # the change was refused (invalid input, unknown product, blocked by reservations, ...)
NOTED: BannerId = "general-response"  # nothing changed (already done, unchanged, kept) or a confirmation prompt


@dataclass(frozen=True)
class OpReply:
    title: str
    description: str | None = None
    tone: str = "info"
    fields: tuple[EmbedField, ...] = ()
    # The banner the reply shows (None: no banner, for the second message of a two-message reply).
    banner: BannerId | None = NOTED


def reply_spec(app: App, reply: OpReply) -> MessageSpec:
    embed = app.ui.operator_embed(reply.title, reply.description, tone=reply.tone, fields=reply.fields)
    return MessageSpec(embeds=(embed,), banner=app.ui.banner(reply.banner) if reply.banner is not None else None)


class InputProblems(Exception):
    """Operator input could not be accepted; ``problems`` are operator-text lines."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class ArchivedProduct(repo.CatalogError):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"product {product_id} is archived")
        self.product_id = product_id


class ArchiveBlocked(repo.CatalogError):
    def __init__(self, product_id: int, reserved: int) -> None:
        super().__init__(f"product {product_id} has {reserved} reserved units")
        self.product_id = product_id
        self.reserved = reserved


def money_format(app: App) -> views.MoneyFormat:
    return views.MoneyFormat.from_settings(app.settings)


def parse_price(app: App, text: str) -> int:
    money = money_format(app)
    if not money.currency:
        raise InputProblems([app.op("catalog.invalid.currency")])
    try:
        return parse_amount(text, money.exponent, money.decimal_separator, money.thousands_separator)
    except MoneyError as exc:
        raise InputProblems([app.op("catalog.invalid.price", reason=str(exc))]) from None


def parse_product_form(app: App, fields: dict[str, str]) -> dict[str, Any]:
    """Validate the product editor form. Returns repository fields or raises ``InputProblems``."""
    op = app.op
    problems: list[str] = []
    values: dict[str, Any] = {}
    name = fields.get(views.INPUT_NAME, "").strip()
    if 1 <= len(name) <= repo.NAME_MAX:
        values["name"] = name
    else:
        problems.append(op("catalog.invalid.name"))
    try:
        values["price_minor"] = parse_price(app, fields.get(views.INPUT_PRICE, ""))
    except InputProblems as exc:
        problems.extend(exc.problems)
    stock = fields.get(views.INPUT_STOCK, "").strip()
    if not stock:
        values["stock_on_hand"] = None
    elif _WHOLE_RE.match(stock) and int(stock) <= repo.STOCK_MAX:
        values["stock_on_hand"] = int(stock)
    else:
        problems.append(op("catalog.invalid.stock", max=repo.STOCK_MAX))
    limit = fields.get(views.INPUT_MAX_PER_ORDER, "").strip()
    if not limit:
        values["max_per_order"] = None
    elif _WHOLE_RE.match(limit) and 1 <= int(limit) <= repo.MAX_PER_ORDER_MAX:
        values["max_per_order"] = int(limit)
    else:
        problems.append(op("catalog.invalid.max_per_order", max=repo.MAX_PER_ORDER_MAX))
    description = fields.get(views.INPUT_DESCRIPTION, "").strip()
    if len(description) <= repo.DESCRIPTION_MAX:
        values["description"] = description
    else:
        problems.append(op("catalog.invalid.description"))
    if problems:
        raise InputProblems(problems)
    return values


def validate_image_url(app: App, url: str) -> str | None:
    """Accept a permanent https image link (optionally restricted to ``catalog.image_hosts``); '' clears.

    Uploaded files are not accepted: Discord attachment CDN links are signed and expire, so a product
    image must be a link to a permanent image host.
    """
    text = url.strip()
    if not text:
        return None
    invalid = InputProblems([app.op("catalog.cmd.image_invalid")])
    if len(text) > repo.IMAGE_URL_MAX or any(ch.isspace() for ch in text) or not text.startswith("https://"):
        raise invalid
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        credentials = parts.username is not None or parts.password is not None
    except ValueError:
        raise invalid from None
    if parts.scheme != "https" or not host or credentials:
        raise invalid
    hosts = app.settings.catalog.image_hosts
    if hosts and host not in {h.lower() for h in hosts}:
        raise InputProblems([app.op("catalog.cmd.image_host_not_allowed", hosts=", ".join(hosts))])
    return text


def _load_mutable(conn: sqlite3.Connection, guild_id: int, product_id: int) -> Product:
    product = repo.get_product(conn, guild_id, product_id)
    if product is None:
        raise repo.ProductNotFound(product_id)
    if product.archived:
        raise ArchivedProduct(product_id)
    return product


def _tx_update(
    conn: sqlite3.Connection, guild_id: int, product_id: int, now: int, fields: dict[str, Any]
) -> tuple[Product, Product]:
    current = _load_mutable(conn, guild_id, product_id)
    return current, repo.update_product(conn, product_id, current.version, now=now, **fields)


def _tx_create(
    conn: sqlite3.Connection, guild_id: int, nonce: str | None, values: dict[str, Any], now: int
) -> tuple[Product, bool]:
    marker = f"catalog.addform.{nonce}" if nonce else None
    if marker is not None:
        existing = state.kv_get(conn, marker)
        if existing is not None and existing.isdigit():
            product = repo.get_product(conn, guild_id, int(existing))
            if product is not None:
                return product, False
    product = repo.create_product(conn, guild_id, now=now, **values)
    if marker is not None:
        state.kv_set(conn, marker, str(product.id), now)
    return product, True


def _tx_edit(
    conn: sqlite3.Connection, guild_id: int, product_id: int, expected_version: int, values: dict[str, Any], now: int
) -> tuple[Product, Product]:
    current = _load_mutable(conn, guild_id, product_id)
    if current.version != expected_version and all(getattr(current, k) == v for k, v in values.items()):
        return current, current  # a repeated submission of values that are already saved
    return current, repo.update_product(conn, product_id, expected_version, now=now, **values)


def _tx_stock(
    conn: sqlite3.Connection, guild_id: int, product_id: int, mode: str, amount: int | None, now: int
) -> tuple[Product, Product]:
    current = _load_mutable(conn, guild_id, product_id)
    if mode == "add":
        return current, repo.add_stock(conn, product_id, amount or 0, now)
    return current, repo.set_stock(conn, product_id, None if mode == "unlimited" else amount, now)


def _tx_toggle(conn: sqlite3.Connection, guild_id: int, product_id: int, now: int) -> tuple[Product, Product]:
    current = _load_mutable(conn, guild_id, product_id)
    return current, repo.update_product(conn, product_id, current.version, now=now, active=not current.active)


def _tx_archive(conn: sqlite3.Connection, guild_id: int, product_id: int, now: int) -> tuple[Product, Product]:
    current = repo.get_product(conn, guild_id, product_id)
    if current is None:
        raise repo.ProductNotFound(product_id)
    if current.archived:
        return current, current
    if current.reserved > 0:
        raise ArchiveBlocked(product_id, current.reserved)
    images.delete_image(conn, guild_id, product_id)  # an archived product is never shown again
    return current, repo.update_product(conn, product_id, current.version, now=now, archived=True)


def error_reply(app: App, exc: Exception) -> OpReply:
    """Operator reply for an expected administration error."""
    op = app.op
    if isinstance(exc, InputProblems):
        return OpReply(op("catalog.cmd.invalid"), "\n".join(exc.problems), tone="warning", banner=REFUSED)
    if isinstance(exc, repo.ProductNotFound):
        return OpReply(op("catalog.cmd.not_found", product_id=exc.product_id), tone="warning", banner=REFUSED)
    if isinstance(exc, ArchivedProduct):
        return OpReply(op("catalog.cmd.archived_product", product_id=exc.product_id), tone="warning", banner=REFUSED)
    if isinstance(exc, repo.ConcurrentUpdate):
        return OpReply(op("catalog.cmd.stale", product_id=exc.product_id), tone="warning", banner=REFUSED)
    if isinstance(exc, repo.StockBelowReserved):
        if exc.requested < 0:
            return OpReply(
                op("catalog.cmd.invalid"), op("catalog.cmd.stock_set_negative"), tone="warning", banner=REFUSED
            )
        text = op("catalog.cmd.stock_below_reserved", reserved=exc.reserved, requested=exc.requested)
        return OpReply(op("catalog.cmd.invalid"), text, tone="warning", banner=REFUSED)
    if isinstance(exc, repo.UnlimitedStock):
        return OpReply(op("catalog.cmd.stock_is_unlimited", product_id=exc.product_id), tone="warning", banner=REFUSED)
    if isinstance(exc, ArchiveBlocked):
        text = op("catalog.cmd.archive_reserved", product_id=exc.product_id, reserved=exc.reserved)
        return OpReply(text, tone="warning", banner=REFUSED)
    if isinstance(exc, repo.InvalidProduct):
        reasons = {
            "name": op("catalog.invalid.name"),
            "description": op("catalog.invalid.description"),
            "stock_on_hand": op("catalog.cmd.stock_too_large", max=repo.STOCK_MAX),
            "delta": op("catalog.cmd.stock_too_large", max=repo.STOCK_MAX),
            "max_per_order": op("catalog.invalid.max_per_order", max=repo.MAX_PER_ORDER_MAX),
            "sort_order": op("catalog.invalid.position", min=-repo.SORT_LIMIT, max=repo.SORT_LIMIT),
            "image_url": op("catalog.cmd.image_invalid"),
        }
        return OpReply(op("catalog.cmd.invalid"), reasons.get(exc.field, str(exc)), tone="warning", banner=REFUSED)
    raise exc


_EXPECTED = (InputProblems, repo.CatalogError)


async def _finish(
    app: App,
    reply: OpReply,
    *,
    action: str | None,
    actor_id: int,
    product: Product,
    details: dict[str, Any],
    before: Product | None = None,
) -> OpReply:
    """Audit a committed change, refresh the listing, tell subscribers (``PRODUCT_CHANGED``), and attach the
    refresh report to the reply."""
    if action is not None:
        await app.audit.record(
            f"catalog.product_{action}", actor_id=actor_id, target_type="product", target_id=product.id, details=details
        )
    if action is not None:
        # Before the (slow, Discord-bound) listing refresh: subscribers schedule their durable tasks right after the
        # commit, so a restart during the refresh cannot lose them.
        await app.events.publish(PRODUCT_CHANGED, action=action, before=before, after=product, actor_id=actor_id)
    report = await request_listing_refresh(app)
    tone = reply.tone if report.ok else "warning"
    listing = EmbedField(app.op("catalog.refresh.field"), "\n".join(report.lines)) if report.lines else None
    fields = reply.fields + ((listing,) if listing else ())
    return OpReply(reply.title, reply.description, tone=tone, fields=fields, banner=reply.banner)


def _changes(before: Product, after: Product, names: Sequence[str]) -> dict[str, Any]:
    return {
        name: [getattr(before, name), getattr(after, name)]
        for name in names
        if getattr(before, name) != getattr(after, name)
    }


async def create_from_form(app: App, *, actor_id: int, fields: dict[str, str], nonce: str | None) -> OpReply:
    try:
        values = parse_product_form(app, fields)
        nonce_value = nonce if nonce and _NONCE_RE.match(nonce) else None
        product, created = await app.db.transaction(_tx_create, app.env.guild_id, nonce_value, values, app.clock.now())
    except _EXPECTED as exc:
        return error_reply(app, exc)
    if not created:
        return OpReply(app.op("catalog.cmd.already_created", product_id=product.id), tone="info", banner=NOTED)
    details = {
        "name": product.name,
        "price_minor": product.price_minor,
        "stock_on_hand": product.stock_on_hand,
        "max_per_order": product.max_per_order,
    }
    reply = OpReply(app.op("catalog.cmd.created", product_id=product.id), tone="success", banner=SAVED)
    return await _finish(app, reply, action="add", actor_id=actor_id, product=product, details=details)


async def edit_from_form(
    app: App, *, actor_id: int, product_id: int, expected_version: int, fields: dict[str, str]
) -> OpReply:
    try:
        values = parse_product_form(app, fields)
        before, after = await app.db.transaction(
            _tx_edit, app.env.guild_id, product_id, expected_version, values, app.clock.now()
        )
    except _EXPECTED as exc:
        return error_reply(app, exc)
    if after.version == before.version:
        return OpReply(app.op("catalog.cmd.unchanged", product_id=product_id), banner=NOTED)
    details = _changes(before, after, ("name", "price_minor", "stock_on_hand", "max_per_order"))
    if before.description != after.description:
        details["description_changed"] = True
    reply = OpReply(app.op("catalog.cmd.updated", product_id=product_id), tone="success", banner=SAVED)
    return await _finish(app, reply, action="edit", actor_id=actor_id, product=after, details=details, before=before)


async def set_price(app: App, *, actor_id: int, product_id: int, amount: str) -> OpReply:
    try:
        price = parse_price(app, amount)
        before, after = await app.db.transaction(
            _tx_update, app.env.guild_id, product_id, app.clock.now(), {"price_minor": price}
        )
    except _EXPECTED as exc:
        return error_reply(app, exc)
    shown = views.operator_price(app.ui, app.op, money_format(app), after.price_minor)
    reply = OpReply(app.op("catalog.cmd.price_set", product_id=product_id, price=shown), tone="success", banner=SAVED)
    if before.version == after.version:
        return reply
    details = _changes(before, after, ("price_minor",))
    return await _finish(app, reply, action="price", actor_id=actor_id, product=after, details=details, before=before)


async def change_stock(app: App, *, actor_id: int, product_id: int, mode: str, amount: int | None) -> OpReply:
    op = app.op
    if mode not in ("set", "add", "unlimited"):
        return OpReply(op("catalog.cmd.invalid"), tone="warning", banner=REFUSED)
    if mode != "unlimited" and amount is None:
        text = op("catalog.cmd.stock_amount_required")
        return OpReply(op("catalog.cmd.invalid"), text, tone="warning", banner=REFUSED)
    if mode == "add" and amount == 0:
        return OpReply(op("catalog.cmd.invalid"), op("catalog.cmd.stock_add_zero"), tone="warning", banner=REFUSED)
    if mode == "set" and amount is not None and amount < 0:
        text = op("catalog.cmd.stock_set_negative")
        return OpReply(op("catalog.cmd.invalid"), text, tone="warning", banner=REFUSED)
    try:
        before, after = await app.db.transaction(_tx_stock, app.env.guild_id, product_id, mode, amount, app.clock.now())
    except _EXPECTED as exc:
        return error_reply(app, exc)
    if after.stock_on_hand is None:
        title = op("catalog.cmd.stock_unlimited", product_id=product_id)
    elif mode == "add":
        title = op("catalog.cmd.stock_added", product_id=product_id, delta=f"{amount:+d}", stock=after.stock_on_hand)
    else:
        title = op("catalog.cmd.stock_set", product_id=product_id, stock=after.stock_on_hand)
    details: dict[str, Any] = {"mode": mode, "stock_on_hand": [before.stock_on_hand, after.stock_on_hand]}
    if mode == "add":
        details["delta"] = amount
    reply = OpReply(title, tone="success", banner=SAVED)
    return await _finish(app, reply, action="stock", actor_id=actor_id, product=after, details=details, before=before)


def _tx_image(
    conn: sqlite3.Connection,
    guild_id: int,
    product_id: int,
    url: str | None,
    upload: images.UploadedImage | None,
    actor_id: int,
    now: int,
) -> tuple[Product, Product, images.ProductImage | None]:
    """Set the product's image link (``url``), its uploaded image (``upload``), or clear both (neither). One kind
    replaces the other. Every change bumps the product version (it is what the listing shows)."""
    current = _load_mutable(conn, guild_id, product_id)
    previous = images.get_image(conn, guild_id, product_id, with_data=False)
    if upload is not None:
        changed = images.store_image(conn, guild_id, product_id, upload, actor_id=actor_id, now=now)
    else:
        changed = images.delete_image(conn, guild_id, product_id)
    if url != current.image_url:
        after = repo.update_product(conn, product_id, current.version, now=now, image_url=url)
    elif changed:
        after = repo.touch_product(conn, product_id, current.version, now=now)
    else:
        after = current
    return current, after, previous


def upload_problem(app: App, exc: images.ImageRejected) -> InputProblems:
    op = app.op
    reasons = {
        "type": op("catalog.cmd.image_type"),
        "size": op("catalog.cmd.image_size", max_mb=images.MAX_IMAGE_BYTES // (1024 * 1024)),
        "empty": op("catalog.cmd.image_empty"),
        "download": op("catalog.cmd.image_download"),
    }
    return InputProblems([reasons.get(exc.reason, op("catalog.cmd.image_type"))])


async def set_image(
    app: App, *, actor_id: int, product_id: int, url: str, upload: images.Upload | None = None
) -> OpReply:
    """``/product image``: an https link (``url``), an uploaded file (``upload``, downloaded and stored in the
    database), or neither to remove the image. Both at once are refused."""
    try:
        if upload is not None and url.strip():
            raise InputProblems([app.op("catalog.cmd.image_both")])
        link = validate_image_url(app, url) if upload is None else None
        uploaded: images.UploadedImage | None = None
        if upload is not None:
            product = await load_product(app, product_id)  # refuse before downloading anything for a bad product
            if product is None:
                raise repo.ProductNotFound(product_id)
            if product.archived:
                raise ArchivedProduct(product_id)
            try:
                uploaded = await images.read_upload(upload)
            except images.ImageRejected as exc:
                raise upload_problem(app, exc) from None
        before, after, previous = await app.db.transaction(
            _tx_image, app.env.guild_id, product_id, link, uploaded, actor_id, app.clock.now()
        )
    except _EXPECTED as exc:
        return error_reply(app, exc)
    key = "catalog.cmd.image_set" if (after.image_url or uploaded is not None) else "catalog.cmd.image_cleared"
    reply = OpReply(app.op(key, product_id=product_id), tone="success", banner=SAVED)
    if before.version == after.version:
        return reply
    details: dict[str, Any] = {"image_url": [before.image_url, after.image_url]}
    if previous is not None:
        details["previous_upload"] = {"sha256": previous.sha256, "size": previous.size}
    if uploaded is not None:
        details["upload"] = {
            "filename": uploaded.filename,
            "content_type": uploaded.content_type,
            "size": uploaded.size,
            "sha256": uploaded.sha256,
        }
    return await _finish(app, reply, action="image", actor_id=actor_id, product=after, details=details, before=before)


async def toggle_active(app: App, *, actor_id: int, product_id: int) -> OpReply:
    try:
        before, after = await app.db.transaction(_tx_toggle, app.env.guild_id, product_id, app.clock.now())
    except _EXPECTED as exc:
        return error_reply(app, exc)
    key = "catalog.cmd.activated" if after.active else "catalog.cmd.deactivated"
    reply = OpReply(app.op(key, product_id=product_id), tone="success", banner=SAVED)
    details = {"active": after.active}
    return await _finish(app, reply, action="toggle", actor_id=actor_id, product=after, details=details, before=before)


async def archive(app: App, *, actor_id: int, product_id: int, confirm: bool, via: str = "archive") -> OpReply:
    """Archive (soft-delete) a product: hidden for good, never orderable again, order history kept, uploaded image
    deleted. Refused while pending orders hold units. ``via='remove'`` words the replies for ``/product remove``
    (same rules); the audit action and the ``PRODUCT_CHANGED`` event are ``archive`` either way."""
    if not confirm:
        return OpReply(app.op("catalog.cmd.archive_confirm", product_id=product_id), tone="warning", banner=NOTED)
    try:
        before, after = await app.db.transaction(_tx_archive, app.env.guild_id, product_id, app.clock.now())
    except ArchiveBlocked as exc:
        if via == "remove":
            text = app.op("catalog.cmd.remove_reserved", product_id=exc.product_id, reserved=exc.reserved)
            return OpReply(text, tone="warning", banner=REFUSED)
        return error_reply(app, exc)
    except _EXPECTED as exc:
        return error_reply(app, exc)
    done_key = "catalog.cmd.removed" if via == "remove" else "catalog.cmd.archived"
    reply = OpReply(app.op(done_key, product_id=product_id), tone="success", banner=SAVED)
    if before.version == after.version:
        return reply  # already archived: nothing changed
    details = {"name": after.name, "via": via}
    return await _finish(app, reply, action="archive", actor_id=actor_id, product=after, details=details, before=before)


async def remove_prompt(app: App, product_id: int) -> MessageSpec:
    """``/product remove``: the confirmation message (or why the product cannot be removed)."""
    product = await load_product(app, product_id)
    if product is None:
        return reply_spec(app, error_reply(app, repo.ProductNotFound(product_id)))
    if product.archived:
        removed = app.op("catalog.cmd.removed", product_id=product_id)
        return reply_spec(app, OpReply(removed, tone="info", banner=SAVED))
    if product.reserved > 0:
        text = app.op("catalog.cmd.remove_reserved", product_id=product_id, reserved=product.reserved)
        return reply_spec(app, OpReply(text, tone="warning", banner=REFUSED))
    return views.remove_confirm(app.ui, app.op, product)


async def set_position(app: App, *, actor_id: int, product_id: int, position: int) -> OpReply:
    try:
        before, after = await app.db.transaction(
            _tx_update, app.env.guild_id, product_id, app.clock.now(), {"sort_order": position}
        )
    except _EXPECTED as exc:
        return error_reply(app, exc)
    reply = OpReply(
        app.op("catalog.cmd.sorted", product_id=product_id, position=position), tone="success", banner=SAVED
    )
    if before.version == after.version:
        return reply
    details = {"sort_order": [before.sort_order, after.sort_order]}
    return await _finish(app, reply, action="sort", actor_id=actor_id, product=after, details=details, before=before)


async def load_product(app: App, product_id: int) -> Product | None:
    return await app.db.run(repo.get_product, app.env.guild_id, product_id)


async def product_details(app: App, product_id: int) -> list[MessageSpec]:
    """Staff details of a product, followed by its customer card (when the catalog text is complete). Only the first
    message shows a banner (``staff-dashboard``); the card preview shows none, like the listing cards."""
    product = await load_product(app, product_id)
    if product is None:
        return [reply_spec(app, error_reply(app, repo.ProductNotFound(product_id)))]
    image = await app.db.run(images.get_image, app.env.guild_id, product.id)
    specs = [views.product_details_message(app.ui, app.op, money_format(app), product, image)]
    image_url, files = images.embed_image(product, image)
    try:
        embed = views.product_embed(
            app.ui, product, image_url=image_url, low_stock_threshold=app.settings.catalog.low_stock_threshold
        )
        card = MessageSpec(embeds=(embed,), files=tuple(files))
    except MissingContent:
        card = reply_spec(app, _preview_missing(app))
    if validate_message(card):
        card = reply_spec(app, _preview_missing(app))
    specs.append(card)
    return specs


def _preview_missing(app: App) -> OpReply:
    """The second message of /product view when the customer card cannot be shown (no banner: a continuation)."""
    return OpReply(app.op("catalog.view.customer_preview_missing"), tone="warning", banner=None)


def _admin_page(
    conn: sqlite3.Connection, guild_id: int, page: int, include_archived: bool
) -> tuple[list[Product], int, int]:
    total = repo.count_products(conn, guild_id, include_inactive=True, include_archived=include_archived)
    pages = max(1, -(-total // views.ADMIN_PAGE_SIZE))
    page = min(max(0, page), pages - 1)
    products = repo.list_products(
        conn,
        guild_id,
        include_inactive=True,
        include_archived=include_archived,
        offset=page * views.ADMIN_PAGE_SIZE,
        limit=views.ADMIN_PAGE_SIZE,
    )
    return products, total, page


async def admin_list_view(app: App, page: int, include_archived: bool) -> MessageSpec:
    products, total, page = await app.db.run(_admin_page, app.env.guild_id, page, include_archived)
    return views.product_list_page(
        app.ui, app.op, money_format(app), products, page=page, total=total, include_archived=include_archived
    )


async def autocomplete_products(app: App, query: str, *, include_archived: bool = False) -> list[Product]:
    return await app.db.run(repo.search_products, app.env.guild_id, query, include_archived=include_archived)
