"""Administrator slash commands: /status, /content, /reload, /backup, /audit, /tasks.

Every command is hidden from non-administrators by ``default_permissions(administrator=True)`` (a
convenience) and protected by ``require_admin()`` (authoritative, evaluated on every invocation). Replies are
ephemeral operator text.
"""

from __future__ import annotations

import io
import logging
import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from shopbot.commands import app_of, reply, require_admin
from shopbot.config.banner_ids import BannerId
from shopbot.db.repos import audit as audit_repo
from shopbot.db.repos import state as state_repo
from shopbot.db.repos import tasks as task_repo
from shopbot.features.admin import edit_text, service, views
from shopbot.features.admin import repo as admin_repo
from shopbot.features.admin.content_toml import UnrepresentableValue, render_content_toml
from shopbot.features.admin.state import state_for
from shopbot.logging_setup import log_event
from shopbot.ui.discord_render import check_modal, to_modal
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

AUTOCOMPLETE_LIMIT = 25
CHOICE_MAX = 100
TASK_LIST_LIMIT = 50
TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def _is_admin(interaction: discord.Interaction[Any]) -> bool:
    app = app_of(interaction)
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    return interaction.guild_id == app.env.guild_id and app.perms.is_admin(member, interaction.user.id)


def _choices(values: Sequence[str]) -> list[app_commands.Choice[str]]:
    usable = [v for v in values if 0 < len(v) <= CHOICE_MAX]
    return [app_commands.Choice(name=v, value=v) for v in usable[:AUTOCOMPLETE_LIMIT]]


async def feature_autocomplete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[str]]:
    if not _is_admin(interaction):
        return []
    needle = current.strip().lower()
    return _choices([n for n in service.feature_names(app_of(interaction)) if needle in n])


def _key_choices(app: App, keys: Sequence[str]) -> list[app_commands.Choice[str]]:
    """Choices named "key · current text" (so admins can recognise the text), with the key as value."""
    choices: list[app_commands.Choice[str]] = []
    for key in keys:
        if not 0 < len(key) <= CHOICE_MAX:
            continue
        text = " ".join((app.content.raw(key) or "").split())
        name = f"{key} · {text}" if text else key
        choices.append(app_commands.Choice(name=views.clip(name, CHOICE_MAX), value=key))
        if len(choices) == AUTOCOMPLETE_LIMIT:
            break
    return choices


async def key_autocomplete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[str]]:
    if not _is_admin(interaction):
        return []
    app = app_of(interaction)
    return _key_choices(app, service.suggest_keys(app, current, limit=AUTOCOMPLETE_LIMIT * 2))


async def clear_key_autocomplete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[str]]:
    """Registered keys plus unknown keys that still have an override (so stale overrides can be removed)."""
    if not _is_admin(interaction):
        return []
    app = app_of(interaction)
    needle = current.strip().lower()
    overrides = await app.db.run(state_repo.overrides_all)
    stale = sorted(k for k in overrides if k not in app.content.registry and needle in k)
    return _choices([*stale, *service.suggest_keys(app, current, limit=AUTOCOMPLETE_LIMIT * 2)])


async def _notice(
    interaction: discord.Interaction[Any],
    title: str,
    description: str | None = None,
    *,
    tone: str,
    banner: BannerId,
) -> None:
    """A one-embed operator reply (refusals, failures, work in progress) under ``banner``."""
    app = app_of(interaction)
    await reply(interaction, views.simple(app.ui, app.op, title, description, tone=tone, banner=banner))


async def _send_all(interaction: discord.Interaction[Any], specs: Sequence[MessageSpec]) -> None:
    for spec in specs:
        await reply(interaction, spec, ephemeral=True)


async def send_view_reply(interaction: discord.Interaction[Any], result: views.Reply) -> None:
    files: list[discord.File] = []
    if result.attachment is not None:
        filename, text = result.attachment
        files.append(discord.File(io.BytesIO(text.encode("utf-8")), filename=filename))
    await reply(interaction, result.spec, ephemeral=True, files=files)


async def _defer(interaction: discord.Interaction[Any]) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


