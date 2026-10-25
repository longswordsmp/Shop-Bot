"""Interaction routing for buttons, selects, and forms.

All interactive components carry a custom_id ``sb:<route>:<action>[:<args>]`` and are wrapped in one
``RoutedItem`` dynamic item, registered once at startup. Form (modal) submissions are routed from the
client's ``on_interaction`` event. Nothing about a pending interaction lives only in memory, so every
control keeps working after a restart.

Before a handler runs, the router enforces, in order:
1. the interaction comes from the configured guild (or a DM, for routes that allow it);
2. the per-user rate limit;
3. no identical click from the same user is still being processed;
4. the route's feature is enabled and fully configured;
5. the route's access level (staff/admin) — always checked server-side.
Handlers then load records by ID and verify ownership/state themselves.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import discord

from shopbot.config.content import MissingContent
from shopbot.core.events import FAILURE
from shopbot.core.ratelimit import Cooldowns, InFlight, SlidingWindowLimiter
from shopbot.features import core_keys as ck
from shopbot.logging_setup import log_event
from shopbot.ui import custom_ids
from shopbot.ui.discord_render import RenderError, check_modal, edit_kwargs, send_kwargs, to_modal
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

Access = Literal["any", "staff", "admin"]
RouteKind = Literal["component", "modal"]
Handler = Callable[["Ctx"], Awaitable[None]]


class HandlerError(Exception):
    """Raise from a handler to answer with a specific configured error message."""

    def __init__(self, key: str = ck.ERROR_GENERIC, **values: str | int) -> None:
        super().__init__(key)
        self.key = key
        self.values = values


class RoutedItem(discord.ui.DynamicItem[discord.ui.Item[Any]], template=custom_ids.TEMPLATE):
    """Single persistent dynamic item for every routed button and select."""

    def __init__(self, item: discord.ui.Item[Any], *, row: int | None = None) -> None:
        super().__init__(item, row=row)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Any], item: discord.ui.Item[Any], match: Any, /
    ) -> RoutedItem:
        return cls(item)

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        app: App | None = getattr(interaction.client, "app", None)
        if app is None:
            return
        values: tuple[str, ...] = ()
        if isinstance(self.item, discord.ui.Select):
            values = tuple(str(v) for v in self.item.values)
        await app.router.dispatch(interaction, self.custom_id, kind="component", values=values)


def parse_modal_fields(data: Any) -> dict[str, str]:
    """Extract ``{custom_id: value}`` from raw modal-submit data (action rows and label components)."""
    fields: dict[str, str] = {}

    def walk(components: Any) -> None:
        for component in components or []:
            ctype = component.get("type")
            if ctype == 1:
                walk(component.get("components"))
            elif ctype == 18:
                walk([component.get("component")])
            elif ctype == 4 and "custom_id" in component:
                fields[str(component["custom_id"])] = str(component.get("value") or "")

    if isinstance(data, dict):
        walk(data.get("components"))
    return fields


@dataclass(frozen=True)
class Route:
    route: str
    action: str
    handler: Handler
    kind: RouteKind = "component"
    feature: str | None = None
    access: Access = "any"
    allow_dm: bool = False
    # Extra role keys (besides settings.permissions.staff_roles) that count as staff for this route,
    # e.g. checkout.staff_roles. Evaluated at click time so configuration reloads apply.
    staff_roles: Callable[[App], tuple[str, ...]] | None = None


class Ctx:
    """Everything a handler needs. Response helpers pick the correct Discord response type."""

    def __init__(
        self,
        app: App,
        interaction: discord.Interaction[Any],
        route: Route,
        args: tuple[str, ...],
        values: tuple[str, ...] = (),
        fields: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        self.interaction = interaction
        self.route = route
        self.args = args
        self.values = values
        self.fields = fields or {}
        self._deferred_update = False  # the response so far is ``defer_update`` (edits target the clicked message)

    # -- identity ---------------------------------------------------------------------------------

    @property
    def user_id(self) -> int:
        return self.interaction.user.id

    @property
    def member(self) -> discord.Member | None:
        user = self.interaction.user
        return user if isinstance(user, discord.Member) else None

    @property
    def guild(self) -> discord.Guild | None:
        return self.interaction.guild

    @property
    def channel_id(self) -> int | None:
        return self.interaction.channel_id

    @property
    def message_id(self) -> int | None:
        message = self.interaction.message
        return message.id if message is not None else None

    def arg_int(self, index: int) -> int:
        try:
            value = int(self.args[index])
        except (IndexError, ValueError):
            raise HandlerError(ck.ERROR_NOT_FOUND) from None
        if value < 0:
            raise HandlerError(ck.ERROR_NOT_FOUND)
        return value

    def is_admin(self) -> bool:
        return self.app.perms.is_admin(self.member, self.user_id)

    def is_staff(self, extra_role_keys: tuple[str, ...] = ()) -> bool:
        return self.app.perms.is_staff(self.member, self.user_id, extra_role_keys)

    # -- responses --------------------------------------------------------------------------------

    @property
    def responded(self) -> bool:
        return self.interaction.response.is_done()

    async def respond(self, spec: MessageSpec, *, ephemeral: bool = True) -> discord.Message | None:
        """Send a new message in reply to the interaction (private by default)."""
        kwargs = send_kwargs(spec)
        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(ephemeral=ephemeral, **kwargs)
            return None
        return await self.interaction.followup.send(ephemeral=ephemeral, wait=True, **kwargs)

    async def update(self, spec: MessageSpec) -> None:
        """Replace the message that holds the clicked component (single editable workflow message)."""
        # The clicked message's attachments are known (its banner can be kept) while the edit targets that message:
        # before any response, or after ``defer_update``. After another response the target is that response.
        message = self.interaction.message
        targets_message = not self.interaction.response.is_done() or self._deferred_update
        current = message.attachments if message is not None and targets_message else None
        kwargs = edit_kwargs(spec, current)
        if not self.interaction.response.is_done():
            await self.interaction.response.edit_message(**kwargs)
        else:
            await self.interaction.edit_original_response(**kwargs)

    async def defer_update(self) -> None:
        """Acknowledge a component click without changing anything yet (allows a later ``update``)."""
        if not self.interaction.response.is_done():
            await self.interaction.response.defer()
            self._deferred_update = True

    async def defer_reply(self, *, ephemeral: bool = True) -> None:
        """Acknowledge with a 'thinking' state; the answer follows via ``respond``."""
        if not self.interaction.response.is_done():
            await self.interaction.response.defer(ephemeral=ephemeral, thinking=True)

    async def send_modal(self, spec: ModalSpec) -> None:
        check_modal(spec)
        modal = to_modal(spec)
        await self.interaction.response.send_modal(modal)
        # Submissions are routed by custom_id from on_interaction; drop the in-memory copy so a restart
        # between opening and submitting the form behaves exactly like the normal path.
        modal.stop()

    async def ack(self) -> None:
        """Silently acknowledge (no visible message)."""
        if self.interaction.response.is_done():
            return
        if self.interaction.type == discord.InteractionType.component or (
            self.interaction.type == discord.InteractionType.modal_submit and self.interaction.message is not None
        ):
            await self.interaction.response.defer()
        else:
            await self.interaction.response.defer(ephemeral=True, thinking=False)

    async def fail(self, key: str = ck.ERROR_GENERIC, **values: str | int) -> None:
        """Answer privately with a configured error message, or acknowledge silently if it is not
        configured (an unconfigured message is never replaced by invented wording)."""
        embed = None
        try:
            embed = self.app.ui.error_embed(key, **values)
        except Exception:  # unknown key etc. — never let error reporting raise
            log.exception("error_embed failed for %s", key)
        if embed is None:
            await self.ack()
            return
        try:
            await self.respond(MessageSpec(embeds=(embed,)), ephemeral=True)
        except (discord.HTTPException, RenderError):
            log.exception("failed to send error response")


class Router:
    def __init__(self, app: App) -> None:
        self.app = app
        self._routes: dict[tuple[str, str, str], Route] = {}
        settings = app.settings.bot
        self._limiter = SlidingWindowLimiter(settings.rate_limit_per_10s, 10.0)
        self._inflight = InFlight()
        self._spacing = Cooldowns()
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._closing = False

    def add(self, route: Route) -> None:
        key = (route.kind, route.route, route.action)
        if key in self._routes:
            raise ValueError(f"route {key} registered twice")
        custom_ids.cid(route.route, route.action)  # validates names
        self._routes[key] = route

    def component(
        self,
        route: str,
        action: str,
        *,
        feature: str | None,
        access: Access = "any",
        allow_dm: bool = False,
        staff_roles: Callable[[App], tuple[str, ...]] | None = None,
    ) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.add(Route(route, action, handler, "component", feature, access, allow_dm, staff_roles))
            return handler

        return decorator

    def modal(
        self,
        route: str,
        action: str,
        *,
        feature: str | None,
        access: Access = "any",
        allow_dm: bool = False,
        staff_roles: Callable[[App], tuple[str, ...]] | None = None,
    ) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.add(Route(route, action, handler, "modal", feature, access, allow_dm, staff_roles))
            return handler

        return decorator

    def routes(self) -> list[Route]:
        return list(self._routes.values())

    def reset_limits(self) -> None:
        self._limiter = SlidingWindowLimiter(self.app.settings.bot.rate_limit_per_10s, 10.0)

    async def drain(self, wait_seconds: float) -> bool:
        """Stop starting new handlers and wait up to ``wait_seconds`` for the running ones (shutdown)."""
        self._closing = True
        if self._active == 0:
            return True
        log_event(log, logging.INFO, "router_draining", handlers=self._active, wait_seconds=wait_seconds)
        try:
            await asyncio.wait_for(self._idle.wait(), wait_seconds)
        except TimeoutError:
            log_event(log, logging.WARNING, "router_drain_timeout", handlers=self._active)
            return False
        return True

    async def dispatch(
        self,
        interaction: discord.Interaction[Any],
        custom_id: str,
        *,
        kind: RouteKind,
        values: tuple[str, ...] = (),
        fields: dict[str, str] | None = None,
    ) -> None:
        parsed = custom_ids.parse(custom_id)
        user_id = interaction.user.id
        if parsed is None:
            log_event(log, logging.WARNING, "interaction_unparseable", custom_id=custom_id[:100], user_id=user_id)
            return
        route_name, action, args = parsed
        route = self._routes.get((kind, route_name, action))
        if route is None:
            log_event(log, logging.WARNING, "interaction_unknown_route", kind=kind, route=route_name, action=action)
            stale = Route(route_name, action, _noop, kind)
            await Ctx(self.app, interaction, stale, args).fail(ck.ERROR_UNAVAILABLE)
            return
        ctx = Ctx(self.app, interaction, route, args, values, fields)

        if interaction.guild_id is None:
            if not route.allow_dm:
                await ctx.ack()
                return
        elif interaction.guild_id != self.app.env.guild_id:
            log_event(log, logging.WARNING, "interaction_foreign_guild", guild_id=interaction.guild_id)
            await ctx.ack()
            return

        if self._closing:
            # Shutting down: do not start work that the restart would cut off half-way.
            await ctx.fail(ck.ERROR_UNAVAILABLE)
            return

        retry_after = self._limiter.hit(user_id)
        if retry_after > 0:
            await ctx.fail(ck.ERROR_COOLDOWN, retry_after=math.ceil(retry_after))
            return

        flight_key = (user_id, kind, custom_id)
        spacing = self.app.settings.bot.interaction_cooldown_ms / 1000
        if spacing and self._spacing.remaining(flight_key, spacing) > 0:
            # The same control clicked again within the anti-double-click window: treat as a duplicate.
            if self.app.content.has(ck.ERROR_BUSY):
                await ctx.fail(ck.ERROR_BUSY)
            else:
                await ctx.ack()
            return
        if not self._inflight.try_acquire(flight_key):
            if self.app.content.has(ck.ERROR_BUSY):
                await ctx.fail(ck.ERROR_BUSY)
            else:
                await ctx.ack()
            return
        self._active += 1
        self._idle.clear()
        try:
            if route.feature is not None and not self.app.readiness.is_ready(route.feature):
                await ctx.fail(ck.ERROR_UNAVAILABLE)
                return
            extra_roles = route.staff_roles(self.app) if route.staff_roles is not None else ()
            denied = (route.access == "admin" and not ctx.is_admin()) or (
                route.access == "staff" and not ctx.is_staff(extra_roles)
            )
            if denied:
                log_event(log, logging.INFO, "interaction_forbidden", route=route_name, action=action, user_id=user_id)
                await ctx.fail(ck.ERROR_PERMISSION)
                return
            await self._run(ctx)
        finally:
            self._inflight.release(flight_key)
            self._spacing.mark(flight_key)
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def _run(self, ctx: Ctx) -> None:
        route = ctx.route
        try:
            await route.handler(ctx)
        except HandlerError as exc:
            values = dict(exc.values)
            if exc.key == ck.ERROR_GENERIC and "error_id" not in values:
                # Handlers report expected failures (e.g. a ticket channel could not be created) with the generic
                # text; give them a reference too, logged with the cause, so a customer's report can be matched.
                values["error_id"] = uuid.uuid4().hex[:12]
                cause = exc.__cause__ or exc.__context__
                log_event(
                    log,
                    logging.WARNING,
                    "interaction_generic_error",
                    route=route.route,
                    action=route.action,
                    user_id=ctx.user_id,
                    error_id=values["error_id"],
                    cause=type(cause).__name__ if cause is not None else None,
                )
            await ctx.fail(exc.key, **values)
            return
        except MissingContent as exc:
            log_event(
                log, logging.ERROR, "content_missing_at_runtime", key=exc.key, route=route.route, action=route.action
            )
            await ctx.fail(ck.ERROR_UNAVAILABLE)
            return
        except discord.Forbidden as exc:
            error_id = uuid.uuid4().hex[:12]
            log_event(
                log,
                logging.ERROR,
                "discord_forbidden",
                route=route.route,
                action=route.action,
                code=exc.code,
                error=str(exc.text)[:200],
                error_id=error_id,
            )
            await ctx.fail(ck.ERROR_GENERIC, error_id=error_id)
            await self._report_failure(route, error_id, exc)
            return
        except discord.NotFound as exc:
            log_event(log, logging.WARNING, "discord_not_found", route=route.route, action=route.action, code=exc.code)
            await ctx.fail(ck.ERROR_NOT_FOUND)
            return
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            log_event(
                log,
                logging.ERROR,
                "interaction_handler_failed",
                route=route.route,
                action=route.action,
                error_id=error_id,
                error=type(exc).__name__,
            )
            log.exception("handler failure %s", error_id)
            await ctx.fail(ck.ERROR_GENERIC, error_id=error_id)
            await self._report_failure(route, error_id, exc)
            return
        if not ctx.responded:
            log_event(log, logging.DEBUG, "interaction_auto_ack", route=route.route, action=route.action)
            await ctx.ack()

    async def _report_failure(self, route: Route, error_id: str, exc: BaseException) -> None:
        """Tell interested features (operations: audit + staff alert) about an unexpected handler failure."""
        await self.app.events.publish(
            FAILURE,
            kind="interaction",
            source=f"{route.route}:{route.action}",
            error_id=error_id,
            error=type(exc).__name__,
        )


async def _noop(ctx: Ctx) -> None:
    return None
