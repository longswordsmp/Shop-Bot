"""Staff alerts (order alerts, failure alerts) in ``operations.staff_channel`` and the Owner's private alerts.

Flow: checkout publishes ``ORDER_SUBMITTED``/``ORDER_STATUS_CHANGED`` from its durable follow-ups, and the router,
command tree, scheduler and notifier publish ``FAILURE`` (``shopbot.core.events``). The handlers here only record
what must be recorded (audit) and schedule ONE deduplicated ``operations.alert`` task per alert, so alerts survive a
restart and are never sent twice (the notification itself is deduplicated by the same key). The task builds the
message from the current data when it runs.

Failure alerts of the same kind and source are grouped: at most one per ``FAILURE_WINDOW_SECONDS`` (every failure is
still recorded in the audit log). A failed alert task never triggers another alert (no loops).

The Owner (``OWNER_ID``) receives order alerts and critical failure alerts by direct message when
``operations.owner_dm_alerts`` is on; when the direct message fails, the alert is posted in the staff channel with a
mention of the Owner, and the failure is logged and audited (no private data).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import discord
from discord.utils import escape_markdown

from shopbot.config.content import MissingContent
from shopbot.core.permissions import can_manage_role, channel_send_permissions, missing_permissions
from shopbot.core.scheduler import PermanentTaskError
from shopbot.db.repos import notifications as notif_repo
from shopbot.db.repos import state
from shopbot.db.repos.tasks import Task
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import models as checkout_models
from shopbot.features.checkout import payment_methods as checkout_payment_methods
from shopbot.features.checkout import service as checkout_service
from shopbot.features.checkout import views as checkout_views
from shopbot.features.operations import content_keys as k
from shopbot.features.operations import customer_role as customer_role_module
from shopbot.features.operations import stock, views
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import RenderError, send_kwargs
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

TASK_ALERT = stock.TASK_ALERT
ALERT_FAILURE = "failure"
# Failure alerts of one kind and source are grouped into one alert per window (technical anti-flood, not business).
FAILURE_WINDOW_SECONDS = 600
OWNER_PREFIX = "operations.owner:"
OWN_PREFIX = "operations."


class AlertIncomplete(Exception):
    """The staff channel post failed (transient Discord error): the alert task is retried."""


# -- helpers --------------------------------------------------------------------------------------------------------


def staff_channel_id(app: App) -> int | None:
    return app.resources.channel_id(app.settings.operations.staff_channel)


def ping_role_ids(app: App) -> tuple[int, ...]:
    return tuple(sorted(app.resources.role_ids(app.settings.operations.ping_roles)))


def message_url(app: App, channel_id: int | None, message_id: int | None) -> str | None:
    if not channel_id or not message_id:
        return None
    return f"https://discord.com/channels/{app.env.guild_id}/{channel_id}/{message_id}"


def order_values(app: App, view: checkout_models.OrderView) -> dict[str, str | int]:
    """Placeholder values of order alerts and reminders (raises ``MissingContent`` without the money format)."""
    order = view.order
    total = checkout_views.order_money(app.ui, app.settings, order.currency)(order.total_minor)
    status = app.ui.text(checkout_keys.status_label_key(order.status))
    if status is None:
        raise MissingContent(checkout_keys.status_label_key(order.status))
    return {
        "order_number": str(order.number),
        "customer_mention": checkout_views.user_mention(order.user_id),
        "total": total,
        "status": status,
        "order_channel": checkout_views.channel_mention(view.channel_id),
    }


async def schedule_alert(app: App, dedupe_key: str, payload: dict[str, Any]) -> None:
    """One durable alert per ``dedupe_key`` (a second request for the same alert changes nothing)."""
    await app.scheduler.schedule(TASK_ALERT, run_at=app.clock.now(), payload=payload, dedupe_key=dedupe_key)


def order_alert_key(alert: str, order_id: int) -> str:
    return f"operations.alert:{alert}:{order_id}"


# -- event handlers (registered in ``register``) ------------------------------------------------------------------


async def on_order_submitted(app: App, *, order_id: int, **_: Any) -> None:
    await schedule_alert(app, order_alert_key(k.NEW_ORDER, order_id), {"alert": k.NEW_ORDER, "order_id": order_id})


async def on_order_status_changed(app: App, *, order_id: int, status: str, **_: Any) -> None:
    if status == checkout_models.PAID:
        payload = {"alert": k.PAID_ORDER, "order_id": order_id}
        await schedule_alert(app, order_alert_key(k.PAID_ORDER, order_id), payload)
    elif status == checkout_models.PAYMENT_SUBMITTED:
        # Once per submission: a customer may submit again after staff refused a card (the order version differs).
        order = await checkout_service.get_order(app, order_id)
        if order is not None and order.status == status:
            payload = {"alert": k.PAYMENT_SUBMITTED, "order_id": order_id}
            await schedule_alert(app, f"{order_alert_key(k.PAYMENT_SUBMITTED, order_id)}:v{order.version}", payload)


async def on_payment_method_requested(app: App, *, order_id: int, method: str, **_: Any) -> None:
    """Once per order and method (the customer may also ask for another method)."""
    payload = {"alert": k.PAYMENT_METHOD, "order_id": order_id, "method": method}
    await schedule_alert(app, f"{order_alert_key(k.PAYMENT_METHOD, order_id)}:{method}", payload)


async def on_failure(
    app: App,
    *,
    kind: str,
    source: str | None = None,
    error_id: str | None = None,
    error: str | None = None,
    task_id: int | None = None,
    attempts: int | None = None,
    user_id: int | None = None,
    role_id: int | None = None,
    channel_id: int | None = None,
    **_: Any,
) -> None:
    """Audit the failure (the scheduler audits task failures itself) and schedule a grouped staff alert."""
    now = app.clock.now()
    window = now // FAILURE_WINDOW_SECONDS
    name = source or "-"
    if kind == "task":
        if name == TASK_ALERT:
            return  # an alert task itself failed: the audit log has it; alerting again could loop
        failure = k.FAILURE_TASK
        values: dict[str, str | int] = {"task": name, "task_id": task_id or 0, "attempts": attempts or 0}
        dedupe = f"operations.alert:failure:task:{task_id}"
    elif kind in ("interaction", "command"):
        await app.audit.record(
            "operations.error",
            actor_id=None,
            target_type=kind,
            target_id=name,
            details={"error_id": error_id, "error": error},
        )
        failure = k.FAILURE_ERROR
        values = {"source": name, "error_id": error_id or "-", "error": error or "-"}
        dedupe = f"operations.alert:failure:{kind}:{name}:{window}"
    elif kind == "notification":
        # Who it was for: the member (DMs, posts addressed to a member) or else the channel (posts for staff).
        target_type, target_id = (
            ("user", user_id) if user_id else ("channel", channel_id) if channel_id else (None, None)
        )
        await app.audit.record(
            "notifications.failed", actor_id=None, target_type=target_type, target_id=target_id, details={"kind": name}
        )
        if name.startswith(OWN_PREFIX):
            return  # one of operations' own alerts/reminders failed: audited only; alerting about alerts could loop
        failure = k.FAILURE_NOTIFICATION
        values = {"user_mention": checkout_views.user_mention(user_id) if user_id else "-", "notification": name}
        dedupe = f"operations.alert:failure:notification:{name}:{user_id}:{window}"
    elif kind == "role":
        failure = k.FAILURE_ROLE
        values = {
            "user_mention": checkout_views.user_mention(user_id) if user_id else "-",
            "role_mention": f"<@&{role_id}>" if role_id else "-",
        }
        dedupe = f"operations.alert:failure:role:{name}"
    else:
        return
    await schedule_alert(app, dedupe, {"alert": ALERT_FAILURE, "kind": failure, "values": values})


# -- the Owner's private alerts -------------------------------------------------------------------------------------


async def notify_owner(app: App, spec: MessageSpec, *, kind: str, dedupe_key: str) -> str:
    """DM the Owner (at most once per ``dedupe_key``); when that fails, post ``spec`` in the staff channel mentioning
    the Owner. Returns 'dm' | 'channel' | 'none' | 'duplicate' | 'off'."""
    owner_id = app.env.owner_id
    if not app.settings.operations.owner_dm_alerts or not owner_id or app.bot is None:
        return "off"
    if await app.db.run(notif_repo.already_sent, dedupe_key):
        return "duplicate"
    reason: str
    try:
        user = app.bot.get_user(owner_id) or await app.bot.fetch_user(owner_id)
        await user.send(**send_kwargs(spec))
    except discord.Forbidden:
        reason = "dm_forbidden"
        await app.db.transaction(state.set_dm_unavailable, owner_id, app.clock.now())
    except (discord.HTTPException, RenderError) as exc:
        reason = f"dm_failed:{type(exc).__name__}"
    else:
        await app.db.transaction(
            notif_repo.record,
            dedupe_key=dedupe_key,
            user_id=owner_id,
            channel_id=None,
            kind=kind,
            delivery="dm",
            status="sent",
            error=None,
            now=app.clock.now(),
        )
        return "dm"
    log_event(log, logging.WARNING, "operations_owner_dm_failed", kind=kind, reason=reason)
    await app.audit.record(
        "operations.owner_dm_failed", actor_id=None, target_type="user", target_id=owner_id, details={"kind": kind}
    )
    channel_id = staff_channel_id(app)
    if not channel_id:
        return "none"
    posted = await app.notifier.notify_channel(
        channel_id=channel_id, spec=replace(spec, mention_user_ids=(owner_id,)), kind=kind, dedupe_key=dedupe_key
    )
    return "channel" if posted is not None else "none"


# -- the alert task ---------------------------------------------------------------------------------------------------


async def _post(app: App, spec: MessageSpec, *, kind: str, dedupe_key: str) -> None:
    channel_id = staff_channel_id(app)
    if not channel_id:
        log_event(log, logging.ERROR, "operations_staff_channel_missing", kind=kind)
        await app.audit.record(
            "operations.alert_undelivered",
            actor_id=None,
            target_type="alert",
            target_id=kind,
            details={"reason": "channel_missing"},
        )
        return
    if await app.db.run(notif_repo.already_sent, dedupe_key):
        return
    if await app.notifier.notify_channel(channel_id=channel_id, spec=spec, kind=kind, dedupe_key=dedupe_key) is None:
        raise AlertIncomplete(kind)  # retried with backoff; the notifier never posts the same key twice


async def _order_alert(
    app: App, alert: str, order_id: int, dedupe_key: str, extra: dict[str, str | int] | None = None
) -> None:
    view = await checkout_service.order_view(app, order_id)
    if view is None:
        return
    order = view.order
    record_url = message_url(app, order.staff_channel_id, order.staff_message_id)
    ticket_url = f"https://discord.com/channels/{app.env.guild_id}/{view.channel_id}" if view.channel_id else None
    values = {**order_values(app, view), **(extra or {})}
    staff_spec = views.order_alert(
        app.ui, alert, values, ping_role_ids=ping_role_ids(app), record_url=record_url, ticket_url=ticket_url
    )
    owner_spec = views.order_alert(app.ui, alert, values, owner=True, record_url=record_url, ticket_url=ticket_url)
    kind = f"operations.alert.{alert}"
    try:
        await _post(app, staff_spec, kind=kind, dedupe_key=dedupe_key)
    finally:
        await notify_owner(app, owner_spec, kind=f"operations.owner.{alert}", dedupe_key=OWNER_PREFIX + dedupe_key)


async def _stock_alert(app: App, alert: str, product_id: int, dedupe_key: str) -> None:
    product = await app.db.run(catalog_repo.get_product, app.env.guild_id, product_id)
    if product is None or product.archived or not app.settings.operations.stock_alerts:
        return  # removed meanwhile, or the alerts were switched off
    if stock.level(product, app.settings.catalog.low_stock_threshold) != alert:
        # Restocked, or the order that caused it ended unpaid, before the alert ran: it would no longer be true.
        log_event(log, logging.INFO, "stock_alert_skipped", product_id=product_id, stock=alert)
        return
    values: dict[str, str | int] = {"name": escape_markdown(product.name), "count": product.available or 0}
    spec = views.stock_alert(app.ui, alert, values, ping_role_ids=ping_role_ids(app))
    await _post(app, spec, kind=f"operations.alert.{alert}", dedupe_key=dedupe_key)


async def _failure_alert(app: App, failure: str, values: dict[str, str | int], dedupe_key: str) -> None:
    staff_spec = views.failure_alert(
        app.ui, failure, values, ping_role_ids=ping_role_ids(app) if failure != k.FAILURE_ERROR else ()
    )
    kind = f"operations.failure.{failure}"
    try:
        await _post(app, staff_spec, kind=kind, dedupe_key=dedupe_key)
    finally:
        if failure in k.CRITICAL_FAILURES:
            owner_spec = views.failure_alert(app.ui, failure, values, owner=True)
            await notify_owner(
                app, owner_spec, kind=f"operations.owner.failure_{failure}", dedupe_key=OWNER_PREFIX + dedupe_key
            )


async def alert_task(app: App, task: Task) -> None:
    """``operations.alert``: build and deliver one alert (staff channel, and the Owner when applicable)."""
    if not app.ready(k.FEATURE):
        log_event(log, logging.WARNING, "operations_alert_skipped", reason="not_ready", key=task.dedupe_key)
        return
    dedupe_key = task.dedupe_key or f"{TASK_ALERT}:{task.id}"
    alert = task.payload.get("alert")
    try:
        if alert in k.ORDER_ALERTS:
            order_id = task.payload.get("order_id")
            if not isinstance(order_id, int):
                raise PermanentTaskError("payload has no order_id")
            await _order_alert(app, str(alert), order_id, dedupe_key)
        elif alert == k.PAYMENT_METHOD:
            order_id, method = task.payload.get("order_id"), task.payload.get("method")
            if not isinstance(order_id, int) or not isinstance(method, str):
                raise PermanentTaskError("payload has no order_id/method")
            if app.settings.payments.method(method) is None:
                return  # the method is no longer offered: nothing to ask the staff
            name = checkout_payment_methods.method_name(app.ui, method)
            await _order_alert(app, k.PAYMENT_METHOD, order_id, dedupe_key, {"method": name})
        elif alert in k.STOCK_ALERTS:
            product_id = task.payload.get("product_id")
            if not isinstance(product_id, int):
                raise PermanentTaskError("payload has no product_id")
            await _stock_alert(app, str(alert), product_id, dedupe_key)
        elif alert == ALERT_FAILURE:
            failure = task.payload.get("kind")
            raw = task.payload.get("values")
            if failure not in k.FAILURES or not isinstance(raw, dict):
                raise PermanentTaskError("payload has no failure kind/values")
            values = {str(key): v for key, v in raw.items() if isinstance(v, (str, int))}
            await _failure_alert(app, str(failure), values, dedupe_key)
        else:
            raise PermanentTaskError(f"unknown alert {alert!r}")
    except MissingContent as exc:  # the feature became incomplete meanwhile: nothing incomplete is sent
        log_event(log, logging.ERROR, "content_missing_at_runtime", key=exc.key, where="operations.alert")


# -- /setup report ----------------------------------------------------------------------------------------------------


async def publish(app: App) -> list[str]:
    """Operations has no panel; /setup reports what would keep alerts from reaching staff."""
    guild = app.guild
    if guild is None:
        return [app.op("operations.publish.no_guild")]
    o = app.settings.operations
    lines: list[str] = []
    me = guild.me
    channel = app.resources.text_channel(guild, o.staff_channel)
    if channel is None:
        lines.append(app.op("operations.publish.staff_channel_missing", name=o.staff_channel))
    else:
        missing = missing_permissions(me, channel, channel_send_permissions(uploads=app.banners.enabled))
        if missing:
            lines.append(
                app.op(
                    "operations.publish.staff_channel_permissions",
                    permissions=", ".join(missing),
                    channel=channel.mention,
                )
            )
        else:
            lines.append(app.op("operations.publish.alerts", channel=channel.mention))
    unmentionable: list[str] = []
    can_ping_all = (channel.permissions_for(me) if channel is not None else me.guild_permissions).mention_everyone
    for key in o.ping_roles:
        role = app.resources.role(guild, key)
        if role is None:
            lines.append(app.op("operations.publish.ping_role_missing", name=key))
        elif not role.mentionable and not can_ping_all:
            unmentionable.append(role.mention)
    if unmentionable:
        lines.append(app.op("operations.publish.ping_not_mentionable", roles=", ".join(unmentionable)))
    if o.owner_dm_alerts and not app.env.owner_id:
        lines.append(app.op("operations.publish.owner_unknown"))
    role_key = app.settings.checkout.customer_role
    if role_key and app.settings.features.checkout:
        customer_role = app.resources.role(guild, role_key)
        if customer_role is None:
            lines.append(app.op("operations.publish.customer_role_missing", name=role_key))
        elif not can_manage_role(me, customer_role) or not me.guild_permissions.manage_roles:
            lines.append(app.op("operations.publish.customer_role_hierarchy", role=customer_role.mention))
        restored = await customer_role_module.restore(app)
        if restored is not None:
            lines.append(app.op("operations.publish.customer_role_restored", count=restored))
    return lines