def _known_feature(interaction: discord.Interaction[Any], feature: str | None) -> MessageSpec | None:
    """None when ``feature`` is empty or known; otherwise the operator 'unknown feature' reply."""
    app = app_of(interaction)
    names = service.feature_names(app)
    if feature is None or feature in names:
        return None
    return views.unknown_feature(app.ui, app.op, feature, names)


# --------------------------------------------------------------------------------------------------
# Handlers (separate from the decorated callbacks so they are easy to test)
# --------------------------------------------------------------------------------------------------


async def handle_status(interaction: discord.Interaction[Any], feature: str | None) -> None:
    app = app_of(interaction)
    if feature:
        status = service.feature_status(app, feature)
        if status is None:
            names = service.feature_names(app)
            await reply(interaction, views.unknown_feature(app.ui, app.op, feature, names), ephemeral=True)
            return
        await reply(interaction, views.status_feature(app.ui, app.op, feature, status, 1), ephemeral=True)
        return
    await _defer(interaction)
    snapshot = await service.status_snapshot(app)
    await reply(interaction, views.status_overview(app.ui, app.op, snapshot), ephemeral=True)


async def handle_content_set(interaction: discord.Interaction[Any], key: str) -> None:
    app = app_of(interaction)
    if key not in app.content.registry:
        await reply(interaction, views.unknown_key(app.ui, app.op, key, service.close_keys(app, key)))
        return
    await open_content_form(interaction, key)
    log_event(log, logging.INFO, "admin_content_form_opened", key=key, user_id=interaction.user.id)


async def handle_edit_text(interaction: discord.Interaction[Any], message: discord.Message) -> None:
    """ "Edit text" on a message: list the configured texts it shows; picking one opens the /content set form."""
    app = app_of(interaction)
    me = interaction.client.user
    if me is None or message.author.id != me.id:
        await _notice(interaction, app.op("admin.edit.not_bot"), tone="warning", banner="general-response")
        return
    await _defer(interaction)  # matching every text against every key can take a moment (3-second deadline)
    values = app.content.effective_values()
    keys = edit_text.find_keys(
        edit_text.message_texts(message),
        values,
        app.content.registry.all(),
        features=edit_text.message_features(message, app.router),
    )
    if not keys:
        title, text = app.op("admin.edit.none_title"), app.op("admin.edit.none")
        await _notice(interaction, title, text, tone="warning", banner="general-response")
        return
    await reply(interaction, views.edit_text_choices(app.ui, app.op, [(k, values[k]) for k in keys]))


async def open_content_form(interaction: discord.Interaction[Any], key: str) -> None:
    """Open the /content set form for a registered ``key``, prefilled with its current value."""
    app = app_of(interaction)
    token = state_for(app).pending.create(key, interaction.user.id, app.clock.now())
    spec = views.content_modal(app.op, token, key, app.content.raw(key))
    check_modal(spec)
    modal = to_modal(spec)
    await interaction.response.send_modal(modal)
    # The submission is routed by custom_id (``sb:ad:cset:<token>``); drop the in-memory modal copy.
    modal.stop()


async def handle_content_show(interaction: discord.Interaction[Any], key: str) -> None:
    app = app_of(interaction)
    info = service.key_info(app, key)
    if info is None:
        await reply(interaction, views.unknown_key(app.ui, app.op, key, service.close_keys(app, key)))
        return
    await send_view_reply(interaction, views.content_show(app.ui, app.op, info))


async def handle_content_clear(interaction: discord.Interaction[Any], key: str) -> None:
    app = app_of(interaction)
    overrides = await app.db.run(state_repo.overrides_all)
    if not service.clearable(app, key, overrides):
        await reply(interaction, views.unknown_key(app.ui, app.op, key, service.close_keys(app, key)))
        return
    await _defer(interaction)  # clearing can withdraw many panels over Discord (3-second reply deadline)
    result = await service.clear_override(app, key, actor_id=interaction.user.id)
    # The posted panels go back to the file's text right away (as after /content set), no /setup needed.
    panels = views.panel_refresh_lines(app.op, await service.refresh_panels(app)) if result.removed else []
    await reply(interaction, views.content_cleared(app.ui, app.op, result, panels))


async def handle_content_missing(interaction: discord.Interaction[Any], feature: str | None) -> None:
    app = app_of(interaction)
    unknown = _known_feature(interaction, feature)
    if unknown is not None:
        await reply(interaction, unknown)
        return
    grouped = service.missing_content(app, feature)
    await reply(interaction, views.content_missing(app.ui, app.op, feature, grouped, 1))


async def handle_content_keys(interaction: discord.Interaction[Any], feature: str | None, page: int) -> None:
    app = app_of(interaction)
    unknown = _known_feature(interaction, feature)
    if unknown is not None:
        await reply(interaction, unknown)
        return
    grouped = service.key_rows(app, feature)
    await reply(interaction, views.content_keys(app.ui, app.op, feature, grouped, page))


async def handle_content_export(interaction: discord.Interaction[Any]) -> None:
    app = app_of(interaction)
    await _defer(interaction)
    values = service.export_values(app)
    try:
        text = render_content_toml(app.content.registry, values, feature_order=service.feature_names(app))
    except (UnrepresentableValue, ValueError) as exc:
        await _notice(
            interaction, app.op("admin.content.export_failed"), str(exc)[:1000], tone="danger", banner="error"
        )
        return
    configured = sum(1 for v in values.values() if v)
    overrides = sum(1 for k in values if app.content.source(k) == "override")
    stamp = service.backup_stamp(app.clock.now())
    file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"content-{stamp}.toml")
    spec = views.content_export(app.ui, app.op, total=len(values), configured=configured, overrides=overrides)
    await reply(interaction, spec, ephemeral=True, files=[file])


async def handle_reload(interaction: discord.Interaction[Any]) -> None:
    app = app_of(interaction)
    await _defer(interaction)
    report = await service.reload_configuration(app, actor_id=interaction.user.id)
    await _send_all(interaction, views.reload_report(app.ui, app.op, report))


async def handle_backup(interaction: discord.Interaction[Any]) -> None:
    app = app_of(interaction)
    lock = state_for(app).backup_lock
    if lock.locked():
        await _notice(interaction, app.op("admin.backup.busy"), tone="warning", banner="submission-and-processing")
        return
    await _defer(interaction)
    async with lock:
        try:
            report = await service.create_backup(app, actor_id=interaction.user.id)
        except service.BackupUnavailable:
            await _notice(interaction, app.op("admin.backup.unavailable"), tone="danger", banner="error")
            return
        except (sqlite3.Error, OSError) as exc:
            log_event(log, logging.ERROR, "db_backup_failed", error=type(exc).__name__)
            error = f"{type(exc).__name__}: {exc}"[:1000]
            await _notice(interaction, app.op("admin.backup.failed"), error, tone="danger", banner="error")
            return
    spec = views.backup_report(
        app.ui, app.op, path=str(report.path), size=report.size, kept=report.kept, removed=len(report.removed)
    )
    await reply(interaction, spec, ephemeral=True)


async def handle_audit(
    interaction: discord.Interaction[Any], limit: int, target_type: str | None, target_id: str | None
) -> None:
    app = app_of(interaction)
    await _defer(interaction)
    events = await app.db.run(
        audit_repo.recent, app.env.guild_id, limit=limit, target_type=target_type, target_id=target_id
    )
    await _send_all(interaction, views.audit_list(app.ui, app.op, events))


async def handle_tasks(interaction: discord.Interaction[Any], status: str | None) -> None:
    app = app_of(interaction)
    await _defer(interaction)
    tasks = await app.db.run(task_repo.list_tasks, status=status, limit=TASK_LIST_LIMIT)
    counts = await app.db.run(admin_repo.task_counts)
    await _send_all(interaction, views.tasks_list(app.ui, app.op, tasks, counts))


# --------------------------------------------------------------------------------------------------
# Command definitions
# --------------------------------------------------------------------------------------------------


def build_content_group() -> app_commands.Group:
    group = app_commands.Group(
        name="content",
        description="Inspect and edit customer-facing text (content keys).",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @group.command(name="set", description="Set a content key's text (opens a form prefilled with the current value).")
    @app_commands.describe(key="Content key, e.g. tickets.panel.title")
    @app_commands.autocomplete(key=key_autocomplete)
    @require_admin()
    async def content_set(interaction: discord.Interaction[Any], key: app_commands.Range[str, 1, 200]) -> None:
        await handle_content_set(interaction, key)

    @group.command(name="show", description="Show a content key's current value, source and constraints.")
    @app_commands.describe(key="Content key")
    @app_commands.autocomplete(key=key_autocomplete)
    @require_admin()
    async def content_show(interaction: discord.Interaction[Any], key: app_commands.Range[str, 1, 200]) -> None:
        await handle_content_show(interaction, key)

    @group.command(name="clear", description="Remove a /content set override (content.toml applies again).")
    @app_commands.describe(key="Content key")
    @app_commands.autocomplete(key=clear_key_autocomplete)
    @require_admin()
    async def content_clear(interaction: discord.Interaction[Any], key: app_commands.Range[str, 1, 200]) -> None:
        await handle_content_clear(interaction, key)

    @group.command(name="missing", description="List required content that is missing or invalid.")
    @app_commands.describe(feature="Only this feature (default: all enabled features)")
    @app_commands.autocomplete(feature=feature_autocomplete)
    @require_admin()
    async def content_missing(interaction: discord.Interaction[Any], feature: str | None = None) -> None:
        await handle_content_missing(interaction, feature)

    @group.command(name="keys", description="List content keys with their status.")
    @app_commands.describe(feature="Only this feature (default: all)", page="Page number")
    @app_commands.autocomplete(feature=feature_autocomplete)
    @require_admin()
    async def content_keys(
        interaction: discord.Interaction[Any],
        feature: str | None = None,
        page: app_commands.Range[int, 1, 1000] = 1,
    ) -> None:
        await handle_content_keys(interaction, feature, page)

    @group.command(name="export", description="Download every content key with its current value as a TOML file.")
    @require_admin()
    async def content_export(interaction: discord.Interaction[Any]) -> None:
        await handle_content_export(interaction)

    return group


def build_top_level_commands() -> list[app_commands.Command[Any, ..., None]]:
    """Fresh command objects (one set per application instance)."""

    @app_commands.command(name="status", description="Show readiness, missing configuration, permissions and tasks.")
    @app_commands.describe(feature="Show the problems of one feature")
    @app_commands.autocomplete(feature=feature_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def status_command(interaction: discord.Interaction[Any], feature: str | None = None) -> None:
        await handle_status(interaction, feature)

    @app_commands.command(name="reload", description="Reload settings.toml and content.toml without restarting.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def reload_command(interaction: discord.Interaction[Any]) -> None:
        await handle_reload(interaction)

    @app_commands.command(name="backup", description="Create a database backup on the server's disk (keeps 10).")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def backup_command(interaction: discord.Interaction[Any]) -> None:
        await handle_backup(interaction)

    @app_commands.command(name="audit", description="Show recent audit log events.")
    @app_commands.describe(
        limit="Number of events (1-100, default 20)",
        target_type="Only events for this target type, e.g. ticket",
        target_id="Only events for this target ID",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def audit_command(
        interaction: discord.Interaction[Any],
        limit: app_commands.Range[int, 1, 100] = 20,
        target_type: app_commands.Range[str, 1, 100] | None = None,
        target_id: app_commands.Range[str, 1, 100] | None = None,
    ) -> None:
        await handle_audit(interaction, limit, target_type, target_id)

    @app_commands.command(name="tasks", description="Show scheduled tasks, including errors of failed ones.")
    @app_commands.describe(status="Only tasks with this status")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def tasks_command(interaction: discord.Interaction[Any], status: TaskStatus | None = None) -> None:
        await handle_tasks(interaction, status)

    return [status_command, reload_command, backup_command, audit_command, tasks_command]


EDIT_TEXT_COMMAND = "Edit text"


def build_message_commands() -> list[app_commands.ContextMenu]:
    """Message commands (right-click a message → Apps)."""

    @app_commands.context_menu(name=EDIT_TEXT_COMMAND)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @require_admin()
    async def edit_text_command(interaction: discord.Interaction[Any], message: discord.Message) -> None:
        await handle_edit_text(interaction, message)

    return [edit_text_command]


def register_commands(app: App) -> None:
    bot = app.bot
    if bot is None:
        return
    for command in build_top_level_commands():
        bot.tree.add_command(command, guild=app.guild_object)
    bot.tree.add_command(build_content_group(), guild=app.guild_object)
    for menu in build_message_commands():
        bot.tree.add_command(menu, guild=app.guild_object)
